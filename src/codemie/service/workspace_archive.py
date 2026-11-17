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

"""Download-all: every file a workspace lists, zipped in memory and audited once per request."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from codemie.configs import config, logger
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ValidationException
from codemie.rest_api.models.agent_workspace import AgentWorkspace, AgentWorkspaceFile
from codemie.rest_api.security.user import User
from codemie.service.file_service.workspace_path_rules import (
    MAX_WORKSPACE_PATH_LENGTH,
    is_reserved_workspace_path,
    validate_relative_path,
)
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService

if TYPE_CHECKING:
    from codemie.service.agent_workspace_service import AgentWorkspaceService

SKIPPED_MANIFEST_NAME = "_SKIPPED_FILES.txt"
WORKSPACE_ARCHIVE_METRIC = "workspace_archive_download"


class WorkspaceArchive(NamedTuple):
    """A workspace's files as one zip, plus the workspace paths that had to be left out."""

    content: bytes
    filename: str
    included_count: int
    skipped_paths: list[str]
    total_bytes: int


class WorkspaceArchiveTooLargeError(Exception):
    """The workspace holds more than one archive may carry; the message is safe to show the client."""


class WorkspaceArchiveFailedError(Exception):
    """No workspace file could be read into the archive; the message is safe to show the client."""


@dataclass
class _ArchiveProgress:
    """What one archive request got through so far, for its single audit line."""

    outcome: str = "failed"
    included: int = 0
    skipped: int = 0
    total_bytes: int = 0


def build_workspace_archive(service: AgentWorkspaceService, workspace_id: str, user: User) -> WorkspaceArchive:
    """Zip every file the workspace lists (see ``_zip_workspace_files``); each request is audited exactly once."""
    progress = _ArchiveProgress()
    try:
        archive = _zip_workspace_files(service, workspace_id, user, progress)
        progress.outcome = "partial" if archive.skipped_paths else "success"
        return archive
    except WorkspaceArchiveTooLargeError:
        progress.outcome = "too_large"
        raise
    finally:
        _audit_archive(user, workspace_id, progress)


def _archive_filename(workspace: AgentWorkspace) -> str:
    """A header-safe zip name from the workspace name, falling back to its id."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", workspace.name or "").strip("._")
    return f"{stem or re.sub(r'[^A-Za-z0-9._-]+', '_', workspace.id)}.zip"


def _audit_archive(user: User, workspace_id: str, progress: _ArchiveProgress) -> None:
    logger.info(
        f"Workspace archive download: user_id={user.id} workspace_id={workspace_id} outcome={progress.outcome} "
        f"included={progress.included} skipped={progress.skipped} total_bytes={progress.total_bytes}"
    )
    try:
        BaseMonitoringService.send_count_metric(
            name=WORKSPACE_ARCHIVE_METRIC, attributes={"outcome": progress.outcome, "user_id": user.id}
        )
    except Exception as exc:  # noqa: BLE001 - a metrics outage must not fail the download
        logger.warning(f"Workspace archive metric not sent: {type(exc).__name__}")


def _zip_workspace_files(
    service: AgentWorkspaceService, workspace_id: str, user: User, progress: _ArchiveProgress
) -> WorkspaceArchive:
    """Zip every file the workspace lists, in memory.

    An entry is named by the stored workspace path and holds the stored bytes, read as ``application/octet-stream``
    so no backend decodes text. A file that cannot be read, or whose stored path is not a safe zip entry name, is
    left out and listed in ``_SKIPPED_FILES.txt``.
    """
    workspace = service.get_workspace(workspace_id, user)
    _ensure_conversation_readable(workspace, user)
    files = service._workspace_source_files(workspace)
    if not files:
        progress.outcome = "empty"
        raise ValidationException("Workspace has no downloadable files")
    max_files = config.WORKSPACE_DOWNLOAD_MAX_FILES
    if len(files) > max_files:
        raise WorkspaceArchiveTooLargeError(
            f"Workspace has {len(files)} files; an archive holds at most {max_files}. "
            "Download individual files instead."
        )
    max_bytes = config.WORKSPACE_DOWNLOAD_MAX_BYTES

    buffer = io.BytesIO()
    names: set[str] = set()
    skipped: list[str] = []
    total = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for workspace_file in files:
            data = _read_archive_entry(service, workspace_file)
            if data is None:
                skipped.append(workspace_file.path)
                progress.skipped += 1
                continue
            total += len(data)
            progress.total_bytes = total
            if total > max_bytes:
                raise WorkspaceArchiveTooLargeError(
                    f"Workspace files exceed the archive limit of {max_bytes} bytes. "
                    "Download individual files instead."
                )
            archive.writestr(workspace_file.path, data)
            names.add(workspace_file.path)
            progress.included += 1
        if not names:
            raise WorkspaceArchiveFailedError("None of the workspace files could be read")
        if skipped:
            archive.writestr(_free_manifest_name(names), "\n".join(skipped) + "\n")

    return WorkspaceArchive(buffer.getvalue(), _archive_filename(workspace), len(names), skipped, total)


def _ensure_conversation_readable(workspace: AgentWorkspace, user: User) -> None:
    """Conversation uploads are archived too, so the caller must be able to read that conversation.

    ``create_workspace`` accepts any conversation id; without this a caller could archive another user's uploads.
    Fails like a missing workspace so nothing about the conversation is revealed.
    """
    from codemie.rest_api.models.conversation import Conversation  # deferred: models import the service layer

    conversation = Conversation.find_by_id(workspace.conversation_id)
    if conversation is not None and not Ability(user).can(Action.READ, conversation):
        raise ValidationException(f"Workspace '{workspace.id}' not found")


def _read_archive_entry(service: AgentWorkspaceService, workspace_file: AgentWorkspaceFile) -> bytes | None:
    """The file's bytes, or None when its path is not a safe entry name or its blob cannot be read."""
    path = workspace_file.path
    try:
        safe = validate_relative_path(path, MAX_WORKSPACE_PATH_LENGTH) == path and not is_reserved_workspace_path(path)
    except ValueError:
        safe = False
    if not safe:
        logger.warning(f"Workspace file {path!r} left out of the archive: not a safe entry name")
        return None
    try:
        # octet-stream makes every backend return raw bytes (GCP and the filesystem otherwise decode text types)
        file_object = service.file_repository.read_file(
            file_name=workspace_file.blob_name, owner=workspace_file.blob_owner, mime_type="application/octet-stream"
        )
    except Exception as exc:  # noqa: BLE001 - storage backends raise different not-found errors
        logger.warning(f"Workspace file {path!r} left out of the archive: blob unreadable: {type(exc).__name__}")
        return None
    return file_object.bytes_content() or b""


def _free_manifest_name(taken: set[str]) -> str:
    name, index = SKIPPED_MANIFEST_NAME, 0
    while name in taken:
        index += 1
        name = f"_SKIPPED_FILES_{index}.txt"
    return name
