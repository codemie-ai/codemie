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

"""Node effects: what one state contributes to the shared context (EPMCDME-16503, parent §4.1).

Pure, and a mirror of runtime rules this package may read but never edit. One function per node kind, selected from
`NODE_EFFECTS`; a kind with no entry is an unknown source, so a new node kind is one function and no change to the
propagator. An unknown source contributes nothing and can never cause a "not guaranteed" finding (AC-5). How an effect
is written into the context is `transitions.apply_transition`; the context itself is `abstract_context`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from codemie.workflows.context_rules import StateNodeType, state_node_type
from codemie.workflows.contracts.abstract_context import TYPE_ARRAY, TYPE_STRING
from codemie.workflows.contracts.derive import TRANSFORM_NODE_KIND
from codemie.workflows.contracts.models import ContractLocation, LocationSection
from codemie.workflows.contracts.reader import (
    ContractField,
    ContractForm,
    OutputContract,
    ProducerNote,
    _schema_fields,
    classify_output_contract,
)
from codemie.workflows.transform_constants import (
    CONFIG_KEY_DEFAULT_OUTPUT,
    CONFIG_KEY_MAPPINGS,
    CONFIG_KEY_ON_ERROR,
    CONFIG_KEY_OUTPUT_SCHEMA,
    ERROR_STRATEGY_DEFAULT,
    ERROR_STRATEGY_FAIL,
    ERROR_STRATEGY_PARTIAL,
    ERROR_STRATEGY_SKIP,
    MAPPING_KEY_ELSE_VALUE,
    MAPPING_KEY_OUTPUT_FIELD,
    MAPPING_KEY_THEN_VALUE,
    MAPPING_KEY_TYPE,
    MAPPING_KEY_VALUE,
    MAPPING_TYPE_ARRAY_MAP,
    MAPPING_TYPE_CONDITION,
    MAPPING_TYPE_CONSTANT,
    MAPPING_TYPE_EXTRACT,
    MAPPING_TYPE_TEMPLATE,
    MAPPING_TYPES,
    SCALAR_COERCIONS,
    SCHEMA_KEY_PROPERTIES,
    SCHEMA_KEY_REQUIRED,
    SCHEMA_KEY_TYPE,
)

if TYPE_CHECKING:
    from codemie.core.workflow_models.workflow_models import CustomWorkflowNode, WorkflowState

# Custom-node kinds whose class overrides the base write path; the kind is `CustomWorkflowNode.custom_node_id`, the
# module name under `codemie.workflows.nodes` (`workflow.py:639-649`).
SUMMARIZE_NODE_KIND = "summarize_conversation_node"
SUPERVISOR_NODE_KIND = "supervisor_node"

# Transform strategies that can return without some mapping fields; an unrecognised value re-raises, so it fails fast
# like `fail`.
TRANSFORM_DROPPING_STRATEGIES = frozenset({ERROR_STRATEGY_SKIP, ERROR_STRATEGY_DEFAULT, ERROR_STRATEGY_PARTIAL})

# Python classes of the JSON types, to read what `SCALAR_COERCIONS` keeps as is.
_JSON_TYPE_CLASSES: dict[str, type] = {
    "boolean": bool,
    "integer": int,
    "number": float,
    TYPE_STRING: str,
    TYPE_ARRAY: list,
    "object": dict,
    "null": type(None),
}

NOTE_TYPE_CONFLICT = "type_conflict"
NOTE_DECLARED_FIELD_NOT_PRODUCED = "declared_field_not_produced"
NOTE_OUTPUT_CONTRACT_IGNORED = "output_contract_ignored"

REASON_UNKNOWN_NODE_KIND = "unknown_node_kind"
REASON_TRANSFORM_CONFIG_UNREADABLE = "transform_config_unreadable"
REASON_TRANSFORM_MAPPINGS_UNREADABLE = "transform_mappings_unreadable"


class NodeKind(str, Enum):
    AGENT = "agent"
    TOOL = "tool"
    TRANSFORM = "transform"
    SUB_WORKFLOW = "sub_workflow"
    CUSTOM = "custom"
    SUMMARIZE = "summarize"
    SUPERVISOR = "supervisor"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProducedField:
    """One field a node writes. `types` is `None` when the contract states none; `optional` means the node can
    finish without writing it (a list output that may be empty, a transform strategy that drops fields)."""

    name: str
    types: tuple[str, ...] | None = None
    optional: bool = False
    types_certain: bool = True


@dataclass(frozen=True)
class EffectNote:
    """A producer-side finding of kind "contract-form"; `name` is the field it is about, or empty for the node."""

    code: str
    name: str = ""
    location: ContractLocation | None = None


@dataclass(frozen=True)
class NodeEffect:
    """What one state contributes. `writes_context` is `False` for a node whose class never returns a context update,
    so the whole transition is a no-op for it; `schema_form` answers AC-17's workflow-strict rule."""

    kind: NodeKind
    fields: tuple[ProducedField, ...] = ()
    known_source: bool = False
    writes_context: bool = True
    form: ContractForm = ContractForm.NONE
    schema_form: bool = False
    recovered: bool = False
    producer_note: ProducerNote | None = None
    output_value_types: tuple[str, ...] | None = (TYPE_STRING,)
    output_value_certain: bool = True
    notes: tuple[EffectNote, ...] = ()
    reason: str = ""

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.fields)


