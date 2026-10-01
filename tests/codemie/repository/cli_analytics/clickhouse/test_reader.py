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

from __future__ import annotations

import re
import uuid
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from codemie.repository.cli_analytics.clickhouse.reader import (
    SESSION_IDS_PARAM_BUDGET_BYTES,
    ClickHouseCliAnalyticsReader,
)
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import CliAnalyticsReader
from tests.codemie.repository.cli_analytics.support.contracts import assert_implements_port


def _flt(**kwargs) -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(start_dt=datetime(2026, 9, 1), end_dt=datetime(2026, 9, 8), **kwargs)


def _reader(rows=None) -> tuple[ClickHouseCliAnalyticsReader, AsyncMock]:
    query = AsyncMock(return_value=rows or [])
    return ClickHouseCliAnalyticsReader(query), query


def _sql(query: AsyncMock) -> str:
    return " ".join(query.call_args.args[0].split())


def _encoded_size(ids: list[str]) -> int:
    """Bytes clickhouse-connect sends for an Array(String) parameter: ['a','b']."""
    return 2 + sum(len(i.encode()) + 3 + i.count("'") + i.count("\\") for i in ids)


def test_reader_implements_the_reader_port():
    assert_implements_port(ClickHouseCliAnalyticsReader, CliAnalyticsReader)


@pytest.mark.asyncio
async def test_repository_sessions_keep_plugin_less_sessions():
    # Sessions without plugin data have repository '' and must not be filtered out.
    reader, _ = _reader(
        [
            {"session_id": "s1", "repository": "my-repo", "branch": "main", "project_name": "proj"},
            {"session_id": "s2", "repository": "", "branch": "", "project_name": ""},
        ]
    )

    rows = await reader.get_repository_sessions(_flt())

    assert [r["session_id"] for r in rows] == ["s1", "s2"]


@pytest.mark.asyncio
async def test_session_detail_meta_resolves_a_plugin_less_session():
    # A session with cost data only (no plugin row) still has a detail page.
    row = {"session_id": "native-only", "developer_name": "dev@test.com", "repository": None, "branch": None}
    reader, _ = _reader([row])

    assert await reader.get_session_detail_meta("native-only") == [row]


def test_sessions_cte_filters_on_the_branch_only_when_one_is_given():
    assert "branch = {branch:String}" in ClickHouseCliAnalyticsReader._sessions_cte(_flt(branch="main"))
    assert "{branch:String}" not in ClickHouseCliAnalyticsReader._sessions_cte(_flt())


# ── deny_all (defect D5) ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deny_all_selects_no_session_and_scopes_facts_to_that_empty_set():
    reader, query = _reader()

    await reader.get_cost_kpis(_flt(deny_all=True))

    sql, params = _sql(query), query.call_args.args[1]
    assert "AND 1 = 0" in sql
    assert "session_id IN (SELECT session_id FROM sel)" in sql
    assert "projects" not in params


@pytest.mark.asyncio
async def test_without_deny_all_the_session_set_is_unfiltered():
    reader, query = _reader()

    await reader.get_cost_kpis(_flt())

    assert "1 = 0" not in _sql(query)


# ── deterministic ordering (defect D1) ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "arg", "order_by"),
    [
        ("get_model_breakdown", _flt(), "ORDER BY session_count DESC, cost_usd DESC, model_name"),
        ("get_cost_by_user", _flt(), "ORDER BY cost_usd DESC, developer_name"),
        ("get_users", _flt(), "ORDER BY cost_usd DESC, developer_name"),
        ("get_tool_usage", _flt(), "ORDER BY call_count DESC, tool_name LIMIT 20"),
        ("get_session_detail_tools", "s1", "ORDER BY call_count DESC, tool_name LIMIT 20"),
        ("get_session_detail_events", "s1", "ORDER BY timestamp, event_type, tool_name, model_name"),
        (
            "get_session_detail_dispatches",
            "s1",
            "ORDER BY span_start, is_slash_command, subagent_type, skill_name",
        ),
    ],
)
@pytest.mark.asyncio
async def test_ordered_queries_break_ties_deterministically(method, arg, order_by):
    reader, query = _reader()

    await getattr(reader, method)(arg)

    assert _sql(query).endswith(order_by)


