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

import pytest

from codemie.configs import config
from codemie.workflows.workflow_generator.schemas import (
    WorkflowStep,
    WorkflowIntent,
    MappedNode,
    NodeMappingPlan,
    GeneratedWorkflowConfig,
)
from codemie.workflows.workflow_generator.state import WorkflowGeneratorState

pytestmark = pytest.mark.skipif(
    not config.WORKFLOW_GENERATION_ENABLED,
    reason="WORKFLOW_GENERATION_ENABLED is False",
)


def test_workflow_step_instantiation():
    step = WorkflowStep(
        id="analyze-input",
        description="Analyze user input",
        state_type="agent",
        next_step_id="generate-output",
    )
    assert step.id == "analyze-input"
    assert step.state_type == "agent"
    assert step.next_step_id == "generate-output"


def test_workflow_step_last_has_null_next():
    step = WorkflowStep(id="last", description="Last step", state_type="agent", next_step_id=None)
    assert step.next_step_id is None


def test_workflow_intent_instantiation():
    intent = WorkflowIntent(
        workflow_name="Code Reviewer",
        workflow_description="Reviews code for bugs",
        steps=[WorkflowStep(id="review", description="Review code", state_type="agent", next_step_id=None)],
    )
    assert intent.workflow_name == "Code Reviewer"
    assert len(intent.steps) == 1


def test_mapped_node_agent_type():
    node = MappedNode(
        step_id="review",
        state_type="agent",
        assistant_ref="code-reviewer",
        task="Review the submitted code for bugs and return structured findings.",
    )
    assert node.assistant_ref == "code-reviewer"
    assert node.tools == []


def test_node_mapping_plan():
    plan = NodeMappingPlan(
        nodes=[
            MappedNode(
                step_id="s1",
                state_type="agent",
                assistant_ref="a1",
                task="Do something",
            ),
        ]
    )
    assert len(plan.nodes) == 1


def test_generated_workflow_config():
    config = GeneratedWorkflowConfig(states=[], assistants=[], tools=[])
    assert config.states == []


def test_workflow_generator_state_is_typeddict():
    state: WorkflowGeneratorState = {
        "nl_query": "create a workflow",
        "user": None,
        "project": "demo",
        "available_tools": [],
        "intent": None,
        "node_plan": None,
        "generated_config": None,
        "validation_errors": [],
        "validation_attempts": 0,
        "result": None,
        "error": None,
    }
    assert state["nl_query"] == "create a workflow"
    assert state["validation_attempts"] == 0


def test_prompts_importable():
    from codemie.templates.agents.workflow_generator import (
        INTENT_ANALYSIS_PROMPT,
        NODE_GENERATION_PROMPT,
        STEP_PLANNING_PROMPT,
        TOOLS_SELECTION_PROMPT,
    )

    assert "{nl_query}" in INTENT_ANALYSIS_PROMPT
    assert "{step_plan}" in NODE_GENERATION_PROMPT
    assert "{previous_node}" in NODE_GENERATION_PROMPT
    assert "{intent}" in STEP_PLANNING_PROMPT
    assert "{step_action}" in TOOLS_SELECTION_PROMPT