# --- node kinds and their effects ---------------------------------------------------------------


_STATE_NODE_KINDS = {
    StateNodeType.AGENT: NodeKind.AGENT,
    StateNodeType.TOOL: NodeKind.TOOL,
    StateNodeType.SUB_WORKFLOW: NodeKind.SUB_WORKFLOW,
}


def classify_node_kind(state: WorkflowState, custom_node: CustomWorkflowNode | None = None) -> NodeKind:
    """The node kind, from the executor's own `state_node_type`.

    A custom node whose `CustomWorkflowNode` the caller could not resolve is UNKNOWN, as is a state with none of the
    four type fields (only reachable through `model_construct`).
    """
    node_type = state_node_type(state)
    if node_type is StateNodeType.CUSTOM:
        return _custom_node_kind(getattr(custom_node, "custom_node_id", None))
    return _STATE_NODE_KINDS.get(node_type, NodeKind.UNKNOWN)


def _custom_node_kind(custom_node_id: object) -> NodeKind:
    if custom_node_id == TRANSFORM_NODE_KIND:
        return NodeKind.TRANSFORM
    if custom_node_id == SUMMARIZE_NODE_KIND:
        return NodeKind.SUMMARIZE
    if custom_node_id == SUPERVISOR_NODE_KIND:
        return NodeKind.SUPERVISOR
    if isinstance(custom_node_id, str) and custom_node_id.strip():
        return NodeKind.CUSTOM
    return NodeKind.UNKNOWN


def agent_effect(state: WorkflowState, custom_node: CustomWorkflowNode | None = None) -> NodeEffect:  # noqa: ARG001 - uniform effect-handler signature used for dispatch
    """An agent contributes its declared output contract; `TaskResult.result` is text, so the output key is text."""
    return _declared_contract_effect(NodeKind.AGENT, state)


def tool_effect(state: WorkflowState, custom_node: CustomWorkflowNode | None = None) -> NodeEffect:  # noqa: ARG001 - uniform effect-handler signature used for dispatch
    """A tool contributes its declared output contract; `_add_output_key` unwraps `raw_output.result`
    (`base_node.py:595`), whose type the save cannot know, so the output key's type stays UNVERIFIED."""
    return _declared_contract_effect(NodeKind.TOOL, state, output_value_certain=False)


def sub_workflow_effect(state: WorkflowState, custom_node: CustomWorkflowNode | None = None) -> NodeEffect:  # noqa: ARG001 - uniform effect-handler signature used for dispatch
    """A sub-workflow is a known source only with a declared output contract (AC-10): its output is the child's last
    state output as text (`sub_workflow_node.py:166-169`), which this workflow cannot analyse."""
    return _declared_contract_effect(NodeKind.SUB_WORKFLOW, state)


def custom_effect(state: WorkflowState, custom_node: CustomWorkflowNode | None = None) -> NodeEffect:  # noqa: ARG001 - uniform effect-handler signature used for dispatch
    """A custom node with no entry of its own: the base write path applies, so the declared contract is all there is.
    Its class may post-process the output into any shape, so the output key's type stays UNVERIFIED."""
    return _declared_contract_effect(NodeKind.CUSTOM, state, output_value_certain=False)


