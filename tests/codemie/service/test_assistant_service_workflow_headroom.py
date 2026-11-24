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
"""build_agent_for_workflow resolves node > workflow > assistant > off Headroom config (EPMCDME-15645, slice 3)."""

from unittest.mock import Mock, patch

import pytest

from codemie.core.workflow_models import WorkflowAssistant
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService
from codemie.service.headroom.scope import WorkflowHeadroomLayers
from codemie.service.headroom.models import (
    HeadroomConfig,
    HeadroomOverride,
    HeadroomStrategy,
    ResolvedHeadroomConfig,
)
from codemie.service.tools.agent_tools_result import AgentToolsResult

RESOLVER_CONFIG = "codemie.service.headroom.factory.config"
RESOLVER_CUSTOMER_CONFIG = "codemie.service.headroom.resolver.customer_config"


def _assistant(headroom_config=None):
    assistant = Mock(spec=Assistant)
    assistant.id = "asst-123"
    assistant.name = "Node Assistant"
    assistant.description = "desc"
    assistant.project = "proj-a"
    assistant.is_global = False
    assistant.shared = True
    assistant.mcp_servers = []
    assistant.llm_model_type = "claude-sonnet-4"
    assistant.is_react = False
    assistant.temperature = 0.5
    assistant.context = None
    assistant.tool_permissions = None
    assistant.headroom_config = headroom_config
    return assistant


@pytest.fixture
def build(request):
    """Run build_agent_for_workflow with collaborators patched; returns (call, LangGraphAgent mock, AIToolsAgent mock)."""
    langgraph = Mock(name="LangGraphAgent")
    aitools = Mock(name="AIToolsAgent")
    cfg = patch(RESOLVER_CONFIG).start()
    customer = patch(RESOLVER_CUSTOMER_CONFIG).start()
    cfg.HEADROOM_MODE = "sdk"
    customer.is_feature_enabled.return_value = True
    patches = [
        patch("codemie.service.assistant_service.LangGraphAgent", langgraph),
        patch.object(AssistantService, "_apply_marketplace_tool_mappings"),
        patch.object(AssistantService, "prepare_tools_config_from_toolkits", return_value=None),
        patch.object(AssistantService, "_prepare_workflow_system_prompt", return_value=("prompt", None)),
        patch.object(AssistantService, "_configure_langgraph_agent"),
        patch("codemie.service.assistant_service.set_llm_context"),
        patch("codemie.service.assistant_service.set_disable_prompt_cache"),
        patch("codemie.service.assistant_service.build_unique_file_objects_list", return_value={}),
        patch(
            "codemie.service.assistant_service.ToolkitService.collect_agent_tools",
            return_value=AgentToolsResult(tools=[]),
        ),
    ]
    for p in patches:
        p.start()
    request.addfinalizer(patch.stopall)

    def call(assistant, workflow_assistant=None, layers=None, agent_class=langgraph, **extra):
        user = Mock(spec=User)
        user.id = "user-123"
        user.name = "Test User"
        kwargs = {"headroom_layers": layers} if layers is not None else {}
        with (
            patch.object(AssistantService, "_load_and_configure_workflow_assistant", return_value=assistant),
            patch.object(AssistantService, "_select_agent_class_for_workflow", return_value=agent_class),
        ):
            AssistantService.build_agent_for_workflow(
                user_input="hello",
                user=user,
                request_uuid="req-1",
                workflow_assistant=workflow_assistant or WorkflowAssistant(assistant_id="asst-123"),
                project_name="proj-a",
                execution_id="exec-1",
                **kwargs,
                **extra,
            )

    return call, langgraph, aitools, cfg, customer


def _kwargs(agent_mock):
    return agent_mock.call_args.kwargs


