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

"""Bounded comparisons of linker facts with real node writes, reducers and branch dispatch."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from codemie.core.workflow_models.workflow_models import CustomWorkflowNode, WorkflowState
from codemie.workflows.constants import (
    CONTEXT_STORE_VARIABLE,
    FIRST_STATE_IN_ITERATION,
    ITERATION_NODE_NUMBER_KEY,
    MESSAGES_VARIABLE,
    NEXT_KEY,
    TASK_KEY,
)
from codemie.workflows.contracts.abstract_context import AbstractContext, FieldState, FieldStatus
from codemie.workflows.contracts.effects import NodeKind, node_effect
from codemie.workflows.contracts.models import Outcome
from codemie.workflows.contracts.simulate import simulate_workflow
from codemie.workflows.contracts.transitions import apply_transition
from codemie.workflows.models import AgentMessages, NextAction, add_or_replace_context_store
from codemie.workflows.nodes.base_node import BaseNode
from codemie.workflows.nodes.summarize_conversation_node import SummarizeConversationCommandNode
from codemie.workflows.nodes.supervisor_node import SupervisorNode
from codemie.workflows.nodes.transform_node import TransformationError, TransformNode
from codemie.workflows.workflow import WorkflowExecutor


class _OutputNode(BaseNode):
    """Only the external producer is replaced; finalization and reduction are production code."""

    def execute(self, state_schema, execution_context):
        return self.kwargs["output"]

    def get_task(self, state_schema, *args, **kwargs):
        return "bounded fixture"


def _state(state_id="producer", **kwargs):
    return WorkflowState(id=state_id, **{"assistant_id": "a", "next": {"state_id": "end"}, **kwargs})


def _schema(fields, required=None):
    return json.dumps(
        {
            "type": "object",
            "properties": {name: {"type": kind} for name, kind in fields.items()},
            "required": list(fields) if required is None else required,
        }
    )


def _node(state, cls=_OutputNode, **kwargs):
    return cls(
        callbacks=[],
        workflow_execution_service=Mock(),
        thought_queue=Mock(),
        workflow_state=state,
        node_name=state.id,
        **kwargs,
    )


def _finalize(node, left, output, **extra):
    schema = {CONTEXT_STORE_VARIABLE: dict(left), MESSAGES_VARIABLE: [], **extra}
    processed = json.dumps(output)
    update = node.finalize_and_update_state(output, processed, True, schema)
    return add_or_replace_context_store(dict(left), update[CONTEXT_STORE_VARIABLE]), update


def _abstract(values, scope=0):
    return AbstractContext(
        fields={
            name: FieldState(status=FieldStatus.AVAILABLE, types=(_type(value),), types_certain=True)
            for name, value in values.items()
        },
        scope=scope,
    )


def _type(value):
    if isinstance(value, bool):
        return "boolean"
    return {str: "string", int: "integer", float: "number", list: "array", dict: "object", type(None): "null"}[
        type(value)
    ]


def _assert_guarantees(abstract, concrete):
    for name, field in abstract.fields.items():
        if field.status is FieldStatus.AVAILABLE and not field.optional:
            assert name in concrete, name
            if field.types_certain:
                assert _type(concrete[name]) in field.types or (
                    _type(concrete[name]) == "integer" and "number" in field.types
                ), (name, concrete[name], field.types)
        elif field.status is FieldStatus.REMOVED and not abstract.unknown_contribution:
            assert name not in concrete, name


@pytest.mark.parametrize(
    ("output", "contract", "optional", "known"),
    [
        pytest.param({"a": "value", "b": 2}, _schema({"a": "string", "b": "integer"}), False, True, id="dict"),
        pytest.param([{"a": "first"}, {"a": "last", "b": 2}], '[{"a":""},{"b":0}]', True, True, id="dict-list"),
        pytest.param([], '[{"a":""},{"b":0}]', True, True, id="empty-dict-list"),
        pytest.param("scalar", None, None, False, id="scalar"),
        pytest.param([1, 2], '[1,2]', None, False, id="scalar-list"),
    ],
)
@pytest.mark.parametrize(
    "flags",
    [
        {},
        {"append_to_context": True},
        {"clear_context_store": "keep_current"},
        {"clear_context_store": "keep_current", "append_to_context": True},
        {"reset_keys_in_context_store": ["a"]},
        {"clear_context_store": True, "output_key": "out"},
    ],
)
def test_output_shapes_reset_and_append_match_real_finalization(output, contract, optional, known, flags):
    state = _state(output_schema=contract, next={"state_id": "end", **flags})
    left = {"old": "kept", "a": "previous"}
    real, _ = _finalize(_node(state), left, output)
    effect = node_effect(state)
    result = apply_transition(_abstract(left), state_id=state.id, effect=effect, next_state=state.next)

    assert effect.known_source is known
    assert all(field.optional is optional for field in effect.fields)
    _assert_guarantees(result.context, real)
    if output == [{"a": "first"}, {"a": "last", "b": 2}] and not flags:
        assert real == {"old": "kept", "a": "last", "b": 2}
    if output == {"a": "value", "b": 2} and flags == {"append_to_context": True}:
        assert real == {"old": "kept", "a": ["previous", "value"], "b": [2]}
    if not known and not flags:
        assert result.context.unknown_contribution
        assert not result.context.fields["a"].types_certain


@pytest.mark.parametrize("reset", ["keep_current", True, None])
@pytest.mark.parametrize("output_key", [None, "out"])
def test_output_writing_disabled_preserves_values_except_full_reset(reset, output_key):
    state = _state(
        next={
            "state_id": "end",
            "store_in_context": False,
            "clear_context_store": reset or False,
            "reset_keys_in_context_store": ["old"],
            "output_key": output_key,
        }
    )
    left = {"old": "original", "keep": [1]}
    real, _ = _finalize(_node(state), left, {"new": 2})
    expected = {} if reset is True else dict(left)
    if output_key:
        expected[output_key] = '{"new": 2}'
    assert real == expected
    effect = node_effect(state)
    simulated = apply_transition(_abstract(left), state_id=state.id, effect=effect, next_state=state.next)
    _assert_guarantees(simulated.context, real)


@pytest.mark.parametrize("raw", [{"nested": 1}, [1, 2], 7])
def test_tool_result_unwrapping_keeps_its_concrete_type_and_abstract_uncertainty(raw):
    state = _state(assistant_id=None, tool_id="tool", next={"state_id": "end", "output_key": "out"})
    node = _node(state)
    update = node.finalize_and_update_state(
        SimpleNamespace(result=raw), "processed", True, {CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []}
    )
    real = add_or_replace_context_store({}, update[CONTEXT_STORE_VARIABLE])
    simulated = apply_transition(AbstractContext(), state_id=state.id, effect=node_effect(state), next_state=state.next)
    assert real == {"out": raw}
    assert not simulated.context.fields["out"].types_certain
    _assert_guarantees(simulated.context, real)


def _transform(config, **kwargs):
    state = _state(assistant_id=None, custom_node_id="transform", **kwargs)
    custom = CustomWorkflowNode(id="transform", custom_node_id="transform_node", config=config)
    return state, custom, _node(state, TransformNode, custom_node=custom)


@pytest.mark.parametrize("number", [2, 0])
def test_transform_mapping_values_types_and_real_write_path(number):
    config = {
        "mappings": [
            {"output_field": "number", "type": "extract", "source_path": "number"},
            {
                "output_field": "label",
                "type": "condition",
                "condition": "number > 0",
                "then_value": "yes",
                "else_value": "no",
            },
            {
                "output_field": "mixed",
                "type": "condition",
                "condition": "number > 0",
                "then_value": 1,
                "else_value": "none",
            },
            {"output_field": "names", "type": "array_map", "source_path": "items", "item_field": "name"},
            {"output_field": "text", "type": "template", "template": "Count {{ number }}"},
            {"output_field": "twice", "type": "script", "script": "number * 2"},
            {"output_field": "nothing", "type": "extract", "source_path": "absent"},
        ]
    }
    state, custom, node = _transform(config)
    source = {"number": number, "items": [{"name": "a"}, {"name": "b"}, {}]}
    output = node.execute({CONTEXT_STORE_VARIABLE: source, MESSAGES_VARIABLE: []}, {})
    assert output == {
        "number": number,
        "label": "yes" if number else "no",
        "mixed": 1 if number else "none",
        "names": ["a", "b"],
        "text": f"Count {number}",
        "twice": number * 2,
        "nothing": None,
    }
    real, _ = _finalize(node, source, output)
    effect = node_effect(state, custom_node=custom)
    assert effect.names == tuple(output)
    assert {f.name for f in effect.fields if f.types_certain} == {"label", "names", "text"}
    simulated = apply_transition(_abstract(source), state_id=state.id, effect=effect, next_state=state.next)
    _assert_guarantees(simulated.context, real)


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [("integer", "12", 12), ("number", "2.5", 2.5), ("boolean", 1, True), ("string", 17, "17")],
)
def test_transform_schema_coercion_matches_certain_types(kind, value, expected):
    config = {
        "mappings": [{"output_field": "value", "type": "constant", "value": value}],
        "output_schema": {"properties": {"value": {"type": kind}}, "required": ["value"]},
    }
    state, custom, node = _transform(config)
    output = node.execute({CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []}, {})
    assert output == {"value": expected}
    assert type(output["value"]) is type(expected)
    real, _ = _finalize(node, {}, output)
    simulated = apply_transition(
        AbstractContext(), state_id=state.id, effect=node_effect(state, custom_node=custom), next_state=state.next
    )
    assert simulated.context.fields["value"].types == (kind,)
    assert simulated.context.fields["value"].types_certain
    _assert_guarantees(simulated.context, real)


@pytest.mark.parametrize("strategy", ["fail", "skip", "default", "partial"])
def test_transform_required_field_and_invalid_coercion_follow_real_error_strategy(strategy):
    for config in [
        {"mappings": [], "output_schema": {"properties": {"needed": {"type": "string"}}, "required": ["needed"]}},
        {
            "mappings": [{"output_field": "value", "type": "constant", "value": "not an integer"}],
            "output_schema": {"properties": {"value": {"type": "integer"}}, "required": ["value"]},
        },
    ]:
        config.update(on_error=strategy, default_output={"fallback": "used"})
        state, custom, node = _transform(config)
        effect = node_effect(state, custom_node=custom)
        if strategy in ("fail", "partial"):
            # partial handles mapping errors only; validation still fails after the mapping loop.
            with pytest.raises(TransformationError):
                node.execute({CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []}, {})
        else:
            output = node.execute({CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []}, {})
            assert output == ({"fallback": "used"} if strategy == "default" else {})
            real, _ = _finalize(node, {}, output)
            simulated = apply_transition(AbstractContext(), state_id=state.id, effect=effect, next_state=state.next)
            assert all(field.optional for field in effect.fields)
            _assert_guarantees(simulated.context, real)


def _executor():
    # These branch methods need no service state. Do not create an execution or connect to storage.
    executor = object.__new__(WorkflowExecutor)
    executor.workflow_config = SimpleNamespace(enable_summarization_node=False)
    return executor


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("whitelist", [["keep"], [], ["*"]])
def test_real_iteration_dispatch_matches_abstract_whitelist_and_nested_scope(nested, whitelist):
    state = _state(
        output_schema=_schema({"items": "array"}),
        next={"state_id": "branch", "iter_key": "items", "include_in_iterator_context": whitelist},
    )
    left = {"keep": "kept", "drop": "filtered"}
    output = {"items": [{"item": "one"}, {"item": "two"}]}
    real, update = _finalize(_node(state), left, output)
    update[CONTEXT_STORE_VARIABLE] = real
    if nested:
        update[ITERATION_NODE_NUMBER_KEY] = 1
    sends = _executor().continue_iteration(update, state)
    simulated = apply_transition(
        _abstract(left, scope=int(nested)), state_id=state.id, effect=node_effect(state), next_state=state.next
    )
    assert len(sends) == 2
    branch = simulated.iteration_context
    for send in sends:
        assert send.arg[FIRST_STATE_IN_ITERATION] is True
        _assert_guarantees(branch, send.arg[CONTEXT_STORE_VARIABLE])
    assert set(sends[0].arg[CONTEXT_STORE_VARIABLE]) == (set(real) if nested or whitelist == ["*"] else set(whitelist))


@pytest.mark.parametrize("first", [False, True])
def test_only_first_iteration_node_spreads_the_item_into_context(first):
    state = _state(output_schema=_schema({"made": "string"}))
    real, _ = _finalize(
        _node(state),
        {},
        {"made": "result"},
        **{
            FIRST_STATE_IN_ITERATION: first,
            TASK_KEY: {"item": "from task"},
        },
    )
    assert real == ({"item": "from task", "made": "result"} if first else {"made": "result"})
    # The simulator cannot know item keys from an output's array schema. It must keep that source unknown.
    source = _state(
        "source", output_schema=_schema({"items": "array"}), next={"state_id": "producer", "iter_key": "items"}
    )
    consumer = _state("consumer", input_schema=_schema({"item": "string", "made": "string"}))
    state.next.state_id = "consumer"
    result = simulate_workflow([source, state, consumer], unknown_initial_input=False)
    records = {r.path: r.outcome for r in result.records if r.consumer_state_id == "consumer"}
    assert records["input_schema.item"] is Outcome.UNVERIFIED
    assert records["input_schema.made"] is Outcome.OK


@pytest.mark.parametrize("override", [False, True])
@pytest.mark.parametrize("finish", [False, True])
def test_real_iteration_task_override_and_finish_use_the_produced_or_original_task(override, finish):
    state, custom, node = _transform(
        {"mappings": [{"output_field": "items", "type": "constant", "value": [{"item": "produced"}]}]},
        finish_iteration=finish,
        next={"state_id": "branch", "iter_key": "items", "override_task": override},
    )
    output = node.execute({CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []}, {})
    real, update = _finalize(node, {}, output, **{TASK_KEY: {"item": "original"}})
    update[CONTEXT_STORE_VARIABLE] = real
    sends = _executor().continue_iteration(update, state)
    expected = {"item": "produced"} if finish else (json.dumps(output) if override else {"item": "original"})
    assert [s.arg[TASK_KEY] for s in sends] == [expected]
    simulated = apply_transition(
        AbstractContext(), state_id=state.id, effect=node_effect(state, custom_node=custom), next_state=state.next
    )
    _assert_guarantees(simulated.iteration_context, sends[0].arg[CONTEXT_STORE_VARIABLE])


@pytest.mark.parametrize("finish", [False, True])
def test_real_branch_convergence_retains_the_fields_the_simulator_guarantees(finish):
    source = _state(
        "source",
        output_schema=_schema({"items": "array"}),
        next={"state_id": "branch", "iter_key": "items", "include_in_iterator_context": []},
    )
    branch = _state(
        "branch",
        output_schema=_schema({"made": "string"}),
        finish_iteration=finish,
        next={"state_id": "join", "append_to_context": True},
    )
    join = _state("join", input_schema=_schema({"made": "array"}))
    states = [source, branch, join]
    outputs = {"source": {"items": [1, 2]}, "branch": {"made": "value"}}
    seen = []
    graph = StateGraph(AgentMessages)
    for state in states:
        node = _node(state)

        def run(schema, current=state, current_node=node):
            if current.id == "join":
                seen.append(dict(schema[CONTEXT_STORE_VARIABLE]))
                return {}
            output = outputs[current.id]
            return current_node.finalize_and_update_state(output, json.dumps(output), True, schema)

        graph.add_node(state.id, run, defer=state.id == "join")
    graph.add_edge(START, "source")
    executor = _executor()
    executor._process_transition(graph, source, False)
    graph.add_edge("branch", "join")
    graph.add_edge("join", END)
    compiled = graph.compile()
    compiled.invoke({MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}})
    assert seen == [{"items": [1, 2], "made": ["value", "value"]}]
    result = simulate_workflow(states, unknown_initial_input=False)
    record = next(r for r in result.records if r.consumer_state_id == "join" and r.path == "input_schema.made")
    assert record.outcome is Outcome.OK
    analysis = next(s for s in result.states if s.state_id == "join")
    assert analysis.iteration_depth == (0 if finish else 1)


@pytest.mark.parametrize("reset", [False, True, "keep_current"])
def test_summarization_override_does_not_apply_ordinary_context_flags(reset):
    state = _state(
        assistant_id=None,
        custom_node_id="summary",
        output_schema=_schema({"invented": "string"}),
        next={"state_id": "end", "clear_context_store": reset, "output_key": "out"},
    )
    custom = CustomWorkflowNode(id="summary", custom_node_id="summarize_conversation_node", config={})
    node = _node(state, SummarizeConversationCommandNode, workflow_config=None)
    command = node.finalize_and_update_state(
        "summary",
        "summary",
        True,
        {
            CONTEXT_STORE_VARIABLE: {"old": "kept"},
            MESSAGES_VARIABLE: [HumanMessage(content="message", id="1")],
            NEXT_KEY: ["end"],
        },
    )
    assert CONTEXT_STORE_VARIABLE not in command.update
    effect = node_effect(state, custom_node=custom)
    assert effect.kind is NodeKind.SUMMARIZE
    assert not effect.writes_context
    simulated = apply_transition(_abstract({"old": "kept"}), state_id=state.id, effect=effect, next_state=state.next)
    _assert_guarantees(simulated.context, {"old": "kept"})
    assert "out" not in simulated.context.fields
    assert "invented" not in simulated.context.fields


@pytest.mark.parametrize("reset", [False, True, "keep_current"])
def test_supervisor_uses_base_context_writes_but_not_its_declared_output_fields(reset):
    state = _state(
        assistant_id=None,
        custom_node_id="supervisor",
        output_schema=_schema({"invented": "string"}),
        next={"state_id": "end", "clear_context_store": reset, "output_key": "out"},
    )
    custom = CustomWorkflowNode(id="supervisor", custom_node_id="supervisor_node", config={})
    node = _node(state, SupervisorNode, enable_summarization_node=False)
    output = NextAction(next="end", task="task", reasoning="reason")
    processed = node.post_process_output({}, "task", output)
    command = node.finalize_and_update_state(
        output, processed, True, {CONTEXT_STORE_VARIABLE: {"old": "kept"}, MESSAGES_VARIABLE: []}
    )
    real = add_or_replace_context_store({"old": "kept"}, command.update[CONTEXT_STORE_VARIABLE])
    assert real["out"] == processed
    assert ("old" in real) is (reset is False)
    effect = node_effect(state, custom_node=custom)
    assert effect.kind is NodeKind.SUPERVISOR
    assert effect.fields == ()
    simulated = apply_transition(_abstract({"old": "kept"}), state_id=state.id, effect=effect, next_state=state.next)
    _assert_guarantees(simulated.context, real)


@pytest.mark.parametrize(
    ("declared", "value", "mapping_type", "expected_type"),
    [
        ("array", "still a string", "extract", None),
        ("object", 3, "extract", None),
        ("null", "still a string", "extract", None),
        ("integer", True, "extract", None),
        ("number", True, "extract", None),
        ("integer", True, "constant", "boolean"),
        ("number", True, "constant", "boolean"),
        ("array", "still a string", "constant", "string"),
        (["string"], 7, "constant", "integer"),
        (["integer"], "7", "constant", "string"),
        (["number"], "7.5", "constant", "string"),
        (["boolean"], "false", "constant", "string"),
        (["string"], 7, "extract", None),
        (["string", "null"], 7, "constant", "integer"),
    ],
)
def test_transform_does_not_claim_a_type_that_its_runtime_schema_does_not_enforce(
    declared, value, mapping_type, expected_type
):
    config = {
        "mappings": [{"output_field": "value", "type": mapping_type, "source_path": "value", "value": value}],
        "output_schema": {"properties": {"value": {"type": declared}}, "required": ["value"]},
    }
    state, custom, node = _transform(config)
    output = node.execute({CONTEXT_STORE_VARIABLE: {"value": value}, MESSAGES_VARIABLE: []}, {})
    assert output == {"value": value}
    assert type(output["value"]) is type(value)
    effect = node_effect(state, custom_node=custom)
    field = effect.fields[0]
    assert field.types == ((expected_type,) if expected_type else None)
    assert field.types_certain is (expected_type is not None)
    real, _ = _finalize(node, {}, output)
    simulated = apply_transition(AbstractContext(), state_id=state.id, effect=effect, next_state=state.next)
    _assert_guarantees(simulated.context, real)


def test_transform_default_output_bypasses_the_own_schema_coercion():
    config = {
        "mappings": [{"output_field": "value", "type": "constant", "value": "not an integer"}],
        "output_schema": {"properties": {"value": {"type": "integer"}}, "required": ["value"]},
        "on_error": "default",
        "default_output": {"value": "fallback"},
    }
    state, custom, node = _transform(config)
    output = node.execute({CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []}, {})
    assert output == {"value": "fallback"}
    effect = node_effect(state, custom_node=custom)
    field = effect.fields[0]
    assert field.optional
    assert not field.types_certain
    assert field.types is None
