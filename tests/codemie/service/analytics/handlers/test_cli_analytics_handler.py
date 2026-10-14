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
from datetime import date, datetime, timedelta, timezone
from unittest.mock import ANY, AsyncMock, MagicMock

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler, _iso


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
    """Empty string from the reader for plugin-less session must become None."""
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


@pytest.mark.asyncio
async def test_a_branch_keeps_a_filter_that_denies_every_session_denying():
    # A project admin who administers no project sees nothing, whatever branch /sessions asks for.
    handler = make_handler()
    denied = LocalAnalyticsFilter(start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31), deny_all=True)

    await handler.get_sessions(denied, page=0, per_page=20, sort_by="start_time", search=None, branch="main")

    repo = handler._repo
    for query in (
        repo.get_session_cost_facts,
        repo.get_turns_by_session,
        repo.get_tool_success_by_session,
        repo.get_lines_by_session,
        repo.get_model_breakdown,
    ):
        call_filter = query.call_args.args[0]
        assert (call_filter.deny_all, call_filter.branch) == (True, "main")


def _dispatch_row(
    *, subagent_type="", skill_name="", duration_ms=9, real_duration_ms=152845, offset_s=10, dispatch_offset_s=None
):
    return {
        "subagent_type": subagent_type,
        "skill_name": skill_name,
        "span_start": datetime(2026, 1, 1) + timedelta(seconds=offset_s),
        "dispatch_start": datetime(2026, 1, 1)
        + timedelta(seconds=offset_s if dispatch_offset_s is None else dispatch_offset_s),
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


def test_agent_dispatch_uses_subagent_run_duration_ms():
    """Agent duration_ms is the subagent's real run time (launch to its stop), not the Agent tool-execution span."""
    rows = [_dispatch_row(subagent_type="Explore", duration_ms=6, offset_s=25) | {"subagent_duration_ms": 64500}]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agent = next(d for d in result if d["kind"] == "agent")
    assert agent["duration_ms"] == 64500


def test_agent_dispatch_without_subagent_stop_has_null_duration():
    """A subagent with no stop event (session cut off mid-dispatch) reports null, never the tool span or 0."""
    rows = [_dispatch_row(subagent_type="Explore", duration_ms=6, offset_s=25) | {"subagent_duration_ms": None}]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agent = next(d for d in result if d["kind"] == "agent")
    assert agent["duration_ms"] is None


def test_skill_dispatch_ignores_subagent_duration_ms():
    rows = [
        _dispatch_row(skill_name="codemie:codemie-analytics", real_duration_ms=152845) | {"subagent_duration_ms": 1}
    ]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    skill = next(d for d in result if d["kind"] == "skill")
    assert skill["duration_ms"] == 152845


def test_dead_sessions_counts_a_session_with_no_successful_file_change_as_dead():
    """A priced session whose only file touches were rejected/failed (files_changed == 0, once
    rollups.py/reader.py gate it on success) must be classified dead with its cost wasted
    (EPMCDME-15583)."""
    cost_facts = [{"session_id": "s1", "cost_usd": 2.0}]
    files_by_session = {"s1": {"files_changed": 0}}
    dead, wasted = LocalAnalyticsHandler._dead_sessions(cost_facts, files_by_session)
    assert dead == 1
    assert wasted == 2.0


def test_dead_sessions_excludes_a_session_with_a_successful_file_change():
    """A priced session with at least one genuinely successful Write/Edit (files_changed == 1)
    must not be classified dead, even though other calls in the session may have been rejected
    or failed."""
    cost_facts = [{"session_id": "s1", "cost_usd": 2.0}]
    files_by_session = {"s1": {"files_changed": 1}}
    dead, wasted = LocalAnalyticsHandler._dead_sessions(cost_facts, files_by_session)
    assert dead == 0
    assert wasted == 0.0


def test_dead_sessions_counts_a_prompt_only_session_as_dead():
    """A priced session with no tool-call rows at all (prompt-only - no entry in files_by_session,
    not just files_changed == 0) must still be classified dead with its cost wasted (EPMCDME-15448)."""
    cost_facts = [{"session_id": "s1", "cost_usd": 2.0}]
    files_by_session: dict[str, dict] = {}
    dead, wasted = LocalAnalyticsHandler._dead_sessions(cost_facts, files_by_session)
    assert dead == 1
    assert wasted == 2.0


def test_dead_sessions_counts_a_read_only_session_as_dead():
    """A priced session that only read files (successfully or not) produces files_changed == 0,
    same as the rejected/failed-write case - a Read is never written/edited (EPMCDME-15448), so
    this must be classified dead with its cost wasted."""
    cost_facts = [{"session_id": "s1", "cost_usd": 0.0426152}]
    files_by_session = {"s1": {"files_changed": 0}}
    dead, wasted = LocalAnalyticsHandler._dead_sessions(cost_facts, files_by_session)
    assert dead == 1
    assert wasted == 0.0426152


def _users_repo(user_rows):
    repo = MagicMock()
    for name in (
        "get_users_daily_activity",
        "get_users_last_active",
        "get_lines_by_user",
        "get_turns_by_session",
        "get_file_facts_by_session",
        "get_tool_success_by_session",
    ):
        setattr(repo, name, AsyncMock(return_value=[]))
    repo.get_users = AsyncMock(return_value=user_rows)
    repo.get_session_durations = AsyncMock(
        return_value=[
            {"session_id": "s1", "duration_ms": 1000},
            {"session_id": "s2", "duration_ms": 3000},
            {"session_id": "s3", "duration_ms": 50000},
        ]
    )
    return repo


@pytest.mark.asyncio
async def test_get_users_avg_duration_only_priced_sessions():
    repo = _users_repo([{"developer_name": "dev", "session_ids": ["s1", "s2"]}])
    result = await LocalAnalyticsHandler(repo).get_users(make_filter(), page=None, per_page=None)
    assert result["avg_session_duration_ms"] == 2000


@pytest.mark.asyncio
async def test_get_users_avg_duration_none_when_no_priced_sessions():
    repo = _users_repo([])
    result = await LocalAnalyticsHandler(repo).get_users(make_filter(), page=None, per_page=None)
    assert result["avg_session_duration_ms"] is None


def test_iso_attaches_utc_to_naive_datetime():
    """A naive datetime must serialize with an explicit UTC offset, same wall-clock value."""
    result = _iso(datetime(2026, 9, 30, 12, 28, 8, 937211))
    assert result == "2026-09-30T12:28:08.937211+00:00"


def test_iso_leaves_aware_datetime_unchanged():
    """An already-aware datetime (including a non-UTC offset) must not be re-converted."""
    aware = datetime(2026, 9, 30, 14, 28, 8, tzinfo=timezone(timedelta(hours=2)))
    result = _iso(aware)
    assert result == aware.isoformat() == "2026-09-30T14:28:08+02:00"


def test_iso_date_passthrough_unaffected():
    """A plain date value (day buckets) has no time-of-day — it must serialize unchanged."""
    assert _iso(date(2026, 9, 30)) == "2026-09-30"


def test_iso_none_still_returns_empty_string():
    """None must keep falling through to the non-datetime/date branch, unchanged by this fix."""
    assert _iso(None) == ""


@pytest.mark.asyncio
async def test_get_sessions_start_time_carries_utc_offset_for_naive_started_at():
    """Sessions-list start_time must carry an explicit UTC offset even when the repository returns a naive started_at."""
    row = {**BASE_COST_ROW, "repository": "my-repo", "branch": "main"}
    handler = make_handler(cost_facts=[row])
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    session = data["sessions"][0]
    assert session["start_time"] == "2026-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_rows_with_developer_name_also_carry_user_email() -> None:
    """user, cost-by-user, session and detail rows carry user_email equal to developer_name."""
    from codemie.rest_api.models.cli_analytics import (
        LocalAnalyticsCostByUserRow,
        LocalAnalyticsSessionDetail,
        LocalAnalyticsSessionRow,
        LocalAnalyticsUserRow,
    )

    email = "dev@example.com"
    repo = MagicMock()
    repo.get_users = AsyncMock(
        return_value=[
            {
                "developer_name": email,
                "session_count": 1,
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cost_usd": 1.0,
                "session_ids": [],
            }
        ]
    )
    for name in (
        "get_users_daily_activity",
        "get_users_last_active",
        "get_lines_by_user",
        "get_turns_by_session",
        "get_file_facts_by_session",
        "get_tool_success_by_session",
        "get_session_durations",
        "get_cost_kpis",
        "get_model_breakdown",
        "get_session_detail_cost",
        "get_session_detail_scalars",
        "get_session_detail_tools",
        "get_session_detail_events",
        "get_session_detail_dispatches",
        "get_skill_names_by_session",
        "get_lines_by_session",
    ):
        setattr(repo, name, AsyncMock(return_value=[]))
    repo.get_cost_by_user = AsyncMock(return_value=[{"developer_name": email, "cost_usd": 2.0}])
    repo.get_session_cost_facts = AsyncMock(return_value=[{**BASE_COST_ROW, "developer_name": email}])
    repo.get_session_detail_meta = AsyncMock(
        return_value=[
            {
                "session_id": "s1",
                "developer_name": email,
                "started_at": datetime(2026, 1, 1),
                "duration_ms": 0,
            }
        ]
    )
    handler = LocalAnalyticsHandler(repo)

    users = await handler.get_users(make_filter(), page=None, per_page=None)
    cost, _ = await handler.get_cost(make_filter())
    sessions, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    detail, _ = await handler.get_session_detail("s1")

    dumped = [
        LocalAnalyticsUserRow(**users["rows"][0]).model_dump(by_alias=True),
        LocalAnalyticsCostByUserRow(**cost["cost_by_user"][0]).model_dump(by_alias=True),
        LocalAnalyticsSessionRow(**sessions["sessions"][0]).model_dump(by_alias=True),
        LocalAnalyticsSessionDetail(**detail).model_dump(by_alias=True),
    ]
    for row in dumped:
        assert row["developer_name"] == email
        assert row["user_email"] == email


def test_ctx_per_call_helper_divides_and_zero_guards():
    from codemie.service.analytics.handlers.cli_analytics_handler import _ctx_per_call

    assert _ctx_per_call(1000, 10) == 100.0
    assert _ctx_per_call(1000, 0) == 0.0  # zero-guard: api_call_count == 0 must not raise
    assert _ctx_per_call(0, 5) == 0.0


@pytest.mark.asyncio
async def test_overview_avg_context_per_call_divides_by_api_call_count():
    """One turn can trigger many API calls (tool use); turns must not be the divisor."""
    repo = MagicMock()
    repo.get_cost_kpis = AsyncMock(
        return_value=[
            {
                "total_sessions": 1,
                "total_cost_usd": 1.0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cache_read_tokens": 1000,
                "total_cache_creation_tokens": 0,
                "total_api_calls": 10,
            }
        ]
    )
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_lines_totals = AsyncMock(return_value=[])
    repo.get_lines_daily = AsyncMock(return_value=[])
    repo.get_turns_by_session = AsyncMock(return_value=[{"session_id": "s1", "turns": 1}])
    repo.get_file_facts_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_session_durations = AsyncMock(return_value=[])
    repo.get_session_cost_facts = AsyncMock(return_value=[{**BASE_COST_ROW, "session_id": "s1"}])
    handler = LocalAnalyticsHandler(repo)

    data, _, _ = await handler.get_overview(make_filter())

    # 1000 cache_read_tokens / 10 api calls = 100 — the old formula (/1 turn) would give 1000.
    assert data["kpis"]["avg_context_per_call"] == 100.0


@pytest.mark.asyncio
async def test_sessions_ctx_per_call_field_uses_api_call_count_and_sorts_by_it():
    s1 = {**BASE_COST_ROW, "session_id": "s1", "cache_read_tokens": 1000, "api_call_count": 200}
    s2 = {**BASE_COST_ROW, "session_id": "s2", "cache_read_tokens": 1000, "api_call_count": 20}
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=[s1, s2])
    # turns-based ratio would rank s1 (1000/10=100) above s2 (1000/100=10) — the opposite order.
    repo.get_turns_by_session = AsyncMock(
        return_value=[{"session_id": "s1", "turns": 10}, {"session_id": "s2", "turns": 100}]
    )
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(repo)

    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="ctx_per_call", search=None)

    rows = data["sessions"]
    sessions = {r["trace_id"]: r for r in rows}
    assert sessions["s1"]["ctx_per_call"] == 5.0  # 1000 / 200 api calls
    assert sessions["s2"]["ctx_per_call"] == 50.0  # 1000 / 20 api calls
    assert [r["trace_id"] for r in rows] == ["s2", "s1"]  # highest ctx_per_call first

    from codemie.rest_api.models.cli_analytics import LocalAnalyticsSessionRow

    # rows[0] is "s2" (highest ctx_per_call, sorted above) — its source row carries api_call_count=20.
    dumped = LocalAnalyticsSessionRow(**rows[0]).model_dump(by_alias=True)
    assert dumped["ctx_per_call"] == rows[0]["ctx_per_call"]
    assert dumped["api_call_count"] == 20


