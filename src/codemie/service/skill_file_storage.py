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

import base64
import binascii
import mimetypes

from fastapi import status

from codemie.repository.base_file_repository import FileRepository
from codemie_tools.base.file_object import FileObject
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.file_service.blob_ref_rules import skill_blob_owner
from codemie.service.file_service.workspace_path_rules import MAX_SKILL_BUNDLE_PATH_LENGTH, validate_relative_path
from codemie.rest_api.models.skill import (
    SkillCompanionFilePayload,
)

_INVALID_COMPANION_PATH_MSG = "Invalid companion file path"


class SkillFileStorage:
    """Thin wrapper around FileRepository that fixes the owner to ``skill-{skill_id}``.

    Keeps the naming logic in one place so callers never construct the owner string directly.
    """

    def __init__(self, skill_id: str, file_repo: FileRepository) -> None:
        self._owner = skill_blob_owner(skill_id)
        self._repo = file_repo

    def write_file(self, name: str, mime_type: str, content: bytes) -> FileObject:
        return self._repo.write_file(name=name, mime_type=mime_type, owner=self._owner, content=content)

    def read_file(self, file_name: str, mime_type: str = None) -> FileObject:
        return self._repo.read_file(file_name=file_name, owner=self._owner, mime_type=mime_type)

    @staticmethod
    def _strip_bundle_wrapper(paths: list[str]) -> dict[str, str]:
        """Strip an optional single top-level wrapper directory from zip member paths."""
        if not paths:
            return {}

        first_segments = {path.split("/", 1)[0] for path in paths if "/" in path}
        has_root_level_file = any("/" not in path for path in paths)

        if len(first_segments) == 1 and not has_root_level_file:
            wrapper = next(iter(first_segments))
            prefix = f"{wrapper}/"
            return {path: path[len(prefix) :] for path in paths}

        return {path: path for path in paths}

    @staticmethod
    def _encode_companion_file_record(
        path: str,
        raw_content: bytes,
    ) -> dict[str, str | int]:
        """Serialize companion file bytes into the storage format used by skill bundles."""
        normalized_path = SkillFileStorage._normalize_companion_file_path(path)
        mime_type = mimetypes.guess_type(normalized_path)[0] or "application/octet-stream"

        try:
            content = raw_content.decode("utf-8")
            encoding = "text"
        except UnicodeDecodeError:
            content = base64.b64encode(raw_content).decode("ascii")
            encoding = "base64"

        return {
            "path": normalized_path,
            "mime_type": mime_type,
            "encoding": encoding,
            "size_bytes": len(raw_content),
            "content": content,
        }

    @staticmethod
    def _decode_companion_file_payload(
        file_payload: SkillCompanionFilePayload,
    ) -> bytes:
        """Decode a companion file payload into raw bytes before normalizing storage."""
        if file_payload.encoding == "base64":
            try:
                return base64.b64decode(file_payload.content, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ExtendedHTTPException(
                    code=status.HTTP_400_BAD_REQUEST,
                    message="Invalid companion file payload",
                    details=f"Companion file '{file_payload.path}' is not valid base64: {exc}",
                    help="Ensure binary companion files are base64-encoded before sending them",
                )

        if file_payload.encoding != "text":
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Invalid companion file payload",
                details=f"Companion file '{file_payload.path}' has unsupported encoding '{file_payload.encoding}'",
                help="Use 'text' for UTF-8 files or 'base64' for binary payloads",
            )

        return file_payload.content.encode("utf-8")

    @staticmethod
    def _normalize_companion_file_path(path: str) -> str:
        """Normalize a bundle-relative companion file path."""
        try:
            return validate_relative_path(path, MAX_SKILL_BUNDLE_PATH_LENGTH)
        except ValueError as exc:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message=_INVALID_COMPANION_PATH_MSG,
                details=f"Companion file path '{path}' is invalid: {exc}",
                help=(
                    "Provide a relative bundle path such as 'references/foo.md', "
                    f"at most {MAX_SKILL_BUNDLE_PATH_LENGTH} characters"
                ),
            ) from exc
