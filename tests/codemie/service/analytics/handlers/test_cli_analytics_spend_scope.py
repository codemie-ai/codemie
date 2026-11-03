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

"""Aggregate spend excludes Personal-paid work-project sessions end to end through the five handlers
(EPMCDME-16440, review CR-008 / CR-009).

The reader is a fake that honours `spend_only` the way the SQL does (`_spend`): it drops Personal-paid sessions from
the priced model rows and counts only the others in `spend_sessions`. Everything else it answers is the same
three-session fact set, so activity stays visible and only the money and its divisors can move. Dropping the flag at
any call site, or dividing by `total_sessions`, changes an asserted value.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs.llm_config import CostConfig
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

CATALOG = "codemie.service.analytics.handlers.coding_agent_pricing.llm_service.get_all_llm_model_info"
MODEL = "claude-haiku"
# input $1 per 1M tokens: 1_000_000 input tokens cost exactly $1.
SESSIONS = {
    "corp": {"dev": "alice", "repo": "repo-a", "paid": "corporate", "tokens": 1_000_000},  # the only spend session
    "pers1": {"dev": "bob", "repo": "repo-b", "paid": "personal", "tokens": 2_000_000},  # Personal-paid, work project
    "pers2": {"dev": "bob", "repo": "repo-b", "paid": "personal", "tokens": 1_000_000},
}
CORPORATE_COST = 1.0
PERSONAL_COST = 3.0


def make_filter():
    return LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31))


def catalog():
    model = MagicMock()
    model.base_name = MODEL
    model.cost = CostConfig(input=1e-6, output=5e-6)
    return patch(CATALOG, return_value=[model])


def handler_over(sessions):
    """A handler over a reader that answers `sessions` and honours `spend_only`; `calls` records every flag."""
    spend = {sid for sid, s in sessions.items() if s["paid"] != "personal"}
    calls: list[bool] = []

    async def model_costs(_f, *, spend_only=False):
        calls.append(spend_only)
        return [
            {"session_id": sid, "model_name": MODEL, "normalized_model_name": MODEL, "input_tokens": s["tokens"]}
            for sid, s in sessions.items()
            if not spend_only or sid in spend
        ]

    by_dev: dict[str, list[str]] = {}
    for sid, s in sessions.items():
        by_dev.setdefault(s["dev"], []).append(sid)

    repo = MagicMock()
    repo.get_session_model_costs = model_costs
    repo.get_cost_kpis = AsyncMock(return_value=[{"total_sessions": len(sessions), "spend_sessions": len(spend)}])
    repo.get_cost_by_user = AsyncMock(
        return_value=[{"developer_name": d, "session_ids": ids} for d, ids in by_dev.items()]
    )
    repo.get_users = AsyncMock(
        return_value=[{"developer_name": d, "session_ids": ids, "session_count": len(ids)} for d, ids in by_dev.items()]
    )
    repo.get_session_cost_facts = AsyncMock(
        return_value=[
            {
                "session_id": sid,
                "model_name": MODEL,
                "input_tokens": s["tokens"],
                "repository": s["repo"],
                "branch": "main",
                "project_name": "work",
                "started_at": datetime(2026, 1, 1),
                "last_event_at": datetime(2026, 1, 1, 0, 1),
            }
            for sid, s in sessions.items()
        ]
    )
    repo.get_session_page = AsyncMock(return_value=(len(sessions), list(sessions)))
    for name in (
        "get_model_breakdown",
        "get_lines_totals",
        "get_lines_daily",
        "get_lines_by_user",
        "get_lines_by_session",
        "get_turns_by_session",
        "get_file_facts_by_session",  # no session changed a file: every priced session is dead
        "get_tool_success_by_session",
        "get_session_durations",
        "get_class_lists",
        "get_users_daily_activity",
        "get_users_last_active",
        "get_skill_names_by_session",
    ):
        setattr(repo, name, AsyncMock(return_value=[]))
    return LocalAnalyticsHandler(repo), calls


@pytest.mark.asyncio
async def test_cost_excludes_personal_paid_spend_and_divides_by_the_spend_sessions():
    handler, calls = handler_over(SESSIONS)
    with catalog():
        data, _ = await handler.get_cost(make_filter())

    kpis = data["kpis"]
    assert kpis["total_sessions"] == 3  # activity stays whole
    assert kpis["total_cost_usd"] == pytest.approx(CORPORATE_COST)
    assert kpis["avg_cost_per_session"] == pytest.approx(CORPORATE_COST / 1)  # not / 3
    users = {r["developer_name"]: r["cost_usd"] for r in data["cost_by_user"]}
    assert users == {"alice": pytest.approx(CORPORATE_COST), "bob": 0.0}
    assert [(r["model_name"], r["cost_usd"]) for r in data["cost_by_model"]] == [(MODEL, pytest.approx(CORPORATE_COST))]
    assert calls == [True]


@pytest.mark.asyncio
async def test_cost_without_any_spend_session_is_zero_and_keeps_every_session():
    only_personal = {k: v for k, v in SESSIONS.items() if v["paid"] == "personal"}
    handler, _ = handler_over(only_personal)
    with catalog():
        data, _ = await handler.get_cost(make_filter())

    assert data["kpis"]["total_sessions"] == 2
    assert data["kpis"]["total_cost_usd"] == 0.0
    assert data["kpis"]["avg_cost_per_session"] == 0.0
    assert [r["cost_usd"] for r in data["cost_by_user"]] == [0.0]


@pytest.mark.asyncio
async def test_overview_spend_excludes_personal_paid_and_keeps_the_activity_count():
    handler, calls = handler_over(SESSIONS)
    with catalog():
        data, _, _ = await handler.get_overview(make_filter())

    assert data["kpis"]["total_sessions"] == 3
    assert data["kpis"]["total_cost_usd"] == pytest.approx(CORPORATE_COST)
    assert data["kpis"]["dead_sessions"] == 1  # only the spend session can be dead
    assert calls == [True]


@pytest.mark.asyncio
async def test_users_cost_is_corporate_only_and_every_user_stays_listed():
    handler, calls = handler_over(SESSIONS)
    with catalog():
        data, _ = await handler.get_users(make_filter(), None, None)

    rows = {r["developer_name"]: r for r in data["rows"]}
    assert set(rows) == {"alice", "bob"}
    assert rows["alice"]["cost_usd"] == pytest.approx(CORPORATE_COST)
    assert rows["bob"]["cost_usd"] == 0.0
    assert (rows["alice"]["session_count"], rows["bob"]["session_count"]) == (1, 2)
    assert calls == [True]


@pytest.mark.asyncio
async def test_repositories_cost_is_corporate_only_and_every_repository_stays_listed():
    handler, calls = handler_over(SESSIONS)
    with catalog():
        data, _ = await handler.get_repositories(make_filter(), 0, 50, include_branches=False)

    rows = {r["repository"]: r for r in data["rows"]}
    assert set(rows) == {"repo-a", "repo-b"}
    assert rows["repo-a"]["cost_usd"] == pytest.approx(CORPORATE_COST)
    assert rows["repo-b"]["cost_usd"] == 0.0
    assert rows["repo-b"]["session_count"] == 2
    assert calls == [True]


@pytest.mark.asyncio
async def test_efficiency_dead_session_cost_and_percentage_use_the_spend_sessions():
    handler, calls = handler_over(SESSIONS)
    with catalog():
        data, _ = await handler.get_efficiency(make_filter())

    dead = data["dead_sessions"]
    assert data["kpis"]["total_cost_usd"] == pytest.approx(CORPORATE_COST)
    assert dead["count"] == 1
    assert dead["wasted_cost_usd"] == pytest.approx(CORPORATE_COST)
    assert dead["avg_cost_per_dead"] == pytest.approx(CORPORATE_COST / 1)
    assert dead["pct_of_sessions"] == 100.0  # 1 of 1 spend session, not 1 of 3 (33.3)
    assert sum(b["count"] for b in data["session_depth"]) == 3  # activity keeps all three
    assert calls == [True]


@pytest.mark.asyncio
async def test_efficiency_without_any_spend_session_has_no_dead_session_and_zero_percentage():
    only_personal = {k: v for k, v in SESSIONS.items() if v["paid"] == "personal"}
    handler, _ = handler_over(only_personal)
    with catalog():
        data, _ = await handler.get_efficiency(make_filter())

    assert data["dead_sessions"]["count"] == 0
    assert data["dead_sessions"]["pct_of_sessions"] == 0.0
    assert data["dead_sessions"]["wasted_cost_usd"] == 0
    assert data["dead_sessions"]["avg_cost_per_dead"] is None
    assert sum(b["count"] for b in data["session_depth"]) == 2


@pytest.mark.asyncio
async def test_the_session_list_keeps_the_personal_paid_sessions_with_their_own_cost():
    """The raw session view is not spend: all three are listed and a Personal-paid session shows its real cost."""
    handler, calls = handler_over(SESSIONS)
    with catalog():
        data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)

    costs = {s["trace_id"]: s["cost_usd"] for s in data["sessions"]}
    assert costs == {"corp": pytest.approx(1.0), "pers1": pytest.approx(2.0), "pers2": pytest.approx(1.0)}
    assert calls == [False]
