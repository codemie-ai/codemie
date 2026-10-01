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

"""PostgreSQL reader: parameter building, row normalisation, time windows and the SQL of each query."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import CliAnalyticsReader
from codemie.repository.cli_analytics.postgres.reader import (
    PostgresCliAnalyticsReader,
    normalise,
    positional,
    truncate_to_ms,
    window_params,
)
from tests.codemie.repository.cli_analytics.support.contracts import assert_implements_port, port_methods

UTC = timezone.utc


def _flt(**kwargs) -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(start_dt=datetime(2026, 9, 1, 8, 30), end_dt=datetime(2026, 9, 8, 17, 45), **kwargs)


def test_reader_implements_the_reader_port():
    assert_implements_port(PostgresCliAnalyticsReader, CliAnalyticsReader)


def test_named_parameters_become_positional_in_first_use_order():
    sql, args = positional("SELECT $b, $a::text, $b", {"a": 1, "b": 2, "unused": 3})

    assert sql == "SELECT $1, $2::text, $1"
    assert args == [2, 1]


def test_a_missing_parameter_fails_loudly():
    with pytest.raises(KeyError):
        positional("SELECT $nope", {})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime(2026, 9, 1, 12, tzinfo=timezone(timedelta(hours=2))), datetime(2026, 9, 1, 10)),
        (datetime(2026, 9, 1, 12), datetime(2026, 9, 1, 12)),
        (Decimal("42"), 42),
        (Decimal("0.125"), 0.125),
        (date(2026, 9, 1), date(2026, 9, 1)),
        (["a", "b"], ["a", "b"]),
        (None, None),
        (1.5, 1.5),
    ],
)
def test_values_are_normalised_to_the_clickhouse_row_contract(value, expected):
    result = normalise(value)

    assert result == expected
    assert type(result) is type(expected)


def test_window_bounds_are_truncated_to_milliseconds_and_naive_means_utc():
    assert truncate_to_ms(datetime(2026, 9, 1, 8, 30, 0, 123999)) == datetime(2026, 9, 1, 8, 30, 0, 123000, tzinfo=UTC)
    assert truncate_to_ms(datetime(2026, 9, 1, 10, tzinfo=timezone(timedelta(hours=2)))) == datetime(
        2026, 9, 1, 8, tzinfo=UTC
    )


def test_window_params_carry_the_bounds_and_filters():
    p = window_params(_flt(users=["Dev@X.com"], projects=["p"], repositories=["r"], branch="main"))

    assert "d0" not in p and "d1" not in p
    assert p["start_dt"] == datetime(2026, 9, 1, 8, 30, tzinfo=UTC)
    assert (p["users"], p["projects"], p["repositories"], p["branch"]) == (["dev@x.com"], ["p"], ["r"], "main")


def test_window_params_for_deny_all_carry_no_filter_values():
    p = window_params(_flt(deny_all=True, projects=["p"]))

    assert set(p) == {"start_dt", "end_dt"}


@pytest.mark.asyncio
async def test_skill_names_for_no_session_run_no_query():
    class NoEngine:
        @asynccontextmanager
        async def acquire(self, timeout=None):
            raise AssertionError("no query expected")
            yield

    assert await PostgresCliAnalyticsReader(NoEngine()).get_skill_names_by_session([]) == []  # type: ignore[arg-type]


def test_deny_all_selects_no_session():
    assert "WHERE false" in PostgresCliAnalyticsReader._sessions_cte(_flt(deny_all=True))
    assert "WHERE false" not in PostgresCliAnalyticsReader._sessions_cte(_flt())
    assert PostgresCliAnalyticsReader._scope(_flt(deny_all=True)) != ""


def test_filters_become_array_parameters():
    cte = PostgresCliAnalyticsReader._sessions_cte(_flt(users=["u"], projects=["p"], repositories=["r"], branch="b"))

    assert "lower(user_email) = ANY($users::text[])" in cte
    assert "project_name = ANY($projects::text[])" in cte
    assert "repository = ANY($repositories::text[])" in cte
    assert "branch = $branch::text" in cte


# ── session scope of every windowed query ──

_FACT_TABLES = (
    "cost_daily",
    "lines_daily",
    "active_time_daily",
    "turns_daily",
    "tool_facts_daily",
    "session_files_daily",
    "invocations_hourly",
    "session_skills",
    "log_events",
    "spans",
    "hook_events",
    "metric_points",
)
_FACT_REFERENCE = re.compile(rf"\b(?:FROM|JOIN)\s+(?:{'|'.join(_FACT_TABLES)})\b")
_DECLARED = re.compile(
    r"\b(?:FROM|JOIN)\s+(\w+)(?:\s+(?:AS\s+)?(?!WHERE\b|ON\b|JOIN\b|GROUP\b|LEFT\b)(\w+))?|\)\s+(?:AS\s+)?(\w+)"
)
_QUALIFIED = re.compile(r"\b([a-z_]\w*)\.[a-z_]\w*")
_SCOPE_SUBQUERY = "SELECT session_id FROM sel"


class _Block:
    """A parenthesised part of a statement: its own text, with nested blocks as `(#n)`."""

    def __init__(self) -> None:
        self.text = ""
        self.children: list[_Block] = []


def _blocks(sql: str) -> list[_Block]:
    root, stack, blocks = _Block(), [], []
    current = root
    for char in re.sub(r"'[^']*'", "''", sql):  # no literal can hide a parenthesis or a dot
        if char == "(":
            child = _Block()
            current.text += f"(#{len(current.children)})"
            current.children.append(child)
            stack.append(current)
            current = child
        elif char == ")":
            blocks.append(current)
            current = stack.pop()
        else:
            current.text += char
    return [root, *blocks]


def _unscoped_fact_branches(sql: str) -> list[str]:
    """UNION branches reading a fact table without the `sel` session scope.

    A branch is scoped by `... IN (SELECT session_id FROM sel)` or by joining `sel`; a
    correlated subquery (naming an alias of the query around it) is scoped by that query.
    """
    unscoped = []
    for block in _blocks(sql):
        for branch in re.split(r"\bUNION(?:\s+ALL)?\b", block.text):
            if not _FACT_REFERENCE.search(branch):
                continue
            nested = [" ".join(child.text.split()) for child in block.children]
            scoped = any(f"(#{i})" in branch and text == _SCOPE_SUBQUERY for i, text in enumerate(nested)) or re.search(
                r"\b(?:FROM|JOIN)\s+sel\b", branch
            )
            declared = {name for match in _DECLARED.finditer(branch) for name in match.groups() if name}
            correlated = any(alias not in declared for alias in _QUALIFIED.findall(branch))
            if not (scoped or correlated):
                unscoped.append(" ".join(branch.split()))
    return unscoped


class _SqlRecorder:
    def __init__(self) -> None:
        self.statements: list[str] = []

    @asynccontextmanager
    async def acquire(self, timeout=None):
        yield self

    async def fetch(self, sql, *args):
        self.statements.append(sql)
        return []


_WINDOWED = [
    name
    for name in port_methods(CliAnalyticsReader)
    if not name.startswith("get_session_detail_") and name != "get_skill_names_by_session"
]


@pytest.mark.parametrize("filters", [{"deny_all": True}, {"projects": ["p"]}], ids=["deny_all", "projects"])
@pytest.mark.parametrize("method", _WINDOWED)
@pytest.mark.asyncio
async def test_every_fact_a_windowed_query_reads_is_scoped_to_the_selected_sessions(method, filters):
    # Missing in one branch, a project admin would see other projects' facts there.
    recorder = _SqlRecorder()

    await getattr(PostgresCliAnalyticsReader(recorder), method)(_flt(**filters))  # type: ignore[arg-type]

    assert recorder.statements
    for sql in recorder.statements:
        assert _unscoped_fact_branches(sql) == []


def test_the_scope_audit_finds_a_branch_without_the_scope():
    sql = (
        "WITH sel AS (SELECT session_id FROM session_dims) "
        "SELECT name FROM invocations_hourly WHERE kind = 1 AND session_id IN (SELECT session_id FROM sel) "
        "UNION ALL SELECT skill_name FROM spans WHERE span_kind = 3"
    )

    assert _unscoped_fact_branches(sql) == ["SELECT skill_name FROM spans WHERE span_kind = 3"]


# ── session-start window (the predicate lives in `sel`) ──


async def _sql_of(method: str, filters: dict | None = None, **kwargs) -> tuple[str, list]:
    recorder = _SqlRecorder()
    await getattr(PostgresCliAnalyticsReader(recorder), method)(_flt(**(filters or {})), **kwargs)  # type: ignore[arg-type]
    assert len(recorder.statements) == 1
    return " ".join(recorder.statements[0].split()), recorder.statements


_WINDOW_PREDICATE = "started_at BETWEEN $start_dt::timestamptz AND $end_dt::timestamptz"


@pytest.mark.parametrize(
    "filters", [{}, {"deny_all": True}, {"projects": ["p"]}], ids=["unfiltered", "deny", "projects"]
)
def test_sessions_cte_decides_the_window_by_session_start(filters):
    cte = " ".join(PostgresCliAnalyticsReader._sessions_cte(_flt(**filters)).split())

    assert f"d.started_at IS NOT NULL AND {_WINDOW_PREDICATE}" in cte


def test_scope_always_restricts_to_the_selected_sessions():
    assert "SELECT session_id FROM sel" in PostgresCliAnalyticsReader._scope(_flt())


@pytest.mark.asyncio
async def test_session_durations_have_no_overlap_check():
    sql, _ = await _sql_of("get_session_durations")

    assert "last_event_at >=" not in sql
    assert "FROM sel" in sql


@pytest.mark.asyncio
async def test_repository_sessions_read_the_windowed_sel():
    sql, _ = await _sql_of("get_repository_sessions")

    assert "FROM sel" in sql
    assert re.search(r"started_at BETWEEN \$\d+::timestamptz AND \$\d+::timestamptz", sql)


@pytest.mark.asyncio
async def test_session_start_times_rely_on_sel_alone():
    sql, _ = await _sql_of("get_session_start_times")

    assert sql.count("BETWEEN") == 1  # only the one inside sel


@pytest.mark.asyncio
async def test_users_last_active_has_no_overlap_where():
    sql, _ = await _sql_of("get_users_last_active")

    assert "d.last_event_at >=" not in sql
    assert "d.started_at <=" not in sql


@pytest.mark.asyncio
async def test_a_window_without_session_starts_returns_no_rows():
    recorder = _SqlRecorder()

    assert await PostgresCliAnalyticsReader(recorder).get_session_durations(_flt()) == []  # type: ignore[arg-type]


def test_window_bounds_are_inclusive_and_millisecond_truncated():
    p = window_params(LocalAnalyticsFilter(start_dt=datetime(2026, 9, 1, 0, 0, 0, 123999), end_dt=datetime(2026, 9, 2)))

    assert p["start_dt"] == datetime(2026, 9, 1, 0, 0, 0, 123000, tzinfo=UTC)
    assert p["end_dt"] == datetime(2026, 9, 2, tzinfo=UTC)


# ── cost_daily methods ──


@pytest.mark.parametrize(
    "method", ["get_cost_kpis", "get_model_breakdown", "get_cost_by_user", "get_users", "get_users_daily_activity"]
)
@pytest.mark.asyncio
async def test_cost_methods_have_no_day_window_and_are_bound_to_sel(method):
    sql, _ = await _sql_of(method)

    assert "day BETWEEN" not in sql
    assert "LEFT JOIN sel" not in sql
    assert "JOIN sel" in sql or "IN (SELECT session_id FROM sel)" in sql


@pytest.mark.asyncio
async def test_session_cost_facts_inner_join_sel_without_epoch_defaults():
    sql, _ = await _sql_of("get_session_cost_facts")

    assert "day BETWEEN" not in sql
    assert "1970-01-01" not in sql
    assert "LEFT JOIN sel" not in sql
    assert "JOIN sel" in sql


@pytest.mark.asyncio
async def test_users_daily_activity_still_groups_by_the_fact_day():
    sql, _ = await _sql_of("get_users_daily_activity")

    assert "c.day AS day" in sql
    assert "GROUP BY 1, 2" in sql


# ── lines, turns, files, active time ──


@pytest.mark.parametrize(
    "method",
    [
        "get_lines_totals",
        "get_lines_daily",
        "get_lines_by_user",
        "get_lines_by_session",
        "get_turns_by_session",
        "get_file_facts_by_session",
        "get_active_ms_by_session",
    ],
)
@pytest.mark.asyncio
async def test_fact_methods_have_no_day_window_and_stay_scoped(method):
    sql, _ = await _sql_of(method)

    assert "day BETWEEN" not in sql
    assert "WHERE AND" not in sql
    assert "JOIN sel" in sql or "IN (SELECT session_id FROM sel)" in sql


@pytest.mark.asyncio
async def test_lines_by_user_inner_joins_sel():
    sql, _ = await _sql_of("get_lines_by_user")

    assert "LEFT JOIN sel" not in sql
    assert "JOIN sel" in sql


@pytest.mark.asyncio
async def test_file_facts_scope_both_branches_without_a_window():
    sql, _ = await _sql_of("get_file_facts_by_session")

    assert _unscoped_fact_branches(sql) == []
    assert sql.count("IN (SELECT session_id FROM sel)") == 2


# ── invocations and tools ──


@pytest.mark.parametrize("method", ["get_tool_success_by_session", "get_tool_usage", "get_invocations"])
@pytest.mark.asyncio
async def test_invocation_methods_read_only_the_scoped_hourly_rollup(method):
    sql, _ = await _sql_of(method)

    assert "FROM invocations_hourly" in sql
    assert "IN (SELECT session_id FROM sel)" in sql
    for forbidden in ("hour >=", "ts BETWEEN", "ts >=", "FROM spans", "FROM log_events", "UNION"):
        assert forbidden not in sql


@pytest.mark.parametrize("method", ["get_tool_success_by_session", "get_tool_usage", "get_invocations"])
@pytest.mark.asyncio
async def test_invocation_methods_bind_only_window_and_filter_parameters(method):
    recorder = _SqlRecorder()
    await getattr(PostgresCliAnalyticsReader(recorder), method)(_flt())  # type: ignore[arg-type]

    assert "$3" not in recorder.statements[0]


# -- audit: the window is decided only in sel --

_ALL_FILTERS = [{}, {"deny_all": True}, {"projects": ["p"]}]
_REMOVED_WINDOWS = ("day BETWEEN", "$d0", "$d1", "$h0", "$h1", "_EDGES", "_INNER_HOURS")


@pytest.mark.parametrize("filters", _ALL_FILTERS, ids=["unfiltered", "deny_all", "projects"])
@pytest.mark.parametrize("method", _WINDOWED)
@pytest.mark.asyncio
async def test_no_fact_query_carries_its_own_window_and_all_are_scoped_to_sel(method, filters):
    recorder = _SqlRecorder()

    await getattr(PostgresCliAnalyticsReader(recorder), method)(_flt(**filters))  # type: ignore[arg-type]

    assert recorder.statements
    for sql in recorder.statements:
        assert _unscoped_fact_branches(sql) == []
        for removed in _REMOVED_WINDOWS:
            assert removed not in sql


# -- window boundaries: inclusive BETWEEN evaluated against the parameters actually bound --


class _ArgRecorder(_SqlRecorder):
    def __init__(self) -> None:
        super().__init__()
        self.args: list[tuple] = []

    async def fetch(self, sql, *args):
        self.args.append(args)
        return await super().fetch(sql, *args)


async def _bound_window(f: LocalAnalyticsFilter) -> tuple[datetime, datetime]:
    """The (lower, upper) the database receives for the `started_at BETWEEN` predicate in sel."""
    recorder = _ArgRecorder()
    await PostgresCliAnalyticsReader(recorder).get_session_start_times(f)  # type: ignore[arg-type]
    match = re.search(r"started_at BETWEEN \$(\d+)::timestamptz AND \$(\d+)::timestamptz", recorder.statements[0])
    assert match
    return recorder.args[0][int(match.group(1)) - 1], recorder.args[0][int(match.group(2)) - 1]


def _in_window(started_at: datetime, bounds: tuple[datetime, datetime]) -> bool:
    return bounds[0] <= started_at <= bounds[1]  # SQL BETWEEN is inclusive on both ends


@pytest.mark.parametrize(
    ("offset_from", "delta", "included"),
    [
        ("start", timedelta(0), True),
        ("end", timedelta(0), True),
        ("start", timedelta(milliseconds=-1), False),
        ("end", timedelta(milliseconds=1), False),
        ("start", timedelta(milliseconds=1), True),
        ("end", timedelta(milliseconds=-1), True),
    ],
    ids=["at-start", "at-end", "just-before-start", "just-after-end", "just-after-start", "just-before-end"],
)
@pytest.mark.asyncio
async def test_session_start_boundaries_are_inclusive_at_both_ends(offset_from, delta, included):
    f = _flt()
    bounds = await _bound_window(f)
    anchor = bounds[0] if offset_from == "start" else bounds[1]

    assert _in_window(anchor + delta, bounds) is included


@pytest.mark.asyncio
async def test_bound_window_values_are_the_ms_truncated_filter_bounds():
    f = LocalAnalyticsFilter(
        start_dt=datetime(2026, 9, 1, 8, 30, 0, 999999), end_dt=datetime(2026, 9, 8, 17, 45, 0, 500)
    )

    assert await _bound_window(f) == (
        datetime(2026, 9, 1, 8, 30, 0, 999000, tzinfo=UTC),
        datetime(2026, 9, 8, 17, 45, 0, 0, tzinfo=UTC),
    )
