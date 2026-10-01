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

"""Deterministic output of LocalAnalyticsHandler whatever order the storage returns rows in.

Both storage adapters must produce identical API responses, so no response may depend on
the row order of a query that has no total ORDER BY (design D1, D3).
"""

from __future__ import annotations

from datetime import datetime
from itertools import permutations
from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler, depth_bucket

T0 = datetime(2026, 9, 1, 10, 0, 0)
T1 = datetime(2026, 9, 1, 11, 0, 0)


def _flt() -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(start_dt=datetime(2026, 9, 1), end_dt=datetime(2026, 9, 8))


def _cost_row(sid: str, **overrides) -> dict:
    row = {
        "session_id": sid,
        "developer_name": "dev@example.com",
        "repository": "",
        "branch": "",
        "project_name": "",
        "prompt": f"prompt {sid}",
        "started_at": T0,
        "last_event_at": T1,
        "model_name": "claude-sonnet",
        "cost_usd": 1.0,
        "input_tokens": 10,
        "output_tokens": 10,
        "cache_read_tokens": 100,
        "cache_creation_tokens": 0,
    }
    row.update(overrides)
    return row


def _handler(cost_facts=(), turns=(), invocations=()) -> LocalAnalyticsHandler:
    repo = MagicMock()
    for name in (
        "get_turns_by_session",
        "get_tool_success_by_session",
        "get_lines_by_session",
        "get_model_breakdown",
        "get_skill_names_by_session",
        "get_file_facts_by_session",
        "get_lines_totals",
        "get_tool_usage",
    ):
        setattr(repo, name, AsyncMock(return_value=[]))
    repo.get_session_cost_facts = AsyncMock(return_value=list(cost_facts))
    repo.get_turns_by_session = AsyncMock(return_value=list(turns))
    repo.get_invocations = AsyncMock(return_value=list(invocations))
    repo.get_cost_kpis = AsyncMock(return_value=[{"total_sessions": len(cost_facts), "total_cost_usd": 1.0}])
    return LocalAnalyticsHandler(repo)


@pytest.mark.parametrize(
    ("turns", "bucket"),
    [(0, "1"), (1, "1"), (2, "2-5"), (5, "2-5"), (6, "6-10"), (10, "6-10"), (11, "11-25"), (25, "11-25")]
    + [(26, "26-50"), (50, "26-50"), (51, "50+")],
)
def test_depth_bucket_boundaries(turns, bucket):
    assert depth_bucket(turns) == bucket


@pytest.mark.asyncio
async def test_sessions_with_equal_start_times_are_ordered_by_trace_id():
    rows = [_cost_row("b"), _cost_row("a"), _cost_row("c", started_at=T1)]
    orders = set()
    for perm in permutations(rows):
        data, _, _ = await _handler(cost_facts=perm).get_sessions(_flt(), 0, 20, "start_time", None)
        orders.add(tuple(s["trace_id"] for s in data["sessions"]))

    assert orders == {("c", "a", "b")}


@pytest.mark.asyncio
async def test_sessions_with_equal_cost_are_ordered_by_trace_id():
    rows = [_cost_row("b", cost_usd=2.0), _cost_row("a", cost_usd=2.0), _cost_row("c", cost_usd=1.0)]
    orders = set()
    for perm in permutations(rows):
        data, _, _ = await _handler(cost_facts=perm).get_sessions(_flt(), 0, 20, "cost_usd", None)
        orders.add(tuple(s["trace_id"] for s in data["sessions"]))

    assert orders == {("a", "b", "c")}


@pytest.mark.asyncio
async def test_repositories_with_equal_sessions_and_cost_are_ordered_by_name_then_branch():
    rows = [
        _cost_row("s1", repository="repo-b", branch="main"),
        _cost_row("s2", repository="repo-a", branch="main"),
        _cost_row("s3", repository="repo-a", branch="dev"),
    ]
    orders = set()
    for perm in permutations(rows):
        data = await _handler(cost_facts=perm).get_repositories(_flt(), 0, 20, True)
        orders.add(tuple((r["repository"], r["branch"]) for r in data["rows"]))

    assert orders == {(("repo-a", "dev"), ("repo-a", "main"), ("repo-b", "main"))}


@pytest.mark.parametrize(
    ("projects", "label"),
    [
        (["p2", "p1", "p2"], "p2"),  # most sessions wins
        (["p2", "p1"], "p1"),  # a tie goes to the alphabetically first
        (["", "p3", ""], "p3"),  # sessions without a project do not count
        (["", ""], None),
    ],
)
@pytest.mark.asyncio
async def test_repository_project_label_is_the_most_frequent_project(projects, label):
    rows = [_cost_row(f"s{i}", repository="repo", project_name=p) for i, p in enumerate(projects)]
    labels = set()
    for perm in permutations(rows):
        data = await _handler(cost_facts=perm).get_repositories(_flt(), 0, 20, False)
        labels.add(data["rows"][0]["project_name"])

    assert labels == {label}


@pytest.mark.asyncio
async def test_worst_context_session_tie_goes_to_the_lowest_session_id():
    rows = [_cost_row("b"), _cost_row("a"), _cost_row("c", cache_read_tokens=1)]
    turns = [{"session_id": sid, "turns": 1} for sid in "abc"]
    worst = set()
    for perm in permutations(rows):
        data, _ = await _handler(cost_facts=perm, turns=turns).get_efficiency(_flt())
        worst.add((data["kpis"]["worst_session_trace_id"], data["kpis"]["worst_session_prompt"]))

    assert worst == {("a", "prompt a")}


@pytest.mark.asyncio
async def test_invocations_with_equal_counts_are_ordered_by_name():
    rows = [
        {"kind": "skill", "name": "beta", "count": 2},
        {"kind": "skill", "name": "alpha", "count": 2},
        {"kind": "skill", "name": "gamma", "count": 5},
    ]
    orders = set()
    for perm in permutations(rows):
        data = await _handler(invocations=perm).get_tools(_flt())
        orders.add(tuple(i["name"] for i in data["skills_invoked"]))

    assert orders == {("gamma", "alpha", "beta")}
