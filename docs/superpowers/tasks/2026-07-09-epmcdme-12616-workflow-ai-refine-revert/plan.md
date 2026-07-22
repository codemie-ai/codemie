# Workflow AI Refine Rework (EPMCDME-12616) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ad-hoc `PromptGeneratorChain` refine implementation with a `WorkflowRefinerGraph` that reuses 10037's node infrastructure, and remove all revert-related code.

**Architecture:** A new `WorkflowRefinerGraph` (sibling to `WorkflowGeneratorGraph`) wires a single new `RefinePlannerNode` entry point into the existing `node_generator_router → node_generator → tools_selector → config_assembly → validation → node_regenerator` loop. `RefinePlannerNode` converts `(existing_yaml_config, refine_prompt, available_tools)` into `WorkflowIntent` + `list[StepPlan]` so the shared generation loop can run unmodified. All revert endpoint code and the flat `workflow_generator_prompt.py` file are deleted.

**Tech Stack:** Python 3.11, LangGraph, LangChain, pydantic v2, pytest-asyncio, httpx AsyncClient

## Global Constraints

- All new Python files must begin with the EPAM Apache 2.0 license header (copy from any existing file in the same package)
- Use `from __future__ import annotations` in every new file
- Follow existing TypedDict / Pydantic patterns exactly; no `dataclass` or `attrs`
- `get_llm_by_credentials(llm_model, temperature=0.0, streaming=False, request_id=...)` is the only way to obtain an LLM instance
- Never import `WorkflowGeneratorGraph` from `workflow_refiner.py` — the two graphs are independent siblings
- Commit message prefix: `EPMCDME-12616: `

---

### Task 1: Extend state fields and state_keys

**Test-first: no** — TypedDict additions have no behaviour to test independently.

**Files:**
- Modify: `src/codemie/workflows/workflow_generator/state_keys.py`
- Modify: `src/codemie/workflows/workflow_generator/state.py`

**Interfaces:**
- Produces: `sk.EXISTING_YAML_CONFIG = "existing_yaml_config"`, `sk.REFINE_PROMPT = "refine_prompt"` — used by `RefinePlannerNode` and graph initialisation

- [ ] **Step 1: Add two new keys to `state_keys.py`**

Append after the last line of `src/codemie/workflows/workflow_generator/state_keys.py`:

```python
EXISTING_YAML_CONFIG = "existing_yaml_config"
REFINE_PROMPT = "refine_prompt"
```

- [ ] **Step 2: Add the two optional fields to `WorkflowGeneratorState`**

In `src/codemie/workflows/workflow_generator/state.py`, append inside the `WorkflowGeneratorState` TypedDict body, after `error: Optional[str]`:

```python
    existing_yaml_config: Optional[str]  # set by the refine path; absent on the generate path
    refine_prompt: Optional[str]         # user's refinement instruction; absent on the generate path
```

- [ ] **Step 3: Commit**

```bash
git add src/codemie/workflows/workflow_generator/state_keys.py \
        src/codemie/workflows/workflow_generator/state.py
git commit -m "EPMCDME-12616: Add existing_yaml_config and refine_prompt state fields"
```

---

### Task 2: Add `RefinePlan` schema

**Test-first: no** — pure data model; validated implicitly by later node tests.

**Files:**
- Modify: `src/codemie/workflows/workflow_generator/schemas.py`

**Interfaces:**
- Produces: `RefinePlan(intent: WorkflowIntent, plans: list[StepPlan])` — structured output type for `RefinePlannerNode`

- [ ] **Step 1: Append `RefinePlan` to `schemas.py`**

At the end of `src/codemie/workflows/workflow_generator/schemas.py`, after the `WorkflowPlan` class:

```python
class RefinePlan(BaseModel):
    """Structured output from RefinePlannerNode — combines intent and step plans in one LLM call."""

    intent: WorkflowIntent = Field(
        description=(
            "Complete desired WorkflowIntent for the refined workflow. "
            "workflow_name and workflow_description should match the existing workflow unless "
            "the refine_prompt explicitly requests renaming. "
            "steps must list ALL steps in the final workflow — preserved, modified, and new — "
            "in execution order. Removed steps must be absent."
        )
    )
    plans: list[StepPlan] = Field(
        description=(
            "One StepPlan per step in intent.steps, in the same order. "
            "Preserved steps carry a description that matches the original node configuration "
            "so the generator can reproduce them faithfully. "
            "Modified and new steps carry the desired change description."
        )
    )
```

