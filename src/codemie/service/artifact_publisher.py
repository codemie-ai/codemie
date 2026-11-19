# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The one publisher of produced files: validates, names and stores them in the conversation workspace."""

from __future__ import annotations

import base64
import dataclasses
import mimetypes
import threading
import uuid
from pathlib import Path, PurePosixPath

from codemie.configs import config, logger
from codemie.core.exceptions import ValidationException
from codemie.repository.base_file_repository import FileRepository
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie.service.file_service.sandbox_links import SANDBOX_FILE_PREFIX
from codemie_tools.base.artifact_publishing import (
    PublishContext,
    PublishResult,
    strip_unsafe_name_chars,
    zip_container_error,
)

DEFAULT_ARTIFACT_NAME = "artifact"

# Parallel tool calls of one turn share its folder: picking a free name and writing it must not interleave.
# shortcut: serializes within one process only; move to an insert-if-absent on (workspace_id, path) if
# publishes of one conversation can run on several workers at once.
_PUBLISH_LOCKS = tuple(threading.Lock() for _ in range(64))  # striped by turn: unrelated publishes do not queue


class _Refused(Exception):
    """A validation failure whose message is the user-facing reason."""


class ArtifactPublisherService:
    """Implements `ArtifactPublisher`; `publish` never raises."""

    def __init__(self, file_repository: FileRepository | None, workspace_service: AgentWorkspaceService | None = None):
        self.file_repository = file_repository
        self.workspace_service = workspace_service or AgentWorkspaceService()

    def publish(  # noqa: PLR0913 - keyword-only payload options mirror the ArtifactPublisher protocol
        self,
        ctx: PublishContext,
        name: str,
        *,
        content: bytes | str | None = None,
        path: str | None = None,
        mime: str | None = None,
        base64_content: bool = False,
    ) -> PublishResult:
        name = self._last_segment(name)
        try:
            if not ctx.user_id or not ctx.user_id.strip():
                raise _Refused(f"`{name}` was not saved: no user is known to own it")
            data = self._read_payload(name, content, path, base64_content)
            if zip_error := zip_container_error(name, data):
                raise _Refused(f"`{name}` was not saved: {zip_error}")
            mime = mime or mimetypes.guess_type(name)[0] or "application/octet-stream"
            if ctx.conversation_id:
                name, link = self._publish_to_workspace(ctx, name, data, mime)
            else:
                link = self._publish_per_user(ctx, name, data, mime)
            return PublishResult(link=link, name=name, size=len(data), mime=mime)
        except _Refused as exc:
            return PublishResult(name=name, reason=str(exc))
        except ValidationException as exc:
            # An invalid workspace path (too long, control characters) is the input's fault, not storage's.
            return PublishResult(name=name, reason=f"`{name}` was not saved: {exc}")
        except Exception:
            logger.exception(f"Publishing artifact {name} failed")
            return PublishResult(name=name, reason=f"`{name}` could not be saved because of a storage error")

    @staticmethod
    def _last_segment(name: str) -> str:
        segment = PurePosixPath(strip_unsafe_name_chars(name or "").replace("\\", "/")).name.strip()
        return segment if segment not in ("", ".", "..") else DEFAULT_ARTIFACT_NAME

    @staticmethod
    def _read_payload(name: str, content: bytes | str | None, path: str | None, base64_content: bool) -> bytes:
        max_size = config.FILES_STORAGE_MAX_UPLOAD_SIZE
        too_big = f"`{name}` exceeds the maximum upload size of {max_size} bytes and was not saved"
        if path is not None:
            data = ArtifactPublisherService._read_file(name, path, max_size, too_big)
        elif base64_content:
            data = ArtifactPublisherService._decode_base64(name, content or b"", max_size, too_big)
        else:
            data = content.encode("utf-8") if isinstance(content, str) else (content or b"")
            if len(data) > max_size:
                raise _Refused(too_big)
        # An empty base64 payload (MCP `blob=""`, an image) delivers nothing; a real file may be empty, as on main.
        if not data and base64_content and path is None:
            raise _Refused(f"`{name}` was not saved: it is empty")
        return data

    @staticmethod
    def _read_file(name: str, path: str, max_size: int, too_big: str) -> bytes:
        try:
            size = Path(path).stat().st_size
        except OSError as exc:
            raise _Refused(f"`{name}` was not saved: the file could not be read") from exc
        if size > max_size:
            raise _Refused(too_big)
        return Path(path).read_bytes()

    @staticmethod
    def _decode_base64(name: str, encoded: bytes | str, max_size: int, too_big: str) -> bytes:
        # Wrapped base64 (RFC 2045 line breaks) is valid input; any other non-alphabet byte is still refused. Bound
        # the raw length first so stripping never copies a payload that is hopelessly over the limit.
        if len(encoded) * 3 // 4 > 2 * max_size:
            raise _Refused(too_big)
        encoded = b"".join(encoded.split()) if isinstance(encoded, bytes) else "".join(encoded.split())
        # Decoded size is 3/4 of the encoded length; checked before decoding so a huge payload is never decoded.
        if len(encoded) * 3 // 4 > max_size:
            raise _Refused(too_big)
        try:
            return base64.b64decode(encoded, validate=True)
        except ValueError as exc:  # binascii.Error is a ValueError
            raise _Refused(f"`{name}` was not saved: its content is not valid base64") from exc

    def _publish_to_workspace(self, ctx: PublishContext, name: str, data: bytes, mime: str) -> tuple[str, str]:
        if not ctx.turn_id.strip():
            ctx = dataclasses.replace(ctx, turn_id=uuid.uuid4().hex)
        with _PUBLISH_LOCKS[hash((ctx.conversation_id, ctx.turn_id)) % len(_PUBLISH_LOCKS)]:
            name = self._unique_name(ctx, name)
            link = self.workspace_service.publish_artifact_file(
                ctx.conversation_id, User(id=ctx.user_id), f"artifacts/{ctx.turn_id}/{name}", data, mime
            )
        return name, link

    def _unique_name(self, ctx: PublishContext, name: str) -> str:
        repository = self.workspace_service.repository
        workspace = repository.get_by_conversation_for_user(ctx.conversation_id, ctx.user_id)
        if workspace is None:
            return name
        stem, suffix = PurePosixPath(name).stem, PurePosixPath(name).suffix
        candidate, counter = name, 1
        while repository.get_file(workspace.id, f"artifacts/{ctx.turn_id}/{candidate}", include_deleted=True):
            counter += 1
            candidate = f"{stem} ({counter}){suffix}"
        return candidate

    def _publish_per_user(self, ctx: PublishContext, name: str, data: bytes, mime: str) -> str:
        if not self.file_repository:
            raise _Refused(f"`{name}` was not delivered: storage is not configured")
        logger.warning(f"No conversation for artifact {name}; storing it for user {ctx.user_id} only")
        stored = self.file_repository.write_file(
            name=f"{uuid.uuid4()}_{name}", mime_type=mime, content=data, owner=ctx.user_id
        )
        return f"{SANDBOX_FILE_PREFIX}{stored.to_encoded_url()}"
