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

"""Outcomes and records of the availability simulator (EPMCDME-16503, parent §4.1).

`requirement_outcome` decides the raw outcome of one context requirement against the context that reaches the state
(AC-2..AC-7, AC-13, AC-15); it reads no configuration and has no side effect. `Recorder` collects the findings of a
pass in order and emits the records that are not about a context requirement.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from codemie.workflows.contracts.abstract_context import AbstractContext, FieldState, FieldStatus, Mechanism
from codemie.workflows.contracts.derive import ORIGIN_TOOL_BINDING_OVERRIDE, UNVERIFIABLE_ORIGINS, OwnOutputCheck
from codemie.workflows.contracts.effects import NodeEffect
from codemie.workflows.contracts.models import (
    ContractLocation,
    ContractRecord,
    ContractRequirement,
    Outcome,
    Provenance,
    RecordKind,
)
from codemie.workflows.contracts.reader import ProducerNote
from codemie.workflows.contracts.simulate_context import CycleEffect, MergeNote
from codemie.workflows.contracts.simulate_model import (
    REASON_OK,
    REASON_NOT_GUARANTEED,
    REASON_REMOVED,
    REASON_ITERATION_ITEM_MAY_SUPPLY,
    REASON_WRITE_SUPPRESSED,
    REASON_POSSIBLY_REMOVED,
    REASON_NOT_ON_EVERY_ALTERNATIVE,
    REASON_POSSIBLY_PRESENT,
    REASON_PRODUCED_ONLY_IN_CYCLE,
    REASON_UNKNOWN_SOURCE_ON_PATH,
    REASON_TYPE_MISMATCH,
    REASON_TYPE_UNVERIFIED,
    REASON_OPTIONAL_SOURCE,
    REASON_CONTEXT_VALUE_WINS,
    REASON_TOOL_INPUT_KEY_NAMESPACE,
    REASON_PRODUCER_UNKNOWN_SOURCE,
    REASON_PRODUCER_CONVERT_TO_SCHEMA,
    REASON_MULTIPLE_PARALLEL_WRITERS,
    REASON_REMOVAL_RACES_WRITE,
    CONTEXT_PATH_PREFIX,
    OUTPUT_SCHEMA_PATH,
    MAX_PROVENANCE_NAMES,
    PRESENT_STATUSES,
    Finding,
)

_NULL_TYPE = "null"
_INTEGER_TYPE = "integer"
_NUMBER_TYPE = "number"


def _available_outcome(requirement: ContractRequirement, state: FieldState) -> tuple[Outcome, str]:
    """An available field: optionality and nullability first, then the declared type, then whether the type is
    certain (AC-7)."""
    if state.optional:
        return Outcome.WARNING, REASON_OPTIONAL_SOURCE
    if (
        state.types_certain
        and state.types
        and _NULL_TYPE in state.types
        and _NULL_TYPE not in (requirement.types or ())
    ):
        return Outcome.WARNING, REASON_OPTIONAL_SOURCE
    if requirement.types and state.types and state.types_certain and not _types_overlap(requirement.types, state.types):
        return Outcome.WARNING, REASON_TYPE_MISMATCH
    if not state.types_certain:
        return Outcome.UNVERIFIED, REASON_TYPE_UNVERIFIED
    return Outcome.OK, REASON_OK


def _types_overlap(required: Iterable[str], produced: Iterable[str]) -> bool:
    """Whether a produced type list can satisfy a required one: `null` is nullability, not a type, and a JSON-schema
    integer is a number, so the two never mismatch."""
    return bool(_value_types(required) & _value_types(produced))


def _value_types(types: Iterable[str]) -> set[str]:
    return {_NUMBER_TYPE if item == _INTEGER_TYPE else item for item in types if item != _NULL_TYPE}


def requirement_outcome(
    requirement: ContractRequirement,
    context: AbstractContext,
    cycle: CycleEffect | None,
    *,
    item_may_supply: bool = False,
) -> tuple[Outcome, str]:
    """The raw outcome of one context requirement (AC-2..AC-7, AC-13, AC-15).

    `item_may_supply` is True on a node of an iteration branch unless it is a transform: the field the
    `include_in_iterator_context` whitelist removed may still arrive in the iteration item, whose keys cannot be
    known at save time, so that removal is UNVERIFIED there and not a definite ERROR (AC-9, AC-23).
    """
    if requirement.origin in UNVERIFIABLE_ORIGINS:
        return Outcome.UNVERIFIED, REASON_TOOL_INPUT_KEY_NAMESPACE
    state = context.get(requirement.name)
    if requirement.origin == ORIGIN_TOOL_BINDING_OVERRIDE:
        return _binding_override_outcome(state)
    if not requirement.required:
        return Outcome.OK, REASON_OK
    if state is None:
        return _absent(requirement, context)
    return _known_outcome(requirement, state, cycle, item_may_supply=item_may_supply)


def _binding_override_outcome(state: FieldState | None) -> tuple[Outcome, str]:
    """Inverted (AC-15): the binding already has a value, so a same-named context field is what surprises."""
    if state is not None and state.status in PRESENT_STATUSES:
        return Outcome.WARNING, REASON_CONTEXT_VALUE_WINS
    return Outcome.OK, REASON_OK


def _known_outcome(
    requirement: ContractRequirement,
    state: FieldState,
    cycle: CycleEffect | None,
    *,
    item_may_supply: bool,
) -> tuple[Outcome, str]:
    """A required field the context knows about: its status first, then what an available field promises."""
    if state.status is FieldStatus.REMOVED:
        if item_may_supply and state.mechanism is Mechanism.ITERATION_FILTER:
            return Outcome.UNVERIFIED, REASON_ITERATION_ITEM_MAY_SUPPLY
        return Outcome.ERROR, REASON_REMOVED
    if state.status is FieldStatus.POSSIBLY_REMOVED:
        return Outcome.WARNING, REASON_POSSIBLY_REMOVED
    if state.status is FieldStatus.PARTIALLY_AVAILABLE:
        return Outcome.WARNING, REASON_NOT_ON_EVERY_ALTERNATIVE
    if state.status is FieldStatus.POSSIBLY_PRESENT:
        inside = cycle is not None and requirement.name in cycle.produced
        return Outcome.UNVERIFIED, REASON_PRODUCED_ONLY_IN_CYCLE if inside else REASON_POSSIBLY_PRESENT
    return _available_outcome(requirement, state)


def _absent(requirement: ContractRequirement, context: AbstractContext) -> tuple[Outcome, str]:
    if context.unknown_contribution:
        return Outcome.UNVERIFIED, REASON_UNKNOWN_SOURCE_ON_PATH
    if requirement.name in context.suppressions:
        return Outcome.ERROR, REASON_WRITE_SUPPRESSED
    return Outcome.ERROR, REASON_NOT_GUARANTEED


class Recorder:
    """The findings of one pass, in the order they were added."""

    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def add(  # noqa: PLR0913 - keyword-only builder/internal helper; a parameter object would add indirection without clarity
        self,
        kind: RecordKind,
        outcome: Outcome,
        state_id: str,
        path: str,
        reason: str,
        *,
        provenance: Provenance | None = None,
        label: str | None = None,
        requirement: ContractRequirement | None = None,
        detail: str = "",
        location: ContractLocation | None = None,
        field: str | None = None,
    ) -> None:
        if field is None and requirement is not None:
            field = requirement.name
        record = ContractRecord(
            kind=kind,
            outcome=outcome,
            consumer_state_id=state_id,
            path=path,
            field=field,
            message=reason,
            label=label,
            provenance=provenance or Provenance(),
            location=requirement.location if requirement is not None else location,
        )
        self.findings.append(Finding(record=record, reason=reason, requirement=requirement, detail=detail))

    def failure(self, state_id: str, reason: str) -> None:
        self.add(
            RecordKind.ANALYSIS_FAILURE,
            Outcome.UNVERIFIED,
            state_id,
            "",
            reason,
            location=ContractLocation.state(state_id) if state_id else ContractLocation.workflow("states"),
        )

    def record_check(self, node: str, check: OwnOutputCheck) -> None:
        self.add(
            RecordKind.DERIVED,
            check.outcome,
            node,
            check.requirement.path,
            check.reason,
            provenance=Provenance(producers=(node,)),
            requirement=check.requirement,
        )

    def record_contract_form(self, node: str, effect: NodeEffect) -> None:
        if effect.producer_note is ProducerNote.UNKNOWN_SOURCE:
            self.add(
                RecordKind.CONTRACT_FORM,
                Outcome.WARNING,
                node,
                OUTPUT_SCHEMA_PATH,
                REASON_PRODUCER_UNKNOWN_SOURCE,
                detail=effect.reason,
                location=ContractLocation.state(node, OUTPUT_SCHEMA_PATH),
            )
        elif effect.producer_note is ProducerNote.CONVERT_TO_SCHEMA:
            self.add(
                RecordKind.CONTRACT_FORM,
                Outcome.UNVERIFIED,
                node,
                OUTPUT_SCHEMA_PATH,
                REASON_PRODUCER_CONVERT_TO_SCHEMA,
                detail=effect.reason,
                location=ContractLocation.state(node, OUTPUT_SCHEMA_PATH),
            )
        for note in effect.notes:
            path = f"{OUTPUT_SCHEMA_PATH}.{note.name}" if note.name else OUTPUT_SCHEMA_PATH
            self.add(
                RecordKind.CONTRACT_FORM,
                Outcome.WARNING,
                node,
                path,
                note.code,
                field=note.name,
                location=note.location or ContractLocation.state(node, OUTPUT_SCHEMA_PATH),
            )

    def emit_convergence(self, node: str, notes: Mapping[str, MergeNote]) -> None:
        for name, note in notes.items():
            if note.multiple_writers:
                self.add(
                    RecordKind.CONVERGENCE,
                    Outcome.WARNING,
                    node,
                    f"{CONTEXT_PATH_PREFIX}{name}",
                    REASON_MULTIPLE_PARALLEL_WRITERS,
                    provenance=Provenance(producers=note.writers[:MAX_PROVENANCE_NAMES]),
                    location=ContractLocation.state(node),
                    field=name,
                )
            if note.raced:
                self.add(
                    RecordKind.CONVERGENCE,
                    Outcome.WARNING,
                    node,
                    f"{CONTEXT_PATH_PREFIX}{name}",
                    REASON_REMOVAL_RACES_WRITE,
                    provenance=Provenance(producers=note.writers[:MAX_PROVENANCE_NAMES]),
                    location=ContractLocation.state(node),
                    field=name,
                )