def summarize_effect(state: WorkflowState, custom_node: CustomWorkflowNode | None = None) -> NodeEffect:  # noqa: ARG001 - uniform effect-handler signature used for dispatch
    """`SummarizeConversationCommandNode.finalize_and_update_state` returns a `Command` that updates the message
    history only (`summarize_conversation_node.py:382-406`), so the context store is untouched and every `next` flag
    of this state is inert. It is the class `init_custom_node` picks for the module, being the first in alphabetical
    order (`workflow.py:651-666`)."""
    return _fixed_output_effect(NodeKind.SUMMARIZE, state, writes_context=False)


def supervisor_effect(state: WorkflowState, custom_node: CustomWorkflowNode | None = None) -> NodeEffect:  # noqa: ARG001 - uniform effect-handler signature used for dispatch
    """`SupervisorNode` writes through the base path but its processed output is a fixed sentence
    (`supervisor_node.py:134-135`), which never parses into a dict, so it contributes no field."""
    return _fixed_output_effect(NodeKind.SUPERVISOR, state, writes_context=True)


def unknown_kind_effect(state: WorkflowState, custom_node: CustomWorkflowNode | None = None) -> NodeEffect:  # noqa: ARG001 - uniform effect-handler signature used for dispatch
    """A kind with no entry in `NODE_EFFECTS`: an unknown source, so nothing downstream can become an ERROR."""
    return NodeEffect(
        kind=NodeKind.UNKNOWN,
        producer_note=ProducerNote.UNKNOWN_SOURCE,
        output_value_certain=False,
        reason=REASON_UNKNOWN_NODE_KIND,
    )


def transform_effect(state: WorkflowState, custom_node: CustomWorkflowNode | None = None) -> NodeEffect:
    """AC-44: a transform is a known source with its mapping output fields even without a schema.

    Types come from its own output schema (`transform_node.py:573-596`), else from the mapping type, with a
    schema-form state output contract on top; a disagreement is a conflict note. Fields are guaranteed under
    fail-fast and optional under a strategy that can drop them, and `default_output` keys are added as optional.
    """
    config = getattr(custom_node, "config", None)
    if not isinstance(config, dict):
        return _unknown_transform(REASON_TRANSFORM_CONFIG_UNREADABLE)
    mappings = config.get(CONFIG_KEY_MAPPINGS, [])
    if not isinstance(mappings, list):
        return _unknown_transform(REASON_TRANSFORM_MAPPINGS_UNREADABLE)

    on_error = config.get(CONFIG_KEY_ON_ERROR, ERROR_STRATEGY_FAIL)
    produced, malformed = _mapping_outputs(mappings)
    optional = on_error in TRANSFORM_DROPPING_STRATEGIES or malformed
    rows = {name: _Row(types=types, optional=optional) for name, types in produced.items()}

    own_fields = _own_schema_fields(config)
    notes = _apply_declared_fields(
        rows,
        own_fields,
        state_level=False,
        location=ContractLocation(
            section=LocationSection.CUSTOM_NODES, owner_id=custom_node.id, segments=("config", "output_schema")
        ),
    )
    if on_error == ERROR_STRATEGY_DEFAULT:
        # The fallback is returned directly after a failure; its values do not pass through the own schema.
        _apply_default_output(rows, config.get(CONFIG_KEY_DEFAULT_OUTPUT))
    state_contract = classify_output_contract(getattr(state, "output_schema", None))
    if state_contract.form is ContractForm.SCHEMA:
        notes += _apply_declared_fields(
            rows,
            state_contract.fields,
            state_level=True,
            location=ContractLocation.state(state.id, "output_schema"),
        )

    return NodeEffect(
        kind=NodeKind.TRANSFORM,
        fields=tuple(
            ProducedField(name, row.types, optional=row.optional, types_certain=row.types is not None)
            for name, row in rows.items()
        ),
        known_source=True,
        form=state_contract.form,
        schema_form=bool(own_fields) or state_contract.form is ContractForm.SCHEMA,
        notes=notes,
    )


NODE_EFFECTS: dict[NodeKind, Callable[[Any, Any], NodeEffect]] = {
    NodeKind.AGENT: agent_effect,
    NodeKind.TOOL: tool_effect,
    NodeKind.TRANSFORM: transform_effect,
    NodeKind.SUB_WORKFLOW: sub_workflow_effect,
    NodeKind.CUSTOM: custom_effect,
    NodeKind.SUMMARIZE: summarize_effect,
    NodeKind.SUPERVISOR: supervisor_effect,
}


