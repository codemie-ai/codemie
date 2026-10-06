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
from typing import Any, ClassVar, Optional, Type

from pydantic import BaseModel, Field, PrivateAttr

from codemie.rest_api.models.agent_workspace import CreateAgentWorkspaceRequest
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie.service.script_tool_calls.context import ScriptRunContext, ScriptToolRegistry
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.data_management.code_executor.tool_calling_limits import ToolCallingSettings
from codemie_tools.data_management.workspace.tools_vars import (
    EXECUTE_WORKSPACE_SCRIPT_TOOL,
)


class ExecuteWorkspaceScriptInput(BaseModel):
    script_path: str = Field(description="Workspace-relative path to the Python script to execute.")
    export_files: Optional[list[str]] = Field(
        default=None,
        description="Optional list of workspace-relative files to export from execution results.",
    )


class ExecuteWorkspaceScriptTool(CodeMieTool):
    name: str = EXECUTE_WORKSPACE_SCRIPT_TOOL.name
    description: str = EXECUTE_WORKSPACE_SCRIPT_TOOL.description
    args_schema: Type[BaseModel] = ExecuteWorkspaceScriptInput
    conversation_id: str = Field(exclude=True)
    user: User = Field(exclude=True)
    # ``Any``: tests and callers hand in stand-ins; the default is the real service.
    workspace_service: Any = Field(default_factory=AgentWorkspaceService, exclude=True)
    workspace_id: str | None = Field(default=None, exclude=True)
    #: Filled once the run's final tool list is known; without it a script's tool calls are refused.
    script_registry: ScriptToolRegistry | None = Field(default=None, exclude=True)
    #: The run's tool-calling settings, read once when the toolkit was built; ``None``: tool calling is off.
    tool_calling: ToolCallingSettings | None = Field(default=None, exclude=True)
    _workspace_id: str | None = PrivateAttr(default=None)

    def _get_workspace_id(self) -> str:
        if self._workspace_id is None:
            if self.workspace_id:
                self.workspace_service.get_workspace(self.workspace_id, self.user)
                self._workspace_id = self.workspace_id
            else:
                workspace = self.workspace_service.create_workspace(
                    CreateAgentWorkspaceRequest(conversation_id=self.conversation_id),
                    self.user,
                )
                self._workspace_id = workspace.id
        return self._workspace_id

    @staticmethod
    def _dump_json(payload) -> str:
        if isinstance(payload, list):
            data = [
                item.model_dump(mode="json", exclude={"checksum"}) if hasattr(item, "model_dump") else item
                for item in payload
            ]
        elif hasattr(payload, "model_dump"):
            raw = payload.model_dump(mode="json")
            raw.pop("checksum", None)
            for val in raw.values():
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            item.pop("checksum", None)
            data = raw
        else:
            data = payload
        return json.dumps(data, ensure_ascii=False, indent=2)

    def execute(self, script_path: str, export_files: Optional[list[str]] = None) -> str:
        workspace_id = self._get_workspace_id()
        response = self.workspace_service.execute_workspace_script(
            workspace_id=workspace_id,
            script_path=script_path,
            user=self.user,
            export_files=export_files,
            run_context=self._run_context(),
            tool_calling=self.tool_calling,
        )
        return self._dump_json(response)

    def _run_context(self) -> ScriptRunContext:
        if self.script_registry is not None:
            return self.script_registry.context()
        return ScriptRunContext.without_context(self.user)