@pytest.mark.asyncio
async def test_efficiency_worst_session_and_avg_use_api_call_count():
    """Heavy tool use (many calls per turn) must not look less bloated than few calls."""
    s1 = {**BASE_COST_ROW, "session_id": "s1", "cache_read_tokens": 1000, "api_call_count": 10}
    s2 = {**BASE_COST_ROW, "session_id": "s2", "cache_read_tokens": 1000, "api_call_count": 100}
    repo = MagicMock()
    repo.get_cost_kpis = AsyncMock(
        return_value=[
            {
                "total_sessions": 2,
                "total_cost_usd": 2.0,
                "total_cache_read_tokens": 2000,
                "total_api_calls": 110,
            }
        ]
    )
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_session_cost_facts = AsyncMock(return_value=[s1, s2])
    # turns-based ratio would make s2 (1000/5=200) "worse" than s1 (1000/100=10) — reversed.
    repo.get_turns_by_session = AsyncMock(
        return_value=[{"session_id": "s1", "turns": 100}, {"session_id": "s2", "turns": 5}]
    )
    repo.get_file_facts_by_session = AsyncMock(return_value=[])
    repo.get_lines_totals = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(repo)

    data, _ = await handler.get_efficiency(make_filter())

    kpis = data["kpis"]
    assert kpis["worst_session_trace_id"] == "s1"  # 1000/10=100 beats 1000/100=10
    assert kpis["avg_context_per_call"] == pytest.approx(2000 / 110)


