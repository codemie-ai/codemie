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

"""The state graph of one workflow, as the availability simulator walks it (EPMCDME-16503, parent §4.1).

* **Edges** go to the targets of `WorkflowNextState.leads_to()`, each classified once into `EdgeKind` - a `state_ids`
  fan-out is PARALLEL, an `iter_key` branch head is ITERATION, a condition or switch target is ALTERNATIVE, everything
  else is SINGLE. The kinds and the YAML paths are what `leads_to` does not say.
* **Cycles** are the strongly connected components of the reachable subgraph (AR-17), computed with `networkx`;
  `components` returns them in topological order, earliest declared first, so records come out in a stable order.
  Nothing here recurses: a 1,000-state chain or a deep cycle cannot exhaust the Python stack.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import networkx as nx

from codemie.workflows.constants import END_NODE, END_STATE
from codemie.workflows.contracts.simulate_model import EdgeKind, text_of

if TYPE_CHECKING:
    from codemie.core.workflow_models.workflow_models import WorkflowState

# Terminal transition targets: `get_final_state` maps them to the graph's END (`utils.py:284-287`) and the
# cross-reference stage accepts `end` beside the real state ids (`validation/schema.py:691`), so they are not states
# and not a missing target either.
_TERMINAL_TARGETS = frozenset({END_NODE, END_STATE})


@dataclass(frozen=True)
class Edge:
    """One out-edge: where it goes, how, and the YAML path that declares it."""

    target: str
    kind: EdgeKind
    path: str
    segments: tuple[str | int, ...] = ()


class Graph:
    """The states of one pass, in declared order, with their out-edges. Filled by `add`, then `build_edges`."""

    def __init__(self) -> None:
        self.nodes: dict[str, Any] = {}
        self.order: list[str] = []
        self.position: dict[str, int] = {}
        self.edges: dict[str, tuple[Edge, ...]] = {}
        self.targets: dict[str, tuple[str, ...]] = {}
        self.dangling: dict[str, tuple[Edge, ...]] = {}
        self.digraph = nx.DiGraph()

    def add(self, state_id: str, item: Any) -> None:
        self.position[state_id] = len(self.order)
        self.order.append(state_id)
        self.nodes[state_id] = item

    def build_edges(self) -> None:
        for state_id in self.order:
            known: list[Edge] = []
            missing: list[Edge] = []
            for edge in out_edges(self.nodes[state_id]):
                (known if edge.target in self.nodes else missing).append(edge)
            self.edges[state_id] = tuple(known)
            self.targets[state_id] = tuple(edge.target for edge in known)
            if missing:
                self.dangling[state_id] = tuple(missing)
        self.digraph.add_nodes_from(self.order)
        self.digraph.add_edges_from((state_id, target) for state_id in self.order for target in self.targets[state_id])

    def reachable(self) -> set[str]:
        entry = self.order[0]
        return nx.descendants(self.digraph, entry) | {entry}

    def components(self, reachable: set[str]) -> list[list[str]]:
        """Strongly connected components of the reachable subgraph in topological order, earliest declared first."""
        condensed = nx.condensation(self.digraph.subgraph(reachable))
        members = {
            number: sorted(condensed.nodes[number]["members"], key=self.position.__getitem__) for number in condensed
        }
        ordered = nx.lexicographical_topological_sort(condensed, key=lambda number: self.position[members[number][0]])
        return [members[number] for number in ordered]

    def body_order(self, component: list[str], members: frozenset[str], inbound: Mapping[str, Any]) -> list[str]:
        """Body order: breadth-first from the nodes an outside edge reaches, earliest declared first."""
        declared = sorted(component, key=lambda state_id: self.position[state_id])
        heads = [state_id for state_id in declared if inbound.get(state_id)] or declared[:1]
        seen = set(heads)
        queue = list(heads)
        body: list[str] = []
        while queue:
            node = queue.pop(0)
            body.append(node)
            for target in self.targets.get(node, ()):
                if target in members and target not in seen:
                    seen.add(target)
                    queue.append(target)
        body.extend(state_id for state_id in declared if state_id not in seen)
        return body


def out_edges(state: WorkflowState) -> tuple[Edge, ...]:
    """The out-edges of one state: the targets of `leads_to()`, with the kind and the YAML path of each.

    The first kind that claims a target keeps it, so an iteration branch head stays ITERATION and a target that is
    both a condition branch and `state_id` keeps the ALTERNATIVE kind. A target of `leads_to()` that no `next` form
    below claims still gets an edge (SINGLE, at `next`). Only ITERATION and PARALLEL change what the pass does (they
    push a branch tag); SINGLE and ALTERNATIVE are descriptive, because any two contexts on the same tag are merged as
    alternatives anyway.
    """
    next_state = getattr(state, "next", None)
    if next_state is None:
        return ()
    claimed = list(_declared_targets(next_state))
    targets = _leads_to(next_state, claimed)
    edges: list[Edge] = []
    seen: set[str] = set()
    for target, kind, segments in claimed:
        if target in targets:
            _append_edge(edges, seen, target, kind, segments)
    for target in sorted(targets - seen):
        _append_edge(edges, seen, target, EdgeKind.SINGLE, ("next",))
    return tuple(edges)


def _declared_targets(next_state: object) -> Iterator[tuple[str, EdgeKind, tuple[str | int, ...]]]:
    """Every target a `next` form names, with its kind and YAML segments, in the order the kinds are claimed."""
    single = text_of(getattr(next_state, "state_id", None))
    if text_of(getattr(next_state, "iter_key", None)):
        # The schema requires `state_id` with `iter_key` and forbids `condition` beside it.
        yield single, EdgeKind.ITERATION, ("next", "state_id")
    state_ids = getattr(next_state, "state_ids", None)
    if isinstance(state_ids, list):
        for index, target in enumerate(state_ids):
            yield text_of(target), EdgeKind.PARALLEL, ("next", "state_ids", index)
    condition = getattr(next_state, "condition", None)
    if condition is not None:
        yield text_of(getattr(condition, "then", None)), EdgeKind.ALTERNATIVE, ("next", "condition", "then")
        yield text_of(getattr(condition, "otherwise", None)), EdgeKind.ALTERNATIVE, ("next", "condition", "otherwise")
    switch = getattr(next_state, "switch", None)
    if switch is not None:
        yield text_of(getattr(switch, "default", None)), EdgeKind.ALTERNATIVE, ("next", "switch", "default")
        for index, case in enumerate(getattr(switch, "cases", None) or ()):
            yield (
                text_of(getattr(case, "state_id", None)),
                EdgeKind.ALTERNATIVE,
                ("next", "switch", "cases", index, "state_id"),
            )
    yield single, EdgeKind.SINGLE, ("next", "state_id")


def _leads_to(next_state: object, claimed: list[tuple[str, EdgeKind, tuple[str | int, ...]]]) -> set[str]:
    """`leads_to()` of a `next` model; a stand-in without it, or one it cannot read, keeps the targets named above."""
    leads_to = getattr(next_state, "leads_to", None)
    if callable(leads_to):
        try:
            return {target for target in leads_to() if isinstance(target, str)}
        except (AttributeError, TypeError):
            pass
    return {target for target, _, _ in claimed}


def _append_edge(
    edges: list[Edge], seen: set[str], target: str, kind: EdgeKind, segments: tuple[str | int, ...]
) -> None:
    if not target or target in seen or target in _TERMINAL_TARGETS:
        return
    seen.add(target)
    edges.append(Edge(target=target, kind=kind, path=_yaml_path(segments), segments=segments))


def _yaml_path(segments: tuple[str | int, ...]) -> str:
    """`("next", "state_ids", 0)` -> `next.state_ids[0]`."""
    path = ""
    for segment in segments:
        if isinstance(segment, int):
            path += f"[{segment}]"
        elif path:
            path += f".{segment}"
        else:
            path = segment
    return path


def is_cyclic(component: list[str], targets: Mapping[str, tuple[str, ...]]) -> bool:
    return len(component) > 1 or component[0] in targets.get(component[0], ())