def _depth(sql: str, index: int) -> int:
    return sql.count("(", 0, index) - sql.count(")", 0, index)


@pytest.mark.parametrize(
    ("method", "order_by"),
    [
        ("get_session_detail_events", "ORDER BY timestamp, event_type, tool_name, model_name"),
        ("get_session_detail_dispatches", "ORDER BY span_start, is_slash_command, subagent_type, skill_name"),
    ],
)
@pytest.mark.asyncio
async def test_union_queries_order_the_whole_union_not_only_its_last_select(method, order_by):
    # ClickHouse applies an ORDER BY written after UNION ALL to the last SELECT alone, so the
    # union has to be a subquery for the timeline to come back in time order.
    reader, query = _reader()

    await getattr(reader, method)("s1")

    sql = " ".join(re.sub(r"--[^\n]*", "", query.call_args.args[0]).split())
    unions = [m.start() for m in re.finditer("UNION ALL", sql)]
    assert unions
    assert all(_depth(sql, at) >= 1 for at in unions)
    assert _depth(sql, sql.rindex(order_by)) == 0


# ── session ids sent in parts (defect D10) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_skill_names_for_many_sessions_are_fetched_in_parts_under_the_field_size_limit():
    ids = [str(uuid.UUID(int=i)) for i in range(5000)]
    reader, query = _reader()
    query.side_effect = lambda sql, params: [{"session_id": s, "skill_names": ["x"]} for s in params["session_ids"]]

    rows = await reader.get_skill_names_by_session(ids)

    sent = [call.args[1]["session_ids"] for call in query.call_args_list]
    assert len(sent) > 1
    assert all(_encoded_size(part) <= SESSION_IDS_PARAM_BUDGET_BYTES for part in sent)
    assert [sid for part in sent for sid in part] == ids
    assert [row["session_id"] for row in rows] == ids


@pytest.mark.asyncio
async def test_skill_names_for_few_sessions_use_one_query():
    reader, query = _reader()

    await reader.get_skill_names_by_session(["a", "b"])

    assert query.await_count == 1
    assert query.call_args.args[1] == {"session_ids": ["a", "b"]}


@pytest.mark.asyncio
async def test_skill_names_deduplicate_session_ids():
    reader, query = _reader()

    await reader.get_skill_names_by_session(["a", "b", "a"])

    assert query.call_args.args[1] == {"session_ids": ["a", "b"]}


@pytest.mark.asyncio
async def test_skill_names_for_no_session_run_no_query():
    reader, query = _reader()

    assert await reader.get_skill_names_by_session([]) == []
    query.assert_not_awaited()


def test_session_id_budget_stays_below_the_clickhouse_default_field_limit():
    assert SESSION_IDS_PARAM_BUDGET_BYTES < 131_072


@pytest.mark.asyncio
async def test_a_session_id_too_long_for_the_field_limit_on_its_own_is_left_out():
    # Session ids come from clients; one that alone exceeds the limit would fail its whole part.
    huge = "x" * SESSION_IDS_PARAM_BUDGET_BYTES
    reader, query = _reader()

    await reader.get_skill_names_by_session(["s1", huge, "s2"])

    sent = [sid for call in query.call_args_list for sid in call.args[1]["session_ids"]]
    assert sorted(sent) == ["s1", "s2"]
    assert all(
        _encoded_size(call.args[1]["session_ids"]) <= SESSION_IDS_PARAM_BUDGET_BYTES for call in query.call_args_list
    )


# ── session-start window (predicate lives in `sel` only) ─────────────────────────────

_WINDOW = "started_at BETWEEN {start_dt:DateTime64(3)} AND {end_dt:DateTime64(3)}"


