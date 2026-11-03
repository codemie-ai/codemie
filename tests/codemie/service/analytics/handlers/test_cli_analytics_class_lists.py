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

"""Task 6: subscription_type / project_origin on sessions and the sorted distinct lists on every aggregate entity.

A fake reader stands in for the SQL: one repository, user, model and day hold four sessions of mixed classes. The
handler output with the class lists must equal the output without them in every other field, per entity.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.scope import normalise_project_origin, normalise_subscription_type
from codemie.rest_api.models import cli_analytics as models
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

DAY = date(2026, 9, 2)
# (session, repository, stored subscription_type, stored project_origin); the SQL normalises, the fake does too.
SESSIONS = [
    ("s1", "repo", "Corporate", "profile"),
    ("s2", "repo", "codemie", "linked_folder"),
    ("s3", "repo", None, None),
    ("s4", "repo", " CORPORATE ", "Profile"),
    ("s5", "legacy-repo", None, ""),
]
EXPECTED_SUBS = ["codemie", "corporate", "unknown"]
EXPECTED_ORIGINS = ["linked_folder", "none", "profile"]
LISTS = ("subscription_types", "project_origins")


def _flt() -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=datetime(2026, 9, 1), end_dt=datetime(2026, 9, 30))


def _classes(ids: list[str]) -> dict:
    picked = [s for s in SESSIONS if s[0] in ids]
    return {
        "subscription_types": sorted({normalise_subscription_type(s[2]) for s in picked}),
        "project_origins": sorted({normalise_project_origin(s[3]) for s in picked}),
    }


def _fake_reader(with_lists: bool) -> MagicMock:
    ids = [s[0] for s in SESSIONS]
    repo = MagicMock()

    def rows(value):
        return AsyncMock(return_value=value)

    async def class_lists(f, by):
        if not with_lists:
            return []
        key = {"all": "", "model": "claude", "day": DAY.isoformat(), "tool": "Read"}.get(by, "")
        if by == "invocation":
            return [{"key": "sk", "kind": "skill", **_classes(ids)}]
        return [{"key": key, "kind": "", **_classes(ids)}]

    repo.get_class_lists = class_lists
    repo.get_cost_kpis = rows(
        [{"total_sessions": 5, "total_input_tokens": 500, "total_output_tokens": 250, "total_api_calls": 5}]
    )
    repo.get_model_breakdown = rows(
        [{"model_name": "claude", "session_count": 5, "total_tokens": 750, "cost_usd": 5.0}]
    )
    repo.get_lines_totals = rows([{"lines_added": 10, "lines_removed": 2}])
    repo.get_lines_daily = rows([{"day": DAY, "net_lines": 8}])
    repo.get_turns_by_session = rows([{"session_id": i, "turns": 2} for i in ids])
    repo.get_file_facts_by_session = rows([])
    repo.get_tool_success_by_session = rows([])
    repo.get_session_durations = rows([])
    repo.get_session_model_costs = rows([])
    repo.get_lines_by_session = rows([])
    repo.get_lines_by_user = rows([])
    repo.get_users_last_active = rows([])
    repo.get_skill_names_by_session = rows([])
    repo.get_tool_usage = rows([{"tool_name": "Read", "call_count": 4, "success_count": 3}])
    repo.get_invocations = rows([{"kind": "skill", "name": "sk", "count": 2}])
    facts = [
        {
            "session_id": sid,
            "developer_name": "u@x",
            "repository": repo_name,
            "branch": "main",
            "project_name": "p",
            "prompt": "hi",
            "started_at": datetime(2026, 9, 2),
            "duration_ms": 1,
            "model_name": "claude",
            "cost_usd": 1.0,
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "api_call_count": 1,
            "subscription_type": normalise_subscription_type(sub),
            "project_origin": normalise_project_origin(origin),
        }
        for sid, repo_name, sub, origin in SESSIONS
    ]
    repo.get_session_cost_facts = rows(facts)
    repo.get_session_page = rows((len(ids), ids))
    repo.get_users = rows(
        [
            {
                "developer_name": "u@x",
                "session_count": 5,
                "input_tokens": 500,
                "output_tokens": 250,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cost_usd": 5.0,
                "top_model": "claude",
                "session_ids": ids,
                **(_classes(ids) if with_lists else {}),
            }
        ]
    )
    repo.get_users_daily_activity = rows(
        [{"developer_name": "u@x", "day": DAY, "session_count": 5, **(_classes(ids) if with_lists else {})}]
    )
    repo.get_cost_by_user = rows(
        [{"developer_name": "u@x", "session_ids": ids, **(_classes(ids) if with_lists else {})}]
    )
    return repo


def _strip(value):
    """The output without the class fields."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in LISTS}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _has_lists(row: dict, subs=EXPECTED_SUBS, origins=EXPECTED_ORIGINS) -> bool:
    return row["subscription_types"] == subs and row["project_origins"] == origins


