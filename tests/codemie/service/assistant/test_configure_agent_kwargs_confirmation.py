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

from unittest.mock import MagicMock, patch

from codemie.core.models import ToolCallPolicy
from codemie.rest_api.models.assistant import ToolPermissionsConfig
from codemie.service.assistant.assistant_engine_builder import LangGraphAssistantBuilder


def _run_configure(*, request_policy, assistant_policy):
    agent_kwargs = {}
    assistant = MagicMock()
    assistant.tool_permissions = ToolPermissionsConfig(tool_call_policy=assistant_policy)
    request = MagicMock()
    request.tool_call_policy = request_policy

    customer_config_mock = MagicMock()
    customer_config_mock.is_feature_enabled.return_value = True
    customer_config_mock.get_feature_setting.return_value = None
    with patch("codemie.service.tool_permissions_service.customer_config", customer_config_mock):
        LangGraphAssistantBuilder.configure_agent_kwargs(
            agent_kwargs=agent_kwargs,
            assistant=assistant,
            user=MagicMock(),
            request=request,
            request_uuid="uuid-1",
            thread_generator=MagicMock(),
            llm_model="gpt-4",
            smart_tool_selection_enabled=False,
            allow_tool_confirmation=True,
            create_subagent_executors=lambda **_: [],
            get_subagent_descriptions=lambda a, u: {},
        )
    return agent_kwargs["require_tool_confirmation"]


def test_request_ask_for_approval_activates_confirmation():
    assert (
        _run_configure(
            request_policy=ToolCallPolicy.ASK_FOR_APPROVAL,
            assistant_policy=ToolCallPolicy.AUTO_APPROVE,
        )
        is True
    )


def test_assistant_ask_for_approval_wins_when_no_request_override():
    assert (
        _run_configure(
            request_policy=None,
            assistant_policy=ToolCallPolicy.ASK_FOR_APPROVAL,
        )
        is True
    )


def test_auto_approve_leaves_confirmation_off():
    assert (
        _run_configure(
            request_policy=ToolCallPolicy.AUTO_APPROVE,
            assistant_policy=ToolCallPolicy.AUTO_APPROVE,
        )
        is False
    )


def test_no_request_policy_auto_approve_assistant_leaves_confirmation_off():
    assert (
        _run_configure(
            request_policy=None,
            assistant_policy=ToolCallPolicy.AUTO_APPROVE,
        )
        is False
    )


def _run_configure_full(*, request_policy, assistant_policy):
    """Return the full agent_kwargs dict (not just require_tool_confirmation)."""
    agent_kwargs = {}
    assistant = MagicMock()
    assistant.tool_permissions = ToolPermissionsConfig(tool_call_policy=assistant_policy)
    request = MagicMock()
    request.tool_call_policy = request_policy

    customer_config_mock = MagicMock()
    customer_config_mock.is_feature_enabled.return_value = True
    customer_config_mock.get_feature_setting.return_value = None
    with patch("codemie.service.tool_permissions_service.customer_config", customer_config_mock):
        LangGraphAssistantBuilder.configure_agent_kwargs(
            agent_kwargs=agent_kwargs,
            assistant=assistant,
            user=MagicMock(),
            request=request,
            request_uuid="uuid-1",
            thread_generator=MagicMock(),
            llm_model="gpt-4",
            smart_tool_selection_enabled=False,
            allow_tool_confirmation=True,
            create_subagent_executors=lambda **_: [],
            get_subagent_descriptions=lambda a, u: {},
        )
    return agent_kwargs


def test_tool_call_policy_propagated_for_ask_for_approval():
    kwargs = _run_configure_full(
        request_policy=ToolCallPolicy.ASK_FOR_APPROVAL,
        assistant_policy=ToolCallPolicy.AUTO_APPROVE,
    )
    assert kwargs["tool_call_policy"] == ToolCallPolicy.ASK_FOR_APPROVAL


def test_tool_call_policy_propagated_for_approve_for_me():
    kwargs = _run_configure_full(
        request_policy=ToolCallPolicy.APPROVE_FOR_ME,
        assistant_policy=ToolCallPolicy.AUTO_APPROVE,
    )
    assert kwargs["tool_call_policy"] == ToolCallPolicy.APPROVE_FOR_ME


def test_tool_call_policy_propagated_for_auto_approve():
    kwargs = _run_configure_full(
        request_policy=ToolCallPolicy.AUTO_APPROVE,
        assistant_policy=ToolCallPolicy.AUTO_APPROVE,
    )
    assert kwargs["tool_call_policy"] == ToolCallPolicy.AUTO_APPROVE


def test_tool_call_policy_uses_assistant_policy_when_no_request_override():
    kwargs = _run_configure_full(
        request_policy=None,
        assistant_policy=ToolCallPolicy.APPROVE_FOR_ME,
    )
    assert kwargs["tool_call_policy"] == ToolCallPolicy.APPROVE_FOR_ME