def _cte_sql(f: LocalAnalyticsFilter) -> str:
    return " ".join(ClickHouseCliAnalyticsReader._sessions_cte(f).split())


@pytest.mark.parametrize("flt", [_flt(), _flt(deny_all=True), _flt(projects=["p"]), _flt(users=["a@b.c"])])
def test_sessions_cte_always_applies_the_session_start_window(flt):
    assert _WINDOW in _cte_sql(flt)


def test_session_scope_is_applied_even_when_unfiltered():
    scope = ClickHouseCliAnalyticsReader._session_scope(_flt(), "c.session_id")

    assert "c.session_id IN (SELECT session_id FROM sel)" in scope


@pytest.mark.asyncio
async def test_session_durations_have_no_overlap_predicate():
    reader, query = _reader()

    await reader.get_session_durations(_flt())

    sql = _sql(query)
    assert "last_event_at >=" not in sql
    assert "FROM sel" in sql


@pytest.mark.asyncio
async def test_session_start_times_do_not_repeat_the_window_outside_the_cte():
    reader, query = _reader()

    await reader.get_session_start_times(_flt())

    sql = _sql(query)
    assert sql.count("BETWEEN") == 1
    assert sql.endswith("FROM sel")


@pytest.mark.asyncio
async def test_repository_sessions_read_from_sel_and_return_empty_for_an_empty_window():
    reader, query = _reader([])

    assert await reader.get_repository_sessions(_flt()) == []
    assert "FROM sel" in _sql(query)


_COST_METHODS = [
    "get_cost_kpis",
    "get_model_breakdown",
    "get_cost_by_user",
    "get_users",
    "get_users_daily_activity",
    "get_session_cost_facts",
]


@pytest.mark.parametrize("method", _COST_METHODS)
@pytest.mark.asyncio
async def test_cost_methods_have_no_day_window_and_are_scoped_to_sel(method):
    reader, query = _reader()

    await getattr(reader, method)(_flt())

    sql = _sql(query)
    assert "day BETWEEN" not in sql
    assert "LEFT JOIN sel" not in sql
    assert "JOIN sel" in sql or "IN (SELECT session_id FROM sel)" in sql


_ROLLUP_METHODS = [
    "get_lines_totals",
    "get_lines_daily",
    "get_lines_by_user",
    "get_lines_by_session",
    "get_turns_by_session",
    "get_file_facts_by_session",
    "get_active_ms_by_session",
]


@pytest.mark.parametrize("method", _ROLLUP_METHODS)
@pytest.mark.asyncio
async def test_rollup_methods_have_no_day_window_and_are_scoped_to_sel(method):
    reader, query = _reader()

    await getattr(reader, method)(_flt())

    sql = _sql(query)
    assert "day BETWEEN" not in sql
    assert "LEFT JOIN sel" not in sql
    assert "JOIN sel" in sql or "IN (SELECT session_id FROM sel)" in sql


@pytest.mark.parametrize("method", ["get_tool_success_by_session", "get_tool_usage", "get_invocations"])
@pytest.mark.asyncio
async def test_tool_and_invocation_queries_have_no_timestamp_window_and_scope_to_sel(method):
    reader, query = _reader()

    await getattr(reader, method)(_flt())

    sql = _sql(query)
    assert "Timestamp BETWEEN" not in sql
    assert "IN (SELECT session_id FROM sel)" in sql


@pytest.mark.asyncio
async def test_invocations_scope_all_three_branches_to_sel():
    reader, query = _reader()

    await reader.get_invocations(_flt())

    assert _sql(query).count("IN (SELECT session_id FROM sel)") == 3


@pytest.mark.asyncio
async def test_users_last_active_inner_joins_sel_with_only_an_upper_bound():
    reader, query = _reader()

    await reader.get_users_last_active(_flt())

    sql = _sql(query)
    assert "LEFT JOIN sel" not in sql
    assert "JOIN sel s" in sql
    assert "Timestamp BETWEEN" not in sql
    assert "h.Timestamp <= {end_dt:DateTime64(3)}" in sql
    assert "h.Timestamp >=" not in sql


