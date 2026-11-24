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

"""build_agent passes a resolved Headroom config to LangGraphAgent only (EPMCDME-15645, assistant scope)."""

from unittest.mock import Mock, patch

import pytest

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_engine_builder import LangGraphAssistantBuilder, SubagentBuildContext
from codemie.service.assistant_service import AssistantService
from codemie.service.subagents.builtin_subagents import BuiltinSubagent
from codemie.service.tools.agent_tools_result import AgentToolsResult
from codemie.service.headroom.models import HeadroomConfig, HeadroomStrategy, ResolvedHeadroomConfig

RESOLVER_CONFIG = "codemie.service.headroom.factory.config"
RESOLVER_CUSTOMER_CONFIG = "codemie.service.headroom.resolver.customer_config"


def _assistant(headroom_config=None, with_headroom_attr=True):
    assistant = Mock(spec=Assistant)
    assistant.id = "asst-123"
    assistant.name = "Test Assistant"
    assistant.description = "d"
    assistant.system_prompt = "You are a helpful assistant"
    assistant.context = []
    assistant.toolkits = []
    assistant.llm_model_type = "claude-sonnet-4"
    assistant.temperature = 0.7
    assistant.top_p = 0.9
    assistant.project = "test-project"
    assistant.bedrock = None
    assistant.smart_tool_selection_enabled = False
    assistant.prompt_variables = []
    assistant.is_global = False
    assistant.mcp_servers = []
    assistant.tool_permissions = None
    if with_headroom_attr:
        assistant.headroom_config = headroom_config
    return assistant


def _user():
    user = Mock(spec=User)
    user.id = "user-123"
    user.name = "Test User"
    user.full_name = "Test User Full Name"
    user.username = "test@email.com"
    return user


def _gates(mock_resolver_config, mock_customer_config, enabled=True, feature=True):
    mock_resolver_config.HEADROOM_MODE = "sdk" if enabled else "off"
    mock_customer_config.is_feature_enabled.return_value = feature


@pytest.fixture
def build(request):
    """Run build_agent with heavy collaborators patched; returns (call, LangGraphAgent mock, AIToolsAgent mock)."""
    patches = [
        patch("codemie.service.assistant_service.Conversation.find_by_id", return_value=None),
        patch(
            "codemie.service.assistant_service.ToolkitService.collect_agent_tools",
            return_value=AgentToolsResult(tools=[]),
        ),
        patch("codemie.service.assistant_service.set_llm_context"),
        patch("codemie.service.assistant_service.build_unique_file_objects", return_value={}),
        patch("codemie.service.assistant_service.BedrockOrchestratorService.is_bedrock_assistant", return_value=False),
        patch("codemie.service.assistant_service.AssistantService._configure_langgraph_agent"),
    ]
    for p in patches:
        p.start()
    langgraph = patch("codemie.service.assistant_service.LangGraphAgent").start()
    aitools = patch("codemie.service.assistant_service.AIToolsAgent").start()
    mock_llm_service = patch("codemie.service.assistant_service.llm_service").start()
    mock_llm_service.get_react_llms.return_value = []
    mock_llm_service.default_llm_model = "claude-sonnet-4"
    mock_config = patch("codemie.service.assistant_service.config").start()
    mock_config.ENABLE_LANGGRAPH_AITOOLS_AGENT = True
    request.addfinalizer(patch.stopall)

    def call(assistant, use_langgraph=True, **extra):
        mock_config.ENABLE_LANGGRAPH_AITOOLS_AGENT = use_langgraph
        AssistantService.build_agent(
            assistant=assistant,
            request=AssistantChatRequest(text="Hello", file_names=[]),
            user=_user(),
            request_uuid="req-123",
            thread_generator=None,
            tool_callbacks=None,
            **extra,
        )

    return call, langgraph, aitools


@patch(RESOLVER_CUSTOMER_CONFIG)
@patch(RESOLVER_CONFIG)
def test_enabled_stored_config_reaches_langgraph_agent(mock_cfg, mock_customer, build):
    _gates(mock_cfg, mock_customer)
    call, langgraph, aitools = build
    stored = HeadroomConfig(enabled=True, compress_tool_outputs=False, strategy=HeadroomStrategy.AGGRESSIVE)

    call(_assistant(stored))

    kwargs = langgraph.call_args.kwargs
    assert kwargs["headroom_config"] == ResolvedHeadroomConfig(
        compress_user_messages=True, compress_tool_outputs=False, strategy=HeadroomStrategy.AGGRESSIVE
    )
    aitools.assert_not_called()


@patch(RESOLVER_CUSTOMER_CONFIG)
@patch(RESOLVER_CONFIG)
def test_subagent_build_still_receives_headroom_config(mock_cfg, mock_customer, build):
    _gates(mock_cfg, mock_customer)
    call, langgraph, _ = build

    call(_assistant(HeadroomConfig(enabled=True)), is_subagent=True)

    kwargs = langgraph.call_args.kwargs
    assert kwargs["is_subagent"] is True
    assert kwargs["headroom_config"] == ResolvedHeadroomConfig(
        compress_user_messages=True, compress_tool_outputs=True, strategy=HeadroomStrategy.BALANCED
    )