def node_effect(state: WorkflowState, *, custom_node: CustomWorkflowNode | None = None) -> NodeEffect:
    """The effect of one state, from the dispatch table; a kind with no entry is an unknown source."""
    handler = NODE_EFFECTS.get(classify_node_kind(state, custom_node), unknown_kind_effect)
    return handler(state, custom_node)


def _declared_contract_effect(kind: NodeKind, state: WorkflowState, *, output_value_certain: bool = True) -> NodeEffect:
    contract = classify_output_contract(getattr(state, "output_schema", None))
    return NodeEffect(
        kind=kind,
        fields=_contract_fields(contract),
        known_source=contract.known_source,
        form=contract.form,
        schema_form=contract.form is ContractForm.SCHEMA,
        recovered=contract.recovered,
        producer_note=contract.producer_note,
        output_value_certain=output_value_certain,
        reason=contract.reason,
    )


def _contract_fields(contract: OutputContract) -> tuple[ProducedField, ...]:
    """A list contract's fields are optional: an empty array merges into nothing (`base_node.py:606-619`)."""
    optional_form = contract.form is ContractForm.LIST
    return tuple(
        ProducedField(
            entry.name,
            entry.types,
            optional=optional_form or entry.required is False,
            types_certain=entry.types is not None,
        )
        for entry in contract.fields
    )


def _fixed_output_effect(kind: NodeKind, state: WorkflowState, *, writes_context: bool) -> NodeEffect:
    """A node class whose output shape is fixed by the class: a known source that contributes no field, so a declared
    output contract on it is only reported, never believed."""
    notes = (EffectNote(NOTE_OUTPUT_CONTRACT_IGNORED),) if getattr(state, "output_schema", None) else ()
    return NodeEffect(kind=kind, known_source=True, writes_context=writes_context, notes=notes)


# --- AC-44 helpers ------------------------------------------------------------------------------


@dataclass
class _Row:
    """One field under construction; mutable so the schemas can be layered over the mapping inference."""

    types: tuple[str, ...] | None = None
    optional: bool = False


def _unknown_transform(reason: str) -> NodeEffect:
    return NodeEffect(kind=NodeKind.TRANSFORM, producer_note=ProducerNote.UNKNOWN_SOURCE, reason=reason)


def _mapping_outputs(mappings: list[object]) -> tuple[dict[str, tuple[str, ...] | None], bool]:
    """The output field of each mapping the runtime can apply, plus whether any mapping is malformed.

    A malformed or unknown-type mapping raises at runtime (`transform_node.py:337-363`), so the node either fails
    outright or drops fields; its name is never claimed and every other field becomes optional.
    """
    produced: dict[str, tuple[str, ...] | None] = {}
    malformed = False
    for mapping in mappings:
        if not isinstance(mapping, dict):
            malformed = True
            continue
        name = mapping.get(MAPPING_KEY_OUTPUT_FIELD)
        if not isinstance(name, str) or not name:
            malformed = True
            continue
        types, applicable = _mapping_types(mapping)
        if not applicable:
            malformed = True
            continue
        produced[name] = types
    return produced, malformed


def _mapping_types(mapping: dict[str, object]) -> tuple[tuple[str, ...] | None, bool]:
    """The type a mapping always produces, and whether the runtime can apply it at all."""
    mapping_type = mapping.get(MAPPING_KEY_TYPE, MAPPING_TYPE_EXTRACT)
    if mapping_type == MAPPING_TYPE_CONSTANT:
        return _json_types(mapping.get(MAPPING_KEY_VALUE)), True
    if mapping_type == MAPPING_TYPE_TEMPLATE:
        return (TYPE_STRING,), True
    if mapping_type == MAPPING_TYPE_ARRAY_MAP:
        return (TYPE_ARRAY,), True
    if mapping_type == MAPPING_TYPE_CONDITION:
        then_types = _json_types(mapping.get(MAPPING_KEY_THEN_VALUE))
        else_types = _json_types(mapping.get(MAPPING_KEY_ELSE_VALUE))
        return (then_types if then_types == else_types else None), True
    # Any other type the runtime dispatches produces a value whose type the save cannot know.
    return None, mapping_type in MAPPING_TYPES


def _json_types(value: object) -> tuple[str, ...] | None:  # noqa: PLR0911 - flat decision table; early returns read clearer than a dispatch table
    if isinstance(value, bool):
        return ("boolean",)
    if isinstance(value, int):
        return ("integer",)
    if isinstance(value, float):
        return ("number",)
    if isinstance(value, str):
        return (TYPE_STRING,)
    if isinstance(value, list):
        return (TYPE_ARRAY,)
    if isinstance(value, dict):
        return ("object",)
    return None


