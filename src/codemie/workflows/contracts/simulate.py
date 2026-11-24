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

"""Availability simulator (EPMCDME-16503, parent §4.1, §4.2): one forward pass over the reachable graph.

`simulate_workflow` is the entry. It is iterative (no recursion anywhere, so a 1,000-state chain or a deep cycle
cannot exhaust the Python stack) and linear in edges: each reachable state is evaluated once, each edge is traversed
once, and a cyclic region is traversed twice (once to collect what its body writes and removes, once to evaluate it).

The pass is split by what each part knows:

* `simulate_graph` - the states, their classified out-edges, the strongly connected components (cycles);
* `simulate_context` - the contexts in flight, how they merge at a join, and how a cycle summary is applied;
* `simulate_tools` - the tool parameter schema lookups, with their memo and budget;
* `simulate_outcomes` - the raw outcome of one requirement, and the records of a pass;
* `simulate_model` - the reason codes and result shapes this module returns.

This module drives them. A cyclic component's body is walked once to collect what it produces and removes, and that
summary is applied to every node inside it (AC-11, AR-17). A work cap of `WORK_CAP` abstract operations stops the pass
and turns every requirement it did not reach into UNVERIFIED; tool lookups have their own budget, `LOOKUP_BUDGET`.

What this module does *not* decide: `reported_as` (the mode policy, T7), the rendered message text (T7 too - a record
carries a fixed reason code in `message`, never user data, and T7 replaces it with its template), and where the tool
parameter schemas come from (T10's resolver; with `resolver=None` every tool binding that needs a schema stays
UNVERIFIED, AC-38).
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from codemie.workflows.contracts.abstract_context import (
    INITIAL_INPUT_SOURCE,
    AbstractContext,
    TransitionResult,
    entry_input_fields,
    initial_context,
)
from codemie.workflows.contracts.derive import (
    LABEL_RENDERS_EMPTY,
    RENDERS_EMPTY_ORIGINS,
    StateRequirements,
    derive_state_requirements,
)
from codemie.workflows.contracts.jinja_analysis import TemplateAnalysisCapReached
from codemie.workflows.contracts.effects import NodeEffect, NodeKind, node_effect
from codemie.workflows.contracts.models import (
    ContractLocation,
    ContractRequirement,
    Outcome,
    Provenance,
    RecordKind,
)
from codemie.workflows.contracts.reader import ContractForm, ProducerNote
from codemie.workflows.contracts.simulate_context import (
    Arrival,
    ContextMerger,
    CycleEffect,
    Point,
    collect_cycle_effect,
    mark_item_unknown,
    mark_transition_unknown,
    restore_hidden,
    written_now,
)
from codemie.workflows.contracts.simulate_graph import Edge, Graph, is_cyclic
from codemie.workflows.contracts.simulate_model import (
    MAX_PROVENANCE_NAMES,
    REASON_DERIVATION_FAILED,
    REASON_DUPLICATE_STATE_ID,
    REASON_EFFECT_FAILED,
    REASON_LOOKUP_BUDGET_EXHAUSTED,
    REASON_MANDATORY_PARAMETER_NOT_BOUND,
    REASON_STATE_NOT_ANALYSABLE,
    REASON_TOOL_SCHEMA_UNKNOWN,
    REASON_TRANSITION_FAILED,
    REASON_TRANSITION_TARGET_MISSING,
    REASON_WORK_CAP_REACHED,
    TOOL_ARGS_PATH,
    EdgeKind,
    SimulationResult,
    StateAnalysis,
    index_by_id,
    join_names,
    text_of,
)
from codemie.workflows.contracts.simulate_outcomes import Recorder, requirement_outcome
from codemie.workflows.contracts.simulate_tools import ToolLookups
from codemie.workflows.contracts.transitions import apply_transition

if TYPE_CHECKING:
    from codemie.workflows.contracts.protocols import ToolSchemaResolver

logger = logging.getLogger(__name__)

# Abstract operations one call may perform before every remaining requirement becomes UNVERIFIED (spec Decisions).
WORK_CAP = 100_000
# Distinct built-in tools whose parameter schema may be looked up through the discovery service in one call. Static MCP
# tools (already listed by the resource stage) and plugin tools (no schema at all) cost no service call and are not
# counted. A backstop, not a working limit: it sits above the number of registered built-in tools (a test pins that),
# and a made-up tool name never reaches it because the resource stage rejects it before lookups are enabled. Provider
# tools are not in the registry and spend budget too, so it is a ceiling, not a bound that can never be hit. Past it a
# tool's schema stays unknown and strict blocks the save (AC-19). The cost falls only on workflows that use many tools:
# about 25 ms per tool natively (4.3 s for all 168), about 160 ms in the local container, production unmeasured.
LOOKUP_BUDGET = 200
_PROBLEM_OUTCOMES = frozenset({Outcome.ERROR, Outcome.WARNING, Outcome.UNVERIFIED})


def simulate_workflow(  # noqa: PLR0913 - keyword-only builder/internal helper; a parameter object would add indirection without clarity
    states: Sequence[Any],
    *,
    custom_nodes: Sequence[Any] | None = None,
    tools: Sequence[Any] | None = None,
    resolver: ToolSchemaResolver | None = None,
    unknown_initial_input: bool = True,
    work_cap: int = WORK_CAP,
    lookup_budget: int = LOOKUP_BUDGET,
) -> SimulationResult:
    """Simulate what every reachable state finds in the shared context and check it against its requirements.

    `states`, `custom_nodes` and `tools` are the `WorkflowConfigBase` sections; everything is read through `getattr`,
    so a `model_construct`-ed or otherwise malformed member degrades to a record instead of raising. `resolver` is
    T10's tool parameter schema resolver (`mandatory_params(tool)`); `None` - the default of slice B - leaves every
    tool binding that needs a schema UNVERIFIED (AC-38). The entry state's usable `input_schema` is the declared
    initial input: it seeds the entry context with its fields and types as `initial_input` sources and the initial
    input is then a known source (AC-2, AC-3). Without one, `unknown_initial_input` decides: True (the default, and
    every save path) makes the initial input an unknown source (AC-5); False makes the "every source is known"
    branch of AC-3 reachable in a test.
    """
    return _Simulation(
        states=states,
        custom_nodes=custom_nodes,
        tools=tools,
        resolver=resolver,
        unknown_initial_input=unknown_initial_input,
        work_cap=work_cap,
        lookup_budget=lookup_budget,
    ).run()


@dataclass(frozen=True)
class _Facts:
    """Everything derived from one state's configuration, computed once."""

    requirements: StateRequirements
    effect: NodeEffect
    analysed: bool = True
    capped: bool = False


