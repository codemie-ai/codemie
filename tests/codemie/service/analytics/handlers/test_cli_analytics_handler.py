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
from unittest.mock import ANY, AsyncMock, MagicMock

from codemie.repository.cli_analytics_repository import LocalAnalyticsFilter
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler


def make_handler(cost_facts=None):
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=cost_facts or [])
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[])
    return LocalAnalyticsHandler(repo)


BASE_COST_ROW = {
    "session_id": "s1",
    "developer_name": "dev",
    "model_name": "claude",
    "cost_usd": 1.0,
    "input_tokens": 100,
    "output_tokens": 50,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "prompt": "hello",
    "started_at": datetime(2026, 1, 1, 0, 0, 0),
    "last_event_at": datetime(2026, 1, 1, 0, 1, 0),
}


def make_filter():
    return LocalAnalyticsFilter(start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31))


@pytest.mark.asyncio
async def test_session_repository_none_when_plugin_absent():
    """Empty string from ClickHouse for plugin-less session must become None."""
    row = {**BASE_COST_ROW, "repository": "", "branch": ""}
    handler = make_handler(cost_facts=[row])
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    session = data["sessions"][0]
    assert session["repository"] is None
    assert session["branch"] is None


@pytest.mark.asyncio
async def test_session_repository_present_when_plugin_ran():
    """Non-empty repository and branch pass through unchanged."""
    row = {**BASE_COST_ROW, "repository": "my-repo", "branch": "main"}
    handler = make_handler(cost_facts=[row])
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    session = data["sessions"][0]
    assert session["repository"] == "my-repo"
    assert session["branch"] == "main"


@pytest.mark.asyncio
async def test_session_repository_present_branch_none_non_git_dir():
    """Repository present but branch empty (non-git directory) — only branch is None."""
    row = {**BASE_COST_ROW, "repository": "my-repo", "branch": ""}
    handler = make_handler(cost_facts=[row])
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    session = data["sessions"][0]
    assert session["repository"] == "my-repo"
    assert session["branch"] is None


@pytest.mark.asyncio
async def test_search_does_not_crash_when_repository_is_none():
    """Search with repository=None must not raise AttributeError."""
    row = {**BASE_COST_ROW, "repository": "", "branch": ""}
    handler = make_handler(cost_facts=[row])
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search="hello")
    assert isinstance(data["sessions"], list)


def make_repo_handler(cost_facts):
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=cost_facts)
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_file_facts_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    return LocalAnalyticsHandler(repo)


BASE_COST_FACT = {
    "session_id": "s1",
    "model_name": "claude",
    "cost_usd": 1.0,
    "input_tokens": 100,
    "output_tokens": 50,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "repository": None,
    "branch": None,
    "project_name": None,
}


@pytest.mark.asyncio
async def test_repositories_unattributed_bucket_when_plugin_absent():
    """Plugin-less sessions form a bucket with repository=None, not silently excluded."""
    cost_facts = [
        {**BASE_COST_FACT, "session_id": "s1", "repository": "my-repo", "branch": "main", "project_name": "proj"},
        {
            **BASE_COST_FACT,
            "session_id": "s2",
            "cost_usd": 0.5,
            "repository": None,
            "branch": None,
            "project_name": None,
        },
    ]
    handler = make_repo_handler(cost_facts)
    data = await handler.get_repositories(make_filter(), page=0, per_page=50, include_branches=False)
    repos = data["rows"]
    assert len(repos) == 2
    unattributed = next((r for r in repos if r["repository"] is None), None)
    assert unattributed is not None


@pytest.mark.asyncio
async def test_is_unattributed_filter_delegates_to_repository():
    """is_unattributed=True must be forwarded to get_session_cost_facts; handler does not filter in-memory."""
    unattributed_rows = [
        {**BASE_COST_ROW, "session_id": "s2", "repository": "", "branch": ""},
        {**BASE_COST_ROW, "session_id": "s3", "repository": None, "branch": None},
    ]
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=unattributed_rows)
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(repo)

    await handler.get_sessions(
        make_filter(), page=0, per_page=20, sort_by="start_time", search=None, is_unattributed=True
    )
    repo.get_session_cost_facts.assert_called_once_with(ANY, is_unattributed=True)


