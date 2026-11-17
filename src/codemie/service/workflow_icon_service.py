# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""Validation and storage for workflow icon images uploaded as files."""

from __future__ import annotations

import re
import uuid
from pathlib import PurePosixPath

from codemie_tools.base.file_object import FileObject

from codemie.configs import config, logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.repository_factory import FileRepositoryFactory

MIME_PNG = "image/png"
MIME_JPEG = "image/jpeg"
MIME_SVG = "image/svg+xml"
UNSUPPORTED_ICON_TYPE_MESSAGE = "Unsupported icon file type"
WORKFLOW_ICON_PREFIX = "workflow-icons/"
WORKFLOW_ICON_MAX_BYTES = 512 * 1024
ALLOWED_ICON_MIME_TYPES: dict[str, str] = {
    MIME_PNG: "png",
    MIME_JPEG: "jpg",
    MIME_SVG: "svg",
}
# File name extensions an uploaded icon may carry, each mapped to the image type it must be declared as.
ICON_EXTENSION_MIME_TYPES: dict[str, str] = {
    "png": MIME_PNG,
    "jpg": MIME_JPEG,
    "jpeg": MIME_JPEG,
    "svg": MIME_SVG,
}

# Only names this module generates are readable as icons: uuid4 hex plus a known extension, no path segments.
# Anchored by fullmatch in is_workflow_icon_file, so a trailing newline cannot slip past the pattern.
_ICON_NAME_RE = re.compile(rf"{re.escape(WORKFLOW_ICON_PREFIX)}[0-9a-f]{{32}}\.(png|jpg|svg)")


def _sniff_mime(data: bytes) -> str | None:
    """Identify the image type from magic bytes, ignoring whatever the client declared."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return MIME_PNG
    if data.startswith(b"\xff\xd8\xff"):
        return MIME_JPEG
    if b"<svg" in data[:1024].lower():
        return MIME_SVG
    return None


class WorkflowIconService:
    """Validation and storage of workflow icon files."""

    @staticmethod
    def is_icon_file(owner: str, name: str) -> bool:
        return owner == config.CODEMIE_STORAGE_BUCKET_NAME and _ICON_NAME_RE.fullmatch(name) is not None

    @staticmethod
    def check_filename(filename: str, declared_mime: str) -> None:
        """Reject an upload whose file name extension does not belong to its declared image type."""
        extension = PurePosixPath(filename).suffix.lower().lstrip(".")
        if ICON_EXTENSION_MIME_TYPES.get(extension) != declared_mime:
            raise ExtendedHTTPException(
                code=400,
                message=UNSUPPORTED_ICON_TYPE_MESSAGE,
                details=f"File extension .{extension} does not match the declared type {declared_mime}",
            )

    @staticmethod
    def store(data: bytes, declared_mime: str) -> FileObject:
        """Validate the uploaded bytes and store them under the workflow icon namespace."""
        if declared_mime not in ALLOWED_ICON_MIME_TYPES:
            raise ExtendedHTTPException(
                code=400,
                message=UNSUPPORTED_ICON_TYPE_MESSAGE,
                details=f"Allowed types: {', '.join(ALLOWED_ICON_MIME_TYPES)}",
            )
        if not data:
            raise ExtendedHTTPException(code=400, message="Invalid icon file", details="The file is empty")
        if _sniff_mime(data) != declared_mime:
            raise ExtendedHTTPException(
                code=400,
                message=UNSUPPORTED_ICON_TYPE_MESSAGE,
                details=f"File content does not match the declared type {declared_mime}",
            )

        name = f"{WORKFLOW_ICON_PREFIX}{uuid.uuid4().hex}.{ALLOWED_ICON_MIME_TYPES[declared_mime]}"
        repo = FileRepositoryFactory.get_current_repository()
        return repo.write_file(
            name=name,
            mime_type=declared_mime,
            owner=config.CODEMIE_STORAGE_BUCKET_NAME,
            content=data,
        )

    @staticmethod
    def exists(file_url: str) -> bool:
        """True when the encoded file URL points at an existing workflow icon in the namespace."""
        try:
            file_object = FileObject.from_encoded_url(file_url)
        except Exception:
            return False
        if not WorkflowIconService.is_icon_file(file_object.owner, file_object.name):
            return False
        try:
            FileRepositoryFactory.get_current_repository().read_file(file_object.name, file_object.owner)
            return True
        except Exception:
            logger.debug(f"Workflow icon not found or unreadable: {file_object.name}")
            return False
