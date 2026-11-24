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

"""How the handler takes the ranking rollup (EPMCDME-17301): the rollup's rows build the response with no live
statement, a refusal re-enters the heavy lane on the live statements, a search is never asked, every page is its own
cache entry. The values the rollup holds are checked against the live statements in `test_live_ranking_rollup.py`."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs.config import config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import RollupProjects, RollupRanking
from codemie.service.analytics.dashboard_cache import reset_dashboard_cache
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE


@pytest.fixture(autouse=True)
def _live_rankings():
    """Overrides the directory's fixture: here the handler's own rollup read runs."""


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_dashboard_cache()
    yield
    reset_dashboard_cache()


def _f() -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=datetime(2026, 9, 1), end_dt=datetime(2026, 9, 8))


def _ranking() -> RollupRanking:
    group = {"part": "group", "key": "1", "n": 3, "project_name": "P1", "repository": "repo-a", "branch": None}
    facts = {"part": "group", "n": 3, "turns": 5, "repository": "repo-a", "project_name": None, "branch": None}
    return RollupRanking([{**group, "cost_usd": 1.5}, {"part": "count", "n": 1}], [facts])


def _handler(rollup_answer):
    repo = MagicMock()
    repo.rollup_repository_ranking = AsyncMock(return_value=rollup_answer)
    repo.rollup_project_page = AsyncMock(return_value=None)
    return LocalAnalyticsHandler(repo), repo


@pytest.mark.asyncio
async def test_repositories_are_built_from_the_rollup_rows_without_a_live_statement():
    handler, repo = _handler(_ranking())
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
        data, _ = await handler.get_repositories(_f(), 0, 10, False)

    (row,) = data["rows"]
    assert (row["repository"], row["project_name"], row["session_count"], row["turns"], row["cost_usd"]) == (
        "repo-a",
        "P1",
        3,
        5,
        1.5,
    )
    assert data["pagination"]["total_count"] == 1
    repo.bounded.assert_not_called()
    repo.get_repository_ranking.assert_not_called()


@pytest.mark.asyncio
async def test_a_search_is_never_asked_of_the_rollup():
    handler, repo = _handler(_ranking())
    repo.bounded = MagicMock(side_effect=RuntimeError("the live path"))
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True), pytest.raises(RuntimeError):
        await handler.get_repositories(_f(), 0, 10, False, "repo")
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True), pytest.raises(RuntimeError):
        await handler.get_projects(_f(), 0, 10, "repo")

    repo.rollup_repository_ranking.assert_not_called()
    repo.rollup_project_page.assert_not_called()


@pytest.mark.asyncio
async def test_a_refusal_takes_the_live_statements_and_is_kept_for_that_page_only():
    handler, repo = _handler(None)
    repo.bounded = MagicMock(side_effect=RuntimeError("the live path"))
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await handler.get_repositories(_f(), 0, 10, False)
        assert repo.rollup_repository_ranking.await_count == 1  # the refusal of the page is cached
        with pytest.raises(RuntimeError):
            await handler.get_repositories(_f(), 1, 10, False)  # another page is another entry
        with pytest.raises(RuntimeError):
            await handler.get_repositories(_f(), 0, 10, True)  # and so is the branches flag
    assert repo.rollup_repository_ranking.await_count == 3


@pytest.mark.asyncio
async def test_the_kill_switch_keeps_the_rollup_unread():
    handler, repo = _handler(_ranking())
    repo.bounded = MagicMock(side_effect=RuntimeError("the live path"))
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", False), pytest.raises(RuntimeError):
        await handler.get_repositories(_f(), 0, 10, False)
    repo.rollup_repository_ranking.assert_not_called()


@pytest.mark.asyncio
async def test_projects_are_built_from_the_rollup_page():
    page = RollupProjects(
        total=1,
        project_names=["P1"],
        group_rows=[
            {
                "part": "group",
                "key": "1",
                "n": 2,
                "project_name": "P1",
                "repository": "r",
                "branch": "b",
                "cost_usd": 2.0,
            }
        ],
        fact_rows=[{"part": "group", "n": 2, "turns": 4, "project_name": "P1", "repository": "r", "branch": "b"}],
        repository_buckets=[],
        project_buckets=[],
        window_buckets=[],
        unpriced_models=["raw-model"],
    )
    handler, repo = _handler(None)
    repo.rollup_project_page = AsyncMock(return_value=page)
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
        data, pricer = await handler.get_projects(_f(), 0, 10)

    (project,) = data["rows"]
    assert project["project_name"] == "P1" and project["session_count"] == 2 and project["turns"] == 4
    assert data["pagination"]["total_count"] == 1
    assert list(pricer.unpriced) == ["raw-model"]
    repo.bounded.assert_not_called()


@pytest.mark.asyncio
async def test_projects_from_the_rollup_without_branches_null_them_and_keep_the_figures():
    page = RollupProjects(
        total=1,
        project_names=["P1"],
        group_rows=[
            {
                "part": "group",
                "key": "1",
                "n": 2,
                "project_name": "P1",
                "repository": "r",
                "branch": "b",
                "cost_usd": 2.0,
            }
        ],
        fact_rows=[{"part": "group", "n": 2, "turns": 4, "project_name": "P1", "repository": "r", "branch": "b"}],
        repository_buckets=[],
        project_buckets=[],
        window_buckets=[],
        unpriced_models=[],
    )
    handler, repo = _handler(None)
    repo.rollup_project_page = AsyncMock(return_value=page)
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
        with_branches, _ = await handler.get_projects(_f(), 0, 10, include_branches=True)
        handler, repo = _handler(None)
        repo.rollup_project_page = AsyncMock(return_value=page)
        without, _ = await handler.get_projects(_f(), 0, 10, include_branches=False)

    repositories = [r for p in without["rows"] for r in p["repositories"]]
    assert repositories and all(r["branches"] is None for r in repositories)
    assert all(r["branches"] for p in with_branches["rows"] for r in p["repositories"])
    repo.bounded.assert_not_called()  # still the rollup path, no live statement

    def figures(data):
        return [{k: v for k, v in p.items() if k != "repositories"} for p in data["rows"]], data["pagination"]

    assert figures(without) == figures(with_branches)


@pytest.mark.asyncio
async def test_a_ranking_read_that_must_merge_live_re_enters_the_heavy_lane():
    from codemie.repository.cli_analytics.ports import LiveMergeNeeded
    from codemie.repository.cli_analytics.postgres import request_stats

    seen = []

    async def read(*_args):
        stats = request_stats.current()
        seen.append((stats.lane, request_stats.live_merge_allowed.get()))
        if request_stats.live_merge_allowed.get() is False:
            raise LiveMergeNeeded("merge")
        return _ranking()

    handler, repo = _handler(None)
    repo.rollup_repository_ranking = read
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
        data, _ = await handler.get_repositories(_f(), 0, 10, False)

    assert seen == [("light", False), ("heavy", True)]
    assert data["pagination"]["total_count"] == 1


def _efficiency_cohort():
    from decimal import Decimal

    from codemie.repository.cli_analytics.postgres import user_rollup as ur

    cohort = ur.Cohort()
    o, e = cohort.overview, cohort.overview.efficiency
    o.sessions, o.dead_sessions, o.api_calls, o.cache_read_tokens = 10, 2, 50, 1000
    o.spend_cost, o.cache_read_cost = Decimal("4"), Decimal("1")
    e.dead_cost, e.spend_sessions, e.depth = Decimal("0.5"), 8, [1, 2, 0, 3, 0, 4]
    e.worst = (20.0, "s-worst")
    return ur.assemble_efficiency(cohort)


@pytest.mark.asyncio
async def test_the_lean_efficiency_answer_comes_from_the_rollup_rows_alone():
    served = _efficiency_cohort()
    served.fact_rows[0].update(session_title="T", prompt="the prompt", repository="r", branch="b")
    handler, repo = _handler(None)
    repo.rollup_efficiency = AsyncMock(return_value=served)
    repo.bounded = MagicMock(side_effect=RuntimeError("the live path"))
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
        data, _ = await handler.get_efficiency(_f(), include_class_lists=False, include_buckets=False)

    assert [(b["bucket"], b["count"]) for b in data["session_depth"]] == [
        ("1", 1),
        ("2-5", 2),
        ("6-10", 0),
        ("11-25", 3),
        ("26-50", 0),
        ("50+", 4),
    ]
    assert data["kpis"]["worst_session_trace_id"] == "s-worst"
    assert data["kpis"]["worst_session_ctx_per_call"] == 20.0
    assert data["kpis"]["total_cost_usd"] == 4.0 and data["kpis"]["avg_context_per_call"] == 20.0
    assert data["dead_sessions"]["count"] == 2 and data["dead_sessions"]["wasted_cost_usd"] == 0.5
    assert data["dead_sessions"]["pct_of_sessions"] == 25.0


@pytest.mark.asyncio
async def test_efficiency_with_class_lists_or_buckets_never_asks_the_rollup():
    handler, repo = _handler(None)
    repo.rollup_efficiency = AsyncMock(return_value=_efficiency_cohort())
    repo.bounded = MagicMock(side_effect=RuntimeError("the live path"))
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
        with pytest.raises(RuntimeError):
            await handler.get_efficiency(_f(), include_class_lists=True, include_buckets=False)
        with pytest.raises(RuntimeError):
            await handler.get_efficiency(_f(), include_class_lists=False, include_buckets=True)
    repo.rollup_efficiency.assert_not_called()
