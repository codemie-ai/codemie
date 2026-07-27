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
from codemie.workflows.workflow_generator.schemas import (
    GeneratedAssistant,
    GeneratedNextState,
    GeneratedState,
    GeneratedWorkflowConfig,
    MappedNode,
    NodeMappingPlan,
    StepPlan,
    WorkflowIntent,
    WorkflowStep,
)
from codemie.workflows.workflow_generator.state import WorkflowGeneratorState

pytestmark = pytest.mark.skipif(
    not config.WORKFLOW_GENERATION_ENABLED,
    reason="WORKFLOW_GENERATION_ENABLED is False",
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_step(step_id: str = "analyze") -> WorkflowStep:
    return WorkflowStep(id=step_id, description="Do something", state_type="agent", next_step_id=None)


def _make_intent(*step_ids: str) -> WorkflowIntent:
    return WorkflowIntent(
        workflow_name="test-workflow",
        workflow_description="A test workflow",
        steps=[_make_step(sid) for sid in (step_ids or ("analyze",))],
    )


def _make_mapped_node(step_id: str, next_state: str = "end") -> MappedNode:
    return MappedNode(
        step_id=step_id,
        state_type="agent",
        assistant_ref="test-assistant",
        task=f"Do {step_id}",
        transition_type="simple",
        next_state_ids=[next_state],
    )


def _make_state(**overrides) -> WorkflowGeneratorState:
    base: WorkflowGeneratorState = {
        "nl_query": "Create a workflow",
        "user": Mock(id="u1"),
        "project": "demo",
        "available_tools": [],
        "intent": _make_intent("step-a"),
        "step_plans": [],
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
    base.update(overrides)
    return base


def _make_valid_config(state_id: str = "step-a", assistant_id: str = "test-assistant") -> GeneratedWorkflowConfig:
    return GeneratedWorkflowConfig(
        states=[
            GeneratedState(
                id=state_id,
                task="Do something",
                assistant_id=assistant_id,
                next=GeneratedNextState(state_id="end", output_key=state_id),
            )
        ],
        assistants=[GeneratedAssistant(id=assistant_id, tools=[])],
    )


# ── ValidationNode: failed_step_ids extraction ───────────────────────────────


class TestValidationNodeFailedStepIds:
    def test_extracts_step_ids_from_state_errors(self):
        """Error messages matching 'State '<id>':' yield that id in failed_step_ids."""
        from codemie.workflows.workflow_generator.nodes.validation import ValidationNode

        bad_config = GeneratedWorkflowConfig(
            states=[
                GeneratedState(
                    id="step-a",
                    task="Do something",
                    assistant_id=None,  # triggers "assistant_id is required"
                    next=GeneratedNextState(state_id="end"),
                )
            ],
            assistants=[],
        )
        state = _make_state(
            intent=_make_intent("step-a"),
            generated_config=bad_config,
        )
        node = ValidationNode()
        result = node(state)

        assert "failed_step_ids" in result
        assert "step-a" in result["failed_step_ids"]

    @patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow")
    def test_dangling_transition_populates_failed_step_ids(self, mock_validate):
        """Dangling transition errors are tagged with the source State, populating failed_step_ids."""
        from codemie.workflows.workflow_generator.nodes.validation import ValidationNode

        mock_validate.side_effect = ValueError(
            {"errors": [{"reference_state": "step-a", "message": "Unknown transition target 'ghost-target'"}]}
        )

        # step-a has a valid terminal transition (state_id="end") but also a parallel
        # target in state_ids that doesn't exist. The per-state dangling error is now
        # prefixed with "State 'step-a':" so _STEP_ID_RE extracts it into failed_step_ids.
        bad_config = GeneratedWorkflowConfig(
            states=[
                GeneratedState(
                    id="step-a",
                    task="Do something",
                    assistant_id="test-assistant",
                    next=GeneratedNextState(
                        state_ids=["ghost-target"],
                        output_key="step_a_output",
                    ),
                )
            ],
            assistants=[GeneratedAssistant(id="test-assistant", tools=[])],
        )
        state = _make_state(
            intent=_make_intent("step-a"),
            generated_config=bad_config,
        )
        node = ValidationNode()
        result = node(state)

        assert "step-a" in result["failed_step_ids"]
        assert result["validation_errors"]

    @patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow")
    def test_clean_run_yields_empty_failed_step_ids(self, mock_validate):
        """Valid config → failed_step_ids is []."""
        from codemie.workflows.workflow_generator.nodes.validation import ValidationNode

        mock_validate.return_value = Mock()

        state = _make_state(
            intent=_make_intent("step-a"),
            generated_config=_make_valid_config("step-a"),
        )
        node = ValidationNode()
        result = node(state)

        assert result.get("validation_errors") == []
        assert result["failed_step_ids"] == []

    def test_multiple_step_errors_all_extracted(self):
        """When multiple states fail, all their IDs appear in failed_step_ids."""
        from codemie.workflows.workflow_generator.nodes.validation import ValidationNode

        bad_config = GeneratedWorkflowConfig(
            states=[
                GeneratedState(
                    id="step-a",
                    task="Do a",
                    assistant_id=None,
                    next=GeneratedNextState(state_id="step-b"),
                ),
                GeneratedState(
                    id="step-b",
                    task="Do b",
                    assistant_id=None,
                    next=GeneratedNextState(state_id="end"),
                ),
            ],
            assistants=[],
        )
        state = _make_state(
            intent=_make_intent("step-a", "step-b"),
            generated_config=bad_config,
        )
        node = ValidationNode()
        result = node(state)

        assert set(result["failed_step_ids"]) == {"step-a", "step-b"}


# ── NodeGeneratorNode: single-step generation ─────────────────────────────────


def _make_step_plan(step_id: str, next_step_id: str = "end") -> StepPlan:
    return StepPlan(step_id=step_id, transition_type="simple", next_step_id=next_step_id)


class TestNodeGeneratorNode:
    def _make_node(self):
        from codemie.workflows.workflow_generator.nodes.node_generator import NodeGeneratorNode

        return NodeGeneratorNode(llm_model="gpt-4.1", request_id="req-1")

    @patch("codemie.workflows.workflow_generator.nodes.node_generator.get_llm_by_credentials")
    def test_single_step_one_llm_call(self, mock_get_llm):
        """One step at current_node_index → one LLM call, node appended to plan."""
        intent = _make_intent("step-a", "step-b")
        mock_llm = Mock()
        mock_llm.with_structured_output.return_value.invoke.return_value = _make_mapped_node("step-a")
        mock_get_llm.return_value = mock_llm

        state = _make_state(
            intent=intent,
            step_plans=[_make_step_plan("step-a", "step-b"), _make_step_plan("step-b")],
            current_node_index=0,
        )
        result = self._make_node()(state)

        assert result.get("error") is None
        assert mock_llm.with_structured_output.return_value.invoke.call_count == 1
        assert result["current_node_index"] == 1
        assert len(result["node_plan"].nodes) == 1

    @patch("codemie.workflows.workflow_generator.nodes.node_generator.get_llm_by_credentials")
    def test_llm_exception_sets_error(self, mock_get_llm):
        """LLM raises → result has 'error' key, no node_plan update."""
        intent = _make_intent("step-a")
        mock_llm = Mock()
        mock_llm.with_structured_output.return_value.invoke.side_effect = RuntimeError("LLM down")
        mock_get_llm.return_value = mock_llm

        state = _make_state(
            intent=intent,
            step_plans=[_make_step_plan("step-a")],
            current_node_index=0,
        )
        result = self._make_node()(state)

        assert result.get("error") is not None
        assert "generation failed" in result["error"].lower()

    @patch("codemie.workflows.workflow_generator.nodes.node_generator.get_llm_by_credentials")
    def test_accumulates_into_existing_plan(self, mock_get_llm):
        """Second call appends to existing node_plan from prior iteration."""
        intent = _make_intent("step-a", "step-b")
        mock_llm = Mock()
        mock_llm.with_structured_output.return_value.invoke.return_value = _make_mapped_node("step-b")
        mock_get_llm.return_value = mock_llm

        existing_plan = NodeMappingPlan(nodes=[_make_mapped_node("step-a")])
        state = _make_state(
            intent=intent,
            step_plans=[_make_step_plan("step-a", "step-b"), _make_step_plan("step-b")],
            current_node_index=1,
            node_plan=existing_plan,
            previous_node=_make_mapped_node("step-a"),
        )
        result = self._make_node()(state)

        assert result.get("error") is None
        assert len(result["node_plan"].nodes) == 2
        assert result["node_plan"].nodes[1].step_id == "step-b"


# ── NodeRegeneratorNode: partial retry ────────────────────────────────────────


class TestNodeRegeneratorNode:
    def _make_node(self):
        from codemie.workflows.workflow_generator.nodes.node_regenerator import NodeRegeneratorNode

        return NodeRegeneratorNode(llm_model="gpt-4.1", request_id="req-1")

    @patch("codemie.workflows.workflow_generator.nodes.tools_selector.ToolsSelectorNode.select_tools_for_node")
    @patch("codemie.workflows.workflow_generator.nodes.node_generator.get_llm_by_credentials")
    def test_partial_retry_regenerates_only_failed_steps(self, mock_get_llm, mock_select_tools):
        """With failed_step_ids=['step-b'], only step-b gets an LLM call."""
        intent = _make_intent("step-a", "step-b", "step-c")
        new_node_b = MappedNode(
            step_id="step-b",
            state_type="agent",
            assistant_ref="new-assistant",
            task="updated task",
            transition_type="simple",
            next_state_ids=["end"],
        )
        mock_llm = Mock()
        mock_llm.with_structured_output.return_value.invoke.return_value = new_node_b
        mock_get_llm.return_value = mock_llm
        mock_select_tools.side_effect = lambda node, **_: node  # pass through unchanged

        existing_plan = NodeMappingPlan(
            nodes=[_make_mapped_node("step-a"), _make_mapped_node("step-b"), _make_mapped_node("step-c")]
        )
        state = _make_state(
            intent=intent,
            step_plans=[
                _make_step_plan("step-a", "step-b"),
                _make_step_plan("step-b", "step-c"),
                _make_step_plan("step-c"),
            ],
            node_plan=existing_plan,
            failed_step_ids=["step-b"],
        )
        result = self._make_node()(state)

        assert result.get("error") is None
        assert mock_llm.with_structured_output.return_value.invoke.call_count == 1
        nodes_by_id = {n.step_id: n for n in result["node_plan"].nodes}
        assert nodes_by_id["step-a"].assistant_ref == "test-assistant"
        assert nodes_by_id["step-b"].assistant_ref == "new-assistant"
        assert nodes_by_id["step-c"].assistant_ref == "test-assistant"
        assert [n.step_id for n in result["node_plan"].nodes] == ["step-a", "step-b", "step-c"]

    def test_empty_failed_step_ids_is_noop(self):
        """Empty failed_step_ids → returns early, no LLM calls, failed_step_ids cleared."""
        state = _make_state(failed_step_ids=[])
        result = self._make_node()(state)

        assert result == {"failed_step_ids": []}

    @patch("codemie.workflows.workflow_generator.nodes.tools_selector.ToolsSelectorNode.select_tools_for_node")
    @patch("codemie.workflows.workflow_generator.nodes.node_generator.get_llm_by_credentials")
    def test_clears_failed_step_ids_in_return(self, mock_get_llm, mock_select_tools):
        """NodeRegeneratorNode always returns failed_step_ids=[] after regeneration."""
        intent = _make_intent("step-a")
        mock_llm = Mock()
        mock_llm.with_structured_output.return_value.invoke.return_value = _make_mapped_node("step-a")
        mock_get_llm.return_value = mock_llm
        mock_select_tools.side_effect = lambda node, **_: node

        state = _make_state(
            intent=intent,
            step_plans=[_make_step_plan("step-a")],
            node_plan=NodeMappingPlan(nodes=[_make_mapped_node("step-a")]),
            failed_step_ids=["step-a"],
        )
        result = self._make_node()(state)

        assert result["failed_step_ids"] == []
