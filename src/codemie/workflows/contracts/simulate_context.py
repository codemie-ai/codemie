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

"""Contexts in flight through the availability simulator, and how they are combined (EPMCDME-16503, parent §4.1).

* **Merging** groups the contexts arriving at a node by their branch tag. Contexts in one group are alternatives
  (only one path ran), so a field missing from some of them is partially available (a WARNING naming the branches
  that lack it). Groups that are provably siblings of one fan-out are parallel (all of them ran), so any guaranteeing
  branch makes the field available, two or more writers is a WARNING, and a removal either races a write (WARNING) or
  stands alone (ERROR). Anything else is merged as alternatives, which is the direction that cannot invent an ERROR.
* **Iteration scope** is the branch tag's iteration depth. It is pushed on an ITERATION edge, popped on the outgoing
  edge of a `finish_iteration` state, and popped at a join, where the merged tag is the common prefix of the arriving
  tags.
* **A cyclic component** is summarised once as a `CycleEffect` and that summary is applied to every node inside it.

`ContextMerger` charges the pass's work cap through the `spend` callback it is given; nothing here knows the cap.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from codemie.workflows.contracts.abstract_context import (
    AbstractContext,
    FieldState,
    FieldStatus,
    Mechanism,
    TransitionResult,
)
from codemie.workflows.contracts.effects import NodeEffect
from codemie.workflows.contracts.simulate_model import (
    ITERATION_ITEM_SOURCE,
    MAX_PROVENANCE_NAMES,
    PRESENT_STATUSES,
    EdgeKind,
    join_names,
    text_of,
)

# One branch tag: a stack of `(kind, fan_out_state_id, branch_head_state_id)` entries.
Tag = tuple[tuple[str, str, str], ...]

# Two sides writing the same field is a race; one writer is not.
_MULTIPLE_WRITERS = 2


@dataclass(frozen=True)
class Arrival:
    """One context arriving at a node.

    `tag` is the branch tag - a stack of `(kind, fan_out_state_id, branch_head_state_id)` entries, one per fan-out or
    iteration the path is still inside. `written` are the field names written since the last fan-out, which is what
    tells a branch that wrote a field from a branch that only carried it forward (AC-6).
    """

    source_id: str
    tag: Tag
    context: AbstractContext
    appends: bool = False
    written: frozenset[str] = frozenset()
    first_in_iteration: bool = False


@dataclass(frozen=True)
class Part:
    """One side of a merge."""

    source_id: str
    context: AbstractContext
    written: frozenset[str] = frozenset()
    appends: bool = False
    # The states that sent the contexts this side stands for, and who wrote each field. Empty on a single arrival,
    # where `source_id` is both; filled when sides were merged, so a note names states and not the merged side.
    names: tuple[str, ...] = ()
    writers: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def leaf_names(self) -> tuple[str, ...]:
        return self.names or (self.source_id,)

    def writers_of(self, name: str) -> tuple[str, ...]:
        return self.writers.get(name) or ((self.source_id,) if name in self.written else ())


@dataclass(frozen=True)
class MergeNote:
    """What the merge of one field found: which sides lack it, which wrote it, and the AC-6 convergence findings."""

    lacking: tuple[str, ...] = ()
    writers: tuple[str, ...] = ()
    multiple_writers: bool = False
    raced: bool = False


@dataclass(frozen=True)
class Point:
    """The merged context at one node, with the branch tag it is on."""

    context: AbstractContext
    tag: Tag = ()
    written: frozenset[str] = frozenset()
    notes: Mapping[str, MergeNote] = field(default_factory=dict)
    first_in_iteration: bool = False

    @property
    def iteration_depth(self) -> int:
        return sum(1 for entry in self.tag if entry[0] == EdgeKind.ITERATION.value)


@dataclass
class CycleEffect:
    """One summary of what a cyclic component's body does, applied to every node inside it (AR-17)."""

    produced: dict[str, tuple[str, ...]] = field(default_factory=dict)
    removed: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    suppressions: dict[str, tuple[str, str]] = field(default_factory=dict)
    unknown_sources: tuple[str, ...] = ()


