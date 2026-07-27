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

from unittest.mock import Mock, patch

import pytest

from codemie.configs import config
from codemie.core.workflow_models.workflow_models import CreateWorkflowRequest
from codemie.workflows.workflow_generator.schemas import (
    MappedNode,
    StepPlan,
    WorkflowIntent,
    WorkflowPlan,
    WorkflowStep,
)
from codemie.workflows.workflow_generator.state import WorkflowGeneratorState

pytestmark = pytest.mark.skipif(
    not config.WORKFLOW_GENERATION_ENABLED,
    reason="WORKFLOW_GENERATION_ENABLED is False",
)


def _make_intent(*step_ids: str) -> WorkflowIntent:
    return WorkflowIntent(
        workflow_name="Test Workflow",
        workflow_description="A test",
        steps=[
            WorkflowStep(id=sid, description=f"Step {sid}", state_type="agent", next_step_id=None) for sid in step_ids
        ],
    )


def _make_valid_node(step_id: str) -> MappedNode:
    return MappedNode(
        step_id=step_id,
        state_type="agent",
        assistant_ref="agent-1",
        task=f"Do {step_id}",
        transition_type="simple",
        next_state_ids=["end"],
    )


def _make_invalid_node(step_id: str) -> MappedNode:
    """Node with no assistant_ref — fails validation."""
    return MappedNode(
        step_id=step_id,
        state_type="agent",
        assistant_ref=None,
        task=f"Do {step_id}",
        transition_type="simple",
        next_state_ids=["end"],
    )


def _make_mock_llm(return_value):
    llm = Mock()
    llm.with_structured_output.return_value.invoke.return_value = return_value
    return llm


def _make_step_plans(*step_ids: str) -> WorkflowPlan:
    plans = []
    for i, sid in enumerate(step_ids):
        next_id = step_ids[i + 1] if i + 1 < len(step_ids) else "end"
        plans.append(StepPlan(step_id=sid, transition_type="simple", next_step_id=next_id))
    return WorkflowPlan(plans=plans)


def _make_initial_state(nl_query: str = "Create a workflow") -> WorkflowGeneratorState:
    return {
        "nl_query": nl_query,
        "user": Mock(id="u1", name="tester", username="tester@x.com"),
        "project": "demo",
        "available_tools": [],
        "intent": None,
        "step_plans": None,
        "current_node_index": 0,
        "previous_node": None,
        "node_plan": None,
        "generated_config": None,
        "validation_errors": [],
        "validation_attempts": 0,
        "failed_step_ids": [],
        "result": None,
        "error": None,
    }


@patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow")
@patch("codemie.workflows.workflow_generator.nodes.node_generator.get_llm_by_credentials")
@patch("codemie.workflows.workflow_generator.nodes.step_planner.get_llm_by_credentials")
@patch("codemie.workflows.workflow_generator.nodes.intent_analysis.get_llm_by_credentials")
def test_graph_happy_path(mock_intent_llm, mock_step_planner_llm, mock_map_llm, mock_validate):
    from codemie.workflows.workflow_generator.workflow import WorkflowGeneratorGraph

    mock_validate.return_value = Mock()
    mock_intent_llm.return_value = _make_mock_llm(_make_intent("s1"))
    mock_step_planner_llm.return_value = _make_mock_llm(_make_step_plans("s1"))
    mock_map_llm.return_value = _make_mock_llm(_make_valid_node("s1"))

    graph = WorkflowGeneratorGraph(llm_model="gpt-4.1", request_id="req-1")
    final = graph.run(_make_initial_state())

    assert final.get("error") is None
    assert isinstance(final["result"], CreateWorkflowRequest)
    assert final["result"].name == "Test Workflow"
    assert final["failed_step_ids"] == []


@patch("codemie.workflows.workflow_generator.nodes.node_generator.get_llm_by_credentials")
@patch("codemie.workflows.workflow_generator.nodes.step_planner.get_llm_by_credentials")
@patch("codemie.workflows.workflow_generator.nodes.intent_analysis.get_llm_by_credentials")
def test_graph_sets_error_after_max_retries(mock_intent_llm, mock_step_planner_llm, mock_map_llm):
    from codemie.workflows.workflow_generator.workflow import WorkflowGeneratorGraph
    from codemie.workflows.workflow_generator.nodes.validation import MAX_VALIDATION_RETRIES

    # node_generator always returns invalid node → validation always fails
    mock_intent_llm.return_value = _make_mock_llm(_make_intent("s1"))
    mock_step_planner_llm.return_value = _make_mock_llm(_make_step_plans("s1"))
    mock_map_llm.return_value = _make_mock_llm(_make_invalid_node("s1"))

    graph = WorkflowGeneratorGraph(llm_model="gpt-4.1", request_id=None)
    final = graph.run(_make_initial_state())

    assert final.get("error") is not None
    assert final.get("result") is None
    # 1 initial full generation + (MAX_VALIDATION_RETRIES - 1) partial retries = MAX_VALIDATION_RETRIES
    invoke = mock_map_llm.return_value.with_structured_output.return_value.invoke
    assert invoke.call_count == MAX_VALIDATION_RETRIES


@patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow")
@patch("codemie.workflows.workflow_generator.nodes.node_generator.get_llm_by_credentials")
@patch("codemie.workflows.workflow_generator.nodes.step_planner.get_llm_by_credentials")
@patch("codemie.workflows.workflow_generator.nodes.intent_analysis.get_llm_by_credentials")
def test_graph_partial_retry_regenerates_only_failed_steps(
    mock_intent_llm, mock_step_planner_llm, mock_map_llm, mock_validate
):
    """When s1 fails validation, retry generates s1 only (1 call), not s2 again."""
    from codemie.workflows.workflow_generator.workflow import WorkflowGeneratorGraph

    mock_validate.return_value = Mock()
    mock_intent_llm.return_value = _make_mock_llm(_make_intent("s1", "s2"))
    mock_step_planner_llm.return_value = _make_mock_llm(_make_step_plans("s1", "s2"))

    # s1: first call → invalid (fails validation), retry → valid
    # s2: always valid
    s1_calls: list[int] = [0]

    def node_side_effect(prompt):
        # s2 step_plan always contains "step_id": "s2"; s1 calls (initial + retry)
        # use previous_node=None so "step_id": "s2" never appears in the s1 prompts.
        if '"step_id": "s2"' not in prompt:
            s1_calls[0] += 1
            if s1_calls[0] == 1:
                return _make_invalid_node("s1")
            return _make_valid_node("s1")
        return _make_valid_node("s2")

    llm = Mock()
    llm.with_structured_output.return_value.invoke.side_effect = node_side_effect
    mock_map_llm.return_value = llm

    graph = WorkflowGeneratorGraph(llm_model="gpt-4.1", request_id=None)
    final = graph.run(_make_initial_state())

    assert final.get("error") is None
    assert isinstance(final["result"], CreateWorkflowRequest)

    # 2 calls on first run (s1+s2) + 1 call on retry (s1 only) = 3
    invoke = mock_map_llm.return_value.with_structured_output.return_value.invoke
    assert invoke.call_count == 3
    assert s1_calls[0] == 2  # s1 generated twice