def make_detail_handler(meta_row):
    repo = MagicMock()
    repo.get_session_detail_meta = AsyncMock(return_value=[meta_row])
    repo.get_session_detail_cost = AsyncMock(
        return_value=[
            {
                "model_name": "claude",
                "cost_usd": 1.0,
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            }
        ]
    )
    repo.get_session_detail_scalars = AsyncMock(
        return_value=[
            {
                "turns": 1,
                "tool_call_count": 0,
                "lines_added": 0,
                "lines_removed": 0,
                "active_ms": 0,
                "agent_count": 0,
                "skill_count": 0,
            }
        ]
    )
    repo.get_session_detail_tools = AsyncMock(return_value=[])
    repo.get_session_detail_events = AsyncMock(return_value=[])
    repo.get_session_detail_dispatches = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[{"skill_names": []}])
    return LocalAnalyticsHandler(repo)


@pytest.mark.asyncio
async def test_session_detail_harness_name_is_humanized():
    handler = make_detail_handler({"session_id": "s1", "harness_service_name": "claude-code"})
    detail, _ = await handler.get_session_detail("s1")
    assert detail["harness_name"] == "Claude Code"


@pytest.mark.asyncio
async def test_session_detail_harness_name_unknown_when_unresolved():
    handler = make_detail_handler({"session_id": "s1", "harness_service_name": None})
    detail, _ = await handler.get_session_detail("s1")
    assert detail["harness_name"] == "Unknown"


