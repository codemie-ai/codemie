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

from __future__ import annotations

import json
from typing import Optional, Type

from pydantic import BaseModel, Field, PrivateAttr

from codemie.rest_api.models.agent_workspace import CreateAgentWorkspaceRequest
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.data_management.workspace.execute_workspace_script_tool import (
    ExecuteWorkspaceScriptTool,
)
from codemie_tools.data_management.workspace.tools_vars import (
    DELETE_WORKSPACE_FILE_TOOL,
    EDIT_WORKSPACE_FILE_TOOL,
    EXECUTE_WORKSPACE_SCRIPT_TOOL,
    GREP_WORKSPACE_FILES_TOOL,
    LIST_WORKSPACE_FILES_TOOL,
    READ_WORKSPACE_FILE_TOOL,
    WRITE_WORKSPACE_FILE_TOOL,
)


class ListWorkspaceFilesInput(BaseModel):
    prefix: Optional[str] = Field(
        default=None,
        description="Optional workspace-relative path prefix to filter files.",
    )
    recursive: bool = Field(
        default=True,
        description="Whether to search recursively under the given prefix.",
    )


class ReadWorkspaceFileInput(BaseModel):
    file_path: str = Field(description="Workspace-relative file path to read.")


class WriteWorkspaceFileInput(BaseModel):
    file_path: str = Field(description="Workspace-relative file path to create or overwrite.")
    content: str = Field(description="UTF-8 text content to write into the workspace file.")


class EditWorkspaceFileInput(BaseModel):
    file_path: str = Field(description="Workspace-relative file path to edit.")
    old_string: str = Field(description="String to replace. Must be unique unless replace_all is true.")
    new_string: str = Field(description="Replacement string.")
    replace_all: bool = Field(
        default=False,
        description="Replace all occurrences instead of the first occurrence.",
    )


class DeleteWorkspaceFileInput(BaseModel):
    file_path: str = Field(description="Workspace-relative file path to delete.")


class GrepWorkspaceFilesInput(BaseModel):
    query: str = Field(description="Case-insensitive search string to find in workspace text files.")
    prefix: Optional[str] = Field(
        default=None,
        description="Optional workspace-relative prefix to limit the search.",
    )
    recursive: bool = Field(
        default=True,
        description="Whether to search recursively under the given prefix.",
    )


class BaseWorkspaceTool(CodeMieTool):
    conversation_id: str = Field(exclude=True)
    user: User = Field(exclude=True)
    workspace_service: AgentWorkspaceService = Field(default_factory=AgentWorkspaceService, exclude=True)
    _workspace_id: str | None = PrivateAttr(default=None)

    def _get_workspace_id(self) -> str:
        if self._workspace_id is None:
            workspace = self.workspace_service.create_workspace(
                CreateAgentWorkspaceRequest(conversation_id=self.conversation_id),
                self.user,
            )
            self._workspace_id = workspace.id
        return self._workspace_id

    @staticmethod
    def _dump_json(payload) -> str:
        if isinstance(payload, list):
            data = [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in payload]
        elif hasattr(payload, "model_dump"):
            data = payload.model_dump(mode="json")
        else:
            data = payload
        return json.dumps(data, ensure_ascii=False, indent=2)


class ListWorkspaceFilesTool(BaseWorkspaceTool):
    name: str = LIST_WORKSPACE_FILES_TOOL.name
    description: str = LIST_WORKSPACE_FILES_TOOL.description
    args_schema: Type[BaseModel] = ListWorkspaceFilesInput

    def execute(self, prefix: str | None = None, recursive: bool = True) -> str:
        workspace_id = self._get_workspace_id()
        files = self.workspace_service.list_files(workspace_id, self.user, prefix=prefix, recursive=recursive)
        return self._dump_json(files)


class ReadWorkspaceFileTool(BaseWorkspaceTool):
    name: str = READ_WORKSPACE_FILE_TOOL.name
    description: str = READ_WORKSPACE_FILE_TOOL.description
    args_schema: Type[BaseModel] = ReadWorkspaceFileInput

    def execute(self, file_path: str) -> str:
        workspace_id = self._get_workspace_id()
        file_content = self.workspace_service.get_file_content(workspace_id, file_path, self.user)
        return self._dump_json(file_content)


class WriteWorkspaceFileTool(BaseWorkspaceTool):
    name: str = WRITE_WORKSPACE_FILE_TOOL.name
    description: str = WRITE_WORKSPACE_FILE_TOOL.description
    args_schema: Type[BaseModel] = WriteWorkspaceFileInput

    def execute(self, file_path: str, content: str) -> str:
        workspace_id = self._get_workspace_id()
        workspace_file = self.workspace_service.upsert_text_file(workspace_id, file_path, content, self.user)
        return self._dump_json(workspace_file)


class EditWorkspaceFileTool(BaseWorkspaceTool):
    name: str = EDIT_WORKSPACE_FILE_TOOL.name
    description: str = EDIT_WORKSPACE_FILE_TOOL.description
    args_schema: Type[BaseModel] = EditWorkspaceFileInput

    def execute(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> str:
        workspace_id = self._get_workspace_id()
        response = self.workspace_service.edit_file(
            workspace_id=workspace_id,
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
            user=self.user,
        )
        return self._dump_json(response)


class DeleteWorkspaceFileTool(BaseWorkspaceTool):
    name: str = DELETE_WORKSPACE_FILE_TOOL.name
    description: str = DELETE_WORKSPACE_FILE_TOOL.description
    args_schema: Type[BaseModel] = DeleteWorkspaceFileInput

    def execute(self, file_path: str) -> str:
        workspace_id = self._get_workspace_id()
        response = self.workspace_service.delete_file(workspace_id, file_path, self.user)
        return self._dump_json(response)


class GrepWorkspaceFilesTool(BaseWorkspaceTool):
    name: str = GREP_WORKSPACE_FILES_TOOL.name
    description: str = GREP_WORKSPACE_FILES_TOOL.description
    args_schema: Type[BaseModel] = GrepWorkspaceFilesInput

    def execute(self, query: str, prefix: str | None = None, recursive: bool = True) -> str:
        workspace_id = self._get_workspace_id()
        matches = self.workspace_service.grep_files(workspace_id, query, self.user, prefix=prefix, recursive=recursive)
        return self._dump_json(matches)
