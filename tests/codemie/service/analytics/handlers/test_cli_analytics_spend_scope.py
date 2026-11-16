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

"""Aggregate cost counts Personal-paid work-project sessions like any other, end to end through the five handlers
(EPMCDME-17260, which reverses the exclusion of EPMCDME-16440).

The reader is a fake that answers the same three-session fact set to every statement: one corporate session and two
Personal-paid ones. Every money value and its divisors cover all three; the `personal_billed` buckets stay a breakdown
of that total, not an addition to it.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs.llm_config import CostConfig
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.support.bounded import bounded_to_itself
from tests.codemie.repository.cli_analytics.support.grouped_money import fake_cost_money, price_groups
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE
from tests.codemie.repository.cli_analytics.support.users_reads import install_users_reads

CATALOG = "codemie.service.analytics.handlers.coding_agent_pricing.llm_service.get_all_llm_model_info"
MODEL = "claude-haiku"
# input $1 per 1M tokens: 1_000_000 input tokens cost exactly $1.
SESSIONS = {
    # the only spend session; `route` is the client-asserted funding route (absent = NULL, read as unknown)
    "corp": {"dev": "alice", "repo": "repo-a", "paid": "corporate", "tokens": 1_000_000, "route": "api_based"},
    # Personal-paid, work project
    "pers1": {"dev": "bob", "repo": "repo-b", "paid": "personal", "tokens": 2_000_000, "route": "subscription"},
    "pers2": {"dev": "bob", "repo": "repo-b", "paid": "personal", "tokens": 1_000_000},
}
CORPORATE_COST = 1.0
PERSONAL_COST = 3.0


def make_filter():
    return LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31))


def catalog():
    model = MagicMock()
    model.base_name = model.deployment_name = MODEL  # a string, so a model missing from the catalog is unpriced
    model.cost = CostConfig(input=1e-6, output=5e-6, cache_read_input_token_cost=1e-7)
    return patch(CATALOG, return_value=[model])


def handler_over(sessions):
    """A handler over a reader that answers `sessions`; `calls` records every read of the per-session model rows."""
    calls: list[str] = []

    def row(sid, s):
        model = s.get("model", MODEL)
        return {
            "session_id": sid,
            "model_name": model,
            "normalized_model_name": model,
            "input_tokens": s["tokens"],
            "cache_read_tokens": s.get("cache", 0),
        }

    async def model_costs(_f):
        calls.append("model_costs")
        return [row(sid, s) for sid, s in sessions.items()]

    async def funding_costs(_f):
        # as the SQL: every selected session, NULL route read as unknown, the billing class as a flag
        return [
            {
                **row(sid, s),
                "funding_route": s.get("route") or "unknown",
                "personal_billed": s["paid"] == "personal",
                "subscription_type": s["paid"],
            }
            for sid, s in sessions.items()
        ]

    by_dev: dict[str, list[str]] = {}
    for sid, s in sessions.items():
        by_dev.setdefault(s["dev"], []).append(sid)

    repo = MagicMock()
    repo.materialize_sel = AsyncMock(return_value=None)
    repo.get_cost_models = AsyncMock(return_value=[])
    money_rows = [
        {**row(sid, s), "funding_route": s.get("route"), "personal_billed": s["paid"] == "personal"}
        for sid, s in sessions.items()
    ]
    devs = {sid: s["dev"] for sid, s in sessions.items()}
    repo.get_cost_money = fake_cost_money(money_rows, devs)
    repo.get_session_model_costs = model_costs
    repo.get_session_funding_costs = funding_costs
    repo.get_cost_kpis = AsyncMock(return_value=[{"total_sessions": len(sessions), "spend_sessions": len(sessions)}])
    repo.get_cost_by_user = AsyncMock(return_value=[{"developer_name": d} for d in by_dev])
    install_users_reads(
        repo,
        [{"developer_name": d, "session_count": len(ids)} for d, ids in by_dev.items()],
        money=lambda: price_groups(money_rows, devs),
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
    repo.get_window_model_flags = AsyncMock(return_value=[(MODEL, (True, False, False, False, False))])
    for name in (
        "get_model_breakdown",
        "get_lines_totals",
        "get_lines_daily",
        "get_lines_by_session",
        "get_turns_by_session",
        "get_file_facts_by_session",  # no session changed a file: every priced session is dead
        "get_tool_success_by_session",
        "get_session_durations",
        "get_class_lists",
        "get_skill_names_by_session",
    ):
        setattr(repo, name, AsyncMock(return_value=[]))
    return LocalAnalyticsHandler(bounded_to_itself(repo)), calls


@pytest.mark.asyncio
async def test_cost_counts_personal_paid_and_divides_by_every_priced_session():
    handler, calls = handler_over(SESSIONS)
    with catalog():
        data, _ = await handler.get_cost(make_filter())

    kpis = data["kpis"]
    assert kpis["total_sessions"] == 3
    assert kpis["total_cost_usd"] == pytest.approx(CORPORATE_COST + PERSONAL_COST)
    assert kpis["avg_cost_per_session"] == pytest.approx((CORPORATE_COST + PERSONAL_COST) / 3)
    users = {r["developer_name"]: r["cost_usd"] for r in data["cost_by_user"]}
    assert users == {"alice": pytest.approx(CORPORATE_COST), "bob": pytest.approx(PERSONAL_COST)}
    assert [(r["model_name"], r["cost_usd"]) for r in data["cost_by_model"]] == [
        (MODEL, pytest.approx(CORPORATE_COST + PERSONAL_COST))
    ]
    assert calls == []  # the money is grouped in one statement, not priced from session rows


@pytest.mark.asyncio
async def test_cost_with_only_personal_paid_sessions_is_their_cost():
    only_personal = {k: v for k, v in SESSIONS.items() if v["paid"] == "personal"}
    handler, _ = handler_over(only_personal)
    with catalog():
        data, _ = await handler.get_cost(make_filter())

    assert data["kpis"]["total_sessions"] == 2
    assert data["kpis"]["total_cost_usd"] == pytest.approx(PERSONAL_COST)
    assert data["kpis"]["avg_cost_per_session"] == pytest.approx(PERSONAL_COST / 2)
    assert [r["cost_usd"] for r in data["cost_by_user"]] == [pytest.approx(PERSONAL_COST)]


@pytest.mark.asyncio
async def test_overview_est_cost_counts_personal_paid_as_every_tab_does():
    handler, calls = handler_over(SESSIONS)
    with catalog():
        data, _, _ = await handler.get_overview(make_filter())

    assert data["kpis"]["total_sessions"] == 3
    assert data["kpis"]["total_cost_usd"] == pytest.approx(CORPORATE_COST + PERSONAL_COST)
    assert data["kpis"]["dead_sessions"] == 3  # no session changed a file: each priced one is dead, Personal-paid too
    assert calls == ["model_costs"]


# Overview splits the cost in two: API is the sessions routed through the CodeMie proxy
# (`subscription_type` codemie), Subscription is every other class, Personal-paid included. Est. Cost is their sum.
API_SESSIONS = {
    **SESSIONS,
    "api": {"dev": "carol", "repo": "repo-c", "paid": "codemie", "tokens": 4_000_000, "route": "api_based"},
    "unk": {"dev": "dave", "repo": "repo-d", "paid": "unknown", "tokens": 5_000_000},
}
API_COST = 4.0
SUBSCRIPTION_COST = CORPORATE_COST + PERSONAL_COST + 5.0  # corporate, personal and unknown


@pytest.mark.asyncio
@pytest.mark.parametrize("include_buckets", [True, False])
async def test_overview_splits_api_from_subscription_and_est_cost_is_their_sum(include_buckets):
    handler, _ = handler_over(API_SESSIONS)
    with catalog():
        data, _, _ = await handler.get_overview(make_filter(), include_buckets=include_buckets)

    kpis = data["kpis"]
    assert kpis["api_cost_usd"] == pytest.approx(API_COST)
    assert kpis["subscription_cost_usd"] == pytest.approx(SUBSCRIPTION_COST)
    assert kpis["total_cost_usd"] == pytest.approx(API_COST + SUBSCRIPTION_COST)


# cache read is $0.10 per 1M tokens: the spend session costs 1 + 1 and the personal one 1 + 3
CACHE_SESSIONS = {
    "corp": {"dev": "alice", "repo": "repo-a", "paid": "corporate", "tokens": 1_000_000, "cache": 10_000_000},
    "pers": {"dev": "bob", "repo": "repo-b", "paid": "personal", "tokens": 1_000_000, "cache": 30_000_000},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("include_buckets", [True, False])
async def test_overview_cache_reread_cost_and_bloat_are_over_the_total_cost_personal_included(include_buckets):
    handler, _ = handler_over(CACHE_SESSIONS)
    with catalog():
        data, _, _ = await handler.get_overview(make_filter(), include_buckets=include_buckets)

    kpis = data["kpis"]
    assert kpis["total_cost_usd"] == pytest.approx(6.0)
    assert kpis["cache_read_cost_usd"] == pytest.approx(4.0)  # the spend session's 1 and the personal one's 3
    assert kpis["bloat_pct"] == 66.7  # 4 of 6


@pytest.mark.asyncio
async def test_overview_with_only_personal_sessions_is_all_subscription():
    only_personal = {sid: s for sid, s in SESSIONS.items() if s["paid"] == "personal"}
    handler, _ = handler_over(only_personal)
    with catalog():
        data, _, _ = await handler.get_overview(make_filter(), include_buckets=False)

    kpis = data["kpis"]
    assert (kpis["api_cost_usd"], kpis["subscription_cost_usd"]) == (0.0, pytest.approx(PERSONAL_COST))
    assert kpis["total_cost_usd"] == pytest.approx(PERSONAL_COST)


@pytest.mark.asyncio
async def test_users_cost_counts_personal_paid():
    handler, calls = handler_over(SESSIONS)
    with catalog():
        data, _ = await handler.get_users(make_filter(), None, None)

    rows = {r["developer_name"]: r for r in data["rows"]}
    assert set(rows) == {"alice", "bob"}
    assert rows["alice"]["cost_usd"] == pytest.approx(CORPORATE_COST)
    assert rows["bob"]["cost_usd"] == pytest.approx(PERSONAL_COST)
    assert (rows["alice"]["session_count"], rows["bob"]["session_count"]) == (1, 2)
    assert calls == []  # the users' money is grouped in one statement, not priced from session rows


@pytest.mark.asyncio
async def test_repositories_cost_counts_personal_paid():
    handler, calls = handler_over(SESSIONS)
    with catalog():
        data, _ = await handler.get_repositories(make_filter(), 0, 50, include_branches=False)

    rows = {r["repository"]: r for r in data["rows"]}
    assert set(rows) == {"repo-a", "repo-b"}
    assert rows["repo-a"]["cost_usd"] == pytest.approx(CORPORATE_COST)
    assert rows["repo-b"]["cost_usd"] == pytest.approx(PERSONAL_COST)
    assert rows["repo-b"]["session_count"] == 2
    assert calls == ["model_costs"]


@pytest.mark.asyncio
async def test_efficiency_cost_and_dead_sessions_count_personal_paid():
    handler, calls = handler_over(SESSIONS)
    with catalog():
        data, _ = await handler.get_efficiency(make_filter())

    dead = data["dead_sessions"]
    assert data["kpis"]["total_cost_usd"] == pytest.approx(CORPORATE_COST + PERSONAL_COST)
    assert dead["count"] == 3  # no session changed a file
    assert dead["wasted_cost_usd"] == pytest.approx(CORPORATE_COST + PERSONAL_COST)
    assert dead["avg_cost_per_dead"] == pytest.approx((CORPORATE_COST + PERSONAL_COST) / 3)
    assert dead["pct_of_sessions"] == 100.0  # 3 of 3 priced sessions
    assert sum(b["count"] for b in data["session_depth"]) == 3
    assert calls == ["model_costs"]


@pytest.mark.asyncio
async def test_efficiency_with_only_personal_paid_sessions_counts_them_as_dead():
    only_personal = {k: v for k, v in SESSIONS.items() if v["paid"] == "personal"}
    handler, _ = handler_over(only_personal)
    with catalog():
        data, _ = await handler.get_efficiency(make_filter())

    assert data["dead_sessions"]["count"] == 2
    assert data["dead_sessions"]["pct_of_sessions"] == 100.0
    assert data["dead_sessions"]["wasted_cost_usd"] == pytest.approx(PERSONAL_COST)
    assert data["dead_sessions"]["avg_cost_per_dead"] == pytest.approx(PERSONAL_COST / 2)
    assert sum(b["count"] for b in data["session_depth"]) == 2


@pytest.mark.asyncio
async def test_the_session_list_keeps_the_personal_paid_sessions_with_their_own_cost():
    """All three are listed and a Personal-paid session shows its own cost."""
    handler, calls = handler_over(SESSIONS)
    with catalog():
        data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)

    costs = {s["trace_id"]: s["cost_usd"] for s in data["sessions"]}
    assert costs == {"corp": pytest.approx(1.0), "pers1": pytest.approx(2.0), "pers2": pytest.approx(1.0)}
    assert calls == ["model_costs"]