class ContextMerger:
    """Combines the contexts that arrive at one node (AC-6, AC-9) and applies a cycle summary to a context."""

    def __init__(self, spend: Callable[[int], None]) -> None:
        self._spend = spend

    def merge(self, arrivals: list[Arrival]) -> Point:
        if not arrivals:
            return Point(AbstractContext())
        first = any(item.first_in_iteration for item in arrivals)
        groups: dict[Tag, list[Arrival]] = {}
        for arrival in arrivals:
            groups.setdefault(arrival.tag, []).append(arrival)
        notes: dict[str, MergeNote] = {}
        sides: list[tuple[Tag, Part]] = []
        for tag, items in groups.items():
            sides.append((tag, self._merge_group(items, notes)))
        if len(sides) == 1:
            tag, part = sides[0]
            return Point(part.context, tag, part.written, notes, first)
        merged = self._merge_sides(sides, 0, notes)
        prefix = _common_prefix([tag for tag, _ in sides])
        depth = sum(1 for entry in prefix if entry[0] == EdgeKind.ITERATION.value)
        context = dataclasses.replace(merged.context, scope=depth)
        return Point(context, prefix, merged.written, notes, first)

    def _merge_group(self, items: list[Arrival], notes: dict[str, MergeNote]) -> Part:
        parts = [Part(item.source_id, item.context, item.written, item.appends) for item in items]
        if len(parts) == 1:
            return parts[0]
        return self._combine(parts, parallel=False, notes=notes)

    def _merge_sides(self, sides: list[tuple[Tag, Part]], depth: int, notes: dict[str, MergeNote]) -> Part:
        """AC-6 composes: the branches of each fan-out are united first, then the alternatives rule is applied.

        `depth` is the tag entry being resolved. A side whose tag ends here is an alternative by itself; sides that
        carry an entry at this depth are grouped by the fan-out (or iteration) that entry belongs to. Within one
        parallel fan-out the branches are merged by their heads (recursively, so a fan-out nested inside a branch is
        united before its branch meets the others) and then united; each such result stands for one alternative.
        """
        if len(sides) == 1:
            return sides[0][1]
        alternatives = [part for tag, part in sides if len(tag) <= depth]
        fan_outs: dict[tuple[str, str], list[tuple[Tag, Part]]] = {}
        for tag, part in sides:
            if len(tag) > depth:
                fan_outs.setdefault((tag[depth][0], tag[depth][1]), []).append((tag, part))
        for (kind, _fan_out), members in fan_outs.items():
            alternatives.append(self._merge_fan_out(kind, members, depth, notes))
        if len(alternatives) == 1:
            return alternatives[0]
        return self._combine(alternatives, parallel=False, notes=notes)

    def _merge_fan_out(
        self, kind: str, members: list[tuple[Tag, Part]], depth: int, notes: dict[str, MergeNote]
    ) -> Part:
        heads: dict[str, list[tuple[Tag, Part]]] = {}
        for tag, part in members:
            heads.setdefault(tag[depth][2], []).append((tag, part))
        branches = [self._merge_sides(branch, depth + 1, notes) for branch in heads.values()]
        if len(branches) == 1:
            return branches[0]
        # Every branch of a parallel fan-out runs; the targets of anything else (an iteration) are alternatives.
        return self._combine(branches, parallel=kind == EdgeKind.PARALLEL.value, notes=notes)

    def _combine(self, parts: list[Part], *, parallel: bool, notes: dict[str, MergeNote]) -> Part:
        context, found = self._merge_parts(parts, parallel=parallel)
        for name, note in found.items():
            notes[name] = _join_notes(notes.get(name), note)
        written = frozenset().union(*(part.written for part in parts))
        return Part(
            parts[0].source_id,
            context,
            written,
            all(part.appends for part in parts if part.written) if parallel else all(part.appends for part in parts),
            names=join_names(*(part.leaf_names() for part in parts)),
            writers={name: join_names(*(part.writers_of(name) for part in parts)) for name in written},
        )

    def _merge_parts(self, parts: list[Part], *, parallel: bool) -> tuple[AbstractContext, dict[str, MergeNote]]:
        names: dict[str, None] = {}
        for part in parts:
            for name in part.context.fields:
                names.setdefault(name)
        self._spend(len(names) * len(parts))
        fields: dict[str, FieldState] = {}
        notes: dict[str, MergeNote] = {}
        for name in names:
            merged, note = _merge_field(name, parts, parallel=parallel)
            if merged is not None:
                fields[name] = merged
            if note is not None:
                notes[name] = note
        context = AbstractContext(
            fields=fields,
            unknown_contribution=any(part.context.unknown_contribution for part in parts),
            unknown_sources=join_names(*(part.context.unknown_sources for part in parts)),
            scope=min(part.context.scope for part in parts),
            suppressions=_join_suppressions(*(part.context.suppressions for part in parts)),
        )
        return context, notes

    def apply_cycle(self, context: AbstractContext, cycle: CycleEffect) -> AbstractContext:
        """Apply the cycle summary: what the body produces is only possibly present, what it removes only possibly
        removed. A field available before the cycle and untouched inside it is evaluated normally (AC-11)."""
        fields = dict(context.fields)
        self._spend(len(fields) + len(cycle.produced) + len(cycle.removed))
        for name, producers in cycle.produced.items():
            state = fields.get(name)
            if state is None or state.status is not FieldStatus.AVAILABLE:
                fields[name] = FieldState(
                    status=FieldStatus.POSSIBLY_PRESENT,
                    producers=producers,
                    scope=context.scope,
                    types=None,
                    types_certain=False,
                    optional=True,
                )
        for name, (removed_by, mechanism) in cycle.removed.items():
            state = fields.get(name)
            if state is not None and state.status is FieldStatus.AVAILABLE:
                fields[name] = dataclasses.replace(
                    state.unhidden(),
                    status=FieldStatus.POSSIBLY_REMOVED,
                    removed_by=removed_by,
                    mechanism=Mechanism(mechanism) if mechanism else None,
                    types_certain=False,
                )
        return AbstractContext(
            fields=fields,
            unknown_contribution=context.unknown_contribution or bool(cycle.unknown_sources),
            unknown_sources=join_names(context.unknown_sources, cycle.unknown_sources),
            scope=context.scope,
            suppressions=_join_suppressions(context.suppressions, cycle.suppressions),
        )


