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

"""Tool-call confirmation policies apply unchanged to the real workspace script tool.

The script tool keeps the default ``CodeMieTool.is_safe`` (False), so ``ASK_FOR_APPROVAL`` and
``APPROVE_FOR_ME`` both make the gate interrupt before it runs, and ``AUTO_APPROVE`` leaves confirmation off.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from codemie.agents.tool_confirmation.confirmation_gate import call_needs_confirmation
from codemie.core.models import ToolCallPolicy
from codemie.rest_api.models.assistant import ToolPermissionsConfig
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_engine_builder import LangGraphAssistantBuilder, SubagentBuildContext
from codemie_tools.data_management.workspace.execute_workspace_script_tool import ExecuteWorkspaceScriptTool

_SCRIPT_ARGS: dict[str, str] = {"script_path": "scripts/run.py"}


def _make_script_tool() -> ExecuteWorkspaceScriptTool:
    return ExecuteWorkspaceScriptTool(
        conversation_id="conv-1",
        user=User(id="user-1", auth_token=None),
        workspace_service=MagicMock(),
        workspace_id="ws-1",
    )


def _require_tool_confirmation(policy: ToolCallPolicy) -> bool:
    agent_kwargs: dict[str, object] = {}
    assistant = MagicMock()
    assistant.tool_permissions = ToolPermissionsConfig(tool_call_policy=policy)
    request = MagicMock()
    request.tool_call_policy = None

    customer_config_mock = MagicMock()
    customer_config_mock.is_feature_enabled.return_value = True
    customer_config_mock.get_feature_setting.return_value = None
    with (
        patch("codemie.service.tool_permissions_service.customer_config", customer_config_mock),
    ):
        LangGraphAssistantBuilder.configure_agent_kwargs(
            agent_kwargs=agent_kwargs,
            assistant=assistant,
            user=MagicMock(),
            request=request,
            request_uuid="uuid-1",
            thread_generator=MagicMock(),
            llm_model="gpt-4",
            smart_tool_selection_enabled=False,
            resolved_policy=LangGraphAssistantBuilder.resolve_tool_policy(
                assistant, request, allow_tool_confirmation=True
            ),
            build_context=SubagentBuildContext(),
            is_root=True,
            create_subagent_executors=lambda **_: [],
            get_subagent_descriptions=lambda a, u: {},
        )
    return bool(agent_kwargs["require_tool_confirmation"])


def test_script_tool_is_not_safe() -> None:
    assert _make_script_tool().is_safe(_SCRIPT_ARGS) is False


@pytest.mark.parametrize("policy", [ToolCallPolicy.ASK_FOR_APPROVAL, ToolCallPolicy.APPROVE_FOR_ME])
def test_script_tool_call_needs_confirmation(policy: ToolCallPolicy) -> None:
    script_tool = _make_script_tool()
    tool_call = {"id": "tc-1", "name": script_tool.name, "args": _SCRIPT_ARGS}

    assert call_needs_confirmation(policy, {script_tool.name: script_tool}, tool_call) is True


def test_auto_approve_leaves_confirmation_off_for_script_tool_agent() -> None:
    assert _require_tool_confirmation(ToolCallPolicy.AUTO_APPROVE) is False
