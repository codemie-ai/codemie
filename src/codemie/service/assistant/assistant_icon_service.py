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

"""Localizes an assistant's uploaded icon into an assistant-owned entity file.

An icon uploaded through ``POST /v1/files/bulk`` is owned by the uploader, so other readers of the
assistant cannot fetch it. On save the file is copied to ``assistants/{assistant_id}/icon<ext>`` under the
shared storage-bucket owner, which Rule F of ``can_download`` serves to every reader of the assistant.
"""

from __future__ import annotations

import mimetypes
import re
from urllib.parse import unquote

from codemie_tools.base.file_object import FileObject
from fastapi import status

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.agent_workspace_repository import AgentWorkspaceRepository
from codemie.repository.repository_factory import FileRepositoryFactory
from codemie.rest_api.security.user import User
from codemie.service.file_service.download_rules import assistant_icon_name, can_download

_FILES_MARKER = "/v1/files/"
_ENTITY_ICON_NAME = re.compile(r"^assistants/([^/]+)/icon[^/]*$")


def _reject(details: str) -> ExtendedHTTPException:
    return ExtendedHTTPException(
        code=status.HTTP_400_BAD_REQUEST,
        message="Invalid assistant icon",
        details=details,
        help="Upload the icon again as an image file and save the assistant.",
    )


class AssistantIconService:
    @classmethod
    def localize_icon(
        cls, assistant_id: str, icon_url: str | None, current_icon_url: str | None, saver: User
    ) -> str | None:
        """Return the icon_url to persist, copying an uploaded icon to the assistant's entity file.

        ``current_icon_url`` is a value the saver may keep as is: the stored icon on update, or the icon of
        a clone source the saver can read. Such a value is converted when the saver can download its file
        and otherwise kept unchanged, never rejected. External URLs, empty values and this assistant's own
        entity icon pass through. Raises ExtendedHTTPException(400) when a new file cannot be used as an icon.
        """
        if not icon_url or _FILES_MARKER not in icon_url:
            return icon_url
        if icon_url == current_icon_url:
            return cls._convert_kept_icon(assistant_id, icon_url, saver)

        idx = icon_url.rfind(_FILES_MARKER) + len(_FILES_MARKER)
        prefix, token = icon_url[:idx], icon_url[idx:]
        file_object = cls.decode_token(token)

        if cls._is_own_entity_icon(assistant_id, file_object):
            return icon_url

        if not (file_object.mime_type or "").startswith("image/"):
            raise _reject("The icon file is not an image.")
        if not can_download(file_object, saver, None, token, AgentWorkspaceRepository()):
            logger.warning(f"Assistant icon rejected: file not readable by user_id={saver.id}")
            raise _reject("The icon file could not be found.")

        return prefix + cls.copy_as_entity_icon(assistant_id, file_object)

    @classmethod
    def _convert_kept_icon(cls, assistant_id: str, icon_url: str, saver: User) -> str:
        """Convert a kept legacy icon when the saver may download it; otherwise keep the value as it was."""
        try:
            return cls.localize_icon(assistant_id, icon_url, None, saver)
        except ExtendedHTTPException:
            logger.info(f"Assistant icon kept unchanged for assistant_id={assistant_id}: not convertible by saver")
            return icon_url

    @classmethod
    def copy_as_entity_icon(cls, assistant_id: str, file_object: FileObject, *, write: bool = True) -> str:
        """Copy the file to the assistant's entity icon blob and return its encoded token.

        With ``write=False`` the source is still read (so a missing file raises) but nothing is stored.
        """
        try:
            source = (
                FileRepositoryFactory()
                .get_current_repository()
                .read_file(file_name=file_object.name, owner=file_object.owner, mime_type=file_object.mime_type)
            )
        except Exception as e:
            logger.warning(f"Assistant icon source unreadable for assistant_id={assistant_id}")
            raise _reject("The icon file could not be read.") from e

        content = source.bytes_content()
        if content is None:
            raise _reject("The icon file is empty.")

        name = assistant_icon_name(assistant_id, mimetypes.guess_extension(file_object.mime_type) or "")
        owner = config.CODEMIE_STORAGE_BUCKET_NAME
        if not write:
            return FileObject(name=name, mime_type=file_object.mime_type, owner=owner).to_encoded_url()

        new_file = (
            FileRepositoryFactory()
            .get_current_repository()
            .write_file(name=name, mime_type=file_object.mime_type, owner=owner, content=content)
        )
        return new_file.to_encoded_url()

    @staticmethod
    def decode_token(token: str) -> FileObject:
        for candidate in (token, unquote(token)):
            try:
                return FileObject.from_encoded_url(candidate)
            except Exception:  # noqa: BLE001 - any decode failure is the same rejection
                continue
        raise _reject("The icon URL is not a valid file reference.")

    @staticmethod
    def _is_own_entity_icon(assistant_id: str, file_object: FileObject) -> bool:
        bucket = config.CODEMIE_STORAGE_BUCKET_NAME
        match = _ENTITY_ICON_NAME.match(file_object.name or "")
        return bool(bucket) and file_object.owner == bucket and bool(match) and match.group(1) == assistant_id