def test_agent_dispatch_starts_at_dispatch_start_not_span_start():
    # The bar starts when the subagent launched (dispatch_start), not when the Agent tool span began.
    rows = [_dispatch_row(subagent_type="Explore", duration_ms=6, offset_s=7, dispatch_offset_s=10)]
    rows[0] |= {"subagent_duration_ms": 1000}
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agent = next(d for d in result if d["kind"] == "agent")
    assert agent["start_offset_ms"] == 10000


def test_agent_run_is_clamped_to_the_session_end():
    # A subagent that stopped after the session ended must not extend past it: start + duration <= session.
    rows = [_dispatch_row(subagent_type="Explore", offset_s=25) | {"subagent_duration_ms": 200000}]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agent = next(d for d in result if d["kind"] == "agent")
    assert agent["start_offset_ms"] == 25000
    assert agent["duration_ms"] == 135000
    assert agent["start_offset_ms"] + agent["duration_ms"] == 160000


def test_agent_launched_after_the_session_end_has_null_duration():
    rows = [_dispatch_row(subagent_type="Explore", offset_s=200) | {"subagent_duration_ms": 5000}]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agent = next(d for d in result if d["kind"] == "agent")
    assert agent["duration_ms"] is None


def test_agent_launched_before_the_session_has_null_duration_and_a_zero_offset():
    # A launch before the session start cannot be placed on the timeline: no bar, and no negative offset.
    rows = [_dispatch_row(subagent_type="Explore", offset_s=-5) | {"subagent_duration_ms": 9000}]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agent = next(d for d in result if d["kind"] == "agent")
    assert agent["start_offset_ms"] == 0
    assert agent["duration_ms"] is None


