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

from typing import List

from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie_tools.base.base_toolkit import BaseToolkit
from codemie_tools.base.models import Tool, ToolKit
from codemie_tools.data_management.workspace.tools import (
    DeleteWorkspaceFileTool,
    EditWorkspaceFileTool,
    ExecuteWorkspaceScriptTool,
    GrepWorkspaceFilesTool,
    ListWorkspaceFilesTool,
    ReadWorkspaceFileTool,
    WriteWorkspaceFileTool,
)
from codemie_tools.data_management.workspace.tools_vars import (
    AGENT_WORKSPACE_TOOLKIT,
    DELETE_WORKSPACE_FILE_TOOL,
    EDIT_WORKSPACE_FILE_TOOL,
    EXECUTE_WORKSPACE_SCRIPT_TOOL,
    GREP_WORKSPACE_FILES_TOOL,
    LIST_WORKSPACE_FILES_TOOL,
    READ_WORKSPACE_FILE_TOOL,
    WRITE_WORKSPACE_FILE_TOOL,
)


class AgentWorkspaceToolkitUI(ToolKit):
    toolkit: str = AGENT_WORKSPACE_TOOLKIT
    tools: List[Tool] = [
        Tool.from_metadata(LIST_WORKSPACE_FILES_TOOL, tool_class=ListWorkspaceFilesTool),
        Tool.from_metadata(READ_WORKSPACE_FILE_TOOL, tool_class=ReadWorkspaceFileTool),
        Tool.from_metadata(WRITE_WORKSPACE_FILE_TOOL, tool_class=WriteWorkspaceFileTool),
        Tool.from_metadata(EDIT_WORKSPACE_FILE_TOOL, tool_class=EditWorkspaceFileTool),
        Tool.from_metadata(DELETE_WORKSPACE_FILE_TOOL, tool_class=DeleteWorkspaceFileTool),
        Tool.from_metadata(GREP_WORKSPACE_FILES_TOOL, tool_class=GrepWorkspaceFilesTool),
        Tool.from_metadata(EXECUTE_WORKSPACE_SCRIPT_TOOL, tool_class=ExecuteWorkspaceScriptTool),
    ]
    label: str = "Agent Workspace"


class AgentWorkspaceToolkit(BaseToolkit):
    conversation_id: str
    user: User

    @classmethod
    def get_tools_ui_info(cls):
        return AgentWorkspaceToolkitUI().model_dump()

    def get_tools(self) -> list:
        shared_service = AgentWorkspaceService()
        return [
            ListWorkspaceFilesTool(
                conversation_id=self.conversation_id, user=self.user, workspace_service=shared_service
            ),
            ReadWorkspaceFileTool(
                conversation_id=self.conversation_id, user=self.user, workspace_service=shared_service
            ),
            WriteWorkspaceFileTool(
                conversation_id=self.conversation_id, user=self.user, workspace_service=shared_service
            ),
            EditWorkspaceFileTool(
                conversation_id=self.conversation_id, user=self.user, workspace_service=shared_service
            ),
            DeleteWorkspaceFileTool(
                conversation_id=self.conversation_id, user=self.user, workspace_service=shared_service
            ),
            GrepWorkspaceFilesTool(
                conversation_id=self.conversation_id, user=self.user, workspace_service=shared_service
            ),
            ExecuteWorkspaceScriptTool(
                conversation_id=self.conversation_id, user=self.user, workspace_service=shared_service
            ),
        ]

    @classmethod
    def get_toolkit(cls, conversation_id: str, user: User) -> "AgentWorkspaceToolkit":
        return cls(conversation_id=conversation_id, user=user)
