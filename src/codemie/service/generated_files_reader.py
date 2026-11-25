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

"""Workspace-backed source of the files an agent generated in one conversation (read-only)."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import ToolException

from codemie.configs.config import config
from codemie.repository.agent_workspace_repository import AgentWorkspaceRepository
from codemie.repository.repository_factory import FileRepositoryFactory
from codemie.service.file_service.workspace_path_rules import is_reserved_workspace_path
from codemie_tools.base.generated_files import GeneratedFileInfo


class WorkspaceGeneratedFiles:
    """Lists and reads the files (any path, except reserved runtime paths) of one conversation workspace."""

    def __init__(
        self,
        conversation_id: str,
        user_id: str,
        repository: AgentWorkspaceRepository | None = None,
        file_repository: Any = None,
    ):
        self._conversation_id = conversation_id
        self._user_id = user_id
        self._repository = repository or AgentWorkspaceRepository()
        self._file_repository = file_repository
        self._rows: dict[str, Any] = {}

    def _rows_for_workspace(self) -> list[Any]:
        workspace = self._repository.get_by_conversation_for_user(self._conversation_id, self._user_id)
        if workspace is None:
            return []
        return self._repository.list_files(workspace.id)

    def list_generated(self) -> list[GeneratedFileInfo]:
        infos: list[GeneratedFileInfo] = []
        self._rows = {}
        for row in self._rows_for_workspace():
            if not row.path or is_reserved_workspace_path(row.path):
                continue
            name = row.path.rsplit("/", 1)[-1]
            if not name:
                continue
            self._rows[row.path] = row
            infos.append(
                GeneratedFileInfo(
                    name=name,
                    size=row.size or 0,
                    updated=row.update_date,
                    path=row.path,
                    blob=(row.blob_owner, row.blob_name, row.mime_type),
                    checksum=row.checksum,
                )
            )
        return infos

    def read_generated(self, info: GeneratedFileInfo) -> bytes:
        limit = config.FILES_STORAGE_MAX_UPLOAD_SIZE
        if limit and info.size > limit:
            raise ToolException(f"File '{info.name}' is too large to attach ({info.size} bytes, limit {limit}).")
        row = self._rows.get(info.path)
        if row is None:
            self.list_generated()
            row = self._rows.get(info.path)
        if row is None:
            raise ToolException(f"Generated file '{info.name}' is no longer available.")
        file_repository = self._file_repository or FileRepositoryFactory.get_current_repository()
        file_object = file_repository.read_file(file_name=row.blob_name, owner=row.blob_owner, mime_type=row.mime_type)
        content = file_object.content
        data = content if isinstance(content, bytes) else str(content).encode("utf-8")
        if limit and len(data) > limit:
            raise ToolException(f"File '{info.name}' is too large to attach ({len(data)} bytes, limit {limit}).")
        return data