def collect_cycle_effect(collected: CycleEffect, context: AbstractContext, members: frozenset[str]) -> None:
    """Record what the cycle body produced, removed and suppressed, keeping the first finding for each name."""
    for name, (node, mechanism) in context.suppressions.items():
        if node in members:
            collected.suppressions.setdefault(name, (node, mechanism))
    for name, state in context.fields.items():
        if state.status is FieldStatus.REMOVED and state.removed_by in members:
            mechanism = state.mechanism.value if state.mechanism is not None else None
            collected.removed.setdefault(name, (state.removed_by, mechanism))
        elif state.status in PRESENT_STATUSES and any(item in members for item in state.producers):
            collected.produced.setdefault(name, state.producers[:MAX_PROVENANCE_NAMES])


def mark_item_unknown(context: AbstractContext) -> AbstractContext:
    """An iteration branch also carries the item, whose keys cannot be known at save time (AC-9)."""
    return cast(
        AbstractContext,
        dataclasses.replace(
            context,
            unknown_contribution=True,
            unknown_sources=join_names(context.unknown_sources, (ITERATION_ITEM_SOURCE,)),
        ),
    )


def restore_hidden(context: AbstractContext, source_id: str) -> AbstractContext:
    """The context after the iteration that `source_id` started: the branch wrote its updates into the main context,
    which still holds every field the iteration filter hid from the branch, also where only some paths hid it."""
    fields = {
        name: state.hidden if state.hidden is not None and state.hidden_by == source_id else state
        for name, state in context.fields.items()
    }
    return cast(AbstractContext, dataclasses.replace(context, fields=fields))


