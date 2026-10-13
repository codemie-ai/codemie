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

"""Tests for the skill-seeding gate in AgentWorkspaceToolkit.get_tools (EPMCDME-15558)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie_tools.data_management.code_executor.models import SandboxMode
from codemie_tools.data_management.code_executor.tool_calling_limits import ToolCallingSettings
from codemie_tools.data_management.workspace.toolkit import AgentWorkspaceToolkit


def _toolkit(skill_ids=None, user=None):
    assistant = Assistant(
        name="test-assistant",
        description="test",
        system_prompt="test",
        skill_ids=skill_ids or [],
    )
    return AgentWorkspaceToolkit(
        conversation_id="c-1",
        user=user or User(id="u-1", name="tester"),
        assistant=assistant,
    )


def _patch_bridge_inputs(feature_enabled: bool, sandbox_mode: SandboxMode):
    """Patch what resolve_tool_calling_settings reads, so each gate is exercised on its own."""
    return (
        patch(
            "codemie.service.workspace_script_bridge.customer_config",
            MagicMock(
                is_feature_enabled=MagicMock(return_value=feature_enabled),
                get_feature_setting=MagicMock(return_value=None),
            ),
        ),
        patch(
            "codemie.service.workspace_script_bridge.CodeExecutorConfig.from_env",
            return_value=SimpleNamespace(sandbox_mode=sandbox_mode),
        ),
    )


def _patch_create_workspace():
    service = MagicMock(spec=AgentWorkspaceService)
    service.create_workspace.return_value = SimpleNamespace(id="w-1")
    return patch(
        "codemie_tools.data_management.workspace.toolkit.AgentWorkspaceService",
        return_value=service,
    )


class TestGetToolsHasNoSideEffects:
    """Skill files are prepared by ToolkitService.prepare_skill_workspace before the tools are built, not here."""

    def test_get_tools_returns_the_tools_without_reading_skills_or_seeding(self):
        import codemie_tools.data_management.workspace.toolkit as toolkit_module

        toolkit = _toolkit(skill_ids=["skill-1"])

        with (
            _patch_create_workspace(),
            patch(
                "codemie_tools.data_management.workspace.toolkit.resolve_tool_calling_settings",
                return_value=ToolCallingSettings(),
            ),
        ):
            tools = toolkit.get_tools()

        assert len(tools) == 9
        assert not hasattr(toolkit_module, "SkillWorkspaceSeeder")
        assert not hasattr(toolkit_module, "SkillService")
        assert not hasattr(toolkit_module, "AgentWorkspaceRepository")

    def test_the_toolkit_no_longer_carries_a_script_tool_selected_flag(self):
        import inspect

        assert "script_tool_selected" not in inspect.signature(AgentWorkspaceToolkit.get_toolkit).parameters
        assert not hasattr(_toolkit(), "script_tool_selected")
