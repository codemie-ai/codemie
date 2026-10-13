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
from codemie.configs import logger
from codemie.service.script_tool_calls.context import ScriptRunContext, ScriptToolRegistry
from codemie.service.script_tool_calls.observer import ScriptRunObserver
from codemie.service.script_tool_calls.token_count import count_tokens
from codemie.service.script_tool_calls.token_estimate import emit_savings
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
    step_config_fields: ClassVar[tuple[str, ...]] = ("tool_access",)
    tool_access_filter: ClassVar[str | None] = "script_callable"

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
        run_context = self._run_context()
        observer = ScriptRunObserver()
        read_tokens = 0
        completed = False
        try:
            response = self.workspace_service.execute_workspace_script(
                workspace_id=workspace_id,
                script_path=script_path,
                user=self.user,
                export_files=export_files,
                run_context=run_context,
                tool_calling=self.tool_calling,
                observer=observer,
            )
            result = self._dump_json(response)
            read_tokens = count_tokens(result, run_context.llm_model)
            completed = True
            return result
        finally:
            self._emit_estimate(observer, run_context, workspace_id, script_path, export_files, read_tokens, completed)

    def _emit_estimate(
        self,
        observer: ScriptRunObserver,
        run_context: ScriptRunContext,
        workspace_id: str,
        script_path: str,
        export_files: list[str] | None,
        read_tokens: int,
        completed: bool,
    ) -> None:
        """The tool's own cost of the run for the savings estimate; assistant scope only, and not for a workflow tool
        step (no model wrote its script). A run that raised is counted too: the model wrote the call and the script
        whatever came back, only the answer it read is 0. Never raises."""
        try:
            if not run_context.counts_toward_savings:
                return  # skip the script read and the counting; the emit applies the same rule
            arguments = json.dumps({"script_path": script_path, "export_files": export_files}, ensure_ascii=False)
            written_tokens = count_tokens(arguments, run_context.llm_model) + self._script_code_tokens(
                workspace_id, script_path, run_context.llm_model
            )
            emit_savings(
                observer.close(), run_context, written_tokens=written_tokens, read_tokens=read_tokens, run_ok=completed
            )
        except Exception as exc:  # noqa: BLE001 - a metric must never fail the run
            logger.warning(f"Script tool token estimate failed: {exc}")

    def _script_code_tokens(self, workspace_id: str, script_path: str, llm_model: str | None) -> int:
        try:
            content = self.workspace_service.get_file_content(workspace_id, script_path, self.user)
            return count_tokens(content.content or "", llm_model) if not content.is_binary else 0
        except Exception as exc:  # noqa: BLE001 - the script size is best effort
            logger.warning(f"Script code lookup for the token estimate failed: {exc}")
            return 0

    def _run_context(self) -> ScriptRunContext:
        """Built in the request thread: the metadata and request contextvars it reads are not visible later."""
        if self.script_registry is not None:
            return self.script_registry.context(self.metadata)
        return ScriptRunContext.without_context(self.user, self.metadata)
