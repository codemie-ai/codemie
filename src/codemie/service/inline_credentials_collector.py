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

from typing import TYPE_CHECKING

from codemie.core.workflow_models.workflow_models import WorkflowAssistant, WorkflowTool
from codemie.rest_api.models.assistant import InlineCredential, MCPServerDetails

if TYPE_CHECKING:
    from codemie.core.workflow_models.workflow_config import WorkflowConfig
    from codemie.core.workflow_models.workflow_models import WorkflowAssistantTool


class InlineCredentialsCollector:
    """Collects inline credentials from workflow virtual steps and tool nodes.

    Public interface
    ----------------
    collect_for_workflow  — credentials from virtual steps and tool nodes only (no external entity loading)
    """

    # ── private: collection from single objects ──────────────────────────────

    def _from_mcp_servers(self, servers: list[MCPServerDetails]) -> list[InlineCredential]:
        credentials: list[InlineCredential] = []
        for server in servers:
            if getattr(server, "settings", None) and getattr(server.settings, "credential_values", None):
                credentials.append(
                    InlineCredential(
                        mcp_server=server.name,
                        credential_type="mcp_environment_vars",
                        toolkit="MCP",
                    )
                )
            if getattr(server, "mcp_connect_auth_token", None) and getattr(
                server.mcp_connect_auth_token, "credential_values", None
            ):
                credentials.append(
                    InlineCredential(
                        mcp_server=server.name,
                        credential_type="mcp_auth_token",
                        toolkit="MCP",
                    )
                )
            if server.config and server.config.env:
                credentials.append(
                    InlineCredential(
                        mcp_server=server.name,
                        credential_type="mcp_inline_config_env",
                        env_vars=list(server.config.env.keys()),
                        toolkit="MCP",
                    )
                )
            if server.integration_alias is not None:
                credentials.append(
                    InlineCredential(
                        mcp_server=server.name,
                        credential_type="mcp_integration_alias",
                        toolkit="MCP",
                        integration_alias=server.integration_alias,
                    )
                )
        return credentials

    # ── private: workflow-specific helpers ───────────────────────────────────

    @staticmethod
    def _from_virtual_assistant_tools(tools: list[WorkflowAssistantTool]) -> list[InlineCredential]:
        """Collect integration_alias credentials from WorkflowAssistantTool entries."""
        return [
            InlineCredential(
                tool=t.name,
                integration_alias=t.integration_alias,
                credential_type="tool_integration_alias",
            )
            for t in tools
            if t.integration_alias
        ]

    def _from_virtual_assistant(
        self,
        assistant: WorkflowAssistant,
    ) -> list[InlineCredential]:
        """Collect credentials from a virtual (inline) WorkflowAssistant step."""
        return [
            *self._from_mcp_servers(assistant.mcp_servers or []),
            *self._from_virtual_assistant_tools(assistant.tools or []),
        ]

    def _from_tool_node(self, tool: WorkflowTool) -> list[InlineCredential]:
        """Collect credentials from a WorkflowTool node's MCP server and integration_alias."""
        credentials: list[InlineCredential] = []
        if tool.mcp_server is not None:
            credentials.extend(self._from_mcp_servers([tool.mcp_server]))
        if tool.integration_alias is not None:
            credentials.append(
                InlineCredential(
                    tool=tool.tool,
                    integration_alias=tool.integration_alias,
                    credential_type="tool_integration_alias",
                )
            )
        return credentials

    # ── public ────────────────────────────────────────────────────────────────

    def collect_for_workflow(
        self,
        workflow: WorkflowConfig,
    ) -> list[InlineCredential]:
        """Collect inline credentials from a workflow's virtual assistant steps and tool nodes.

        Covers MCP server credentials and integration_alias references on virtual steps,
        and the same on WorkflowTool nodes. Does not recurse into external assistants
        referenced by ID.
        """
        credentials: list[InlineCredential] = []

        for assistant in workflow.assistants or []:
            if isinstance(assistant, WorkflowAssistant) and assistant.assistant_id is None:
                credentials.extend(self._from_virtual_assistant(assistant))

        for tool in workflow.tools or []:
            credentials.extend(self._from_tool_node(tool))

        return credentials
