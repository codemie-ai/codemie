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
import posixpath
import re

from fastapi import status

from codemie.repository.base_file_repository import FileRepository
from codemie_tools.base.file_object import FileObject
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.skill import (
    SkillCompanionFilePayload,
)


class SkillFileStorage:
    """Thin wrapper around FileRepository that fixes the owner to ``skill-{skill_id}``.

    Keeps the naming logic in one place so callers never construct the owner string directly.
    """

    def __init__(self, skill_id: str, file_repo: FileRepository) -> None:
        self._owner = f"skill-{skill_id}"
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
        raw_path = path.strip().replace("\\", "/")
        normalized_path = posixpath.normpath(raw_path)

        if (
            not raw_path
            or raw_path.startswith("/")
            or normalized_path in {"", ".", ".."}
            or normalized_path.startswith("../")
            or re.match(r"^[A-Za-z]:", raw_path)
        ):
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Invalid companion file path",
                details=f"Companion file path '{path}' must be a safe relative file path",
                help="Provide a relative bundle path such as 'references/foo.md'",
            )
        return normalized_path
