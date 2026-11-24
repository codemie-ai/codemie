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

"""The single transition function (EPMCDME-16503, parent §4.1).

`apply_transition` turns the runtime's own `context_store` update into an abstract one: it calls the functions
`BaseNode.finalize_and_update_state` and the executor call (`workflows/context_rules.py`) and reads what they return
the way the LangGraph reducer (`workflows/models.py`) would merge it: the output spread, the explicit output key,
output writing off, the three resets with delete-wins, accumulation and the iteration filter. What stays here is only
why a field is gone, for the finding. `NEXT_FIELD_IMPACTS` / `STATE_FIELD_IMPACTS` classify every model field so a new
one fails the build until it is classified.

Runtime order, for one node (`build_context_update`, then the output key):

1. a full reset (`clear_context_store: true`) replaces the whole update with `None`, so the node's own output is never
   collected; the reducer then returns `{}`;
2. otherwise, with `store_in_context: false` the update is `{}`, so neither keep-current nor the selective reset is
   applied - the one place where the runtime diverges from parent §4.1 as written;
3. otherwise the output (and, on the first node of an iteration, the item's fields) is collected, wrapped for
   accumulation, marked keep-new-only, and then the reset keys are turned into delete markers, so a delete wins over
   the node's own write;
4. `_add_output_key` writes the output key into that same update **last**, so it survives a full reset (AC-40, as
   EPMCDME-16499 corrects it) and overwrites its own delete marker.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import Enum
from typing import cast

from codemie.workflows.constants import (
    CLEAR_CONTEXT_STORE_KEEP_CURRENT,
    CONTEXT_STORE_APPEND_MARKER,
    CONTEXT_STORE_DELETE_MARKER,
    CONTEXT_STORE_KEEP_NEW_ONLY_FLAG,
)
from codemie.workflows.context_rules import (
    build_context_update,
    iteration_branch_context,
    iteration_filter_keys,
    output_key_context_value,
)
from codemie.workflows.contracts.abstract_context import (
    TYPE_ARRAY,
    AbstractContext,
    FieldState,
    FieldStatus,
    Mechanism,
    SuppressedWrite,
    TransitionResult,
)
from codemie.workflows.contracts.effects import NodeEffect, ProducedField
from codemie.workflows.contracts.simulate_model import text_of

# Stands for the output key's value in the update, which is the node's raw result.
_OUTPUT_KEY_VALUE = object()


class FieldImpact(str, Enum):
    """What a `WorkflowState` / `WorkflowNextState` field changes, so a new field cannot pass unclassified."""

    CONTEXT = "context"
    KIND = "kind"
    ROUTING = "routing"
    HISTORY = "history"
    REQUIREMENT = "requirement"
    OTHER = "other"


NEXT_FIELD_IMPACTS: dict[str, FieldImpact] = {
    "append_to_context": FieldImpact.CONTEXT,
    "clear_context_store": FieldImpact.CONTEXT,
    "clear_prior_messages": FieldImpact.HISTORY,
    "condition": FieldImpact.ROUTING,
    "include_in_iterator_context": FieldImpact.CONTEXT,
    "include_in_llm_history": FieldImpact.HISTORY,
    "iter_key": FieldImpact.CONTEXT,
    "keep_history": FieldImpact.HISTORY,
    "output_key": FieldImpact.CONTEXT,
    # Decides what the iteration item carries to the next node of the chain (`base_node.py:576`).
    "override_task": FieldImpact.CONTEXT,
    "reset_keys_in_context_store": FieldImpact.CONTEXT,
    "state_id": FieldImpact.ROUTING,
    "state_ids": FieldImpact.ROUTING,
    "store_in_context": FieldImpact.CONTEXT,
    "switch": FieldImpact.ROUTING,
}

STATE_FIELD_IMPACTS: dict[str, FieldImpact] = {
    "assistant_id": FieldImpact.KIND,
    "custom_node_id": FieldImpact.KIND,
    "finish_iteration": FieldImpact.CONTEXT,
    "id": FieldImpact.OTHER,
    "input_schema": FieldImpact.REQUIREMENT,
    "interrupt_before": FieldImpact.OTHER,
    "next": FieldImpact.CONTEXT,
    "output_schema": FieldImpact.CONTEXT,
    "resolve_dynamic_values_in_prompt": FieldImpact.REQUIREMENT,
    "result_as_human_message": FieldImpact.HISTORY,
    "retry_policy": FieldImpact.OTHER,
    "save_validation": FieldImpact.OTHER,
    "task": FieldImpact.REQUIREMENT,
    "tool_args": FieldImpact.REQUIREMENT,
    "tool_id": FieldImpact.KIND,
    "workflow_id": FieldImpact.KIND,
}


@dataclass(frozen=True)
class _TransitionFlags:
    """The `next` flags the transition reads, with every malformed value falling back to the runtime's own default."""

    store_in_context: bool = True
    full_reset: bool = False
    keep_current: bool = False
    reset_keys: tuple[str, ...] = ()
    output_key: str = ""
    append: bool = False
    iter_key: str = ""
    include_keys: object = None

    # The `WorkflowNextState` names the shared rules read.
    @property
    def clear_context_store(self) -> bool | str:
        if self.full_reset:
            return True
        return CLEAR_CONTEXT_STORE_KEEP_CURRENT if self.keep_current else False

    @property
    def append_to_context(self) -> bool:
        return self.append

    @property
    def reset_keys_in_context_store(self) -> tuple[str, ...]:
        return self.reset_keys

    @classmethod
    def read(cls, next_state: object) -> _TransitionFlags:
        if next_state is None:
            return cls()
        clear = getattr(next_state, "clear_context_store", False)
        reset = getattr(next_state, "reset_keys_in_context_store", None)
        return cls(
            store_in_context=bool(getattr(next_state, "store_in_context", True)),
            full_reset=isinstance(clear, bool) and clear,
            keep_current=clear == CLEAR_CONTEXT_STORE_KEEP_CURRENT,
            reset_keys=tuple(str(key) for key in reset) if isinstance(reset, list) else (),
            output_key=text_of(getattr(next_state, "output_key", None)),
            append=bool(getattr(next_state, "append_to_context", False)),
            iter_key=text_of(getattr(next_state, "iter_key", None)),
            include_keys=getattr(next_state, "include_in_iterator_context", None),
        )