def test_agent_without_a_dispatch_start_has_null_duration():
    # With no known launch the bar cannot be placed, so no duration is shown at the session start.
    rows = [_dispatch_row(subagent_type="Explore") | {"dispatch_start": None, "subagent_duration_ms": 9000}]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agent = next(d for d in result if d["kind"] == "agent")
    assert agent["duration_ms"] is None


def _agent_bars(rows: list[dict]) -> list[tuple[int, int]]:
    """Each agent bar as [start, end) in ms from the session start, in start order."""
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agents = [d for d in result if d["kind"] == "agent"]
    return sorted((d["start_offset_ms"], d["start_offset_ms"] + d["duration_ms"]) for d in agents)


def test_parallel_agents_render_as_overlapping_bars():
    # EPMCDME-15512 AC3: two subagents launched together overlap on the Timeline (session 52d226aa's shape),
    # so the second bar starts before the first one ends; not dots from the 3-6 ms tool-execution span.
    rows = [
        _dispatch_row(subagent_type="Explore", dispatch_offset_s=7.661) | {"subagent_duration_ms": 57102},
        _dispatch_row(subagent_type="Explore", dispatch_offset_s=8.597) | {"subagent_duration_ms": 27502},
    ]
    (first_start, first_end), (second_start, second_end) = _agent_bars(rows)
    assert (first_start, first_end) == (7661, 64763)
    assert (second_start, second_end) == (8597, 36099)
    assert second_start < first_end


def test_sequential_agents_render_as_separate_bars():
    # EPMCDME-15512 AC3: a subagent called after the previous one stopped does not overlap it.
    rows = [
        _dispatch_row(subagent_type="Explore", dispatch_offset_s=13.524) | {"subagent_duration_ms": 20600},
        _dispatch_row(subagent_type="Explore", dispatch_offset_s=40) | {"subagent_duration_ms": 15000},
    ]
    (first_start, first_end), (second_start, second_end) = _agent_bars(rows)
    assert (first_start, first_end) == (13524, 34124)
    assert (second_start, second_end) == (40000, 55000)
    assert second_start >= first_end