# ── audit: every windowed port method ────────────────────────────────────────────────

_WINDOWED_METHODS = [
    "get_cost_kpis",
    "get_model_breakdown",
    "get_cost_by_user",
    "get_users",
    "get_users_daily_activity",
    "get_users_last_active",
    "get_lines_totals",
    "get_lines_daily",
    "get_lines_by_user",
    "get_lines_by_session",
    "get_turns_by_session",
    "get_file_facts_by_session",
    "get_tool_success_by_session",
    "get_tool_usage",
    "get_invocations",
    "get_session_durations",
    "get_active_ms_by_session",
    "get_session_cost_facts",
    "get_session_start_times",
    "get_repository_sessions",
]


def _outside_sel(sql: str) -> str:
    """The recorded SQL with the `sel` CTE definition removed."""
    start = sql.index("sel AS (")
    depth, i = 0, sql.index("(", start)
    while True:
        depth += sql[i] == "("
        depth -= sql[i] == ")"
        if depth == 0:
            break
        i += 1
    return sql[:start] + sql[i + 1 :]


@pytest.mark.parametrize("flt", [_flt(), _flt(deny_all=True), _flt(projects=["p"])], ids=["plain", "deny", "projects"])
@pytest.mark.parametrize("method", _WINDOWED_METHODS)
@pytest.mark.asyncio
async def test_window_is_decided_only_in_sel_for_every_windowed_method(method, flt):
    reader, query = _reader()

    await getattr(reader, method)(flt)

    sql = _sql(query)
    rest = _outside_sel(sql)
    assert _WINDOW in sql  # inclusive BETWEEN on started_at: bound-equal sessions are in
    assert "day BETWEEN" not in rest
    assert "Timestamp BETWEEN" not in rest
    assert "started_at BETWEEN" not in rest
    assert "FROM sel" in rest or "JOIN sel" in rest or "IN (SELECT session_id FROM sel)" in rest
    assert "LEFT JOIN sel" not in rest


@pytest.mark.parametrize("method", ["get_tool_success_by_session", "get_tool_usage"])
@pytest.mark.asyncio
async def test_exec_span_subquery_is_scoped_to_sel(method):
    reader, query = _reader()

    await getattr(reader, method)(_flt())

    assert _sql(query).count("IN (SELECT session_id FROM sel)") == 2


@pytest.mark.parametrize(
    "method",
    ["get_cost_by_user", "get_users", "get_users_daily_activity", "get_lines_by_user", "get_session_cost_facts"],
)
@pytest.mark.asyncio
async def test_inner_joined_methods_carry_no_redundant_scope_or_placeholder(method):
    reader, query = _reader()

    await getattr(reader, method)(_flt())

    sql = _sql(query)
    assert "WHERE 1 = 1" not in sql
    assert _outside_sel(sql).count("SELECT session_id FROM sel") == 0
    assert "JOIN sel" in sql


_START = datetime(2026, 9, 1, 0, 0, 0)
_END = datetime(2026, 9, 8, 23, 59, 59, 999000)


@pytest.mark.parametrize("method", _WINDOWED_METHODS)
@pytest.mark.asyncio
async def test_window_bounds_are_bound_exactly_and_compared_inclusively(method):
    # start_dt / end_dt are bound as-is, so a session starting exactly at either bound satisfies the
    # inclusive BETWEEN, while one starting a millisecond before start_dt or after end_dt does not.
    reader, query = _reader()
    flt = LocalAnalyticsFilter(start_dt=_START, end_dt=_END)

    await getattr(reader, method)(flt)

    params = query.call_args.args[1]
    assert params["start_dt"] == _START
    assert params["end_dt"] == _END
    cte = _cte_sql(flt)
    assert _WINDOW in cte
    for op in ("started_at >", "started_at <", "started_at >=", "started_at <="):
        assert op not in cte