def mark_transition_unknown(context: AbstractContext, state_id: str) -> AbstractContext:
    """The context after a transition that could not be applied: every field is only possibly present (UNVERIFIED)."""
    fields = {
        name: dataclasses.replace(
            state.unhidden(),
            status=FieldStatus.POSSIBLY_PRESENT,
            overwritten_by=state_id,
            types=None,
            types_certain=False,
        )
        for name, state in context.fields.items()
    }
    return cast(
        AbstractContext,
        dataclasses.replace(
            context,
            fields=fields,
            unknown_contribution=True,
            unknown_sources=join_names(context.unknown_sources, (state_id,)),
        ),
    )


def _common_prefix(tags: Sequence[Tag]) -> Tag:
    prefix = tags[0]
    for tag in tags[1:]:
        limit = 0
        while limit < len(prefix) and limit < len(tag) and prefix[limit] == tag[limit]:
            limit += 1
        prefix = prefix[:limit]
    return prefix


def _join_suppressions(*groups: Mapping[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """Union of the suppressed writes of several contexts, the first group naming a field keeping it."""
    joined: dict[str, tuple[str, str]] = {}
    for group in groups:
        for name, blame in group.items():
            joined.setdefault(name, blame)
    return joined


def _join_notes(first: MergeNote | None, second: MergeNote | None) -> MergeNote | None:
    """Two notes about one field from different levels of a nested merge: nothing either found is lost."""
    if first is None or second is None:
        return first or second
    return MergeNote(
        lacking=join_names(first.lacking, second.lacking),
        writers=join_names(first.writers, second.writers),
        multiple_writers=first.multiple_writers or second.multiple_writers,
        raced=first.raced or second.raced,
    )


def _merge_field(name: str, parts: list[Part], *, parallel: bool) -> tuple[FieldState | None, MergeNote | None]:
    """Merge one field across the sides of a convergence (AC-6).

    Where some sides still hide the field behind an iteration filter, the main context is merged as well: a hidden
    side holds the field there, so `finish_iteration` brings back that merge even when the branch sees it on only
    some sides (a path that writes the field next to one that leaves it hidden).
    """
    states = [part.context.get(name) for part in parts]
    merged, note = _merge_states(name, parts, states, parallel=parallel)
    source = next((state.hidden_by for state in states if state is not None and state.hidden is not None), None)
    if merged is None or source is None:
        return merged, note
    main_states = [
        state.hidden if state is not None and state.hidden is not None and state.hidden_by == source else state
        for state in states
    ]
    main, _ = _merge_states(name, parts, main_states, parallel=parallel)
    return dataclasses.replace(merged, hidden=main, hidden_by=source), note


def _merge_states(
    name: str, parts: list[Part], states: list[FieldState | None], *, parallel: bool
) -> tuple[FieldState | None, MergeNote | None]:
    present = [state for state in states if state is not None and state.status in PRESENT_STATUSES]
    available = [state for state in states if state is not None and state.status is FieldStatus.AVAILABLE]
    removed = [state for state in states if state is not None and state.status is FieldStatus.REMOVED]
    if not present and not removed and not any(states):
        return None, None
    writers = join_names(*(part.writers_of(name) for part in parts if name in part.written))
    if parallel:
        # A side the iteration filter only hides deleted nothing, so next to a side that has the field it neither
        # races a write nor takes the field away once the parallel branches join.
        deleted = [state for state in removed if not _only_hidden(state)]
        races = deleted if present else removed
        status, note = _parallel_status(parts, name, present, available, races, writers)
    else:
        status, note = _alternative_status(parts, states, present, available, removed)
    return _merged_state(status, states, present, removed), note


def _only_hidden(state: FieldState) -> bool:
    """The field is gone from the branch only because the iteration filter kept it out."""
    return state.hidden is not None and state.mechanism is Mechanism.ITERATION_FILTER


def _parallel_status(  # noqa: PLR0913 - keyword-only builder/internal helper; a parameter object would add indirection without clarity
    parts: list[Part],
    name: str,
    present: list[FieldState],
    available: list[FieldState],
    removed: list[FieldState],
    writers: tuple[str, ...],
) -> tuple[FieldStatus, MergeNote | None]:
    """All sides run, so any guaranteeing branch wins; a removal only stands when nothing re-wrote the field."""
    writing = [part for part in parts if name in part.written]
    accumulating = all(part.appends for part in writing)
    # Counted by side, not by name: one side that merged two alternatives names both but is one writer.
    multiple = len(writing) >= _MULTIPLE_WRITERS and not accumulating
    raced = bool(removed) and bool(writers)
    if removed and not writers:
        status = FieldStatus.REMOVED
    elif removed:
        status = FieldStatus.POSSIBLY_REMOVED
    elif available:
        status = FieldStatus.AVAILABLE
    elif present:
        status = FieldStatus.POSSIBLY_PRESENT
    else:
        status = FieldStatus.POSSIBLY_REMOVED
    note = MergeNote(writers=writers, multiple_writers=multiple, raced=raced) if multiple or raced else None
    return status, note


def _alternative_status(
    parts: list[Part],
    states: list[FieldState | None],
    present: list[FieldState],
    available: list[FieldState],
    removed: list[FieldState],
) -> tuple[FieldStatus, MergeNote | None]:
    """Exactly one side runs, so a field some sides lack is only partially available, naming the sides that lack it."""
    total = len(parts)
    if len(present) == total:
        return (FieldStatus.AVAILABLE if len(available) == total else FieldStatus.POSSIBLY_PRESENT), None
    if not present:
        return (FieldStatus.REMOVED if len(removed) == total else FieldStatus.POSSIBLY_REMOVED), None
    lacking = join_names(
        *(
            part.leaf_names()
            for part, state in zip(parts, states, strict=False)
            if state is None or state.status not in PRESENT_STATUSES
        )
    )
    return FieldStatus.PARTIALLY_AVAILABLE, MergeNote(lacking=lacking)


def _merged_state(
    status: FieldStatus,
    states: list[FieldState | None],
    present: list[FieldState],
    removed: list[FieldState],
) -> FieldState:
    types, certain = _merge_types(present)
    scope = min((state.scope for state in states if state is not None), default=0)
    removal = next((state for state in removed if state.removed_by), None)
    overwritten = next((state for state in present if state.overwritten_by), None)
    return FieldState(
        status=status,
        producers=join_names(*(state.producers for state in present or [item for item in states if item])),
        overwritten_by=overwritten.overwritten_by if overwritten is not None else None,
        removed_by=removal.removed_by if removal is not None else None,
        mechanism=removal.mechanism if removal is not None else None,
        scope=scope,
        types=types,
        types_certain=certain,
        optional=any(state.optional for state in present),
    )


def _merge_types(present: list[FieldState]) -> tuple[tuple[str, ...] | None, bool]:
    if not present:
        return None, False
    first = present[0].types
    if any(state.types != first for state in present[1:]):
        return None, False
    return first, all(state.types_certain for state in present)


def written_now(effect: NodeEffect, state: object, result: TransitionResult) -> frozenset[str]:
    """The field names this state actually wrote: its contract's fields and its output key, less what was suppressed."""
    names = set(effect.names)
    output_key = text_of(getattr(getattr(state, "next", None), "output_key", None))
    if output_key:
        names.add(output_key)
    return frozenset(names - {item.name for item in result.suppressed})
