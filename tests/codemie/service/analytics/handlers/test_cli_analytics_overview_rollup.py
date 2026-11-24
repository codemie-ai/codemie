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

"""/overview: the optional parts, the answer from the start-day rollup and the memory of a refusal (EPMCDME-17051)."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.configs.config import config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import RollupOverview
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE


def make_filter():
    return LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31))


_CLASSES = {"subscription_types": ["codemie"], "project_origins": ["profile"]}


def _overview_repo(rollup):
    """A reader for a /overview request: the live statements answer rows that carry class lists and estimate buckets,
    `rollup_overview` answers `rollup` (None: it cannot). `bounded` yields the reader itself."""
    repo = MagicMock()
    repo.get_cost_models = AsyncMock(return_value=["claude-sonnet-4-6"])
    repo.get_overview_money = AsyncMock(
        return_value=[
            {"part": "money", "key": "spend", "cost_usd": 4.0, "cache_read_cost_usd": 1.0, "unpriced_models": []},
            {"part": "money", "key": "route", "funding_route": "api_based", "cost_usd": 4.0, "session_count": 3},
            {"part": "dead", "n": 1},
        ]
    )
    repo.get_overview_facts = AsyncMock(
        return_value=[
            {
                "part": "facts",
                "turns": 6,
                "tool_calls": 4,
                "tool_calls_success": 3,
                "duration_ms": 1000,
                "files_changed": 2,
            }
        ]
    )
    repo.get_overview_models = AsyncMock(return_value=[{"part": "model", "key": "m1", "n": 3, **_CLASSES}])
    repo.get_dashboard_kpis = AsyncMock(
        return_value=[
            {
                "total_sessions": 3,
                "total_input_tokens": 10,
                "total_output_tokens": 5,
                "total_cache_read_tokens": 100,
                "total_cache_creation_tokens": 7,
                "total_api_calls": 4,
            }
        ]
    )
    repo.get_overview_lines = AsyncMock(
        return_value=[
            {"part": "total", "lines_added": 9, "lines_removed": 2},
            {"part": "day", "key": "2026-11-01", "net_lines": 7, "nonzero": True, **_CLASSES},
        ]
    )
    repo.get_class_lists = AsyncMock(return_value=[{"key": "", **_CLASSES}])
    repo.rollup_overview = AsyncMock(return_value=rollup)
    repo.materialize_sel = AsyncMock()
    repo.materialize_files = AsyncMock()

    @asynccontextmanager
    async def bounded():
        yield repo

    repo.bounded = bounded
    return repo


def _rollup_overview_answer():
    return RollupOverview(
        money_rows=[
            {
                "part": "money",
                "key": "spend",
                "cost_usd": 8.0,
                "cache_read_cost_usd": 2.0,
                "unpriced_models": ["raw-x"],
            },
            {"part": "dead", "n": 2},
        ],
        fact_rows=[
            {
                "part": "facts",
                "turns": 12,
                "tool_calls": 8,
                "tool_calls_success": 6,
                "duration_ms": 2000,
                "files_changed": 4,
            }
        ],
        model_rows=[{"part": "model", "key": "m9", "n": 6}],
        kpi_rows=[
            {
                "total_sessions": 6,
                "total_input_tokens": 20,
                "total_output_tokens": 10,
                "total_cache_read_tokens": 200,
                "total_cache_creation_tokens": 14,
                "total_api_calls": 8,
            }
        ],
        line_rows=[
            {"part": "total", "lines_added": 18, "lines_removed": 4},
            {"part": "day", "key": "2026-11-02", "net_lines": 14, "nonzero": True},
        ],
    )


_LEAN = {"include_class_lists": False, "include_buckets": False}
_LIVE_STATEMENTS = (
    "get_cost_models",
    "get_overview_money",
    "get_overview_facts",
    "get_overview_models",
    "get_dashboard_kpis",
    "get_overview_lines",
)


@pytest.mark.asyncio
async def test_overview_without_the_optional_parts_is_served_from_the_rollup_with_no_live_statement():
    repo = _overview_repo(_rollup_overview_answer())

    data, pricer, as_of = await LocalAnalyticsHandler(repo).get_overview(make_filter(), **_LEAN)

    repo.rollup_overview.assert_awaited_once()
    for name in (*_LIVE_STATEMENTS, "get_class_lists"):
        getattr(repo, name).assert_not_awaited()
    kpis = data["kpis"]
    assert (kpis["total_sessions"], kpis["total_cost_usd"], kpis["total_turns"], kpis["dead_sessions"]) == (
        6,
        8.0,
        12,
        2,
    )
    assert kpis["total_tokens"] == 20 + 10 + 200 + 14 and kpis["net_lines"] == 14
    assert [(d["day"], d["net_lines"]) for d in data["daily_buckets"]] == [("2026-11-02", 14)]
    assert [(m["model_name"], m["session_count"]) for m in data["model_breakdown"]] == [("m9", 6)]
    assert as_of == "2026-11-02"
    assert pricer.unpriced == ["raw-x"]
    assert "funding" not in data and kpis["subscription_types"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flags", [{"include_class_lists": True}, {"include_buckets": True}, {}], ids=["lists", "buckets", "default"]
)
async def test_overview_with_an_optional_part_never_asks_the_rollup_which_holds_neither(flags):
    repo = _overview_repo(_rollup_overview_answer())

    data, _, _ = await LocalAnalyticsHandler(repo).get_overview(make_filter(), **flags)

    repo.rollup_overview.assert_not_awaited()
    repo.get_overview_money.assert_awaited_once()
    assert data["kpis"]["total_sessions"] == 3  # the live answer


@pytest.mark.asyncio
async def test_the_live_overview_without_the_optional_parts_leaves_every_list_and_estimate_empty():
    repo = _overview_repo(None)

    data, _, _ = await LocalAnalyticsHandler(repo).get_overview(make_filter(), **_LEAN)

    repo.get_class_lists.assert_not_awaited()  # its statement does not run
    empty = {"subscription_types": [], "project_origins": []}
    assert {k: data["kpis"][k] for k in empty} == empty
    assert {k: data["daily_buckets"][0][k] for k in empty} == empty  # also the lists the day statement returned
    assert {k: data["model_breakdown"][0][k] for k in empty} == empty
    assert "funding" not in data and "personal_billed" not in data
    assert data["kpis"]["total_cost_usd"] == 4.0  # the figures are the same


@pytest.mark.asyncio
async def test_the_default_overview_keeps_its_lists_and_estimates():
    repo = _overview_repo(None)

    data, _, _ = await LocalAnalyticsHandler(repo).get_overview(make_filter())

    assert data["kpis"]["subscription_types"] == ["codemie"] and data["kpis"]["project_origins"] == ["profile"]
    assert data["daily_buckets"][0]["subscription_types"] == ["codemie"]
    assert data["model_breakdown"][0]["project_origins"] == ["profile"]
    assert data["funding"]["api_based"]["cost_usd"] == 4.0


@pytest.mark.asyncio
async def test_a_refused_overview_rollup_is_remembered_and_the_live_statements_run():
    repo = _overview_repo(None)
    handler = LocalAnalyticsHandler(repo)

    first, _, _ = await handler.get_overview(make_filter(), **_LEAN)
    second, _, _ = await handler.get_overview(make_filter(), **_LEAN)

    repo.rollup_overview.assert_awaited_once()  # the second request did not repeat the reads to be refused again
    assert repo.get_overview_money.await_count == 2 and first == second


@pytest.mark.asyncio
async def test_an_overview_answer_from_the_rollup_is_reused_for_the_cache_ttl():
    repo = _overview_repo(_rollup_overview_answer())
    handler = LocalAnalyticsHandler(repo)

    await handler.get_overview(make_filter(), **_LEAN)
    await handler.get_overview(make_filter(), **_LEAN)

    repo.rollup_overview.assert_awaited_once()


@pytest.mark.asyncio
async def test_overview_never_asks_the_rollup_when_the_rollup_is_switched_off(monkeypatch):
    monkeypatch.setattr(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", False)
    repo = _overview_repo(_rollup_overview_answer())

    await LocalAnalyticsHandler(repo).get_overview(make_filter(), **_LEAN)

    repo.rollup_overview.assert_not_awaited()
    repo.get_overview_money.assert_awaited_once()


def test_the_rollup_reads_the_handler_asks_for_are_declared_by_the_port_and_implemented_by_the_reader():
    # The handler looks them up by name (a reader without one simply never answers), so a rename would switch the
    # rollup off silently: the port and the PostgreSQL reader must keep the names.
    from codemie.repository.cli_analytics.ports import CliAnalyticsReader
    from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
    from tests.codemie.repository.cli_analytics.support.contracts import port_methods

    for name in ("rollup_users", "rollup_cost", "rollup_overview"):
        assert name in port_methods(CliAnalyticsReader), name
        assert hasattr(PostgresCliAnalyticsReader, name), name


@pytest.mark.asyncio
async def test_a_refused_rollup_read_and_the_live_statements_share_one_request_budget():
    """A slow rollup read that ends in a refusal must not leave the live statements a budget of their own."""
    from codemie.service.analytics.handlers import cli_analytics_handler as module

    deadlines = []
    repo = _overview_repo(None)

    async def refuse(*args, **kwargs):
        deadlines.append(module._request_deadline.get())

    async def live(*args, **kwargs):
        deadlines.append(module._request_deadline.get())
        return []

    repo.rollup_overview = AsyncMock(side_effect=refuse)
    repo.get_overview_money = AsyncMock(side_effect=live)

    await LocalAnalyticsHandler(repo).get_overview(make_filter(), **_LEAN)

    assert len(deadlines) == 2 and deadlines[0] is not None and deadlines[0] == deadlines[1]
    assert module._request_deadline.get() is None  # the budget ends with the request


@pytest.mark.asyncio
async def test_total_users_comes_from_the_rollup_answer_and_is_cached_apart_from_the_plain_one():
    answer = _rollup_overview_answer()
    answer = RollupOverview(**{**answer.__dict__, "total_users": 7})
    repo = _overview_repo(answer)
    repo.get_overview_user_count = AsyncMock(return_value=0)
    handler = LocalAnalyticsHandler(repo)

    data, _, _ = await handler.get_overview(make_filter(), **_LEAN, include_total_users=True)
    again, _, _ = await handler.get_overview(make_filter(), **_LEAN, include_total_users=True)
    plain, _, _ = await handler.get_overview(make_filter(), **_LEAN)

    assert data["kpis"]["total_users"] == again["kpis"]["total_users"] == 7
    assert "total_users" not in plain["kpis"]  # not asked for: the answer is as before
    repo.rollup_overview.assert_awaited_once()  # the counting answer also serves the plain request
    assert repo.rollup_overview.await_args.args[3] is True
    repo.get_overview_user_count.assert_not_awaited()


@pytest.mark.asyncio
async def test_total_users_on_the_live_path_is_one_more_statement_over_sel():
    repo = _overview_repo(None)  # the rollup cannot answer
    repo.get_overview_user_count = AsyncMock(return_value=5)

    data, _, _ = await LocalAnalyticsHandler(repo).get_overview(make_filter(), **_LEAN, include_total_users=True)

    assert data["kpis"]["total_users"] == 5
    repo.get_overview_user_count.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_live_path_does_not_count_users_unless_asked():
    repo = _overview_repo(None)
    repo.get_overview_user_count = AsyncMock(return_value=5)

    data, _, _ = await LocalAnalyticsHandler(repo).get_overview(make_filter(), **_LEAN)

    assert "total_users" not in data["kpis"]
    repo.get_overview_user_count.assert_not_awaited()


@pytest.mark.asyncio
async def test_eight_users_opening_the_overview_together_read_the_rollup_once():
    repo = _overview_repo(_rollup_overview_answer())
    release = asyncio.Event()
    answer = repo.rollup_overview.return_value

    async def slow_read(*args, **kwargs):
        await release.wait()
        return answer

    repo.rollup_overview = AsyncMock(side_effect=slow_read)
    handler = LocalAnalyticsHandler(repo)

    users = [asyncio.create_task(handler.get_overview(make_filter(), **_LEAN)) for _ in range(8)]
    for _ in range(5):
        await asyncio.sleep(0)
    release.set()
    answers = await asyncio.gather(*users)

    assert repo.rollup_overview.await_count == 1  # not eight reads queueing for the light lane's two slots
    assert (
        len({a[0]["kpis"]["total_cost_usd"] for a in answers}) == 1 and answers[0][0]["kpis"]["total_cost_usd"] == 8.0
    )