def apply_transition(  # noqa: PLR0913 - keyword-only builder/internal helper; a parameter object would add indirection without clarity
    context: AbstractContext,
    *,
    state_id: str,
    effect: NodeEffect,
    next_state: object,
    iteration_item_fields: tuple[ProducedField, ...] = (),
    first_state_in_iteration: bool = False,
) -> TransitionResult:
    """Apply one node's writes to the abstract context and return the contexts its edges carry.

    `next_state` is the state's `next` section, read through `getattr` so a malformed or `model_construct`-ed value
    cannot raise. `iteration_item_fields` are the fields of the iteration item; the runtime adds them to the context
    on the first node of an iteration only (`base_node.py:633-639`).
    """
    flags = _TransitionFlags.read(next_state)
    if not effect.writes_context:
        return _result(context, flags, _no_context_write_suppressed(flags, effect), state_id, 0)

    written = (*iteration_item_fields, *effect.fields) if first_state_in_iteration else effect.fields
    own = {produced.name: produced for produced in written}
    collected: list[bool] = []

    def collect() -> dict[str, ProducedField]:
        collected.append(True)
        return own

    update = build_context_update(flags, collect)
    if update is not None and flags.output_key:
        update[flags.output_key] = output_key_context_value(flags, _OUTPUT_KEY_VALUE)

    operations = len(context.fields)
    # The reducer discards the prior store; it is a full reset when the node's own values were not kept either.
    discarded = update is None or bool(update.get(CONTEXT_STORE_KEEP_NEW_ONLY_FLAG))
    fields, unknown, unknown_sources = _prior_fields(
        context, effect, state_id, discarded=discarded, collected=bool(collected)
    )
    suppressed = _suppressed_writes(flags, effect, update, own, discarded=discarded, collected=bool(collected))
    if collected:
        operations += len(written) + sum(1 for name in flags.reset_keys if name != flags.output_key)

    for name, value in (update or {}).items():
        if name != CONTEXT_STORE_KEEP_NEW_ONLY_FLAG:
            fields[name] = _merged_entry(fields.get(name), name, value, own, effect, state_id, context.scope, flags)
    if flags.output_key:
        operations += 1

    updated = AbstractContext(
        fields=fields,
        unknown_contribution=unknown,
        unknown_sources=unknown_sources,
        scope=context.scope,
        suppressions=context.suppressions,
    )
    return _result(updated, flags, tuple(suppressed), state_id, operations)


