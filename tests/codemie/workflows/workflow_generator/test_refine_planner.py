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

from unittest.mock import Mock, patch

import pytest

from codemie.configs import config
from codemie.workflows.workflow_generator import state_keys as sk
from codemie.workflows.workflow_generator.schemas import (
    RefinePlan,
    StepPlan,
    WorkflowIntent,
    WorkflowStep,
)

pytestmark = pytest.mark.skipif(
    not config.WORKFLOW_GENERATION_ENABLED,
    reason="WORKFLOW_GENERATION_ENABLED is False",
)

_EXISTING_YAML = (
    "name: my-workflow\n"
    "assistants:\n"
    "  - id: a1\n"
    "    model: gpt-4.1\n"
    "    system_prompt: You are a helper.\n"
    "states:\n"
    "  - id: step1\n"
    "    assistant_id: a1\n"
    "    task: Do something.\n"
    "    next:\n"
    "      state_id: end\n"
)

_REFINE_PROMPT = "Improve the system prompt to be more specific."


def _make_refine_plan() -> RefinePlan:
    intent = WorkflowIntent(
        workflow_name="my-workflow",
        workflow_description="A refined workflow",
        steps=[
            WorkflowStep(
                id="step1",
                description="Do something with improved prompt",
                state_type="agent",
                next_step_id="end",
            )
        ],
        data_sources=[],
        ambiguities=[],
    )
    plans = [StepPlan(step_id="step1", transition_type="simple", next_step_id="end")]
    return RefinePlan(intent=intent, plans=plans)


class TestRefinePlannerNode:
    def test_emits_intent_and_step_plans(self):
        from codemie.workflows.workflow_generator.nodes.refine_planner import RefinePlannerNode

        mock_llm = Mock()
        mock_llm.with_structured_output.return_value.invoke.return_value = _make_refine_plan()

        with patch(
            "codemie.workflows.workflow_generator.nodes.refine_planner.get_llm_by_credentials",
            return_value=mock_llm,
        ):
            node = RefinePlannerNode(llm_model="gpt-4o", request_id="req-1")
            state = {
                sk.EXISTING_YAML_CONFIG: _EXISTING_YAML,
                sk.REFINE_PROMPT: _REFINE_PROMPT,
                sk.AVAILABLE_TOOLS: [],
                sk.PROJECT: "demo",
                sk.USER: Mock(),
            }
            result = node(state)

        assert result[sk.CURRENT_NODE_INDEX] == 0
        assert result[sk.PREVIOUS_NODE] is None
        intent = result[sk.INTENT]
        assert intent.workflow_name == "my-workflow"
        assert len(intent.steps) == 1
        assert intent.steps[0].id == "step1"
        step_plans = result[sk.STEP_PLANS]
        assert len(step_plans) == 1
        assert step_plans[0].step_id == "step1"

    def test_uses_structured_output_with_refine_plan(self):
        from codemie.workflows.workflow_generator.nodes.refine_planner import RefinePlannerNode

        mock_llm = Mock()
        mock_structured = Mock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = _make_refine_plan()

        with patch(
            "codemie.workflows.workflow_generator.nodes.refine_planner.get_llm_by_credentials",
            return_value=mock_llm,
        ):
            node = RefinePlannerNode(llm_model="gpt-4o", request_id=None)
            state = {
                sk.EXISTING_YAML_CONFIG: _EXISTING_YAML,
                sk.REFINE_PROMPT: None,
                sk.AVAILABLE_TOOLS: [],
                sk.PROJECT: "demo",
                sk.USER: Mock(),
            }
            node(state)

        mock_llm.with_structured_output.assert_called_once_with(RefinePlan)

    def test_formats_available_tools_into_prompt(self):
        from codemie.workflows.workflow_generator.nodes.refine_planner import RefinePlannerNode

        mock_llm = Mock()
        mock_structured = Mock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = _make_refine_plan()

        tools = [{"tools": [{"name": "web_search", "description": "Search the web"}]}]

        with patch(
            "codemie.workflows.workflow_generator.nodes.refine_planner.get_llm_by_credentials",
            return_value=mock_llm,
        ):
            node = RefinePlannerNode(llm_model="gpt-4o", request_id=None)
            state = {
                sk.EXISTING_YAML_CONFIG: _EXISTING_YAML,
                sk.REFINE_PROMPT: "add web search",
                sk.AVAILABLE_TOOLS: tools,
                sk.PROJECT: "demo",
                sk.USER: Mock(),
            }
            node(state)

        prompt_arg = mock_structured.invoke.call_args[0][0]
        assert "web_search" in prompt_arg

    def test_none_refine_prompt_uses_empty_string(self):
        from codemie.workflows.workflow_generator.nodes.refine_planner import RefinePlannerNode

        mock_llm = Mock()
        mock_structured = Mock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = _make_refine_plan()

        with patch(
            "codemie.workflows.workflow_generator.nodes.refine_planner.get_llm_by_credentials",
            return_value=mock_llm,
        ):
            node = RefinePlannerNode(llm_model="gpt-4o", request_id=None)
            state = {
                sk.EXISTING_YAML_CONFIG: _EXISTING_YAML,
                sk.REFINE_PROMPT: None,
                sk.AVAILABLE_TOOLS: [],
                sk.PROJECT: "demo",
                sk.USER: Mock(),
            }
            node(state)

        prompt_arg = mock_structured.invoke.call_args[0][0]
        # refine_prompt placeholder should be empty string, not "None"
        assert "None" not in prompt_arg