@patch(RESOLVER_CUSTOMER_CONFIG)
@patch(RESOLVER_CONFIG)
def test_builtin_subagent_clone_inherits_parent_headroom_config(mock_cfg, mock_customer, build):
    _gates(mock_cfg, mock_customer)
    call, langgraph, _ = build
    stored = HeadroomConfig(enabled=True, strategy=HeadroomStrategy.AGGRESSIVE)
    parent = Assistant(name="parent", description="d", system_prompt="p", headroom_config=stored)
    captured = {}

    class _CapturingFactory:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def build(self):
            return None

    with (
        patch("codemie.service.tools.assistant_factory.AssistantFactory", _CapturingFactory),
        patch(
            "codemie.service.assistant.assistant_engine_builder.BuiltinSubagentsRegistry.get_system_prompt",
            return_value="builtin prompt",
        ),
    ):
        LangGraphAssistantBuilder._build_builtin_subagent_executor(
            parent=parent,
            builtin=BuiltinSubagent.GENERAL_PURPOSE_SUBAGENT,
            user=_user(),
            request=AssistantChatRequest(text="Hello", file_names=[]),
            request_uuid="req-123",
            thread_generator=None,
            resolved_model="claude-sonnet-4",
            agent_name="gp",
            build_context=SubagentBuildContext(),
        )

    child = captured["assistant"]
    assert child is not parent
    assert child.headroom_config == stored

    call(child, is_subagent=True)

    assert langgraph.call_args.kwargs["headroom_config"] == ResolvedHeadroomConfig(
        compress_user_messages=True, compress_tool_outputs=True, strategy=HeadroomStrategy.AGGRESSIVE
    )


@patch(RESOLVER_CUSTOMER_CONFIG)
@patch(RESOLVER_CONFIG)
def test_aitools_agent_never_receives_headroom_config(mock_cfg, mock_customer, build):
    _gates(mock_cfg, mock_customer)
    call, langgraph, aitools = build

    call(_assistant(HeadroomConfig(enabled=True)), use_langgraph=False)

    langgraph.assert_not_called()
    assert "headroom_config" not in aitools.call_args.kwargs


@pytest.mark.parametrize(
    "global_enabled, feature_enabled, stored",
    [
        (False, True, HeadroomConfig(enabled=True)),
        (True, False, HeadroomConfig(enabled=True)),
        (True, True, None),
        (True, True, HeadroomConfig(enabled=False)),
    ],
    ids=["kill-switch-off", "feature-off", "not-configured", "stored-disabled"],
)
@patch(RESOLVER_CUSTOMER_CONFIG)
@patch(RESOLVER_CONFIG)
def test_no_headroom_kwarg_when_resolution_is_none(
    mock_cfg, mock_customer, global_enabled, feature_enabled, stored, build
):
    _gates(mock_cfg, mock_customer, enabled=global_enabled, feature=feature_enabled)
    call, langgraph, _ = build

    call(_assistant(stored))

    assert "headroom_config" not in langgraph.call_args.kwargs


@patch(RESOLVER_CUSTOMER_CONFIG)
@patch(RESOLVER_CONFIG)
def test_mock_assistant_with_default_gates_is_treated_as_unconfigured(mock_cfg, mock_customer, build):
    call, langgraph, _ = build
    # Real defaults: kill switch off, feature not enabled.
    mock_cfg.HEADROOM_MODE = "off"
    mock_customer.is_feature_enabled.return_value = False

    call(_assistant(with_headroom_attr=False))

    assert "headroom_config" not in langgraph.call_args.kwargs


@patch(RESOLVER_CUSTOMER_CONFIG)
@patch(RESOLVER_CONFIG)
def test_mock_assistant_with_gates_on_is_treated_as_unconfigured(mock_cfg, mock_customer, build):
    _gates(mock_cfg, mock_customer)
    call, langgraph, _ = build

    # headroom_config is an auto-created Mock (not a HeadroomConfig) and must not be resolved.
    call(_assistant(headroom_config=Mock()))

    assert "headroom_config" not in langgraph.call_args.kwargs


@patch(RESOLVER_CUSTOMER_CONFIG)
@patch(RESOLVER_CONFIG)
def test_bedrock_path_never_receives_headroom_config(mock_cfg, mock_customer):
    _gates(mock_cfg, mock_customer)
    assistant = _assistant(HeadroomConfig(enabled=True))

    with (
        patch("codemie.service.assistant_service.Conversation.find_by_id", return_value=None),
        patch("codemie.service.assistant_service.set_llm_context"),
        patch("codemie.service.assistant_service.build_unique_file_objects", return_value={}),
        patch("codemie.service.assistant_service.BedrockOrchestratorService.is_bedrock_assistant", return_value=True),
        patch("codemie.service.assistant_service.AssistantService.get_system_prompt", return_value="p"),
        patch("codemie.service.assistant_service.LangGraphAgent") as langgraph,
        patch("codemie.service.assistant_service.AIToolsAgent") as aitools,
    ):
        AssistantService.build_agent(
            assistant=assistant,
            request=AssistantChatRequest(text="Hello", file_names=[]),
            user=_user(),
            request_uuid="req-123",
            thread_generator=None,
            tool_callbacks=None,
        )

    langgraph.assert_not_called()
    assert "headroom_config" not in aitools.call_args.kwargs