- [ ] **Step 2: Commit**

```bash
git add src/codemie/workflows/workflow_generator/schemas.py
git commit -m "EPMCDME-12616: Add RefinePlan schema for refine planner structured output"
```

---

### Task 3: Refine planning prompt template

**Test-first: no** — prompt string; correctness verified by integration test in Task 5.

**Files:**
- Create: `src/codemie/templates/agents/workflow_generator/refine_planning.py`
- Modify: `src/codemie/templates/agents/workflow_generator/__init__.py`

**Interfaces:**
- Produces: `REFINE_PLANNING_PROMPT: str` — imported by `RefinePlannerNode`

- [ ] **Step 1: Create `refine_planning.py`**

Create `src/codemie/templates/agents/workflow_generator/refine_planning.py`:

```python
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

REFINE_PLANNING_PROMPT = """You are an expert workflow engineer. \
Analyse the existing workflow and the refinement instruction, then produce a RefinePlan.

## Existing workflow YAML
{existing_yaml_config}

## Refinement instruction
{refine_prompt}

## Available tools
{available_tools}

## Your task

Produce a RefinePlan with two fields:

### intent
A WorkflowIntent that describes the COMPLETE desired workflow after refinement:
- workflow_name / workflow_description: copy from the existing workflow unless the instruction \
explicitly requests a rename
- steps: list ALL steps in the final workflow in execution order
  - Preserved steps: copy id, description, and all properties from the existing workflow exactly
  - Modified steps: update only the properties the instruction targets; preserve everything else
  - New steps: describe the new step clearly
  - Removed steps: omit entirely
- data_sources / ambiguities: carry over from the existing workflow intent if discernible; \
otherwise use empty lists

### plans
One StepPlan per step in intent.steps, in the same order:
- Preserved steps: set transition_type, next_step_id, output_key, and all data-flow properties \
to match the original step exactly so the node generator reproduces it faithfully
- Modified / new steps: plan the data flow changes the instruction requires

## Rules
- ONLY use tool names that already appear in the existing workflow YAML. \
Do NOT invent new tool names.
- Never add "start" or "end" as entries in intent.steps.
- The last step's StepPlan must have next_step_id = "end".
- If the instruction would require a completely new workflow unrelated to the existing one, \
still return a RefinePlan; treat all steps as new.
"""
```

- [ ] **Step 2: Export from `__init__.py`**

In `src/codemie/templates/agents/workflow_generator/__init__.py`, add the import and export:

```python
from codemie.templates.agents.workflow_generator.refine_planning import REFINE_PLANNING_PROMPT

__all__ = [
    "INTENT_ANALYSIS_PROMPT",
    "NODE_GENERATION_PROMPT",
    "STEP_PLANNING_PROMPT",
    "TOOLS_SELECTION_PROMPT",
    "REFINE_PLANNING_PROMPT",
]
```

- [ ] **Step 3: Commit**

```bash
git add src/codemie/templates/agents/workflow_generator/refine_planning.py \
        src/codemie/templates/agents/workflow_generator/__init__.py
git commit -m "EPMCDME-12616: Add refine planning prompt template"
```

---

### Task 4: `RefinePlannerNode`

**Test-first: yes** — unit test with mocked LLM verifies state output shape.

**Files:**
- Create: `src/codemie/workflows/workflow_generator/nodes/refine_planner.py`
- Create: `tests/codemie/workflows/workflow_generator/nodes/test_refine_planner.py`

**Interfaces:**
- Consumes: `sk.EXISTING_YAML_CONFIG`, `sk.REFINE_PROMPT`, `sk.AVAILABLE_TOOLS`, `sk.PROJECT`, `sk.USER`
- Produces: `sk.INTENT` (`WorkflowIntent`), `sk.STEP_PLANS` (`list[StepPlan]`), `sk.CURRENT_NODE_INDEX` (0), `sk.PREVIOUS_NODE` (None)

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/workflows/workflow_generator/nodes/test_refine_planner.py`:

```python
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

