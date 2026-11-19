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

"""The branches list of one repository: paged named branches, the no-branch row last on the last page only."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE


def _filter(repositories: list[str] | None = None) -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(
        scope=EXCLUDE_SCOPE, start_dt=datetime(2026, 9, 1), end_dt=datetime(2026, 9, 8), repositories=repositories
    )


def _group(rank: int, branch: str | None, n: int, cost: float = 1.0) -> dict:
    return {
        "part": "group",
        "key": str(rank),
        "branch": branch,
        "n": n,
        "cost_usd": cost,
        "turns": 4,
        "files_changed": 2,
        "lines_added": 10,
        "lines_removed": 4,
        "tool_calls": 5,
        "tool_calls_success": 4,
        "subscription_types": ["codemie"],
        "project_origins": ["profile"],
    }


def _handler(groups: list[dict], count: int) -> tuple[LocalAnalyticsHandler, MagicMock]:
    repo = MagicMock()

    @asynccontextmanager
    async def bounded():
        yield repo

    repo.bounded = bounded
    repo.get_cost_models = AsyncMock(return_value=[])
    repo.get_branch_ranking = AsyncMock(return_value=[*groups, {"part": "count", "n": count}])
    return LocalAnalyticsHandler(repo), repo


@pytest.mark.asyncio
async def test_a_page_of_branches_carries_metrics_and_pagination():
    handler, repo = _handler([_group(1, "main", 5), _group(2, "dev", 3)], count=12)

    data, _ = await handler.get_branches(_filter(), "repo-a", 0, 2)

    assert [r["branch"] for r in data["rows"]] == ["main", "dev"]
    assert data["rows"][0]["session_count"] == 5
    assert data["rows"][0]["tool_success_rate"] == 80.0
    assert data["rows"][0]["net_lines"] == 6
    assert data["pagination"] == {"page": 0, "per_page": 2, "total_count": 12, "has_more": True}
    scoped = repo.get_branch_ranking.await_args.args[0]
    assert scoped.repositories == ["repo-a"]
    assert repo.get_branch_ranking.await_args.args[2:] == (0, 2)


@pytest.mark.asyncio
async def test_repository_is_anded_with_the_repositories_filter():
    handler, repo = _handler([_group(1, "main", 1)], count=1)

    await handler.get_branches(_filter(["repo-a", "repo-b"]), "repo-a", 0, 10)
    assert repo.get_branch_ranking.await_args.args[0].repositories == ["repo-a"]

    repo.get_branch_ranking.reset_mock()
    data, _ = await handler.get_branches(_filter(["repo-b"]), "repo-a", 0, 10)
    assert data["rows"] == [] and data["pagination"]["total_count"] == 0
    repo.get_branch_ranking.assert_not_called()


@pytest.mark.asyncio
async def test_no_branch_row_is_last_on_the_last_page_only():
    null = _group(3, None, 2)
    first, _ = await _handler([_group(1, "main", 5), _group(2, "dev", 3), null], 3)[0].get_branches(
        _filter(), "r", 0, 2
    )
    last, _ = await _handler([_group(3, "x", 1), null], 3)[0].get_branches(_filter(), "r", 1, 2)

    assert [r["branch"] for r in first["rows"]] == ["main", "dev"]
    assert first["pagination"]["has_more"] is True
    assert [r["branch"] for r in last["rows"]] == ["x", None]
    assert last["pagination"] == {"page": 1, "per_page": 2, "total_count": 3, "has_more": False}
    assert last["rows"][-1]["session_count"] == 2


@pytest.mark.asyncio
async def test_only_no_branch_sessions_give_that_row_on_page_zero():
    data, _ = await _handler([_group(1, None, 2)], 0)[0].get_branches(_filter(), "r", 0, 10)

    assert [r["branch"] for r in data["rows"]] == [None]
    assert data["pagination"]["total_count"] == 0


@pytest.mark.asyncio
async def test_no_sessions_give_no_rows():
    data, _ = await _handler([], 0)[0].get_branches(_filter(), "r", 0, 10)

    assert data["rows"] == []
