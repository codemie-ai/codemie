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

"""Web Platform funding: when a shared project's platform budget paid for each member (architecture.md §7.2).

Web Platform records carry no LiteLLM customer, so who paid is rebuilt from the app DB with the rule the platform
applies to every request (``budget_resolution_service``): the project has a platform budget assigned, the member has
an allocation of it, and the budget is active (budgets are stopped while their project or cost center is inactive).
Assignments and allocations are soft-deleted and stops are activity events, so each part has dates: a member counts
as funded only inside them. CLI records need none of this: they carry the customer they were billed to.

Facts (``app_db.load_funding``), with timestamps as datetimes, ISO strings or epoch milliseconds:

- ``assignments``: ``[project, budget id, assigned at, deleted at, budget active now, budget deleted at]``
- ``allocations``: ``[project, user id, assigned at, deleted at]``
- ``stops``: ``[budget id, "stopped" | "restored", at]``
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

STOPPED = "stopped"
RESTORED = "restored"

Interval = tuple[int, int]  # epoch milliseconds, the end excluded

_LOW = 0
_HIGH = 2**62


def to_ms(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def union(intervals: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for start, end in sorted(iv for iv in intervals if iv[0] < iv[1]):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for a0, a1 in union(a):
        for b0, b1 in union(b):
            start, end = max(a0, b0), min(a1, b1)
            if start < end:
                out.append((start, end))
    return union(out)


def subtract(a: list[Interval], b: list[Interval]) -> list[Interval]:
    out = union(a)
    for b0, b1 in union(b):
        rest: list[Interval] = []
        for a0, a1 in out:
            if b1 <= a0 or a1 <= b0:
                rest.append((a0, a1))
                continue
            if a0 < b0:
                rest.append((a0, b0))
            if b1 < a1:
                rest.append((b1, a1))
        out = rest
    return out


def _stop_events(stops: list[list]) -> dict[str, list[tuple[int, str]]]:
    """Valid, timestamped stop/restore events, grouped by budget and sorted by time."""
    events: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for budget_id, kind, at in stops:
        at_ms = to_ms(at)
        if at_ms is not None and kind in (STOPPED, RESTORED):
            events[str(budget_id)].append((at_ms, kind))
    for evs in events.values():
        evs.sort()
    return events


def _stopped_from_events(evs: list[tuple[int, str]], active: bool) -> list[Interval]:
    """When a single budget was stopped, given its sorted events and current active state."""
    found: list[Interval] = []
    since = _LOW if evs[0][1] == RESTORED else None
    for at, kind in evs:
        if kind == STOPPED and since is None:
            since = at
        elif kind == RESTORED and since is not None:
            found.append((since, at))
            since = None
    if since is not None:
        found.append((since, _HIGH))
    elif not active:
        found.append((evs[-1][0], _HIGH))
    return union(found)


def stopped_intervals(stops: list[list], active_now: dict[str, bool]) -> dict[str, list[Interval]]:
    """When each budget was stopped. Events give the dates. A budget with no event keeps its current state for the
    whole range, and one that is stopped now although its last event restored it is stopped since that event."""
    events = _stop_events(stops)
    out: dict[str, list[Interval]] = {}
    for budget_id, active in active_now.items():
        evs = events.get(budget_id, ())
        if not evs:
            if not active:
                out[budget_id] = [(_LOW, _HIGH)]
            continue
        out[budget_id] = _stopped_from_events(evs, active)
    return out


def _budgeted_intervals(assignments: list, stopped: dict[str, list[Interval]]) -> dict[str, list[Interval]]:
    """Per project, when a platform budget assigned to it was live and not stopped."""
    budgeted: dict[str, list[Interval]] = defaultdict(list)
    for project, budget_id, assigned_at, deleted_at, _active, budget_deleted_at in assignments:
        ends = [x for x in (to_ms(deleted_at), to_ms(budget_deleted_at)) if x is not None]
        live = [(to_ms(assigned_at) or _LOW, min(ends) if ends else _HIGH)]
        budgeted[str(project)] += subtract(live, stopped.get(str(budget_id), []))
    return budgeted


def _allocated_intervals(
    allocations: list, budgeted: dict[str, list[Interval]]
) -> dict[tuple[str, str], list[Interval]]:
    """Per (project, user), when the user had an allocation of a budgeted project, limited to budgeted projects."""
    allocated: dict[tuple[str, str], list[Interval]] = defaultdict(list)
    for project, uid, assigned_at, deleted_at in allocations:
        if str(project) in budgeted:
            allocated[(str(project), str(uid))].append((to_ms(assigned_at) or _LOW, to_ms(deleted_at) or _HIGH))
    return allocated


def funded_intervals(facts: dict[str, Any] | None, start: datetime, end: datetime) -> dict[str, dict[str, list[list]]]:
    """``{project: {user id: [[from, to], ...]}}``: when the project's platform budget paid for that member within the
    range, in epoch milliseconds."""
    facts = facts or {}
    window = [(to_ms(start) or 0, to_ms(end) or 0)]
    assignments = facts.get("assignments") or []
    stopped = stopped_intervals(facts.get("stops") or [], {str(row[1]): bool(row[4]) for row in assignments})
    budgeted = _budgeted_intervals(assignments, stopped)
    allocated = _allocated_intervals(facts.get("allocations") or [], budgeted)
    out: dict[str, dict[str, list[list]]] = {}
    for (project, uid), intervals in allocated.items():
        funded = intersect(intersect(intervals, budgeted[project]), window)
        if funded:
            out.setdefault(project, {})[uid] = [[a, b] for a, b in funded]
    return out


def plan(funded: dict[str, dict[str, list[list]]], start: datetime, end: datetime) -> dict[str, Any]:
    """What the Elasticsearch request needs: the projects with a funded member, the members funded for the whole
    range, and the members funded for part of it, grouped by project and intervals (one filter per group)."""
    whole = [[to_ms(start), to_ms(end)]]
    full: dict[str, list[str]] = {}
    groups: dict[tuple[str, tuple], list[str]] = defaultdict(list)
    for project in sorted(funded):
        for uid, intervals in sorted(funded[project].items()):
            if intervals == whole:
                full.setdefault(project, []).append(uid)
            else:
                groups[(project, tuple(tuple(iv) for iv in intervals))].append(uid)
    return {
        "projects": sorted(funded),
        "full": full,
        "groups": [[project, uids, [list(iv) for iv in ivs]] for (project, ivs), uids in sorted(groups.items())],
    }


def summary(fplan: dict[str, Any]) -> dict[str, int]:
    """Counts for How It Works."""
    partial = {(g[0], uid) for g in fplan.get("groups") or [] for uid in g[1]}
    return {
        "projects": len(fplan.get("projects") or []),
        "members_whole_range": sum(len(v) for v in (fplan.get("full") or {}).values()),
        "members_part_of_range": len(partial),
    }
