# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""Conditional routing followed by a per-item fan-out, in the two built-in templates that need both.

`next.condition` and `next.iter_key` cannot share a block, so each template routes through a no-LLM dispatch state
(a `transform_node`) that re-emits the list and fans it out. These tests run the template's own states through a real
compiled LangGraph with the production state schema. Only the model calls are replaced, by canned outputs.
"""

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import Mock

import pytest
from langgraph.errors import GraphRecursionError
from langgraph.graph import START

from codemie.configs.config import config
from codemie.core.workflow_models.workflow_config import WorkflowConfig, WorkflowConfigTemplate
from codemie.workflows.constants import CONTEXT_STORE_VARIABLE, MESSAGES_VARIABLE, TASK_KEY
from codemie.workflows.nodes.base_node import BaseNode
from codemie.workflows.workflow import WorkflowExecutor

TEMPLATES_DIR = Path(config.WORKFLOW_TEMPLATES_DIR)
AICE_TEMPLATE = "aice_java_migration_template.yaml"
SONAR_TEMPLATE = "workflow_sonar_issues_resolver_java_template.yaml"

FILES = [["new_app/pom.xml", "add dependencies"], ["new_app/App.java", "add main class"], ["new_app/a.html", "page"]]
ISSUES = [{"rule": "java:S1", "line": 1}, {"rule": "java:S2", "line": 2}, {"rule": "java:S3", "line": 3}]


class _CannedNode(BaseNode):
    """A real BaseNode (so the real state update is built) whose model call is replaced by a canned output."""

    def __init__(self, *args, canned_output: str | list[str] = "{}", seen: list | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.canned_outputs = [canned_output] if isinstance(canned_output, str) else list(canned_output)
        self.seen = seen if seen is not None else []

    def execute(self, state_schema, execution_context):
        self.seen.append(state_schema.get(TASK_KEY))
        # One answer per call; the last one repeats
        return self.canned_outputs.pop(0) if len(self.canned_outputs) > 1 else self.canned_outputs[0]

    def post_process_output(self, state_schema, task, output):
        """Like an agent node, hand the model text through unchanged (the base class would JSON-encode it again)."""
        return output

    def get_task(self, state_schema, *args, **kwargs):
        return "task"


def _recording(node, seen: list):
    """Wrap a real node so each run is recorded with the task it received."""

    def run(state_schema):
        seen.append(state_schema.get(TASK_KEY))
        return node(state_schema)

    return run


def _load(template_file: str) -> WorkflowConfig:
    template = WorkflowConfigTemplate.from_yaml((TEMPLATES_DIR / template_file).read_text(encoding="utf-8"))
    workflow_config = WorkflowConfig(
        name=template.name, description=template.description, mode=template.mode, yaml_config=template.yaml_config
    )
    workflow_config.parse_execution_config()
    return workflow_config


def _run(
    template_file: str,
    entry: str,
    run_states: set[str],
    canned: Mapping[str, str | list[str]],
    store: dict | None = None,
):
    """Run the given states of a template from `entry`; return (calls per state, final context store).

    A state in `canned` is a model-backed state answering with that output (or with each of a list of outputs in turn).
    `transform_node` states run for real.
    Every other state in `run_states` answers `{}`. Each state records the task it received.
    """
    workflow_config = _load(template_file)
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow_config = workflow_config
    executor.callbacks = [Mock()]
    executor.workflow_execution_service = Mock(start_state=Mock(return_value="state_id"))
    executor.thought_queue = Mock()
    executor.execution_id = "exec_id"

    map_states = executor.find_map_nodes()
    states = [s for s in workflow_config.states if s.id in run_states]
    calls: dict[str, list] = {s.id: [] for s in states}

    graph = executor.init_state_graph()
    for state in states:
        if state.custom_node_id:
            graph.add_node(state.id, _recording(executor.init_custom_node(state), calls[state.id]))
        else:
            graph.add_node(
                state.id,
                _CannedNode(
                    [Mock()],
                    executor.workflow_execution_service,
                    executor.thought_queue,
                    workflow_state=state,
                    node_name=state.id,
                    current_task_key=TASK_KEY if state.id in map_states else None,
                    canned_output=canned.get(state.id, "{}"),
                    seen=calls[state.id],
                ),
            )
    graph.add_edge(START, entry)
    for state in states:
        executor._process_transition(graph, state, enable_summarization_node=False)

    result = graph.compile().invoke(
        {CONTEXT_STORE_VARIABLE: dict(store or {}), MESSAGES_VARIABLE: []}, {"recursion_limit": 50}
    )
    return calls, result[CONTEXT_STORE_VARIABLE]


# --- AICE: double-checker -> (to_double_check) -> files-dispatcher -> per-file double-checker-dev ---

AICE_STATES = {"double-checker", "files-dispatcher", "double-checker-dev", "finalizer"}


def _aice(to_double_check: bool, files: list) -> dict[str, list]:
    output = json.dumps({"to_double_check": to_double_check, "files": files})
    calls, _ = _run(AICE_TEMPLATE, "double-checker", AICE_STATES, {"double-checker": output})
    return calls


def test_aice_true_branch_runs_double_checker_dev_once_per_file():
    calls = _aice(True, FILES)

    assert Counter(map(json.dumps, calls["double-checker-dev"])) == Counter(map(json.dumps, FILES))
    assert len(calls["files-dispatcher"]) == 1


def test_aice_true_branch_reaches_finalizer():
    assert _aice(True, FILES)["finalizer"]


def test_aice_false_branch_goes_to_finalizer_without_dispatching():
    calls = _aice(False, FILES)

    assert calls["double-checker-dev"] == []
    assert calls["files-dispatcher"] == []
    assert len(calls["finalizer"]) == 1


def test_aice_full_chain_dispatches_each_double_check_file_once():
    """From the initializer: each fan-out joins back into one run of the next state, and the dispatch fans out last."""
    states = {"initializer", "developer", "checker", "checker-dev", *AICE_STATES}
    canned = {
        "initializer": json.dumps({"files": ["new_app/a", "new_app/b"]}),
        "checker": json.dumps({"files": [["new_app/x.java", "x"], ["new_app/y.java", "y"]]}),
        "double-checker": json.dumps({"to_double_check": True, "files": FILES[:2]}),
    }

    calls, _ = _run(AICE_TEMPLATE, "initializer", states, canned)

    assert len(calls["developer"]) == 2
    assert len(calls["checker-dev"]) == 2
    assert len(calls["double-checker"]) == 1
    assert len(calls["files-dispatcher"]) == 1
    assert Counter(map(json.dumps, calls["double-checker-dev"])) == Counter(map(json.dumps, FILES[:2]))
    assert len(calls["finalizer"]) == 1


def test_aice_single_file_is_dispatched_once():
    calls = _aice(True, FILES[:1])

    assert calls["double-checker-dev"] == FILES[:1]


# --- Sonar: issue-searcher -> (len(issues) <= 35) -> issues-dispatcher -> per-issue issue-resolver ---

SONAR_STATES = {"issue-searcher", "issues-dispatcher", "issue-resolver", "git-pusher"}


def _sonar(*answers: list) -> dict[str, list]:
    """Run the Sonar chain with the searcher answering `answers` in turn (each a list of issues)."""
    searcher = [json.dumps({"issues": issues}) for issues in answers]
    calls, _ = _run(SONAR_TEMPLATE, "issue-searcher", SONAR_STATES, {"issue-searcher": searcher})
    return calls


def test_sonar_within_limit_runs_issue_resolver_once_per_issue():
    calls = _sonar(ISSUES)

    assert Counter(map(json.dumps, calls["issue-resolver"])) == Counter(map(json.dumps, ISSUES))
    assert len(calls["issues-dispatcher"]) == 1
    assert len(calls["issue-searcher"]) == 1


def test_sonar_at_the_limit_still_dispatches():
    calls = _sonar([{"n": i} for i in range(35)])

    assert len(calls["issue-resolver"]) == 35
    assert len(calls["issue-searcher"]) == 1


def test_sonar_over_the_limit_loops_back_to_the_searcher_and_dispatches_the_next_answer():
    calls = _sonar([{"n": i} for i in range(36)], ISSUES)

    assert len(calls["issue-searcher"]) == 2
    assert len(calls["issues-dispatcher"]) == 1
    assert Counter(map(json.dumps, calls["issue-resolver"])) == Counter(map(json.dumps, ISSUES))


def test_sonar_over_the_limit_keeps_looping_instead_of_dispatching():
    """Every answer is over the limit, so the searcher is re-run until the recursion limit stops the graph."""
    with pytest.raises(GraphRecursionError):
        _sonar([{"n": i} for i in range(36)])