class _Simulation:
    """One pass. Never shared between calls: every piece of state here belongs to a single `simulate_workflow`."""

    def __init__(  # noqa: PLR0913 - keyword-only builder/internal helper; a parameter object would add indirection without clarity
        self,
        *,
        states: Sequence[Any],
        custom_nodes: Sequence[Any] | None,
        tools: Sequence[Any] | None,
        resolver: ToolSchemaResolver | None,
        unknown_initial_input: bool,
        work_cap: int,
        lookup_budget: int,
    ) -> None:
        self.unknown_initial_input = unknown_initial_input
        self.work_cap = work_cap
        self.operations = 0
        self.cap_reached = False
        self.graph = Graph()
        self.records = Recorder()
        self.merger = ContextMerger(self._spend)
        self.schemas = ToolLookups(resolver, lookup_budget)
        self.inbound: dict[str, list[Arrival]] = {}
        self.depths: dict[str, int] = {}
        self.analysed_ids: set[str] = set()
        self._facts: dict[str, _Facts] = {}
        self._custom_nodes = index_by_id(custom_nodes)
        self._tools = index_by_id(tools)
        self._read_states(states)

    def _read_states(self, states: Sequence[Any]) -> None:
        for item in states or ():
            state_id = getattr(item, "id", None)
            if not isinstance(state_id, str) or not state_id:
                self.records.failure("", REASON_STATE_NOT_ANALYSABLE)
                continue
            if state_id in self.graph.nodes:
                self.records.failure(state_id, REASON_DUPLICATE_STATE_ID)
                continue
            self.graph.add(state_id, item)

    def run(self) -> SimulationResult:
        if not self.graph.nodes:
            return SimulationResult(findings=tuple(self.records.findings))
        self.graph.build_edges()
        reachable = self.graph.reachable()
        self._report_dangling(reachable)
        self._seed_entry()
        for component in self.graph.components(reachable):
            if self.cap_reached:
                break
            self._process_component(component)
        self._cap_remaining(reachable)
        return self._result(reachable)

    def _report_dangling(self, reachable: set[str]) -> None:
        for state_id in self.graph.order:
            if state_id not in reachable:
                continue
            for edge in self.graph.dangling.get(state_id, ()):
                self.records.add(
                    RecordKind.ANALYSIS_FAILURE,
                    Outcome.UNVERIFIED,
                    state_id,
                    edge.path,
                    REASON_TRANSITION_TARGET_MISSING,
                    location=ContractLocation.state(state_id, *edge.segments),
                )

    def _seed_entry(self) -> None:
        context = initial_context(
            unknown_initial_input=self.unknown_initial_input,
            declared=entry_input_fields(self.graph.nodes[self.graph.order[0]]),
        )
        self.inbound[self.graph.order[0]] = [
            Arrival(
                source_id=INITIAL_INPUT_SOURCE,
                tag=(),
                context=context,
                written=frozenset(context.fields),
            )
        ]

    def _process_component(self, component: list[str]) -> None:
        members = frozenset(component)
        body = self.graph.body_order(component, members, self.inbound)
        cycle = self._collect_cycle(body, members) if is_cyclic(component, self.graph.targets) else None
        if cycle is not None and self.cap_reached:
            # The summary is incomplete: walking with it would read a later producer as absent and report an ERROR
            # where the states belong to `_cap_remaining` (UNVERIFIED).
            return
        self._walk(body, members, cycle, emit=True)

    def _collect_cycle(self, body: list[str], members: frozenset[str]) -> CycleEffect:
        """Walk the body once without emitting, to learn what the cycle produces, removes and leaves unknown."""
        return self._walk(body, members, None, emit=False)

    def _walk(self, body: list[str], members: frozenset[str], cycle: CycleEffect | None, *, emit: bool) -> CycleEffect:
        collected = CycleEffect()
        local: dict[str, list[Arrival]] = {}
        unknown: list[str] = []
        for node in body:
            point = self.merger.merge(list(self.inbound.get(node, ())) + local.get(node, []))
            if cycle is not None:
                point = dataclasses.replace(point, context=self.merger.apply_cycle(point.context, cycle))
            facts = self._state_facts(node)
            if facts.capped:
                break
            if emit:
                self.depths[node] = point.iteration_depth
                self.records.emit_convergence(node, point.notes)
                self._evaluate_state(node, point, facts, cycle)
            result = self._transition(node, point, facts, emit=emit)
            if not emit:
                collect_cycle_effect(collected, result.context, members)
                if not facts.effect.known_source and facts.effect.writes_context:
                    unknown.append(node)
            self._fan_out(node, point, result, facts, members, local, emit=emit)
            if self.cap_reached:
                break
        collected.unknown_sources = tuple(unknown)
        return collected

    def _fan_out(  # noqa: PLR0913 - keyword-only builder/internal helper; a parameter object would add indirection without clarity
        self,
        node: str,
        point: Point,
        result: TransitionResult,
        facts: _Facts,
        members: frozenset[str],
        local: dict[str, list[Arrival]],
        *,
        emit: bool,
    ) -> None:
        written = written_now(facts.effect, self.graph.nodes[node], result)
        for edge in self.graph.edges.get(node, ()):
            arrival = self._arrival(node, edge, point, result, written)
            if edge.target in members:
                local.setdefault(edge.target, []).append(arrival)
            elif emit:
                self.inbound.setdefault(edge.target, []).append(arrival)
            self._spend(1)

    def _arrival(
        self, node: str, edge: Edge, point: Point, result: TransitionResult, written: frozenset[str]
    ) -> Arrival:
        appends = bool(getattr(getattr(self.graph.nodes[node], "next", None), "append_to_context", False))
        if edge.kind is EdgeKind.ITERATION:
            branch = mark_item_unknown(result.iteration_context or result.context)
            tag = (*point.tag, (EdgeKind.ITERATION.value, node, edge.target))
            return Arrival(node, tag, branch, appends, frozenset(), first_in_iteration=True)
        if edge.kind is EdgeKind.PARALLEL:
            tag = (*point.tag, (EdgeKind.PARALLEL.value, node, edge.target))
            return Arrival(node, tag, result.context, appends, frozenset())
        tag, context = point.tag, result.context
        if (
            getattr(self.graph.nodes[node], "finish_iteration", False)
            and tag
            and tag[-1][0] == EdgeKind.ITERATION.value
        ):
            source_id = tag[-1][1]
            tag = tag[:-1]
            context = dataclasses.replace(context, scope=max(0, context.scope - 1))
            if context.scope == 0:
                context = restore_hidden(context, source_id)
        return Arrival(node, tag, context, appends, point.written | written)

    def _transition(self, node: str, point: Point, facts: _Facts, *, emit: bool) -> TransitionResult:
        try:
            result = apply_transition(
                point.context,
                state_id=node,
                effect=facts.effect,
                next_state=getattr(self.graph.nodes[node], "next", None),
                first_state_in_iteration=point.first_in_iteration,
            )
        except Exception:
            logger.warning("Contract linker could not apply the transition of state %s", node, exc_info=True)
            if emit:
                self.records.failure(node, REASON_TRANSITION_FAILED)
            # The removals the transition would have made are not known: nothing downstream may read as OK.
            return TransitionResult(context=mark_transition_unknown(point.context, node))
        self._spend(result.operations)
        return result

    def _state_facts(self, state_id: str) -> _Facts:
        facts = self._facts.get(state_id)
        if facts is not None:
            return facts
        state = self.graph.nodes[state_id]
        custom_node = self._custom_nodes.get(text_of(getattr(state, "custom_node_id", None)))
        tool = self._tools.get(text_of(getattr(state, "tool_id", None)))
        analysed, capped = True, False
        try:
            requirements = derive_state_requirements(
                state,
                custom_node=custom_node,
                tool=tool,
                mandatory_parameters=self.schemas.mandatory_parameters(state_id, state, tool),
            )
        except TemplateAnalysisCapReached as error:
            # A template too costly to analyse stops the pass at the cap, like any other work.
            self.records.add(
                RecordKind.ANALYSIS_FAILURE,
                Outcome.UNVERIFIED,
                state_id,
                error.path,
                REASON_WORK_CAP_REACHED,
                location=error.location,
                field=error.path,
            )
            self.cap_reached = True
            requirements, analysed, capped = StateRequirements(state_id=state_id), False, True
        except Exception:
            logger.warning("Contract linker could not derive requirements of state %s", state_id, exc_info=True)
            self.records.failure(state_id, REASON_DERIVATION_FAILED)
            requirements, analysed = StateRequirements(state_id=state_id), False
        try:
            effect = node_effect(state, custom_node=custom_node)
        except Exception:
            logger.warning("Contract linker could not read the effect of state %s", state_id, exc_info=True)
            self.records.failure(state_id, REASON_EFFECT_FAILED)
            effect = NodeEffect(
                kind=NodeKind.UNKNOWN, producer_note=ProducerNote.UNKNOWN_SOURCE, output_value_certain=False
            )
            analysed = False
        facts = _Facts(requirements=requirements, effect=effect, analysed=analysed, capped=capped)
        self._facts[state_id] = facts
        return facts

    def _evaluate_state(self, node: str, point: Point, facts: _Facts, cycle: CycleEffect | None) -> None:
        self.analysed_ids.add(node)
        for requirement in facts.requirements.context_requirements:
            self._record_requirement(node, requirement, point, cycle, facts)
        for check in facts.requirements.transition_checks:
            self.records.record_check(node, check)
        for name in facts.requirements.missing_mandatory_parameters:
            self.records.add(
                RecordKind.DERIVED,
                Outcome.ERROR,
                node,
                f"{TOOL_ARGS_PATH}.{name}",
                REASON_MANDATORY_PARAMETER_NOT_BOUND,
                location=facts.requirements.bindings_location,
                field=name,
            )
        if facts.requirements.tool_schema_unknown:
            reason = (
                REASON_LOOKUP_BUDGET_EXHAUSTED if node in self.schemas.budget_blocked else REASON_TOOL_SCHEMA_UNKNOWN
            )
            self.records.add(
                RecordKind.DERIVED,
                Outcome.UNVERIFIED,
                node,
                TOOL_ARGS_PATH,
                reason,
                location=facts.requirements.tool_schema_location,
            )
        self.records.record_contract_form(node, facts.effect)

    def _record_requirement(
        self, node: str, requirement: ContractRequirement, point: Point, cycle: CycleEffect | None, facts: _Facts
    ) -> None:
        # The iteration item stays in the context of every node of the branch, except a transform, which never sees it
        # (AC-9).
        item_may_supply = (
            point.first_in_iteration or point.iteration_depth > 0
        ) and facts.effect.kind is not NodeKind.TRANSFORM
        outcome, reason = requirement_outcome(requirement, point.context, cycle, item_may_supply=item_may_supply)
        label = (
            LABEL_RENDERS_EMPTY if requirement.origin in RENDERS_EMPTY_ORIGINS and outcome is Outcome.ERROR else None
        )
        self.records.add(
            requirement.kind,
            outcome,
            node,
            requirement.path,
            reason,
            provenance=self._provenance(requirement.name, point, outcome),
            label=label,
            requirement=requirement,
        )

    def _provenance(self, name: str, point: Point, outcome: Outcome) -> Provenance:
        state = point.context.get(name)
        note = point.notes.get(name)
        checked: tuple[str, ...] = ()
        if outcome in _PROBLEM_OUTCOMES:
            checked = note.lacking if note is not None and note.lacking else self._sources(point.context)
        if state is None:
            suppressed = point.context.suppressions.get(name)
            return Provenance(
                sources_checked=checked,
                removed_by=suppressed[0] if suppressed else None,
                mechanism=suppressed[1] if suppressed else None,
            )
        return Provenance(
            producers=state.producers[:MAX_PROVENANCE_NAMES],
            sources_checked=checked,
            overwritten_by=state.overwritten_by,
            removed_by=state.removed_by,
            mechanism=state.mechanism.value if state.mechanism is not None else None,
        )

    def _sources(self, context: AbstractContext) -> tuple[str, ...]:
        """The sources this context was built from (AC-3 names them), bounded in length."""
        self._spend(len(context.fields))
        producers = [state.producers for state in context.fields.values()]
        return join_names(*producers, context.unknown_sources)

    def _spend(self, amount: int) -> None:
        self.operations += amount
        if self.operations > self.work_cap:
            self.cap_reached = True

    def _cap_remaining(self, reachable: set[str]) -> None:
        """The cap turns every requirement the pass did not reach into UNVERIFIED (spec, Simulation)."""
        if not self.cap_reached:
            return
        self.records.add(
            RecordKind.ANALYSIS_FAILURE,
            Outcome.UNVERIFIED,
            "",
            "",
            REASON_WORK_CAP_REACHED,
            location=ContractLocation.workflow(),
        )
        for state_id in self.graph.order:
            if state_id not in reachable or state_id in self.analysed_ids:
                continue
            facts = self._state_facts(state_id)
            for requirement in facts.requirements.context_requirements:
                self.records.add(
                    requirement.kind,
                    Outcome.UNVERIFIED,
                    state_id,
                    requirement.path,
                    REASON_WORK_CAP_REACHED,
                    requirement=requirement,
                )
            for check in facts.requirements.transition_checks:
                self.records.record_check(state_id, check)

    def _result(self, reachable: set[str]) -> SimulationResult:
        states = [self._state_analysis(state_id, reachable) for state_id in self.graph.order]
        return SimulationResult(
            findings=tuple(self.records.findings),
            states=tuple(states),
            reachable_state_ids=tuple(item.state_id for item in states if item.reachable),
            unreachable_state_ids=tuple(item.state_id for item in states if not item.reachable),
            uncovered_state_ids=tuple(item.state_id for item in states if item.reachable and not item.covered),
            validated_count=sum(1 for item in states if item.reachable and item.covered),
            cap_reached=self.cap_reached,
            operations=self.operations,
            lookups=len(self.schemas.service_lookups),
        )

    def _state_analysis(self, state_id: str, reachable: set[str]) -> StateAnalysis:
        """What the pass knows about one state; a state whose facts were never read keeps the cautious defaults."""
        facts = self._facts.get(state_id)
        depth = self.depths.get(state_id, 0)
        if facts is None:
            return StateAnalysis(
                state_id=state_id,
                reachable=state_id in reachable,
                analysed=False,
                covered=False,
                coverage_source="",
                kind=NodeKind.UNKNOWN,
                known_source=False,
                schema_form=False,
                contract_form=ContractForm.NONE,
                writes_context=True,
                iteration_depth=depth,
            )
        return StateAnalysis(
            state_id=state_id,
            reachable=state_id in reachable,
            analysed=state_id in self.analysed_ids and bool(facts.analysed),
            covered=bool(facts.requirements.covered),
            coverage_source=facts.requirements.coverage_source,
            kind=facts.effect.kind,
            known_source=bool(facts.effect.known_source),
            schema_form=bool(facts.effect.schema_form),
            contract_form=facts.effect.form,
            writes_context=facts.effect.writes_context,
            iteration_depth=depth,
        )
