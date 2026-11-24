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

"""Node effects and the single transition function (EPMCDME-16503, T5).

Covers AC-10 (output key, output writing off, accumulation, list union, sub-workflow), AC-40 (full reset plus an
explicit output key), AC-44 (transform) and the parity of the mirrored write rules against the real reducer path.
The runtime modules (`workflows/models.py:39`, `nodes/base_node.py:663-703`, `nodes/transform_node.py:173-219`) are
imported here, in the test, and never by `contracts/`.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from codemie.core.workflow_models.workflow_models import (
    CustomWorkflowNode,
    WorkflowNextState,
    WorkflowState,
)
from codemie.workflows.constants import (
    CONTEXT_STORE_VARIABLE,
    FIRST_STATE_IN_ITERATION,
    MESSAGES_VARIABLE,
    TASK_KEY,
)
from codemie.workflows.contracts.abstract_context import (
    INITIAL_INPUT_SOURCE,
    AbstractContext,
    FieldState,
    FieldStatus,
    Mechanism,
    initial_context,
)
from codemie.workflows.contracts.effects import (
    NODE_EFFECTS,
    NOTE_DECLARED_FIELD_NOT_PRODUCED,
    NOTE_OUTPUT_CONTRACT_IGNORED,
    NOTE_TYPE_CONFLICT,
    SUMMARIZE_NODE_KIND,
    SUPERVISOR_NODE_KIND,
    NodeEffect,
    NodeKind,
    ProducedField,
    classify_node_kind,
    node_effect,
)
from codemie.workflows.contracts.transitions import (
    NEXT_FIELD_IMPACTS,
    STATE_FIELD_IMPACTS,
    FieldImpact,
    apply_transition,
)
from codemie.workflows.contracts.reader import ContractForm, ProducerNote
from codemie.workflows.models import add_or_replace_context_store
from codemie.workflows.nodes.base_node import BaseNode
from codemie.workflows.nodes.transform_node import TransformationError, TransformNode

SCHEMA_CONTRACT = '{"type":"object","properties":{"doc":{"type":"string"},"items":{"type":"array"}},"required":["doc"]}'
EXAMPLE_CONTRACT = '{"success": "Boolean true | false", "issues": "List of issues"}'
LIST_CONTRACT = '[{"document": "Document title"}, {"content": "Extracted information"}]'
UNKNOWN_LIST_CONTRACT = '["List of found features"]'
RECOVERED_CONTRACT = '{\n  "all_done": "Boolean true | false"\n  "note": "unparseable, no comma above"\n}'


def _state(**overrides) -> WorkflowState:
    data = {"id": "producer", "assistant_id": "assistant-1", "next": {"state_id": "consumer"}}
    data.update(overrides)
    return WorkflowState(**data)


def _transform(config) -> CustomWorkflowNode:
    return CustomWorkflowNode(id="transform-1", custom_node_id="transform_node", config=config)


def _custom(custom_node_id: str) -> CustomWorkflowNode:
    return CustomWorkflowNode(id="node-1", custom_node_id=custom_node_id, config={})


def _effect_fields(effect: NodeEffect) -> list[tuple]:
    return [(item.name, item.types, item.optional) for item in effect.fields]


def _notes(effect: NodeEffect) -> list[tuple[str, str]]:
    return [(note.code, note.name) for note in effect.notes]


def _known_effect(*fields: ProducedField) -> NodeEffect:
    """A stand-in known producer, so the transition tests do not depend on the per-kind functions."""
    return NodeEffect(kind=NodeKind.AGENT, fields=fields, known_source=True, form=ContractForm.SCHEMA, schema_form=True)


def _context(*names: str, unknown: bool = False, scope: int = 0) -> AbstractContext:
    fields = {
        name: FieldState(
            status=FieldStatus.AVAILABLE, producers=("upstream",), types=("string",), types_certain=True, scope=scope
        )
        for name in names
    }
    return AbstractContext(fields=fields, unknown_contribution=unknown, scope=scope)


def _available(context: AbstractContext) -> list[str]:
    return sorted(name for name, state in context.fields.items() if state.status is FieldStatus.AVAILABLE)


def _removed(context: AbstractContext) -> dict[str, Mechanism | None]:
    return {name: state.mechanism for name, state in context.fields.items() if state.status is FieldStatus.REMOVED}


# --- node kinds ---------------------------------------------------------------------------------

KINDS = [
    pytest.param(_state(), None, NodeKind.AGENT, id="agent"),
    pytest.param(_state(assistant_id=None, tool_id="tool-1"), None, NodeKind.TOOL, id="tool"),
    pytest.param(_state(assistant_id=None, workflow_id="wf-1"), None, NodeKind.SUB_WORKFLOW, id="sub-workflow"),
    pytest.param(
        _state(assistant_id=None, custom_node_id="transform-1"), _transform({}), NodeKind.TRANSFORM, id="transform"
    ),
    pytest.param(
        _state(assistant_id=None, custom_node_id="node-1"),
        _custom("state_processor_node"),
        NodeKind.CUSTOM,
        id="generic-custom",
    ),
    pytest.param(
        _state(assistant_id=None, custom_node_id="node-1"),
        _custom(SUMMARIZE_NODE_KIND),
        NodeKind.SUMMARIZE,
        id="summarize",
    ),
    pytest.param(
        _state(assistant_id=None, custom_node_id="node-1"),
        _custom(SUPERVISOR_NODE_KIND),
        NodeKind.SUPERVISOR,
        id="supervisor",
    ),
    pytest.param(_state(assistant_id=None, custom_node_id="node-1"), None, NodeKind.UNKNOWN, id="custom-unresolved"),
    pytest.param(SimpleNamespace(id="broken", next=None), None, NodeKind.UNKNOWN, id="no-type-field"),
]


@pytest.mark.parametrize(("state", "custom_node", "expected"), KINDS)
def test_classify_node_kind(state, custom_node, expected):
    assert classify_node_kind(state, custom_node) is expected


def test_every_kind_but_unknown_has_a_dispatch_entry():
    assert set(NODE_EFFECTS) == {kind for kind in NodeKind if kind is not NodeKind.UNKNOWN}


def test_unknown_kind_is_an_unknown_source():
    effect = node_effect(SimpleNamespace(id="broken", next=None))

    assert effect.kind is NodeKind.UNKNOWN
    assert effect.known_source is False
    assert effect.fields == ()


# --- contract-driven kinds (AC-10, AC-17) -------------------------------------------------------

# (output_schema, known_source, (name, types, optional) rows, form)
CONTRACT_EFFECTS = [
    pytest.param(None, False, [], ContractForm.NONE, id="no-contract"),
    pytest.param(
        SCHEMA_CONTRACT,
        True,
        [("doc", ("string",), False), ("items", ("array",), True)],
        ContractForm.SCHEMA,
        id="schema-form",
    ),
    pytest.param(
        EXAMPLE_CONTRACT,
        True,
        [("success", None, False), ("issues", None, False)],
        ContractForm.EXAMPLE,
        id="example-form-presence-only",
    ),
    pytest.param(
        LIST_CONTRACT,
        True,
        [("document", None, True), ("content", None, True)],
        ContractForm.LIST,
        id="list-union-is-optional",
    ),
    pytest.param(UNKNOWN_LIST_CONTRACT, False, [], ContractForm.LIST, id="list-without-dict-items"),
    pytest.param(
        RECOVERED_CONTRACT,
        True,
        [("all_done", None, False), ("note", None, False)],
        ContractForm.INVALID,
        id="recovered-keys",
    ),
]


@pytest.mark.parametrize(("contract", "known", "rows", "form"), CONTRACT_EFFECTS)
def test_agent_effect_spread_follows_the_output_contract(contract, known, rows, form):
    effect = node_effect(_state(output_schema=contract))

    assert effect.kind is NodeKind.AGENT
    assert effect.known_source is known
    assert _effect_fields(effect) == rows
    assert effect.form is form


def test_schema_form_is_flagged_for_workflow_strict():
    assert node_effect(_state(output_schema=SCHEMA_CONTRACT)).schema_form is True
    assert node_effect(_state(output_schema=EXAMPLE_CONTRACT)).schema_form is False


def test_unknown_source_contract_carries_the_producer_warning():
    assert node_effect(_state(output_schema=UNKNOWN_LIST_CONTRACT)).producer_note is ProducerNote.UNKNOWN_SOURCE


def test_recovered_contract_carries_the_advisory_note():
    effect = node_effect(_state(output_schema=RECOVERED_CONTRACT))

    assert effect.producer_note is ProducerNote.CONVERT_TO_SCHEMA
    assert effect.recovered is True


def test_sub_workflow_without_a_declared_contract_is_an_unknown_source():
    effect = node_effect(_state(assistant_id=None, workflow_id="wf-1"))

    assert effect.kind is NodeKind.SUB_WORKFLOW
    assert effect.known_source is False


def test_sub_workflow_with_a_declared_contract_is_a_known_source():
    effect = node_effect(_state(assistant_id=None, workflow_id="wf-1", output_schema=SCHEMA_CONTRACT))

    assert effect.known_source is True
    assert effect.names == ("doc", "items")


def test_generic_custom_node_without_a_contract_is_an_unknown_source():
    state = _state(assistant_id=None, custom_node_id="node-1")

    assert node_effect(state, custom_node=_custom("generate_documents_tree")).known_source is False


def test_generic_custom_node_follows_its_declared_contract():
    state = _state(assistant_id=None, custom_node_id="node-1", output_schema=SCHEMA_CONTRACT)

    assert node_effect(state, custom_node=_custom("generate_documents_tree")).names == ("doc", "items")


# --- output key value types (AC-10, `.result` unwrapping) ---------------------------------------

OUTPUT_VALUE_TYPES = [
    pytest.param(_state(), None, ("string",), True, id="agent-task-result-is-text"),
    pytest.param(_state(assistant_id=None, tool_id="tool-1"), None, ("string",), False, id="tool-result-unwrapping"),
    pytest.param(
        _state(assistant_id=None, custom_node_id="transform-1"), _transform({}), ("string",), True, id="transform-json"
    ),
    pytest.param(_state(assistant_id=None, workflow_id="wf-1"), None, ("string",), True, id="sub-workflow-text"),
]


@pytest.mark.parametrize(("state", "custom_node", "types", "certain"), OUTPUT_VALUE_TYPES)
def test_output_key_value_type(state, custom_node, types, certain):
    effect = node_effect(state, custom_node=custom_node)

    assert effect.output_value_types == types
    assert effect.output_value_certain is certain


# --- summarize and supervisor -------------------------------------------------------------------


def test_summarize_node_writes_no_context():
    effect = node_effect(_state(assistant_id=None, custom_node_id="node-1"), custom_node=_custom(SUMMARIZE_NODE_KIND))

    assert effect.kind is NodeKind.SUMMARIZE
    assert effect.writes_context is False
    assert effect.known_source is True
    assert effect.fields == ()


def test_supervisor_node_writes_context_but_contributes_nothing():
    effect = node_effect(_state(assistant_id=None, custom_node_id="node-1"), custom_node=_custom(SUPERVISOR_NODE_KIND))

    assert effect.kind is NodeKind.SUPERVISOR
    assert effect.writes_context is True
    assert effect.known_source is True
    assert effect.fields == ()


def test_declared_contract_on_a_fixed_output_node_is_noted_as_ignored():
    state = _state(assistant_id=None, custom_node_id="node-1", output_schema=SCHEMA_CONTRACT)

    assert _notes(node_effect(state, custom_node=_custom(SUPERVISOR_NODE_KIND))) == [(NOTE_OUTPUT_CONTRACT_IGNORED, "")]


# --- AC-44: the transform -----------------------------------------------------------------------


def _transform_effect(config, **state_overrides) -> NodeEffect:
    state = _state(assistant_id=None, custom_node_id="transform-1", **state_overrides)
    node = config if isinstance(config, SimpleNamespace) else _transform(config)
    return node_effect(state, custom_node=node)


TRANSFORM_MAPPING_TYPES = [
    pytest.param({"output_field": "a", "type": "constant", "value": "x"}, ("string",), id="constant-string"),
    pytest.param({"output_field": "a", "type": "constant", "value": 3}, ("integer",), id="constant-integer"),
    pytest.param({"output_field": "a", "type": "constant", "value": 1.5}, ("number",), id="constant-number"),
    pytest.param({"output_field": "a", "type": "constant", "value": True}, ("boolean",), id="constant-boolean"),
    pytest.param({"output_field": "a", "type": "constant", "value": []}, ("array",), id="constant-array"),
    pytest.param({"output_field": "a", "type": "constant", "value": {}}, ("object",), id="constant-object"),
    pytest.param({"output_field": "a", "type": "constant", "value": None}, None, id="constant-null"),
    pytest.param({"output_field": "a", "type": "template", "template": "t"}, ("string",), id="template-string"),
    pytest.param({"output_field": "a", "type": "array_map", "source_path": "x"}, ("array",), id="array-map-array"),
    pytest.param({"output_field": "a", "type": "extract", "source_path": "x"}, None, id="extract-unknown"),
    pytest.param({"output_field": "a", "type": "script", "script": "1"}, None, id="script-unknown"),
    pytest.param({"output_field": "a", "source_path": "x"}, None, id="default-type-is-extract"),
    pytest.param(
        {"output_field": "a", "type": "condition", "condition": "x", "then_value": 1, "else_value": 2},
        ("integer",),
        id="condition-same-type",
    ),
    pytest.param(
        {"output_field": "a", "type": "condition", "condition": "x", "then_value": 1, "else_value": "no"},
        None,
        id="condition-mixed-types",
    ),
]


@pytest.mark.parametrize(("mapping", "types"), TRANSFORM_MAPPING_TYPES)
def test_transform_types_come_from_the_mapping_type(mapping, types):
    effect = _transform_effect({"mappings": [mapping]})

    assert effect.known_source is True
    assert _effect_fields(effect) == [("a", types, False)]


def test_transform_is_a_known_source_without_any_schema():
    effect = _transform_effect(
        {
            "mappings": [
                {"output_field": "number", "type": "extract", "source_path": "number"},
                {"output_field": "summary", "type": "template", "template": "x"},
            ]
        }
    )

    assert effect.known_source is True
    assert effect.names == ("number", "summary")


def test_transform_own_output_schema_cannot_guarantee_integer_for_an_unknown_input():
    effect = _transform_effect(
        {
            "mappings": [{"output_field": "number", "type": "extract", "source_path": "number"}],
            "output_schema": {"type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]},
        }
    )

    assert _effect_fields(effect) == [("number", None, False)]


def test_transform_own_output_schema_without_a_root_type_is_still_read():
    effect = _transform_effect(
        {
            "mappings": [{"output_field": "number", "type": "extract", "source_path": "number"}],
            "output_schema": {"properties": {"number": {"type": "string"}}},
        }
    )

    assert _effect_fields(effect) == [("number", ("string",), False)]


TRANSFORM_ON_ERROR = [
    pytest.param(None, False, id="default-is-fail-fast"),
    pytest.param("fail", False, id="fail"),
    pytest.param("skip", True, id="skip-drops-everything"),
    pytest.param("partial", True, id="partial-drops-one-field"),
    pytest.param("default", True, id="default-output-replaces"),
    pytest.param("nonsense", False, id="unknown-strategy-fails-safely"),
]


@pytest.mark.parametrize(("on_error", "optional"), TRANSFORM_ON_ERROR)
def test_transform_optionality_follows_on_error(on_error, optional):
    config = {"mappings": [{"output_field": "a", "type": "template", "template": "x"}]}
    if on_error is not None:
        config["on_error"] = on_error

    assert _effect_fields(_transform_effect(config)) == [("a", ("string",), optional)]


def test_transform_fallback_output_keys_are_optional():
    effect = _transform_effect(
        {
            "mappings": [{"output_field": "a", "type": "template", "template": "x"}],
            "on_error": "default",
            "default_output": {"a": "", "fallback": 1},
        }
    )

    assert _effect_fields(effect) == [("a", ("string",), True), ("fallback", ("integer",), True)]


def test_transform_fallback_of_a_different_type_makes_the_type_unknown():
    effect = _transform_effect(
        {
            "mappings": [{"output_field": "a", "type": "template", "template": "x"}],
            "on_error": "default",
            "default_output": {"a": 5},
        }
    )

    assert _effect_fields(effect) == [("a", None, True)]


def test_transform_default_output_is_ignored_under_fail_fast():
    effect = _transform_effect(
        {
            "mappings": [{"output_field": "a", "type": "template", "template": "x"}],
            "on_error": "fail",
            "default_output": {"fallback": 1},
        }
    )

    assert effect.names == ("a",)


def test_transform_state_contract_types_sit_on_top_and_a_conflict_is_noted():
    effect = _transform_effect(
        {
            "mappings": [{"output_field": "a", "type": "template", "template": "x"}],
            "output_schema": {"properties": {"a": {"type": "string"}}},
        },
        output_schema='{"type":"object","properties":{"a":{"type":"integer"}},"required":["a"]}',
    )

    assert _effect_fields(effect) == [("a", ("integer",), False)]
    assert _notes(effect) == [(NOTE_TYPE_CONFLICT, "a")]


def test_transform_state_contract_field_nobody_produces_is_optional_and_noted():
    effect = _transform_effect(
        {"mappings": [{"output_field": "a", "type": "template", "template": "x"}]},
        output_schema='{"type":"object","properties":{"b":{"type":"string"}},"required":["b"]}',
    )

    assert _effect_fields(effect) == [("a", ("string",), False), ("b", ("string",), True)]
    assert _notes(effect) == [(NOTE_DECLARED_FIELD_NOT_PRODUCED, "b")]


def test_transform_example_form_state_contract_does_not_override_types():
    effect = _transform_effect(
        {"mappings": [{"output_field": "a", "type": "template", "template": "x"}]},
        output_schema=EXAMPLE_CONTRACT,
    )

    assert _effect_fields(effect) == [("a", ("string",), False)]
    assert _notes(effect) == []


TRANSFORM_UNREADABLE = [
    pytest.param({"mappings": "not-a-list"}, id="mappings-not-a-list"),
    pytest.param("not-a-dict", id="config-not-a-dict"),
]


@pytest.mark.parametrize("config", TRANSFORM_UNREADABLE)
def test_transform_with_an_unreadable_config_is_an_unknown_source(config):
    effect = _transform_effect(SimpleNamespace(id="transform-1", custom_node_id="transform_node", config=config))

    assert effect.kind is NodeKind.TRANSFORM
    assert effect.known_source is False
    assert effect.fields == ()


def test_transform_without_mappings_is_a_known_empty_source():
    effect = _transform_effect({})

    assert effect.known_source is True
    assert effect.fields == ()


def test_transform_malformed_mapping_makes_the_rest_optional():
    effect = _transform_effect(
        {"mappings": [{"output_field": "a", "type": "template", "template": "x"}, {"type": "template"}]}
    )

    assert _effect_fields(effect) == [("a", ("string",), True)]


# --- the single transition function -------------------------------------------------------------

PRODUCED = (ProducedField("a", ("string",)), ProducedField("b", None))


def _apply(next_kwargs: dict, context: AbstractContext | None = None, **kwargs):
    effect = kwargs.pop("effect", None) or _known_effect(*PRODUCED)
    return apply_transition(
        context if context is not None else _context("old", "keep"),
        state_id="n1",
        effect=effect,
        next_state=WorkflowNextState(**next_kwargs),
        **kwargs,
    )


# (next flags, names available afterwards, removed name -> mechanism)
TRANSITIONS = [
    pytest.param({"state_id": "x"}, ["a", "b", "keep", "old"], {}, id="spread"),
    pytest.param({"state_id": "x", "output_key": "out"}, ["a", "b", "keep", "old", "out"], {}, id="output-key"),
    pytest.param({"state_id": "x", "store_in_context": False}, ["keep", "old"], {}, id="output-writing-off"),
    pytest.param(
        {"state_id": "x", "store_in_context": False, "output_key": "out"},
        ["keep", "old", "out"],
        {},
        id="output-writing-off-keeps-the-output-key",
    ),
    pytest.param(
        {"state_id": "x", "store_in_context": False, "clear_context_store": "keep_current"},
        ["keep", "old"],
        {},
        id="output-writing-off-ignores-keep-current",
    ),
    pytest.param(
        {"state_id": "x", "store_in_context": False, "reset_keys_in_context_store": ["old"]},
        ["keep", "old"],
        {},
        id="output-writing-off-ignores-selective-reset",
    ),
    pytest.param(
        {"state_id": "x", "store_in_context": False, "clear_context_store": True},
        [],
        {"keep": Mechanism.FULL_RESET, "old": Mechanism.FULL_RESET},
        id="output-writing-off-still-obeys-the-full-reset",
    ),
    pytest.param(
        {"state_id": "x", "clear_context_store": True},
        [],
        {"keep": Mechanism.FULL_RESET, "old": Mechanism.FULL_RESET},
        id="full-reset",
    ),
    pytest.param(
        {"state_id": "x", "clear_context_store": True, "output_key": "out"},
        ["out"],
        {"keep": Mechanism.FULL_RESET, "old": Mechanism.FULL_RESET},
        id="ac-40-full-reset-plus-output-key",
    ),
    pytest.param(
        {"state_id": "x", "clear_context_store": "keep_current"},
        ["a", "b"],
        {"keep": Mechanism.KEEP_CURRENT, "old": Mechanism.KEEP_CURRENT},
        id="keep-current",
    ),
    pytest.param(
        {"state_id": "x", "clear_context_store": "keep_current", "output_key": "out"},
        ["a", "b", "out"],
        {"keep": Mechanism.KEEP_CURRENT, "old": Mechanism.KEEP_CURRENT},
        id="keep-current-plus-output-key",
    ),
    pytest.param(
        {"state_id": "x", "clear_context_store": "keep_current", "reset_keys_in_context_store": ["a"]},
        ["b"],
        {"a": Mechanism.RESET_KEYS, "keep": Mechanism.KEEP_CURRENT, "old": Mechanism.KEEP_CURRENT},
        id="keep-current-plus-selective-reset",
    ),
    pytest.param(
        {"state_id": "x", "reset_keys_in_context_store": ["old"]},
        ["a", "b", "keep"],
        {"old": Mechanism.RESET_KEYS},
        id="selective-reset",
    ),
    pytest.param(
        {"state_id": "x", "reset_keys_in_context_store": ["a"]},
        ["b", "keep", "old"],
        {"a": Mechanism.RESET_KEYS},
        id="selective-reset-wins-over-the-nodes-own-write",
    ),
    pytest.param(
        {"state_id": "x", "reset_keys_in_context_store": ["out"], "output_key": "out"},
        ["a", "b", "keep", "old", "out"],
        {},
        id="output-key-is-written-after-the-reset-markers",
    ),
]


@pytest.mark.parametrize(("next_kwargs", "available", "removed"), TRANSITIONS)
def test_apply_transition(next_kwargs, available, removed):
    result = _apply(next_kwargs)

    assert _available(result.context) == available
    assert _removed(result.context) == removed


def test_the_node_is_recorded_as_the_producer_of_what_it_writes():
    result = _apply({"state_id": "x", "output_key": "out"})

    assert result.context.fields["a"].producers == ("n1",)
    assert result.context.fields["a"].types == ("string",)
    assert result.context.fields["out"].producers == ("n1",)
    assert result.context.fields["out"].types == ("string",)


def test_accumulating_transitions_give_list_typed_fields():
    result = _apply({"state_id": "x", "append_to_context": True, "output_key": "out"})

    assert result.context.fields["a"].types == ("array",)
    assert result.context.fields["b"].types == ("array",)
    assert result.context.fields["out"].types == ("array",)


def test_a_removal_names_the_node_and_the_mechanism():
    result = _apply({"state_id": "x", "reset_keys_in_context_store": ["old"]})

    assert result.context.fields["old"].removed_by == "n1"
    assert result.context.fields["old"].mechanism is Mechanism.RESET_KEYS


def test_a_field_that_is_reset_and_is_the_output_key_is_not_a_suppressed_write():
    result = _apply({"state_id": "x", "reset_keys_in_context_store": ["a"], "output_key": "a"})

    assert _available(result.context) == ["a", "b", "keep", "old"]
    assert result.suppressed == ()


def test_output_writing_off_reports_the_suppressed_writes():
    result = _apply({"state_id": "x", "store_in_context": False})

    assert [(item.name, item.mechanism) for item in result.suppressed] == [
        ("a", Mechanism.OUTPUT_WRITING_OFF),
        ("b", Mechanism.OUTPUT_WRITING_OFF),
    ]


def test_a_full_reset_suppresses_the_nodes_own_writes():
    result = _apply({"state_id": "x", "clear_context_store": True})

    assert [(item.name, item.mechanism) for item in result.suppressed] == [
        ("a", Mechanism.FULL_RESET),
        ("b", Mechanism.FULL_RESET),
    ]


def test_an_already_removed_field_keeps_its_first_mechanism():
    first = _apply({"state_id": "x", "reset_keys_in_context_store": ["old"]})
    second = apply_transition(
        first.context,
        state_id="n2",
        effect=_known_effect(),
        next_state=WorkflowNextState(state_id="x", clear_context_store=True),
    )

    assert second.context.fields["old"].removed_by == "n1"
    assert second.context.fields["old"].mechanism is Mechanism.RESET_KEYS


def test_an_unknown_source_marks_the_context_and_the_possible_overwriter():
    result = _apply({"state_id": "x"}, effect=node_effect(_state(output_schema=None)))

    assert result.context.unknown_contribution is True
    assert result.context.unknown_sources == ("n1",)
    assert result.context.fields["old"].overwritten_by == "n1"
    assert result.context.fields["old"].types_certain is False
    assert result.context.fields["old"].status is FieldStatus.AVAILABLE


def test_an_unknown_source_can_bring_a_removed_field_back():
    removed = _apply({"state_id": "x", "clear_context_store": True})
    result = apply_transition(
        removed.context, state_id="n2", effect=node_effect(_state(output_schema=None)), next_state=None
    )

    assert result.context.fields["old"].status is FieldStatus.POSSIBLY_PRESENT
    assert result.context.fields["old"].overwritten_by == "n2"


def test_a_full_reset_clears_the_unknown_contribution():
    result = _apply({"state_id": "x", "clear_context_store": True}, _context("old", unknown=True))

    assert result.context.unknown_contribution is False
    assert result.context.unknown_sources == ()


def test_keep_current_clears_the_unknown_contribution_of_a_known_source():
    result = _apply({"state_id": "x", "clear_context_store": "keep_current"}, _context("old", unknown=True))

    assert result.context.unknown_contribution is False


def test_a_node_that_writes_no_context_is_transparent():
    effect = node_effect(_state(assistant_id=None, custom_node_id="node-1"), custom_node=_custom(SUMMARIZE_NODE_KIND))
    result = _apply({"state_id": "x", "clear_context_store": True, "output_key": "out"}, effect=effect)

    assert _available(result.context) == ["keep", "old"]
    assert [(item.name, item.mechanism) for item in result.suppressed] == [("out", Mechanism.NO_CONTEXT_WRITE)]


def test_a_missing_next_section_is_tolerated():
    result = apply_transition(_context("old"), state_id="n1", effect=_known_effect(*PRODUCED), next_state=None)

    assert _available(result.context) == ["a", "b", "old"]
    assert result.iteration_context is None


def test_the_transition_reports_the_abstract_operations_it_performed():
    result = _apply({"state_id": "x"})

    assert result.operations >= len(result.context.fields)


# --- iteration branches (AC-9) ------------------------------------------------------------------


def test_a_first_level_iteration_branch_copies_the_whole_context_by_default():
    result = _apply({"state_id": "x", "iter_key": "items"})

    assert _available(result.iteration_context) == ["a", "b", "keep", "old"]
    assert result.iteration_context.scope == 1


def test_a_first_level_iteration_branch_is_filtered_by_the_whitelist():
    result = _apply({"state_id": "x", "iter_key": "items", "include_in_iterator_context": ["keep"]})

    assert _available(result.iteration_context) == ["keep"]
    assert _removed(result.iteration_context) == {
        "a": Mechanism.ITERATION_FILTER,
        "b": Mechanism.ITERATION_FILTER,
        "old": Mechanism.ITERATION_FILTER,
    }
    assert _available(result.context) == ["a", "b", "keep", "old"]


def test_a_whitelisted_branch_of_a_known_context_has_no_unknown_contribution():
    result = _apply(
        {"state_id": "x", "iter_key": "items", "include_in_iterator_context": ["keep"]},
        _context("old", "keep", unknown=True),
    )

    assert result.iteration_context.unknown_contribution is False
    assert result.context.unknown_contribution is True


def test_a_whitelist_naming_an_unmodelled_key_keeps_the_unknown_contribution():
    result = _apply(
        {"state_id": "x", "iter_key": "items", "include_in_iterator_context": ["mystery"]},
        _context("old", "keep", unknown=True),
    )

    assert result.iteration_context.unknown_contribution is True


def test_a_nested_iteration_branch_reuses_the_parent_context_unfiltered():
    result = _apply(
        {"state_id": "x", "iter_key": "items", "include_in_iterator_context": ["keep"]},
        _context("old", "keep", scope=1),
    )

    assert _available(result.iteration_context) == ["a", "b", "keep", "old"]
    assert result.iteration_context.scope == 2


def test_the_item_fields_become_context_from_the_first_node_of_the_iteration():
    result = _apply(
        {"state_id": "x"},
        _context("old", scope=1),
        iteration_item_fields=(ProducedField("item_field", ("string",)),),
        first_state_in_iteration=True,
    )

    assert _available(result.context) == ["a", "b", "item_field", "old"]
    assert result.context.fields["item_field"].scope == 1


def test_item_fields_are_not_context_when_the_node_is_not_the_first_in_the_iteration():
    result = _apply(
        {"state_id": "x"},
        _context("old", scope=1),
        iteration_item_fields=(ProducedField("item_field", ("string",)),),
    )

    assert "item_field" not in result.context.fields


def test_item_fields_are_not_context_when_output_writing_is_off():
    result = _apply(
        {"state_id": "x", "store_in_context": False},
        _context("old", scope=1),
        iteration_item_fields=(ProducedField("item_field", ("string",)),),
        first_state_in_iteration=True,
    )

    assert "item_field" not in result.context.fields


# --- the entry context (AC-5, AC-S4) ------------------------------------------------------------


def test_the_initial_context_seeds_the_platform_keys_as_optional_known_sources():
    context = initial_context()

    assert _available(context) == ["file_names", "propagated_headers"]
    assert context.fields["file_names"].optional is True
    assert context.fields["file_names"].producers == (INITIAL_INPUT_SOURCE,)


def test_an_undeclared_initial_input_is_an_unknown_contribution():
    assert initial_context().unknown_contribution is True
    assert initial_context(unknown_initial_input=False).unknown_contribution is False


# --- the model-fields guard ---------------------------------------------------------------------


def test_every_next_field_is_classified():
    assert set(WorkflowNextState.model_fields) == set(NEXT_FIELD_IMPACTS)


def test_every_state_field_is_classified():
    assert set(WorkflowState.model_fields) == set(STATE_FIELD_IMPACTS)


def test_the_context_affecting_next_fields_are_the_ones_the_transition_reads():
    affecting = {name for name, impact in NEXT_FIELD_IMPACTS.items() if impact is FieldImpact.CONTEXT}

    assert affecting == {
        "append_to_context",
        "clear_context_store",
        "include_in_iterator_context",
        "iter_key",
        "output_key",
        "override_task",
        "reset_keys_in_context_store",
        "store_in_context",
    }


def test_the_context_affecting_state_fields_are_the_ones_the_effects_read():
    affecting = {name for name, impact in STATE_FIELD_IMPACTS.items() if impact is FieldImpact.CONTEXT}

    assert affecting == {"finish_iteration", "next", "output_schema"}


# --- parity with the real reducer path ----------------------------------------------------------


class _ParityNode(BaseNode):
    def execute(self, state_schema, execution_context):  # pragma: no cover - never run
        return None

    def get_task(self, state_schema, *args, **kwargs):  # pragma: no cover - never run
        return "task"


def _real_store(state: WorkflowState, left: dict, output: object, raw_output=None, schema_extra: dict | None = None):
    """Run node finalization and the real LangGraph context reducer."""
    node = _ParityNode(
        callbacks=[],
        workflow_execution_service=Mock(),
        thought_queue=Mock(),
        node_name="n1",
        workflow_state=state,
    )
    processed_output = json.dumps(output)
    state_schema = {CONTEXT_STORE_VARIABLE: dict(left), MESSAGES_VARIABLE: [], **(schema_extra or {})}
    final_state = node.finalize_and_update_state(
        processed_output if raw_output is None else raw_output, processed_output, True, state_schema
    )
    return add_or_replace_context_store(dict(left), final_state[CONTEXT_STORE_VARIABLE])


PARITY_FLAGS = [
    pytest.param({"state_id": "x"}, id="spread"),
    pytest.param({"state_id": "x", "output_key": "out"}, id="output-key"),
    pytest.param({"state_id": "x", "store_in_context": False}, id="output-writing-off"),
    pytest.param({"state_id": "x", "store_in_context": False, "output_key": "out"}, id="off-plus-output-key"),
    pytest.param(
        {"state_id": "x", "store_in_context": False, "clear_context_store": "keep_current"},
        id="off-plus-keep-current",
    ),
    pytest.param(
        {"state_id": "x", "store_in_context": False, "reset_keys_in_context_store": ["old"]},
        id="off-plus-selective-reset",
    ),
    pytest.param({"state_id": "x", "store_in_context": False, "clear_context_store": True}, id="off-plus-full-reset"),
    pytest.param({"state_id": "x", "clear_context_store": True}, id="full-reset"),
    pytest.param({"state_id": "x", "clear_context_store": "keep_current"}, id="keep-current"),
    pytest.param(
        {"state_id": "x", "clear_context_store": "keep_current", "output_key": "out"},
        id="keep-current-plus-output-key",
    ),
    pytest.param(
        {"state_id": "x", "clear_context_store": "keep_current", "reset_keys_in_context_store": ["a"]},
        id="keep-current-plus-reset",
    ),
    pytest.param({"state_id": "x", "reset_keys_in_context_store": ["old"]}, id="selective-reset"),
    pytest.param({"state_id": "x", "reset_keys_in_context_store": ["a"]}, id="selective-reset-delete-wins"),
    pytest.param(
        {"state_id": "x", "reset_keys_in_context_store": ["out"], "output_key": "out"},
        id="output-key-beats-the-reset",
    ),
    pytest.param({"state_id": "x", "append_to_context": True}, id="append"),
    pytest.param({"state_id": "x", "append_to_context": True, "output_key": "out"}, id="append-plus-output-key"),
]


@pytest.mark.parametrize("next_kwargs", PARITY_FLAGS)
def test_the_simulated_field_names_match_the_real_reducer(next_kwargs):
    state = _state(
        next=next_kwargs,
        output_schema=json.dumps(
            {
                "type": "object",
                "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
                "required": ["a", "b"],
            }
        ),
    )
    effect = node_effect(state)

    real = _real_store(state, {"old": "kept", "keep": "kept"}, {"a": "produced", "b": 2})
    simulated = apply_transition(_context("old", "keep"), state_id="n1", effect=effect, next_state=state.next)

    assert sorted(real) == _available(simulated.context)


@pytest.mark.parametrize("store_in_context", [True, False])
def test_ac40_full_reset_plus_output_key_matches_the_real_reducer(store_in_context):
    state = _state(
        next={"state_id": "x", "clear_context_store": True, "output_key": "out", "store_in_context": store_in_context},
        output_schema='{"a":"produced"}',
    )
    real = _real_store(state, {"old": "kept"}, {"a": "produced"})

    simulated = apply_transition(_context("old"), state_id="n1", effect=node_effect(state), next_state=state.next)

    assert _available(simulated.context) == ["out"]
    assert real == {"out": '{"a": "produced"}'}
    assert sorted(real) == _available(simulated.context)


def test_accumulation_parity_gives_a_list_in_the_real_store():
    state = _state(next={"state_id": "x", "append_to_context": True, "output_key": "out"})

    real = _real_store(state, {"a": "first"}, {"a": "second"})

    assert real["a"] == ["first", "second"]
    assert isinstance(real["out"], list)


def test_result_unwrapping_parity_bypasses_the_processed_string():
    state = _state(next={"state_id": "x", "output_key": "out"})

    real = _real_store(state, {}, {"a": 1}, raw_output=SimpleNamespace(result=[1, 2]))

    assert real["out"] == [1, 2]
    assert node_effect(_state(assistant_id=None, tool_id="t")).output_value_certain is False


def test_first_state_in_iteration_task_keys_parity():
    state = _state(next={"state_id": "x"})

    real = _real_store(
        state, {}, {"a": 1}, schema_extra={FIRST_STATE_IN_ITERATION: True, TASK_KEY: {"item_field": "value"}}
    )

    assert sorted(real) == ["a", "item_field"]


def _real_transform_output(config: dict, source: dict):
    node = TransformNode(
        callbacks=[],
        workflow_execution_service=Mock(),
        thought_queue=Mock(),
        workflow_state=_state(assistant_id=None, custom_node_id="transform-1"),
        custom_node=_transform(config),
    )
    return node.execute({CONTEXT_STORE_VARIABLE: source, MESSAGES_VARIABLE: []}, {})


FAILING_MAPPINGS = [
    {"output_field": "good", "type": "extract", "source_path": "present"},
    {"output_field": "bad", "type": "script", "script": "__import__('os')"},
]

# (on_error, keys of the real output, expected optionality of every derived field)
TRANSFORM_PARITY = [
    pytest.param("skip", [], True, id="skip-returns-nothing"),
    pytest.param("partial", ["good"], True, id="partial-drops-the-failing-field"),
    pytest.param("default", ["fallback"], True, id="default-returns-the-fallback"),
]


@pytest.mark.parametrize(("on_error", "real_keys", "optional"), TRANSFORM_PARITY)
def test_transform_on_error_parity(on_error, real_keys, optional):
    config = {"mappings": FAILING_MAPPINGS, "on_error": on_error, "default_output": {"fallback": "used"}}

    assert sorted(_real_transform_output(config, {"present": "value"})) == real_keys
    assert all(field.optional is optional for field in _transform_effect(config).fields)


def test_transform_fail_fast_parity_raises_instead_of_dropping_fields():
    config = {"mappings": FAILING_MAPPINGS, "on_error": "fail"}

    with pytest.raises(TransformationError):
        _real_transform_output(config, {"present": "value"})

    assert all(field.optional is False for field in _transform_effect(config).fields)


def test_transform_mapping_output_fields_parity():
    config = {
        "mappings": [
            {"output_field": "number", "type": "extract", "source_path": "number"},
            {"output_field": "summary", "type": "template", "template": "PR #{{ number }}"},
            {"output_field": "flag", "type": "constant", "value": True},
        ]
    }

    real = _real_transform_output(config, {"number": 7})

    assert sorted(real) == sorted(_transform_effect(config).names)


# --- shared transform vocabulary (MR !4725 R10) -----------------------------------------------


def test_a_mapping_type_the_runtime_dispatches_is_never_malformed(monkeypatch):
    from codemie.workflows.contracts import effects as effects_module
    from codemie.workflows.transform_constants import MAPPING_TYPES

    monkeypatch.setattr(effects_module, "MAPPING_TYPES", MAPPING_TYPES | {"future_type"})

    effect = _transform_effect({"mappings": [{"output_field": "a", "type": "future_type"}]})

    assert _effect_fields(effect) == [("a", None, False)]


def test_an_unknown_mapping_type_is_malformed():
    effect = _transform_effect(
        {"mappings": [{"output_field": "a", "type": "nope"}, {"output_field": "b", "type": "constant", "value": 1}]}
    )

    assert _effect_fields(effect) == [("b", ("integer",), True)]


_COERCION_SAMPLES = {
    "string": "7",
    "integer": 7,
    "number": 7.5,
    "boolean": True,
    "array": [1],
    "object": {"a": 1},
}
_PYTHON_TYPE_NAMES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


@pytest.mark.parametrize("declared", ["string", "integer", "number", "boolean", "array", "object"])
@pytest.mark.parametrize("inferred", sorted(_COERCION_SAMPLES))
def test_own_schema_coercion_types_match_the_runtime_coercion(declared, inferred):
    node = TransformNode.__new__(TransformNode)
    try:
        coerced = node._coerce_field_type(_COERCION_SAMPLES[inferred], {"type": declared})
    except (TypeError, ValueError):
        pytest.skip("the runtime fails this coercion, so no successful return is typed")
    effect = _transform_effect(
        {
            "mappings": [{"output_field": "a", "type": "constant", "value": _COERCION_SAMPLES[inferred]}],
            "output_schema": {"properties": {"a": {"type": declared}}},
        }
    )

    assert _effect_fields(effect) == [("a", (_PYTHON_TYPE_NAMES[type(coerced)],), False)]