@pytest.mark.asyncio
async def test_is_unattributed_false_delegates_to_repository():
    """is_unattributed=False must be forwarded to get_session_cost_facts with is_unattributed=False."""
    rows = [
        {**BASE_COST_ROW, "session_id": "s1", "repository": "my-repo", "branch": "main"},
        {**BASE_COST_ROW, "session_id": "s2", "repository": None, "branch": None},
    ]
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=rows)
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(repo)

    await handler.get_sessions(
        make_filter(), page=0, per_page=20, sort_by="start_time", search=None, is_unattributed=False
    )
    repo.get_session_cost_facts.assert_called_once_with(ANY, is_unattributed=False)


@pytest.mark.asyncio
async def test_repositories_search_delegates_to_repository():
    """search param must be forwarded to get_session_cost_facts; handler does not filter in-memory."""
    cost_facts = [
        {**BASE_COST_FACT, "session_id": "s1", "repository": "codemie", "branch": "main"},
    ]
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=cost_facts)
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_file_facts_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(repo)

    await handler.get_repositories(make_filter(), page=0, per_page=50, include_branches=False, search="codem")
    repo.get_session_cost_facts.assert_called_once_with(ANY, search="codem")


@pytest.mark.asyncio
async def test_repositories_search_none_delegates_to_repository():
    """None search must be forwarded to get_session_cost_facts with search=None."""
    cost_facts = [
        {**BASE_COST_FACT, "session_id": "s1", "repository": "codemie", "branch": "main"},
        {**BASE_COST_FACT, "session_id": "s2", "repository": None, "branch": None},
    ]
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=cost_facts)
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_file_facts_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(repo)

    await handler.get_repositories(make_filter(), page=0, per_page=50, include_branches=False, search=None)
    repo.get_session_cost_facts.assert_called_once_with(ANY, search=None)


@pytest.mark.asyncio
async def test_branch_filter_forwarded_to_get_session_cost_facts():
    """branch kwarg must be set on the filter before get_session_cost_facts is called."""
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=[])
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(repo)

    await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None, branch="main")

    call_filter = repo.get_session_cost_facts.call_args.args[0]
    assert call_filter.branch == "main"


@pytest.mark.asyncio
async def test_branch_none_when_not_provided():
    """branch defaults to None — filter.branch must remain None when not passed."""
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=[])
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(repo)

    await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)

    call_filter = repo.get_session_cost_facts.call_args.args[0]
    assert call_filter.branch is None


def _dispatch_row(*, subagent_type="", skill_name="", duration_ms=9, real_duration_ms=152845, offset_s=10):
    return {
        "subagent_type": subagent_type,
        "skill_name": skill_name,
        "span_start": datetime(2026, 1, 1, 0, 0, offset_s),
        "duration_ms": duration_ms,
        "real_duration_ms": real_duration_ms,
        "cost_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


def test_skill_dispatch_uses_real_duration_ms():
    """Skill duration_ms must come from real_duration_ms (interaction end minus skill start), not tool execution time."""
    rows = [_dispatch_row(skill_name="codemie:codemie-analytics", duration_ms=9, real_duration_ms=152845)]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    skill = next(d for d in result if d["kind"] == "skill")
    assert skill["duration_ms"] == 152845


def test_agent_dispatch_uses_tool_execution_duration_ms():
    """Agent duration_ms must come from tool execution time, not real_duration_ms (which overcounts post-agent work)."""
    rows = [_dispatch_row(subagent_type="Explore", duration_ms=53235, real_duration_ms=130460, offset_s=25)]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agent = next(d for d in result if d["kind"] == "agent")
    assert agent["duration_ms"] == 53235