def test_node_beats_workflow_beats_assistant_per_field(build):
    call, langgraph, *_ = build
    stored = HeadroomConfig(
        enabled=True, compress_user_messages=False, compress_tool_outputs=False, strategy=HeadroomStrategy.LOSSLESS
    )
    layers = WorkflowHeadroomLayers(
        node=HeadroomOverride(strategy=HeadroomStrategy.AGGRESSIVE),
        workflow=HeadroomOverride(compress_user_messages=True, strategy=HeadroomStrategy.BALANCED),
    )

    call(_assistant(stored), layers=layers)

    assert _kwargs(langgraph)["headroom_config"] == ResolvedHeadroomConfig(
        compress_user_messages=True, compress_tool_outputs=False, strategy=HeadroomStrategy.AGGRESSIVE
    )


def test_workflow_beats_assistant(build):
    call, langgraph, *_ = build
    stored = HeadroomConfig(enabled=True, strategy=HeadroomStrategy.LOSSLESS)

    call(_assistant(stored), layers=WorkflowHeadroomLayers(workflow=HeadroomOverride(strategy="aggressive")))

    assert _kwargs(langgraph)["headroom_config"].strategy == HeadroomStrategy.AGGRESSIVE


def test_assistant_config_alone_still_applies(build):
    call, langgraph, *_ = build

    call(_assistant(HeadroomConfig(enabled=True)), layers=WorkflowHeadroomLayers())

    assert isinstance(_kwargs(langgraph)["headroom_config"], ResolvedHeadroomConfig)


def test_node_disabled_beats_workflow_enabled(build):
    call, langgraph, *_ = build
    layers = WorkflowHeadroomLayers(node=HeadroomOverride(enabled=False), workflow=HeadroomOverride(enabled=True))

    call(_assistant(None), layers=layers)

    assert "headroom_config" not in _kwargs(langgraph)


def test_node_enabled_alone_on_inline_assistant_enables_with_defaults(build):
    call, langgraph, *_ = build
    defaults = HeadroomConfig()

    call(
        _assistant(None),
        workflow_assistant=WorkflowAssistant(assistant_id=None, headroom=HeadroomOverride(enabled=True)),
        layers=WorkflowHeadroomLayers(node=HeadroomOverride(enabled=True)),
    )

    assert _kwargs(langgraph)["headroom_config"] == ResolvedHeadroomConfig(
        compress_user_messages=defaults.compress_user_messages,
        compress_tool_outputs=defaults.compress_tool_outputs,
        strategy=defaults.strategy,
    )


def test_strategy_only_workflow_block_is_off(build):
    call, langgraph, *_ = build

    call(_assistant(None), layers=WorkflowHeadroomLayers(workflow=HeadroomOverride(strategy="aggressive")))

    assert "headroom_config" not in _kwargs(langgraph)


def test_aitools_agent_never_receives_headroom_config(build):
    call, langgraph, aitools, *_ = build
    layers = WorkflowHeadroomLayers(node=HeadroomOverride(enabled=True))

    call(_assistant(HeadroomConfig(enabled=True)), layers=layers, agent_class=aitools)

    assert "headroom_config" not in _kwargs(aitools)
    langgraph.assert_not_called()


@pytest.mark.parametrize("gate", ["kill_switch", "customer_flag"])
def test_gates_off_means_no_headroom_kwarg(build, gate):
    call, langgraph, _, cfg, customer = build
    if gate == "kill_switch":
        cfg.HEADROOM_MODE = "off"
    else:
        customer.is_feature_enabled.return_value = False

    call(_assistant(HeadroomConfig(enabled=True)), layers=WorkflowHeadroomLayers(node=HeadroomOverride(enabled=True)))

    assert "headroom_config" not in _kwargs(langgraph)


def test_nothing_configured_means_no_headroom_kwarg(build):
    call, langgraph, *_ = build

    call(_assistant(None))

    assert "headroom_config" not in _kwargs(langgraph)


def test_mock_inputs_are_treated_as_unconfigured(build):
    call, langgraph, *_ = build
    layers = WorkflowHeadroomLayers(node=Mock(spec=HeadroomOverride), workflow=Mock())

    mock_node = Mock(
        spec=WorkflowAssistant, assistant_id=None, exclude_extra_context_tools=False, limit_tool_output_tokens=None
    )

    call(_assistant(Mock()), workflow_assistant=mock_node, layers=layers)

    assert "headroom_config" not in _kwargs(langgraph)