from codemie.workflows.workflow_generator import state_keys as sk
from codemie.workflows.workflow_generator.schemas import (
    RefinePlan,
    StepPlan,
    WorkflowIntent,
    WorkflowStep,
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
            node(state)  # must not raise

        prompt_arg = mock_structured.invoke.call_args[0][0]
        # refine_prompt placeholder should be empty string, not "None"
        assert "None" not in prompt_arg
```

- [ ] **Step 2: Run test — verify it fails**

```bash
python -m pytest tests/codemie/workflows/workflow_generator/nodes/test_refine_planner.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError` or `ImportError` — `refine_planner` does not exist yet.

- [ ] **Step 3: Implement `RefinePlannerNode`**

Create `src/codemie/workflows/workflow_generator/nodes/refine_planner.py`:

```python
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

from typing import Optional

from codemie.core.dependecies import get_llm_by_credentials
from codemie.templates.agents.workflow_generator import REFINE_PLANNING_PROMPT
from codemie.workflows.workflow_generator.schemas import RefinePlan
from codemie.workflows.workflow_generator.state import WorkflowGeneratorState
from codemie.workflows.workflow_generator import state_keys as sk


class RefinePlannerNode:
    def __init__(self, llm_model: str, request_id: Optional[str] = None):
        self.llm_model = llm_model
        self.request_id = request_id

    def _format_tools(self, available_tools: list) -> str:
        lines: list[str] = []
        for toolkit in available_tools:
            for tool in toolkit.get("tools", []):
                name = tool.get("name", "")
                desc = tool.get("description", "")
                if name:
                    lines.append(f"  {name}: {desc}")
        return "\n".join(lines) if lines else "  (none)"

    def __call__(self, state: WorkflowGeneratorState) -> dict:
        existing_yaml: str = state[sk.EXISTING_YAML_CONFIG]
        refine_prompt: str = state.get(sk.REFINE_PROMPT) or ""
        available_tools: list = state.get(sk.AVAILABLE_TOOLS) or []

        prompt = REFINE_PLANNING_PROMPT.format(
            existing_yaml_config=existing_yaml,
            refine_prompt=refine_prompt,
            available_tools=self._format_tools(available_tools),
        )

        llm = get_llm_by_credentials(
            llm_model=self.llm_model,
            temperature=0.0,
            streaming=False,
            request_id=self.request_id,
        )
        plan: RefinePlan = llm.with_structured_output(RefinePlan).invoke(prompt)

        return {
            sk.INTENT: plan.intent,
            sk.STEP_PLANS: plan.plans,
            sk.CURRENT_NODE_INDEX: 0,
            sk.PREVIOUS_NODE: None,
        }
```

- [ ] **Step 4: Run tests — verify all pass**

```bash
python -m pytest tests/codemie/workflows/workflow_generator/nodes/test_refine_planner.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/workflows/workflow_generator/nodes/refine_planner.py \
        tests/codemie/workflows/workflow_generator/nodes/test_refine_planner.py
git commit -m "EPMCDME-12616: Add RefinePlannerNode with tests"
```

---

### Task 5: `WorkflowRefinerGraph`

**Test-first: yes** — verifies instantiation and that `.run()` delegates to the compiled graph.

**Files:**
- Create: `src/codemie/workflows/workflow_generator/workflow_refiner.py`
- Create: `tests/codemie/workflows/workflow_generator/test_workflow_refiner.py`

**Interfaces:**
- Consumes: `WorkflowGeneratorState` with `existing_yaml_config` and `refine_prompt` populated
- Produces: `WorkflowGeneratorState` with `result` (`CreateWorkflowRequest`) or `error` (str)

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/workflows/workflow_generator/test_workflow_refiner.py`:

```python
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


class TestWorkflowRefinerGraph:
    def test_instantiates_with_llm_model(self):
        from codemie.workflows.workflow_generator.workflow_refiner import WorkflowRefinerGraph

        graph = WorkflowRefinerGraph(llm_model="gpt-4o", request_id=None)
        assert graph.llm_model == "gpt-4o"
        assert graph.request_id is None

    def test_run_invokes_compiled_graph(self):
        from codemie.workflows.workflow_generator.workflow_refiner import WorkflowRefinerGraph

        expected_state = {"result": Mock(), "error": None, "validation_errors": []}
        graph = WorkflowRefinerGraph(llm_model="gpt-4o", request_id="req-1")
        with patch.object(graph, "graph") as mock_compiled:
            mock_compiled.invoke.return_value = expected_state
            initial_state = {
                "existing_yaml_config": "name: test\n",
                "refine_prompt": "improve it",
                "user": Mock(),
                "project": "demo",
                "available_tools": [],
                "nl_query": "",
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
            result = graph.run(initial_state)

        mock_compiled.invoke.assert_called_once_with(initial_state)
        assert result is expected_state

    def test_does_not_import_workflow_generator_graph(self):
        """WorkflowRefinerGraph must not depend on WorkflowGeneratorGraph."""
        import ast
        import pathlib

        source = pathlib.Path(
            "src/codemie/workflows/workflow_generator/workflow_refiner.py"
        ).read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [
                    alias.name for alias in getattr(node, "names", [])
                ]
                module = getattr(node, "module", "") or ""
                assert "WorkflowGeneratorGraph" not in names, (
                    "workflow_refiner.py must not import WorkflowGeneratorGraph"
                )
                assert "workflow_generator.workflow" not in module or "workflow_refiner" in module, (
                    "workflow_refiner.py must not import from workflow_generator.workflow"
                )
```

- [ ] **Step 2: Run test — verify it fails**

```bash
python -m pytest tests/codemie/workflows/workflow_generator/test_workflow_refiner.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError` — `workflow_refiner` does not exist yet.

- [ ] **Step 3: Implement `WorkflowRefinerGraph`**

Create `src/codemie/workflows/workflow_generator/workflow_refiner.py`:

```python
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

from typing import Optional

from langgraph.constants import END
from langgraph.graph import StateGraph

from codemie.workflows.workflow_generator.nodes.config_assembly import ConfigAssemblyNode
from codemie.workflows.workflow_generator.nodes.node_generator import NodeGeneratorNode
from codemie.workflows.workflow_generator.nodes.node_generator_router import NodeGeneratorRouterNode
from codemie.workflows.workflow_generator.nodes.node_regenerator import NodeRegeneratorNode
from codemie.workflows.workflow_generator.nodes.refine_planner import RefinePlannerNode
from codemie.workflows.workflow_generator.nodes.tools_selector import ToolsSelectorNode
from codemie.workflows.workflow_generator.nodes.validation import ValidationNode
from codemie.workflows.workflow_generator.state import WorkflowGeneratorState
from codemie.workflows.workflow_generator import state_keys as sk


def _route_from_router(state: WorkflowGeneratorState) -> str:
    if state.get(sk.ERROR):
        return END
    if state.get(sk.CURRENT_NODE_INDEX, 0) < len(state[sk.INTENT].steps):
        return "generate"
    return "assemble"


def _route_after_validation(state: WorkflowGeneratorState) -> str:
    if state.get(sk.ERROR):
        return END
    if state.get(sk.VALIDATION_ERRORS):
        return "node_regenerator"
    return END


class WorkflowRefinerGraph:
    def __init__(self, llm_model: str, request_id: Optional[str] = None):
        self.llm_model = llm_model
        self.request_id = request_id
        self._refine_planner_node = RefinePlannerNode(llm_model, request_id)
        self._router_node = NodeGeneratorRouterNode()
        self._generator_node = NodeGeneratorNode(llm_model, request_id)
        self._regenerator_node = NodeRegeneratorNode(llm_model, request_id)
        self._config_assembly_node = ConfigAssemblyNode()
        self._tools_selector_node = ToolsSelectorNode(llm_model, request_id)
        self._validation_node = ValidationNode()
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(WorkflowGeneratorState)

        workflow.add_node("refine_planner", self._refine_planner_node)
        workflow.add_node("node_generator_router", self._router_node)
        workflow.add_node("node_generator", self._generator_node)
        workflow.add_node("node_regenerator", self._regenerator_node)
        workflow.add_node("config_assembly", self._config_assembly_node)
        workflow.add_node("tools_selector", self._tools_selector_node)
        workflow.add_node("validation", self._validation_node)

        workflow.set_entry_point("refine_planner")
        workflow.add_edge("refine_planner", "node_generator_router")
        workflow.add_conditional_edges(
            "node_generator_router",
            _route_from_router,
            {
                "generate": "node_generator",
                "assemble": "config_assembly",
                END: END,
            },
        )
        workflow.add_edge("node_generator", "tools_selector")
        workflow.add_edge("tools_selector", "node_generator_router")
        workflow.add_edge("config_assembly", "validation")
        workflow.add_conditional_edges(
            "validation",
            _route_after_validation,
            {
                "node_regenerator": "node_regenerator",
                END: END,
            },
        )
        workflow.add_edge("node_regenerator", "config_assembly")

        return workflow.compile()

    def run(self, initial_state: WorkflowGeneratorState) -> WorkflowGeneratorState:
        return self.graph.invoke(initial_state)
```

- [ ] **Step 4: Run tests — verify all pass**

```bash
python -m pytest tests/codemie/workflows/workflow_generator/test_workflow_refiner.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/workflows/workflow_generator/workflow_refiner.py \
        tests/codemie/workflows/workflow_generator/test_workflow_refiner.py
git commit -m "EPMCDME-12616: Add WorkflowRefinerGraph wiring refine_planner into shared nodes"
```

---

### Task 6: Update `WorkflowGeneratorService.refine_workflow()` to use the graph

**Test-first: yes** — replace the three PromptGeneratorChain-based tests with graph-mocking tests.

**Files:**
- Modify: `src/codemie/service/workflow_generator_service.py`
- Modify: `tests/codemie/service/test_workflow_generator_service.py`

**Interfaces:**
- Consumes: `WorkflowRefinerGraph(llm_model, request_id).run(state)` → `state["result"]` (`CreateWorkflowRequest`) or `state["error"]` (str)
- Produces: `WorkflowRefineResponse(yaml_config=state["result"].yaml_config)`

- [ ] **Step 1: Replace the three old refine tests with graph-mocking tests**

In `tests/codemie/service/test_workflow_generator_service.py`, delete the section starting with `# ── EPMCDME-12616: refine_workflow tests ────` through the end of the file (the three tests: `test_refine_workflow_returns_revised_yaml`, `test_refine_workflow_raises_on_invalid_yaml`, `test_refine_workflow_raises_500_on_chain_failure`), and replace with:

```python
# ── EPMCDME-12616: refine_workflow tests (graph-based) ──────────────────────────

EXAMPLE_PROJECT = "example_project"
EXAMPLE_YAML = "name: my-workflow\nstates:\n  - id: state1\n"
REFINED_YAML = "name: my-workflow\nstates:\n  - id: state1\n    description: improved\n"


@pytest.fixture
def refine_user():
    from codemie.rest_api.security.user import User as _User

    return _User(id="123", username="testuser", name="Test User", project_names=[EXAMPLE_PROJECT])


@patch("codemie.service.workflow_generator_service.request_summary_manager")
@patch("codemie.service.workflow_generator_service.emit_llm_token_metric")
@patch("codemie.service.workflow_generator_service.WorkflowRefinerGraph")
@patch("codemie.service.workflow_generator_service.ToolsInfoService")
@patch("codemie.service.workflow_generator_service.llm_service")
def test_refine_workflow_returns_response(
    mock_llm_svc, mock_tools_svc, mock_graph_class, mock_emit, mock_summary, refine_user
):
    from codemie.core.workflow_models.workflow_models import CreateWorkflowRequest, WorkflowMode
    from codemie.rest_api.models.workflow_generator import WorkflowRefineResponse
    from codemie.service.workflow_generator_service import WorkflowGeneratorService

    mock_llm_svc.default_llm_model = "gpt-4o"
    mock_tools_svc.get_tools_info.return_value = []

    create_req = CreateWorkflowRequest(
        name="my-workflow",
        description="",
        project=EXAMPLE_PROJECT,
        mode=WorkflowMode.SEQUENTIAL,
        states=[],
        assistants=[],
        yaml_config=REFINED_YAML,
    )
    mock_graph = Mock()
    mock_graph.run.return_value = {"result": create_req, "error": None, "validation_errors": []}
    mock_graph_class.return_value = mock_graph

    result = WorkflowGeneratorService.refine_workflow(
        yaml_config=EXAMPLE_YAML,
        refine_prompt="improve descriptions",
        user=refine_user,
        llm_model="gpt-4o",
        request_id="req-1",
    )

    assert isinstance(result, WorkflowRefineResponse)
    assert result.yaml_config == REFINED_YAML
    mock_emit.assert_called_once()
    mock_summary.clear_summary.assert_called_once_with("req-1")


@patch("codemie.service.workflow_generator_service.request_summary_manager")
@patch("codemie.service.workflow_generator_service.WorkflowRefinerGraph")
@patch("codemie.service.workflow_generator_service.ToolsInfoService")
@patch("codemie.service.workflow_generator_service.llm_service")
def test_refine_workflow_raises_on_graph_error(
    mock_llm_svc, mock_tools_svc, mock_graph_class, mock_summary, refine_user
):
    from codemie.core.exceptions import ExtendedHTTPException
    from codemie.service.workflow_generator_service import WorkflowGeneratorService

    mock_llm_svc.default_llm_model = "gpt-4o"
    mock_tools_svc.get_tools_info.return_value = []

    mock_graph = Mock()
    mock_graph.run.return_value = {
        "result": None,
        "error": "Validation failed after 3 retries",
        "validation_errors": ["missing field"],
    }
    mock_graph_class.return_value = mock_graph

    with pytest.raises(ExtendedHTTPException) as exc_info:
        WorkflowGeneratorService.refine_workflow(
            yaml_config=EXAMPLE_YAML,
            refine_prompt=None,
            user=refine_user,
            llm_model=None,
            request_id="req-2",
        )

    assert exc_info.value.code == 500
    mock_summary.clear_summary.assert_called_once_with("req-2")


@patch("codemie.service.workflow_generator_service.request_summary_manager")
@patch("codemie.service.workflow_generator_service.WorkflowRefinerGraph")
@patch("codemie.service.workflow_generator_service.ToolsInfoService")
@patch("codemie.service.workflow_generator_service.llm_service")
def test_refine_workflow_raises_500_on_unexpected_exception(
    mock_llm_svc, mock_tools_svc, mock_graph_class, mock_summary, refine_user
):
    from codemie.core.exceptions import ExtendedHTTPException
    from codemie.service.workflow_generator_service import WorkflowGeneratorService

    mock_llm_svc.default_llm_model = "gpt-4o"
    mock_tools_svc.get_tools_info.return_value = []
    mock_graph_class.side_effect = RuntimeError("LLM unavailable")

    with pytest.raises(ExtendedHTTPException) as exc_info:
        WorkflowGeneratorService.refine_workflow(
            yaml_config=EXAMPLE_YAML,
            refine_prompt=None,
            user=refine_user,
            llm_model=None,
            request_id="req-3",
        )

    assert exc_info.value.code == 500
    mock_summary.clear_summary.assert_called_once_with("req-3")
```

- [ ] **Step 2: Run the new tests — verify they fail**

```bash
python -m pytest tests/codemie/service/test_workflow_generator_service.py -k "refine" -v 2>&1 | tail -10
```

Expected: 3 tests FAIL (service still uses `PromptGeneratorChain`).

- [ ] **Step 3: Update `refine_workflow()` in `workflow_generator_service.py`**

Replace the current `refine_workflow` method body and its imports. In `src/codemie/service/workflow_generator_service.py`:

1. Remove the import of `PromptGeneratorChain`:
   ```python
   # DELETE this line:
   from codemie.service.assistant_generator_service import PromptGeneratorChain
   ```

2. Remove the import of `WORKFLOW_REFINE_TEMPLATE, WorkflowRefineDetails`:
   ```python
   # DELETE these lines:
   from codemie.templates.agents.workflow_generator_prompt import (
       WORKFLOW_REFINE_TEMPLATE,
       WorkflowRefineDetails,
   )
   ```

3. Add an import for `WorkflowRefinerGraph` and `ToolsInfoService` (keep existing if already present):
   ```python
   from codemie.workflows.workflow_generator.workflow_refiner import WorkflowRefinerGraph
   ```

4. Replace the `refine_workflow` method with:
   ```python
   @classmethod
   def refine_workflow(
       cls,
       yaml_config: str,
       refine_prompt: Optional[str],
       user: User,
       llm_model: Optional[str],
       request_id: Optional[str],
   ) -> WorkflowRefineResponse:
       if not llm_model:
           llm_model = config.WORKFLOW_GENERATOR_LLM_MODEL or llm_service.default_llm_model

       try:
           available_tools = ToolsInfoService.get_tools_info(user=user, exclude_toolkits=["Plugin"])

           initial_state = {
               "nl_query": "",
               "user": user,
               "project": user.current_project,
               "available_tools": available_tools,
               "existing_yaml_config": yaml_config,
               "refine_prompt": refine_prompt or "",
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

           graph = WorkflowRefinerGraph(llm_model=llm_model, request_id=request_id)
           final_state = graph.run(initial_state)

           if final_state.get("error"):
               raise ExtendedHTTPException(
                   code=500,
                   message="Workflow refinement failed after validation retries",
                   details=final_state["error"],
                   help=_REFINE_HELP_MESSAGE,
               )

           emit_llm_token_metric(
               name=WORKFLOW_AI_REFINE_TOTAL_METRIC,
               request_id=request_id,
               base_attributes={
                   MetricsAttributes.LLM_MODEL: llm_model,
                   MetricsAttributes.USER_ID: logging_user_id.get("-"),
                   MetricsAttributes.USER_NAME: current_user_email.get("-"),
                   MetricsAttributes.PROJECT: get_project_for_metric(),
               },
           )

           return WorkflowRefineResponse(yaml_config=final_state["result"].yaml_config)

       except ExtendedHTTPException:
           raise
       except Exception as exc:
           logger.error(f"Failed to refine workflow: {exc}", exc_info=True)
           raise ExtendedHTTPException(
               code=500,
               message="Failed to refine workflow",
               details=f"An error occurred while refining the workflow: {str(exc)}",
               help=_REFINE_HELP_MESSAGE,
           ) from exc
       finally:
           if request_id:
               request_summary_manager.clear_summary(request_id)
   ```

5. Remove `_REFINE_HELP_MESSAGE` if it was only used by the old implementation — verify it is still needed (it is; keep it).

- [ ] **Step 4: Run the new tests — verify all pass**

```bash
python -m pytest tests/codemie/service/test_workflow_generator_service.py -v
```

Expected: all tests PASS (old generate tests unaffected, 3 new refine tests PASS).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/workflow_generator_service.py \
        tests/codemie/service/test_workflow_generator_service.py
git commit -m "EPMCDME-12616: Replace PromptGeneratorChain refine with WorkflowRefinerGraph"
```

---

### Task 7: Remove all revert code and cleanup

**Test-first: no** — deletions; we verify by running the full test suite.

**Files:**
- Modify: `src/codemie/rest_api/routers/workflow.py`
- Modify: `src/codemie/service/workflow_service.py`
- Modify: `src/codemie/core/workflow_models/workflow_config.py`
- Modify: `src/codemie/rest_api/models/workflow_generator.py`
- Modify: `src/codemie/service/monitoring/workflow_monitoring_service.py`
- Modify: `src/codemie/service/monitoring/metrics_constants.py`
- Delete: `src/codemie/templates/agents/workflow_generator_prompt.py`
- Modify: `tests/codemie/service/test_workflow_service.py`
- Modify: `tests/codemie/rest_api/routers/test_workflow.py`

- [ ] **Step 1: Remove `WorkflowRevertRequest` import and revert endpoint from router**

In `src/codemie/rest_api/routers/workflow.py`:

1. Remove `WorkflowRevertRequest` from the import block (lines ~46–49):
   ```python
   # Change:
   from codemie.rest_api.models.workflow_generator import (
       WorkflowRefineRequest,
       WorkflowRefineResponse,
       WorkflowRevertRequest,
   )
   # To:
   from codemie.rest_api.models.workflow_generator import (
       WorkflowRefineRequest,
       WorkflowRefineResponse,
   )
   ```

2. Delete the entire `revert_workflow` endpoint function (lines ~432–466):
   ```python
   # DELETE from:
   @router.post(
       "/workflows/{workflow_id}/revert",
   # TO (inclusive):
       return {"message": "Workflow reverted successfully", "data": reverted_workflow}
   ```

- [ ] **Step 2: Remove `revert_workflow` and `change_type` from `WorkflowService`**

In `src/codemie/service/workflow_service.py`:

1. Delete the `revert_workflow` method (lines ~175–199):
   ```python
   # DELETE from:
   def revert_workflow(self, stored_config: WorkflowConfig, user: User) -> WorkflowConfig:
   # TO (inclusive — ends before @staticmethod create_workflow_execution):
       return stored_config
   ```

2. Remove `change_type` from `_update_workflow_values` signature and usage (lines ~676–688):
   ```python
   # Change signature from:
   def _update_workflow_values(
       self,
       stored_config: WorkflowConfig,
       updated_workflow_config: WorkflowConfig,
       user: User,
       change_type: Optional[str] = None,
   ) -> None:
   # To:
   def _update_workflow_values(
       self,
       stored_config: WorkflowConfig,
       updated_workflow_config: WorkflowConfig,
       user: User,
   ) -> None:
   ```

3. Remove `change_type=change_type` from the `YamlConfigHistory(...)` call inside `_update_workflow_values`:
   ```python
   # Change:
   new_history_entry = YamlConfigHistory(
       yaml_config=stored_config.yaml_config,
       date=datetime.now(),
       created_by=user.as_user_model(),
       change_type=change_type,
   )
   # To:
   new_history_entry = YamlConfigHistory(
       yaml_config=stored_config.yaml_config,
       date=datetime.now(),
       created_by=user.as_user_model(),
   )
   ```

- [ ] **Step 3: Remove `change_type` from `YamlConfigHistory`**

In `src/codemie/core/workflow_models/workflow_config.py`, delete line 61:
```python
# DELETE:
    change_type: Optional[str] = None
```

- [ ] **Step 4: Remove `WorkflowRevertRequest` from models**

In `src/codemie/rest_api/models/workflow_generator.py`, delete the `WorkflowRevertRequest` class:
```python
# DELETE:
class WorkflowRevertRequest(BaseModel):
    project: Optional[str] = None
```

- [ ] **Step 5: Remove revert monitoring method and metric constant**

In `src/codemie/service/monitoring/workflow_monitoring_service.py`:
- Remove the `WORKFLOW_REVERT_TOTAL_METRIC` import from `metrics_constants`
- Delete the `send_workflow_revert_metric` class method (lines ~198–214)

In `src/codemie/service/monitoring/metrics_constants.py`, delete line 59:
```python
# DELETE:
WORKFLOW_REVERT_TOTAL_METRIC = "codemie_workflow_revert_total"
```

- [ ] **Step 6: Delete the old flat prompt file**

```bash
git rm src/codemie/templates/agents/workflow_generator_prompt.py
```

- [ ] **Step 7: Remove revert and change_type tests from `test_workflow_service.py`**

In `tests/codemie/service/test_workflow_service.py`, delete the section starting with `# ── EPMCDME-12616: revert and change_type ──` through the end of the revert tests:

Delete all of these test functions:
- `test_yaml_config_history_default_change_type_is_none`
- `test_yaml_config_history_accepts_revert_change_type`
- `test_revert_workflow_calls_update_with_history_yaml`
- `test_revert_workflow_raises_when_no_history`

- [ ] **Step 8: Remove revert router tests from `test_workflow.py`**

In `tests/codemie/rest_api/routers/test_workflow.py`, delete these test functions (and any `YamlConfigHistory` import that is only used by them):
- `test_revert_workflow_success`
- `test_revert_workflow_no_history`

- [ ] **Step 9: Run the full test suite — verify no regressions**

```bash
python -m pytest tests/codemie/rest_api/routers/test_workflow.py \
                 tests/codemie/service/test_workflow_service.py \
                 tests/codemie/service/test_workflow_generator_service.py \
                 -v 2>&1 | tail -20
```

Expected: all remaining tests PASS; zero failures; zero collection errors.

- [ ] **Step 10: Commit**

```bash
git add src/codemie/rest_api/routers/workflow.py \
        src/codemie/service/workflow_service.py \
        src/codemie/core/workflow_models/workflow_config.py \
        src/codemie/rest_api/models/workflow_generator.py \
        src/codemie/service/monitoring/workflow_monitoring_service.py \
        src/codemie/service/monitoring/metrics_constants.py \
        tests/codemie/service/test_workflow_service.py \
        tests/codemie/rest_api/routers/test_workflow.py
git commit -m "EPMCDME-12616: Remove revert endpoint, change_type field, and cleanup"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| `WorkflowRefinerGraph` with `RefinePlannerNode` entry point | Task 4, 5 |
| Reuses `NodeGeneratorNode`, `ConfigAssemblyNode`, `ValidationNode` | Task 5 (graph wiring) |
| New `refine_planning.py` in workflow_generator subpackage | Task 3 |
| `existing_yaml_config` + `refine_prompt` state fields | Task 1 |
| `RefinePlan` schema | Task 2 |
| `refine_workflow()` uses graph, same pattern as `generate()` | Task 6 |
| Remove revert endpoint and all related code | Task 7 |
| Remove `workflow_generator_prompt.py` | Task 7, Step 6 |
| Remove `change_type` from `YamlConfigHistory` | Task 7, Step 3 |
| Remove `WorkflowRevertRequest` | Task 7, Step 4 |
| Remove revert monitoring | Task 7, Step 5 |
| Tests for `RefinePlannerNode` | Task 4 |
| Tests for `WorkflowRefinerGraph` | Task 5 |
| Tests for updated service | Task 6 |
| Existing refine router tests preserved | Task 7, Step 8 |

**Placeholder scan:** No TBDs, TODOs, or "similar to" references. All code blocks are complete.

**Type consistency:**
- `RefinePlan` uses `WorkflowIntent` and `list[StepPlan]` — both from `schemas.py`, consistent with how `StepPlannerNode` outputs `plan.plans` (same `StepPlan` type)
- `RefinePlannerNode` emits `sk.STEP_PLANS` as `plan.plans` — consumed by `NodeGeneratorNode` which reads `state[sk.STEP_PLANS]` — consistent
- `WorkflowRefineResponse(yaml_config=final_state["result"].yaml_config)` — `final_state["result"]` is `CreateWorkflowRequest` which has `yaml_config: Optional[str]` — consistent
