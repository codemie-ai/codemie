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

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from sqlmodel import Field as SQLField

from codemie.core.models import BaseResponse
from codemie.rest_api.models.base import BaseModelWithSQLSupport


class AgentWorkspace(BaseModelWithSQLSupport, table=True):
    __tablename__ = "agent_workspaces"

    conversation_id: str = SQLField(index=True)
    user_id: str = SQLField(index=True)
    name: Optional[str] = None
    status: str = SQLField(default="active")


class AgentWorkspaceFile(BaseModelWithSQLSupport, table=True):
    __tablename__ = "agent_workspace_files"

    workspace_id: str = SQLField(index=True)
    path: str = SQLField(index=True)
    blob_owner: str
    blob_name: str
    mime_type: str
    checksum: str
    size: int = 0
    version: int = 1
    deleted_at: Optional[datetime] = None


class CreateAgentWorkspaceRequest(BaseModel):
    conversation_id: str = Field(description="Conversation or session identifier that owns this workspace")
    name: Optional[str] = Field(default=None, description="Optional display name for the workspace")


class AgentWorkspaceResponse(BaseModel):
    id: str
    conversation_id: str
    user_id: str
    name: Optional[str] = None
    status: str
    date: Optional[datetime] = None
    update_date: Optional[datetime] = None

    @classmethod
    def from_model(cls, workspace: AgentWorkspace) -> "AgentWorkspaceResponse":
        return cls(
            id=workspace.id,
            conversation_id=workspace.conversation_id,
            user_id=workspace.user_id,
            name=workspace.name,
            status=workspace.status,
            date=workspace.date,
            update_date=workspace.update_date,
        )


class WorkspaceFileItemResponse(BaseModel):
    path: str
    mime_type: str
    checksum: str
    size: int
    version: int
    update_date: Optional[datetime] = None

    @classmethod
    def from_model(cls, workspace_file: AgentWorkspaceFile) -> "WorkspaceFileItemResponse":
        return cls(
            path=workspace_file.path,
            mime_type=workspace_file.mime_type,
            checksum=workspace_file.checksum,
            size=workspace_file.size,
            version=workspace_file.version,
            update_date=workspace_file.update_date,
        )


class UpsertWorkspaceFileRequest(BaseModel):
    file_path: str = Field(description="Workspace-relative file path")
    content: str = Field(description="UTF-8 text content to store in the workspace file")


class WorkspaceFileContentResponse(BaseModel):
    path: str
    mime_type: str
    checksum: str
    size: int
    version: int
    is_binary: bool
    content: Optional[str] = None


class WorkspaceEditFileRequest(BaseModel):
    file_path: str
    old_string: str
    new_string: str
    replace_all: bool = False


class WorkspaceEditFileResponse(BaseResponse):
    updated: bool
    occurrences_replaced: int = 0


class WorkspaceDeleteFileResponse(BaseResponse):
    deleted: bool


class WorkspaceGrepMatchResponse(BaseModel):
    file_path: str
    line_number: int
    line: str


class WorkspaceGrepResponse(BaseModel):
    matches: list[WorkspaceGrepMatchResponse]


class ExecuteWorkspaceScriptRequest(BaseModel):
    script_path: str = Field(description="Workspace-relative path to the Python script to execute")
    export_files: Optional[list[str]] = Field(
        default=None,
        description="Optional list of workspace-relative files to export from execution results",
    )


class ExecuteWorkspaceScriptResponse(BaseResponse):
    output: str
    workspace_files: list[WorkspaceFileItemResponse] = Field(default_factory=list)