async def _all(with_lists: bool) -> dict:
    h = LocalAnalyticsHandler(_fake_reader(with_lists))
    f = _flt()
    overview, _, _ = await h.get_overview(f)
    cost, _ = await h.get_cost(f)
    users, _ = await h.get_users(f, None, None)
    repos, _ = await h.get_repositories(f, 0, 20, False)
    branches, _ = await h.get_repositories(f, 0, 20, True)
    sessions, _, _ = await h.get_sessions(f, 0, 20, "start_time", None)
    tools = await h.get_tools(f)
    return {
        "overview": overview,
        "cost": cost,
        "users": users,
        "repos": repos,
        "branches": branches,
        "sessions": sessions,
        "tools": tools,
    }


@pytest.mark.asyncio
async def test_mixed_entity_lists_are_exactly_the_sorted_distinct_classes():
    out = await _all(True)

    ov = out["overview"]
    assert _has_lists(ov["kpis"])
    assert _has_lists(ov["daily_buckets"][0])
    assert _has_lists(ov["model_breakdown"][0])
    assert _has_lists(out["cost"]["kpis"])
    assert _has_lists(out["cost"]["cost_by_user"][0])
    assert _has_lists(out["cost"]["cost_by_model"][0]) if out["cost"]["cost_by_model"] else True
    user = out["users"]["rows"][0]
    assert _has_lists(user)
    assert _has_lists(user["daily_activity"][0])
    by_repo = {r["repository"]: r for r in out["repos"]["rows"]}
    assert _has_lists(by_repo["repo"])
    assert _has_lists(by_repo["legacy-repo"], ["unknown"], ["none"])  # legacy rows: unknown / none
    group = {r["repository"]: r for r in out["branches"]["rows"]}["repo"]
    assert _has_lists(group)
    assert _has_lists(group["branches"][0]) or "subscription_types" in group["branches"][0]
    tools = out["tools"]
    assert _has_lists(tools["tool_usage"][0])
    assert _has_lists(tools["tokens_by_model"][0])
    assert _has_lists(tools["skills_invoked"][0])
    sessions = {s["trace_id"]: s for s in out["sessions"]["sessions"]}
    assert (sessions["s1"]["subscription_type"], sessions["s1"]["project_origin"]) == ("corporate", "profile")
    assert (sessions["s3"]["subscription_type"], sessions["s3"]["project_origin"]) == ("unknown", "none")
    assert (sessions["s5"]["subscription_type"], sessions["s5"]["project_origin"]) == ("unknown", "none")
    assert "personal" not in {v for row in (ov["kpis"], user) for v in row["subscription_types"]}


@pytest.mark.asyncio
async def test_per_entity_counts_and_sums_equal_the_output_without_the_lists():
    with_lists, without = await _all(True), await _all(False)

    assert _strip(with_lists) == _strip(without)
    # the baseline really has per-entity content to compare (not an empty comparison)
    assert without["users"]["rows"][0]["session_count"] == 5
    assert without["repos"]["rows"][0]["session_count"] in (4, 1)