def _no_context_write_suppressed(flags: _TransitionFlags, effect: NodeEffect) -> tuple[SuppressedWrite, ...]:
    names = ((flags.output_key,) if flags.output_key else ()) + effect.names
    return tuple(SuppressedWrite(name, Mechanism.NO_CONTEXT_WRITE) for name in names)


def _prior_fields(
    context: AbstractContext, effect: NodeEffect, state_id: str, *, discarded: bool, collected: bool
) -> tuple[dict[str, FieldState], bool, tuple[str, ...]]:
    """The context before the update is merged: a discarded store is removed, then an unknown source whose values are
    kept may overwrite whatever is left."""
    fields = dict(context.fields)
    unknown = context.unknown_contribution
    unknown_sources = context.unknown_sources
    if discarded:
        mechanism = Mechanism.KEEP_CURRENT if collected else Mechanism.FULL_RESET
        fields = {name: _removed(state, state_id, mechanism) for name, state in fields.items()}
        unknown, unknown_sources = False, ()
    if collected and not effect.known_source:
        fields = {name: _overwritten(state, state_id) for name, state in fields.items()}
        unknown = True
        unknown_sources = (*unknown_sources, state_id)
    return fields, unknown, unknown_sources


def _suppressed_writes(  # noqa: PLR0913 - keyword-only builder/internal helper; a parameter object would add indirection without clarity
    flags: _TransitionFlags,
    effect: NodeEffect,
    update: dict[str, object] | None,
    own: dict[str, ProducedField],
    *,
    discarded: bool,
    collected: bool,
) -> list[SuppressedWrite]:
    """The declared writes the update does not perform: all of them when the node's values are not kept, otherwise the
    ones a reset key deletes."""
    if not collected:
        mechanism = Mechanism.FULL_RESET if discarded else Mechanism.OUTPUT_WRITING_OFF
        return [SuppressedWrite(name, mechanism) for name in effect.names]
    return [
        SuppressedWrite(name, Mechanism.RESET_KEYS)
        for name in flags.reset_keys
        if name in own and update is not None and update.get(name) == CONTEXT_STORE_DELETE_MARKER
    ]


def _merged_entry(  # noqa: PLR0913 - keyword-only builder/internal helper; a parameter object would add indirection without clarity
    current: FieldState | None,
    name: str,
    value: object,
    own: dict[str, ProducedField],
    effect: NodeEffect,
    state_id: str,
    scope: int,
    flags: _TransitionFlags,
) -> FieldState:
    """The field state after the reducer merges one entry of the update."""
    accumulate = isinstance(value, dict) and CONTEXT_STORE_APPEND_MARKER in value
    item = value[CONTEXT_STORE_APPEND_MARKER][0] if accumulate else value
    if item is _OUTPUT_KEY_VALUE:
        return _output_key_state(effect, state_id, scope, accumulate=accumulate)
    if value == CONTEXT_STORE_DELETE_MARKER:
        # A node's own write comes before the marker that deletes it, so the removal is this node's.
        base = _written(own[name], state_id, scope, accumulate=flags.append) if name in own else current
        return _removed(base, state_id, Mechanism.RESET_KEYS)
    return _written(cast(ProducedField, item), state_id, scope, accumulate=accumulate)


def _output_key_state(effect: NodeEffect, state_id: str, scope: int, *, accumulate: bool) -> FieldState:
    return FieldState(
        status=FieldStatus.AVAILABLE,
        producers=(state_id,),
        scope=scope,
        types=(TYPE_ARRAY,) if accumulate else effect.output_value_types,
        types_certain=True if accumulate else effect.output_value_certain,
    )


