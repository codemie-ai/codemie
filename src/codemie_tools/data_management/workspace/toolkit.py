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

from typing import Any, List

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant, VirtualAssistant
from codemie.rest_api.models.agent_workspace import CreateAgentWorkspaceRequest
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie.service.script_tool_calls.context import ScriptToolRegistry
from codemie.service.workspace_script_bridge import resolve_tool_calling_settings
from codemie_tools.base.base_toolkit import BaseToolkit
from codemie_tools.base.models import Tool, ToolKit
from codemie_tools.data_management.code_executor.sdk_reference import compose_description, read_sdk_reference
from codemie_tools.data_management.code_executor.tool_calling_limits import ToolCallingSettings
from codemie_tools.data_management.workspace.generate_image_tool_v2 import GenerateWorkspaceImageToolV2
from codemie_tools.data_management.workspace.inspect_workspace_image_tool import InspectWorkspaceImageTool
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
    GENERATE_WORKSPACE_IMAGE_TOOL_V2,
    GREP_WORKSPACE_FILES_TOOL,
    INSPECT_WORKSPACE_IMAGE_TOOL,
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
        Tool.from_metadata(GENERATE_WORKSPACE_IMAGE_TOOL_V2, tool_class=GenerateWorkspaceImageToolV2),
        Tool.from_metadata(INSPECT_WORKSPACE_IMAGE_TOOL, tool_class=InspectWorkspaceImageTool),
    ]
    label: str | None = "Agent Workspace"


class AgentWorkspaceToolkit(BaseToolkit):
    conversation_id: str
    user: User
    # VirtualAssistant must stay a member: with only Assistant, pydantic converts it and drops execution_id and
    # is_tool_step.
    assistant: Assistant | VirtualAssistant | None = None
    request: AssistantChatRequest | None = None
    request_uuid: str | None = None
    llm_model: Any | None = None
    image_generator: Any | None = None

    @classmethod
    def get_tools_ui_info(cls):
        return AgentWorkspaceToolkitUI().model_dump()

    def _workflow_project(self) -> str | None:
        """The workflow's project, only for a bare workflow tool step.

        An inline workflow assistant node is a virtual assistant with an execution id too, so the execution id says
        nothing; the explicit ``is_tool_step`` marker, set only where the tool step's assistant is created, does.
        """
        if isinstance(self.assistant, VirtualAssistant) and self.assistant.is_tool_step:
            return self.assistant.project
        return None

    @staticmethod
    def _script_tool_description(tool_calling: ToolCallingSettings | None) -> str:
        """The script tool's description: the base text, plus the SDK reference only while tool calling is on.

        Built from parts so that further sections (for example the result shapes of tools) can be appended.
        """
        sections: list[str] = []
        if tool_calling is not None:
            sections.append(read_sdk_reference(tool_calling))
        base = EXECUTE_WORKSPACE_SCRIPT_TOOL.description or ""
        return compose_description(base, sections) if sections else base

    def get_tools(self) -> list:
        shared_service = AgentWorkspaceService()
        workspace = shared_service.create_workspace(
            CreateAgentWorkspaceRequest(conversation_id=self.conversation_id),
            self.user,
        )
        resolved_workspace_id = workspace.id
        tool_calling = resolve_tool_calling_settings()

        return [
            ListWorkspaceFilesTool(
                conversation_id=self.conversation_id,
                user=self.user,
                workspace_service=shared_service,
                workspace_id=resolved_workspace_id,
            ),
            ReadWorkspaceFileTool(
                conversation_id=self.conversation_id,
                user=self.user,
                workspace_service=shared_service,
                workspace_id=resolved_workspace_id,
            ),
            WriteWorkspaceFileTool(
                conversation_id=self.conversation_id,
                user=self.user,
                workspace_service=shared_service,
                workspace_id=resolved_workspace_id,
            ),
            EditWorkspaceFileTool(
                conversation_id=self.conversation_id,
                user=self.user,
                workspace_service=shared_service,
                workspace_id=resolved_workspace_id,
            ),
            DeleteWorkspaceFileTool(
                conversation_id=self.conversation_id,
                user=self.user,
                workspace_service=shared_service,
                workspace_id=resolved_workspace_id,
            ),
            GrepWorkspaceFilesTool(
                conversation_id=self.conversation_id,
                user=self.user,
                workspace_service=shared_service,
                workspace_id=resolved_workspace_id,
            ),
            ExecuteWorkspaceScriptTool(
                conversation_id=self.conversation_id,
                user=self.user,
                workspace_service=shared_service,
                workspace_id=resolved_workspace_id,
                script_registry=ScriptToolRegistry(self.user, workflow_project=self._workflow_project()),
                description=self._script_tool_description(tool_calling),
                tool_calling=tool_calling,
            ),
            GenerateWorkspaceImageToolV2(
                conversation_id=self.conversation_id,
                user=self.user,
                workspace_service=shared_service,
                workspace_id=resolved_workspace_id,
                image_generator=self.image_generator,
            ),
            InspectWorkspaceImageTool(
                conversation_id=self.conversation_id,
                user=self.user,
                workspace_service=shared_service,
                workspace_id=resolved_workspace_id,
                llm_model=self.llm_model,
                request_uuid=self.request_uuid,
            ),
        ]

    @classmethod
    def get_toolkit(
        cls,
        conversation_id: str,
        user: User,
        assistant: Assistant | None = None,
        request: AssistantChatRequest | None = None,
        request_uuid: str | None = None,
        llm_model: Any | None = None,
        image_generator: Any | None = None,
    ) -> "AgentWorkspaceToolkit":
        return cls(
            conversation_id=conversation_id,
            user=user,
            assistant=assistant,
            request=request,
            request_uuid=request_uuid,
            llm_model=llm_model,
            image_generator=image_generator,
        )
