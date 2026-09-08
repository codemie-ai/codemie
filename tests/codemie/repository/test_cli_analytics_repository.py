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

import pytest
from datetime import datetime
from unittest.mock import AsyncMock

from codemie.repository.cli_analytics_repository import LocalAnalyticsFilter, LocalAnalyticsRepository


@pytest.fixture
def repo():
    q = AsyncMock()
    return LocalAnalyticsRepository(q)


def make_filter():
    return LocalAnalyticsFilter(
        start_dt=datetime(2026, 1, 1),
        end_dt=datetime(2026, 12, 31),
    )


@pytest.mark.asyncio
async def test_get_repository_sessions_includes_empty_repository(repo):
    """Plugin-less sessions (repository='') must NOT be filtered out."""
    repo._q = AsyncMock(
        return_value=[
            {"session_id": "s1", "repository": "my-repo", "branch": "main", "project_name": "proj"},
            {"session_id": "s2", "repository": "", "branch": "", "project_name": ""},
        ]
    )
    result = await repo.get_repository_sessions(make_filter())
    assert len(result) == 2
    assert any(r["session_id"] == "s2" for r in result)


@pytest.mark.asyncio
async def test_get_session_detail_meta_resolves_plugin_less_session(repo):
    """A session with only cost data (no plugin row) must return a row, not empty."""
    repo._q = AsyncMock(
        return_value=[
            {
                "session_id": "native-only",
                "developer_name": "dev@test.com",
                "repository": None,
                "branch": None,
                "project_name": None,
                "prompt": None,
                "started_at": None,
                "duration_ms": 0,
            }
        ]
    )
    result = await repo.get_session_detail_meta("native-only")
    assert len(result) == 1
    assert result[0]["session_id"] == "native-only"
    assert result[0]["repository"] is None


def test_sessions_cte_includes_branch_filter_when_set():
    f = LocalAnalyticsFilter(start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31), branch="main")
    cte = LocalAnalyticsRepository._sessions_cte(f)
    assert "branch = {branch:String}" in cte


def test_sessions_cte_omits_branch_filter_when_none():
    f = LocalAnalyticsFilter(start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31))
    cte = LocalAnalyticsRepository._sessions_cte(f)
    assert "{branch:String}" not in cte


def test_params_includes_branch_when_set():
    f = LocalAnalyticsFilter(start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31), branch="feature-x")
    assert f.params().get("branch") == "feature-x"


def test_params_excludes_branch_when_none():
    f = LocalAnalyticsFilter(start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31))
    assert "branch" not in f.params()