def _result(
    context: AbstractContext,
    flags: _TransitionFlags,
    suppressed: tuple[SuppressedWrite, ...],
    state_id: str,
    operations: int,
) -> TransitionResult:
    if suppressed:
        known = dict(context.suppressions)
        for item in suppressed:
            known.setdefault(item.name, (state_id, item.mechanism.value))
        context = dataclasses.replace(context, suppressions=known)
    iteration_context = None
    if flags.iter_key:
        iteration_context = _branch_context(context, flags.include_keys, state_id)
        operations += len(iteration_context.fields)
    return TransitionResult(
        context=context, iteration_context=iteration_context, suppressed=suppressed, operations=operations
    )


def _branch_context(context: AbstractContext, include_keys: object, state_id: str) -> AbstractContext:
    """The context an iteration branch sees (`iteration_branch_context`): a nested branch reuses the parent store
    unfiltered, a first-level one copies it, filtered by `include_in_iterator_context` unless it is `["*"]`."""
    allowed = iteration_filter_keys(include_keys, nested=context.scope > 0) if isinstance(include_keys, list) else None
    if allowed is None:
        return cast(AbstractContext, dataclasses.replace(context, scope=context.scope + 1))
    kept = iteration_branch_context(context.fields, allowed, nested=False)
    fields = {name: state if name in kept else _hidden(state, state_id) for name, state in context.fields.items()}
    # The whitelist is explicit, so the branch is fully known unless it names a key this context does not model.
    unknown = context.unknown_contribution and any(key not in context.fields for key in allowed)
    return AbstractContext(
        fields=fields,
        unknown_contribution=unknown,
        unknown_sources=context.unknown_sources if unknown else (),
        scope=context.scope + 1,
        suppressions=context.suppressions,
    )


def _written(produced: ProducedField, state_id: str, scope: int, *, accumulate: bool) -> FieldState:
    """An accumulating transition wraps every value so the reducer appends it, so the field is always a list."""
    types = (TYPE_ARRAY,) if accumulate else produced.types
    return FieldState(
        status=FieldStatus.AVAILABLE,
        producers=(state_id,),
        scope=scope,
        types=types,
        types_certain=True if accumulate else (produced.types_certain and produced.types is not None),
        optional=produced.optional,
    )


def _hidden(state: FieldState, state_id: str) -> FieldState:
    """A field the iteration filter keeps out of the branch; the branch never writes it to the main context."""
    return cast(
        FieldState,
        dataclasses.replace(_removed(state, state_id, Mechanism.ITERATION_FILTER), hidden=state, hidden_by=state_id),
    )


def _removed(state: FieldState | None, state_id: str, mechanism: Mechanism) -> FieldState:
    """Record a removal, keeping the first one: the earliest mechanism is the one that explains the absence.

    A removal inside the branch also deletes a hidden field from the main context, so it is not restored later.
    """
    if state is not None and state.status is FieldStatus.REMOVED:
        return state.unhidden()
    base = state.unhidden() if state is not None else FieldState()
    return cast(
        FieldState,
        dataclasses.replace(
            base, status=FieldStatus.REMOVED, removed_by=state_id, mechanism=mechanism, types_certain=False
        ),
    )


def _overwritten(state: FieldState, state_id: str) -> FieldState:
    """An unknown source may write anything: a present field keeps its presence but loses type certainty, and a
    removed one becomes possibly present again, so nothing downstream can be an ERROR (AC-5). The unknown write reaches
    the main context too, so a hidden state is overwritten as well."""
    if state.hidden is not None:
        return cast(
            FieldState,
            dataclasses.replace(
                _overwritten(state.unhidden(), state_id),
                hidden=_overwritten(state.hidden, state_id),
                hidden_by=state.hidden_by,
            ),
        )
    if state.status is FieldStatus.AVAILABLE:
        return cast(FieldState, dataclasses.replace(state, overwritten_by=state_id, types_certain=False))
    return cast(
        FieldState,
        dataclasses.replace(
            state,
            status=FieldStatus.POSSIBLY_PRESENT,
            overwritten_by=state_id,
            producers=(*state.producers, state_id),
            types=None,
            types_certain=False,
        ),
    )