def _apply_default_output(rows: dict[str, _Row], default_output: object) -> None:
    """With `on_error: default` the whole output can be replaced by `default_output` (`transform_node.py:213-216`),
    so its keys are possible values of a produced field and optional fields of their own."""
    if not isinstance(default_output, dict):
        return
    for raw_name, value in default_output.items():
        name = str(raw_name)
        types = _json_types(value)
        row = rows.get(name)
        if row is None:
            rows[name] = _Row(types=types, optional=True)
        elif row.types != types:
            row.types = None


def _own_schema_fields(config: dict[str, object]) -> tuple[ContractField, ...]:
    """`_validate_output` reads `properties` / `required` directly and needs no root `type`
    (`transform_node.py:586-596`), so the declared types are read the same way."""
    schema = config.get(CONFIG_KEY_OUTPUT_SCHEMA)
    if not isinstance(schema, dict):
        return ()
    properties = schema.get(SCHEMA_KEY_PROPERTIES)
    if not isinstance(properties, dict):
        return ()
    fields = _schema_fields(properties, schema.get(SCHEMA_KEY_REQUIRED))
    # The runtime compares the raw type to a scalar string. Even ["string"]
    # bypasses coercion; normalising it to ("string",) must not invent a check.
    return tuple(
        ContractField(
            field.name,
            None if isinstance(raw, dict) and isinstance(raw.get(SCHEMA_KEY_TYPE), list) else field.types,
            field.required,
        )
        for field, raw in zip(fields, properties.values(), strict=True)
    )


def _apply_declared_fields(
    rows: dict[str, _Row],
    declared: tuple[ContractField, ...],
    *,
    state_level: bool,
    location: ContractLocation,
) -> tuple[EffectNote, ...]:
    """Layer declared types over the mapping inference.

    `state_level` marks the state's own `output_schema`, where a disagreement is the AC-44 WARNING and a field
    declared optional is believed. The node's own schema is not state level: it applies only the supported scalar
    coercions, and its `required` list is only checked (`transform_node.py:598-610`), so a mapping still writes the
    field unconditionally on success.
    """
    notes: list[EffectNote] = []
    for entry in declared:
        row = rows.get(entry.name)
        if row is None:
            rows[entry.name] = _Row(types=entry.types, optional=True)
            notes.append(
                EffectNote(
                    NOTE_DECLARED_FIELD_NOT_PRODUCED,
                    entry.name,
                    location if state_level else location.child("properties", entry.name),
                )
            )
            continue
        conflict = _layer_declared_field(row, entry, state_level=state_level, location=location)
        if conflict is not None:
            notes.append(conflict)
    return tuple(notes)


def _layer_declared_field(
    row: _Row, entry: ContractField, *, state_level: bool, location: ContractLocation
) -> EffectNote | None:
    """Layer one declared field over a produced row, returning the AC-44 type conflict note if there is one."""
    if state_level:
        row.optional = row.optional or entry.required is False
    if entry.types is None:
        return None
    conflict = None
    if state_level and row.types is not None and row.types != entry.types:
        conflict = EffectNote(NOTE_TYPE_CONFLICT, entry.name, location)
    row.types = entry.types if state_level else _coerced_types(row.types, entry.types)
    return conflict


def _coerced_types(inferred: tuple[str, ...] | None, declared: tuple[str, ...]) -> tuple[str, ...] | None:
    """Types guaranteed by `TransformNode._coerce_field_type` on a successful return, read from `SCALAR_COERCIONS`.

    A declared type with no coercion (array, object, null, a union) is not a runtime check. A value of a class the
    coercion keeps is returned as is, so Python's bool-is-int lets a boolean through an integer or number coercion.
    """
    coercion = SCALAR_COERCIONS.get(declared[0]) if len(declared) == 1 else None
    if coercion is None:
        return inferred
    kept_classes = coercion[0]
    sources = inferred if inferred is not None else tuple(_JSON_TYPE_CLASSES)
    results = {
        name if issubclass(_JSON_TYPE_CLASSES.get(name, object), kept_classes) else declared[0] for name in sources
    }
    return (results.pop(),) if len(results) == 1 else None