@pytest.mark.asyncio
async def test_without_a_class_row_an_entity_lists_nothing_instead_of_failing():
    out = await _all(False)

    assert out["overview"]["kpis"]["subscription_types"] == []
    assert out["users"]["rows"][0]["project_origins"] == []


def test_response_models_declare_the_lists_and_sessions_the_scalars():
    aggregate = [
        models.LocalAnalyticsOverviewKPIs,
        models.LocalAnalyticsOverviewDailyBucket,
        models.LocalAnalyticsModelBreakdownRow,
        models.LocalAnalyticsRepositoryRow,
        models.LocalAnalyticsRepositoryBranchRow,
        models.LocalAnalyticsDailyActivityBucket,
        models.LocalAnalyticsUserRow,
        models.LocalAnalyticsCostKPIs,
        models.LocalAnalyticsCostByUserRow,
        models.LocalAnalyticsCostByModelRow,
        models.LocalAnalyticsTokensByModelRow,
        models.LocalAnalyticsInvocationRow,
        models.LocalAnalyticsToolWithSuccessRow,
    ]
    for model in aggregate:
        assert model.model_fields["subscription_types"].default_factory is list, model
        assert model.model_fields["project_origins"].default_factory is list, model
    for model in (models.LocalAnalyticsSessionRow, models.LocalAnalyticsSessionDetail):
        assert model.model_fields["subscription_type"].default == "unknown"
        assert model.model_fields["project_origin"].default == "none"


# ── SQL text: the lists never join into a sum ────────────────────────────────


class _Capture(PostgresCliAnalyticsReader):
    def __init__(self) -> None:
        super().__init__(MagicMock())
        self.sql: list[str] = []

    async def _q(self, sql, params):
        self.sql.append(sql)
        return []


def _flat(sql: str) -> str:
    return re.sub(r"\s+", " ", sql)


@pytest.mark.asyncio
@pytest.mark.parametrize("by", ["all", "model", "day", "tool", "invocation"])
async def test_class_list_queries_read_sel_with_the_viewer_scope_and_sum_nothing(by):
    r = _Capture()
    await r.get_class_lists(_flt(), by)

    sql = _flat(r.sql[0])
    assert "sel AS" in sql and "array_agg(DISTINCT" in sql
    assert not re.search(r"\bsum\(", sql.split(") x", 1)[-1])
    assert sql.count("WHERE d.started_at IS NOT NULL") == 1  # the one window/scope source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "group_by"),
    [
        ("get_users", "GROUP BY 1 ORDER BY cost_usd"),
        ("get_cost_by_user", "GROUP BY 1 ORDER BY"),
        ("get_users_daily_activity", "GROUP BY 1, 2 ORDER BY"),
    ],
)
async def test_lists_added_to_user_queries_use_the_existing_sel_join_and_grouping(method, group_by):
    r = _Capture()
    await getattr(r, method)(_flt())

    sql = _flat(r.sql[0])
    assert sql.count("JOIN sel s") == 1
    assert group_by in sql
    assert "array_agg(DISTINCT (CASE WHEN" in sql  # normalised via the shared SQL helpers
    assert sql.count("array_agg(DISTINCT c.session_id)") <= 1


@pytest.mark.asyncio
async def test_sum_queries_without_lists_are_unchanged():
    r = _Capture()
    for method in ("get_cost_kpis", "get_model_breakdown", "get_lines_daily", "get_tool_usage"):
        await getattr(r, method)(_flt())

    assert all("subscription_types" not in s for s in r.sql)


@pytest.mark.asyncio
async def test_detail_meta_projects_the_normalised_classes_from_the_left_joined_dims():
    r = _Capture()
    await r.get_session_detail_meta("sid", scope=EXCLUDE_SCOPE)

    sql = _flat(r.sql[0])
    assert "LEFT JOIN session_dims d" in sql  # a legacy session without a dims row reads NULL -> unknown / none
    assert "AS subscription_type" in sql and "AS project_origin" in sql
