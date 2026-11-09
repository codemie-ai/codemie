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
from codemie.repository.cli_analytics.ports import SESSION_PAGE_SORTS, CliAnalyticsReader, ModelRates
from codemie.repository.cli_analytics.postgres import session_derived
from codemie.repository.cli_analytics.postgres.fork_attribution import executed_tool_span, tool_response_agent_id
from codemie.repository.cli_analytics.postgres.reader import (
    _C,
    PostgresCliAnalyticsReader,
    model_token_groups,
    normalise,
    positional,
    rates_params,
    session_cost_key_sql,
    truncate_to_ms,
    window_params,
)
from codemie.repository.cli_analytics.postgres.session_derived import (
    CLASSIFIER_NAMES_SQL,
    classifier_names_by_session_sql,
)
from codemie.repository.cli_analytics.scope import (
    ViewerScope,
    normalise_project_origin,
    normalise_subscription_type,
    sql_list_condition,
    sql_norm,
    sql_personal_billed,
    sql_private,
    sql_project_origin,
    sql_subscription_type,
)
from tests.codemie.repository.cli_analytics.support.contracts import assert_implements_port, port_methods
from tests.codemie.repository.cli_analytics.support.scopes import DETAIL_SCOPE, EXCLUDE_SCOPE

UTC = timezone.utc


def _flt(**kwargs) -> LocalAnalyticsFilter:
    kwargs.setdefault("scope", EXCLUDE_SCOPE)
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
def test_values_are_normalised_to_the_port_row_contract(value: object, expected: object) -> None:
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

    assert set(p) - {"scope_viewer"} == {"start_dt", "end_dt"}


@pytest.mark.asyncio
async def test_skill_names_for_no_session_run_no_query():
    class NoEngine:
        @asynccontextmanager
        async def acquire(self, timeout=None):
            raise AssertionError("no query expected")
            yield

    assert await PostgresCliAnalyticsReader(NoEngine()).get_skill_names_by_session([], scope=DETAIL_SCOPE) == []  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_skill_names_read_session_dims_with_the_sql_of_the_classifier():
    # The list and details views must see the skills the delivery framework column is derived from: a
    # session known only from hooks has its skills in session_dims.skills, not in session_skills.
    recorder = _SqlRecorder()

    await PostgresCliAnalyticsReader(recorder).get_skill_names_by_session(["s1", "s2", "s1"], scope=DETAIL_SCOPE)  # type: ignore[arg-type]

    (sql,) = recorder.statements
    text = " ".join(sql.split())
    assert "FROM session_dims sd WHERE sd.session_id = ANY($1::text[])" in text
    assert " ".join(CLASSIFIER_NAMES_SQL.split()) in text
    assert " ".join(session_derived._READ.split()).count(" ".join(CLASSIFIER_NAMES_SQL.split())) == 1
    names_sql = " ".join(CLASSIFIER_NAMES_SQL.split())
    # One unconditional union: the summary timestamp no longer switches sources off.
    assert "CASE WHEN sd.summary_ts" not in names_sql
    assert "summary_ts" not in names_sql
    for fragment in (
        "session_skills",
        "invocations_hourly",
        "i.kind IN (2, 4)",
        "jsonb_array_elements( CASE WHEN jsonb_typeof(sd.commands) = 'array' THEN sd.commands END)",
        "jsonb_typeof(c) = 'string'",
    ):
        assert fragment in names_sql
    # The dispatch and slash-command names have a lower bound only (one hour before the session start, none when the
    # start is unknown); a later row stays, because the dimension hooks that fill `started_at` can end long before.
    assert names_sql.count("i.hour") == 1
    assert (
        "i.hour >= coalesce(date_trunc('hour', sd.started_at) - interval '1 hour', '-infinity'::timestamptz)"
        in names_sql
    )


@pytest.mark.asyncio
async def test_skill_names_bind_the_distinct_session_ids_and_answer_an_empty_list_for_none():
    recorder = _ArgRecorder()

    await PostgresCliAnalyticsReader(recorder).get_skill_names_by_session(["s2", "s1", "s2"], scope=DETAIL_SCOPE)  # type: ignore[arg-type]

    assert recorder.args == [(["s2", "s1"], "admin@x.com")]
    text = " ".join(recorder.statements[0].split())
    # The five sources of the union and the guard that keeps a non-array `commands` from raising.
    assert text.count("UNION") == 4
    for source in (
        "FROM session_skills ss",
        "FROM invocations_hourly i",
        "FROM jsonb_object_keys(CASE WHEN jsonb_typeof(sd.skills) = 'object' THEN sd.skills END) s (name)",
        "FROM jsonb_object_keys(CASE WHEN jsonb_typeof(sd.agents) = 'object' THEN sd.agents END) a (name)",
        "CASE WHEN jsonb_typeof(sd.commands) = 'array' THEN sd.commands END) c WHERE jsonb_typeof(c) = 'string'",
    ):
        assert source in text
    assert "ARRAY[]::text[]) AS skill_names" in text


def test_deny_all_selects_no_session():
    assert " AND false" in PostgresCliAnalyticsReader._sessions_cte(_flt(deny_all=True))
    assert " AND false" not in PostgresCliAnalyticsReader._sessions_cte(_flt())
    assert PostgresCliAnalyticsReader._scope(_flt(deny_all=True)) != ""


def test_filters_become_array_parameters():
    cte = PostgresCliAnalyticsReader._sessions_cte(_flt(users=["u"], projects=["p"], repositories=["r"], branch="b"))

    assert "lower(user_email) = ANY($users::text[])" in cte
    assert "project_name = ANY($projects::text[])" in cte
    assert "repository = ANY($repositories::text[])" in cte
    assert "branch = $branch::text" in cte


def test_project_unattributed_selects_sessions_without_a_project():
    cte = PostgresCliAnalyticsReader._sessions_cte(_flt(project_unattributed=True))

    assert "project_name = ''" in cte
    assert "ANY($projects" not in cte


def test_project_unattributed_wins_over_the_projects_filter():
    cte = PostgresCliAnalyticsReader._sessions_cte(_flt(projects=["p"], project_unattributed=True))

    assert "project_name = ''" in cte
    assert "ANY($projects" not in cte


# ── session scope of every windowed query ──

_FACT_TABLES = (
    "cost_daily",
    "session_usage",
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

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def fetch(self, sql, *args):
        self.statements.append(sql)
        return []


# plus the get_session_detail_* queries
_NOT_WINDOWED = (
    "get_skill_names_by_session",
    "authorize_session",
    "get_session_page",
    "get_cost_page",
    "get_framework_page",
    "get_framework_sessions",
)


class _PageRecorder(_SqlRecorder):
    """`get_session_page` reads the one row its aggregate always returns."""

    async def fetch(self, sql, *args):
        await super().fetch(sql, *args)
        return [] if "count(*) OVER ()" in sql else [{"total": 0, "session_ids": [], "flags": "[]"}]


_PAGE = {"page": 1, "per_page": 20}
# Every windowed query as the session list calls it: the plain ports, the cost-less facts and the SQL pages.
_WINDOWED_CALLS = [
    *[
        pytest.param(name, {}, id=name)
        for name in port_methods(CliAnalyticsReader)
        if name not in _NOT_WINDOWED and not name.startswith("get_session_detail_")
    ],
    pytest.param("get_session_cost_facts", {"include_costless": True}, id="get_session_cost_facts-include_costless"),
    pytest.param("get_session_page", {"sort_by": "start_time", **_PAGE}, id="get_session_page-start_time"),
    pytest.param(
        "get_session_page",
        {"sort_by": "ctx_per_call", "search": "a%b", "is_unattributed": True, **_PAGE},
        id="get_session_page-ctx_per_call",
    ),
    pytest.param(
        "get_session_page",
        {"sort_by": "cost_usd", "rates": [ModelRates(model="m", input=1e-6)], **_PAGE},
        id="get_session_page-cost_usd",
    ),
    pytest.param(
        "get_cost_page",
        {"rates": [ModelRates(model="m", input=1e-6)], "search": "a%b", **_PAGE},
        id="get_cost_page",
    ),
    pytest.param(
        "get_framework_page",
        {
            "sort_by": "cost_usd",
            "matching": lambda sets: sets,
            "rates": [ModelRates(model="m", input=1e-6)],
            **_PAGE,
        },
        id="get_framework_page-cost_usd",
    ),
    pytest.param(
        "get_framework_page",
        {"sort_by": "start_time", "matching": lambda sets: sets, "rates": [], **_PAGE},
        id="get_framework_page-start_time",
    ),
]


async def _call(method: str, kwargs: dict, filters: dict) -> list[str]:
    recorder = (
        _PageRecorder()
        if method in ("get_session_page", "get_cost_page")
        else _FrameworkRecorder()
        if method == "get_framework_page"
        else _SqlRecorder()
    )
    await getattr(PostgresCliAnalyticsReader(recorder), method)(_flt(**filters), **kwargs)  # type: ignore[arg-type]
    return recorder.statements


@pytest.mark.parametrize("filters", [{"deny_all": True}, {"projects": ["p"]}], ids=["deny_all", "projects"])
@pytest.mark.parametrize(("method", "kwargs"), _WINDOWED_CALLS)
@pytest.mark.asyncio
async def test_every_fact_a_windowed_query_reads_is_scoped_to_the_selected_sessions(method, kwargs, filters):
    # Missing in one branch, a project admin would see other projects' facts there.
    statements = await _call(method, kwargs, filters)

    assert statements
    for sql in statements:
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


_START_DAY_UTC = "($start_dt::timestamptz AT TIME ZONE 'UTC')::date"
_END_DAY_UTC = "($end_dt::timestamptz AT TIME ZONE 'UTC')::date"
_FIRST_WHOLE_DAY_UTC = "(($start_dt::timestamptz AT TIME ZONE 'UTC') - interval '1 millisecond')::date + 1"
_LAST_WHOLE_DAY_UTC = "(($end_dt::timestamptz AT TIME ZONE 'UTC') + interval '1 millisecond')::date - 1"
_RAW_TABLES = ("hook_events", "usage_requests", "log_events", "spans", "metric_points")


def _any_activity(alias: str, days: tuple[str, str] | None = None) -> str:
    bound = f" AND ax.day >= {days[0]} AND ax.day <= {days[1]}" if days else ""
    tables = ("turns_daily", "tool_facts_daily", "cost_daily", "session_usage", "lines_daily")
    clauses = " OR ".join(
        f"EXISTS (SELECT 1 FROM {t} ax WHERE ax.session_id = {alias}.session_id{bound})" for t in tables
    )
    return f"({clauses})"


_HAS_ANY_ACTIVITY = _any_activity("d")
_WHOLE_DAYS = _any_activity("d", (_FIRST_WHOLE_DAY_UTC, _LAST_WHOLE_DAY_UTC))
_WINDOW_DAYS = _any_activity("d", (_START_DAY_UTC, _END_DAY_UTC))
_IN_WINDOW = "d.started_at BETWEEN $start_dt::timestamptz AND $end_dt::timestamptz"
# the five arms of the admission rule (see `_admitted_arms`): started (stored fact), started and underived (asks the
# tables), outside and underived (the rule as is), outside and resumed, outside and all rows on one day (the last two
# decide with `_active_in_window` on their candidates only)
_ARM_STARTED = f"AND {_IN_WINDOW} AND d.has_activity UNION ALL"
_ARM_UNDERIVED = f"AND {_IN_WINDOW} AND d.has_activity IS NULL AND {_HAS_ANY_ACTIVITY} UNION ALL"
_ARM_OUTSIDE_UNDERIVED = (
    f"AND NOT ({_IN_WINDOW}) AND d.has_activity IS NULL AND ({_WHOLE_DAYS} OR ({_WINDOW_DAYS} AND ("
)
_ARM_RESUMED = (
    f"AND NOT ({_IN_WINDOW}) AND d.has_activity AND d.resumed AND d.last_activity_day >= {_START_DAY_UTC} AND"
)
_ARM_ONE_DAY = f"AND NOT ({_IN_WINDOW}) AND d.has_activity AND NOT d.resumed AND ((d.started_at >="
_WINDOW_PREDICATE_HEAD = f"{_ARM_STARTED}"


@pytest.mark.parametrize(
    "filters", [{}, {"deny_all": True}, {"projects": ["p"]}], ids=["unfiltered", "deny", "projects"]
)
def test_sessions_cte_decides_the_window_by_session_start(filters):
    cte = " ".join(PostgresCliAnalyticsReader._sessions_cte(_flt(**filters)).split())

    assert _ARM_STARTED in cte
    assert _ARM_UNDERIVED in cte
    assert _ARM_OUTSIDE_UNDERIVED in cte
    assert _ARM_RESUMED in cte
    assert _ARM_ONE_DAY in cte
    assert cte.count("OFFSET 0") > 0  # the derived arms probe the day tables row by row


@pytest.mark.parametrize("table", _RAW_TABLES)
def test_sessions_cte_times_a_partial_window_day_by_its_raw_records(table):
    cte = " ".join(PostgresCliAnalyticsReader._sessions_cte(_flt()).split())

    assert (
        f"EXISTS (SELECT 1 FROM {table} r WHERE r.session_id = d.session_id "
        "AND r.ts >= $start_dt::timestamptz AND r.ts <= $end_dt::timestamptz"
    ) in cte


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
    assert re.search(r"EXISTS \(SELECT 1 FROM turns_daily ax WHERE ax\.session_id = d\.session_id", sql)


@pytest.mark.asyncio
async def test_framework_sessions_group_raw_keys_and_bind_the_window_and_label():
    recorder = _ArgRecorder()
    start, end = datetime(2026, 9, 1, 8, 30), datetime(2026, 9, 8, 17, 45)

    rows = await PostgresCliAnalyticsReader(recorder).get_framework_sessions(start, end, ["STLC Factory", "SDLC"])  # type: ignore[arg-type]

    sql = " ".join(recorder.statements[0].split())
    assert rows == []
    assert "started_at BETWEEN $1::timestamptz AND $2::timestamptz" in sql
    assert "delivery_framework = ANY($3::text[])" in sql
    assert "delivery_framework <>" not in sql  # the current labels are matched, not "anything but the fallback"
    assert "GROUP BY 1, 2, 3" in sql
    # Global per person and repository like the Elasticsearch side: no project predicate.
    assert "project_name" not in sql.lower()
    assert "NOT " + sql_private("d") in sql
    assert recorder.args[0] == (
        datetime(2026, 9, 1, 8, 30, tzinfo=UTC),
        datetime(2026, 9, 8, 17, 44, 59, 999000, tzinfo=UTC),
        ["STLC Factory", "SDLC"],
    )


@pytest.mark.asyncio
async def test_session_start_times_rely_on_sel_alone():
    sql, _ = await _sql_of("get_session_start_times")

    assert sql.count("BETWEEN") == 5  # only inside sel: one per admission arm (the activity probes use >=/<=)


@pytest.mark.parametrize("method", ["get_session_durations", "get_session_cost_facts", "get_session_start_times"])
@pytest.mark.asyncio
async def test_session_queries_report_the_activity_range(method):
    # Hook times lag the session's OTel events: the reported range also reads log_events and spans.
    sql, _ = await _sql_of(method)

    assert "act AS (" in sql
    assert "least(" in sql and "greatest(" in sql
    assert "FROM log_events" in sql and "FROM spans" in sql
    assert sql.count("BETWEEN") == 5  # the window is still decided only in sel (one per admission arm)


@pytest.mark.asyncio
async def test_activity_probes_are_bounded_by_a_day_around_the_hook_times():
    sql, _ = await _sql_of("get_session_durations")

    assert sql.count("- interval '1 day'") == 2  # the earliest log_events / spans probes
    assert sql.count("+ interval '1 day'") == 2  # the latest ones
    assert re.search(r"\.ts < coalesce\(least\(", sql)  # an event at the candidate start is not earlier
    assert re.search(r"\.ts > coalesce\(greatest\(", sql)


@pytest.mark.asyncio
async def test_users_last_active_has_no_overlap_where():
    sql, _ = await _sql_of("get_users_last_active")

    assert "d.last_event_at >=" not in sql
    assert "d.started_at <=" not in sql


@pytest.mark.asyncio
async def test_a_window_without_session_starts_returns_no_rows():
    recorder = _SqlRecorder()

    assert await PostgresCliAnalyticsReader(recorder).get_session_durations(_flt()) == []  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_session_detail_meta_resolves_the_earliest_service_name():
    recorder = _SqlRecorder()
    reader = PostgresCliAnalyticsReader(recorder)

    await reader.get_session_detail_meta("s1", scope=DETAIL_SCOPE)

    sql = " ".join(recorder.statements[0].split())
    assert "otel_resources" in sql
    assert "harness_service_name" in sql
    assert "ORDER BY" in sql.upper()
    assert (
        "coalesce(nullif(btrim(o.service_name, E' \\t\\r\\n\\u00a0'), ''), "
        "nullif(btrim(d.platform, E' \\t\\r\\n\\u00a0'), ''))"
    ) in sql
    assert "WHERE nullif(btrim(ho.service_name, E' \\t\\r\\n\\u00a0'), '') IS NOT NULL ORDER BY" in sql


@pytest.mark.asyncio
async def test_the_selected_sessions_carry_the_session_title():
    recorder = _SqlRecorder()

    await PostgresCliAnalyticsReader(recorder).get_session_cost_facts(_flt())  # type: ignore[arg-type]

    sql = " ".join(recorder.statements[0].split())
    assert "coalesce(d.session_title, '') AS session_title" in sql
    assert "max(s.session_title) AS session_title" in sql
    assert "max(s.first_prompt) AS prompt" in sql


@pytest.mark.asyncio
async def test_session_detail_meta_reads_the_session_title_next_to_the_first_prompt():
    recorder = _SqlRecorder()

    await PostgresCliAnalyticsReader(recorder).get_session_detail_meta("s1", scope=DETAIL_SCOPE)  # type: ignore[arg-type]

    sql = " ".join(recorder.statements[0].split())
    assert "nullif(d.session_title, '') AS session_title" in sql
    assert "nullif(d.first_prompt, '') AS prompt" in sql


@pytest.mark.asyncio
async def test_session_detail_meta_reads_the_same_activity_range_as_the_list():
    # One definition: the same day-bounded probes, open-ended when the session has no session_dims row.
    recorder = _SqlRecorder()

    await PostgresCliAnalyticsReader(recorder).get_session_detail_meta("s1", scope=DETAIL_SCOPE)  # type: ignore[arg-type]

    sql = " ".join(recorder.statements[0].split())
    assert "FROM log_events le" in sql and "FROM spans sp" in sql
    assert sql.count("- interval '1 day'") == 2 and sql.count("+ interval '1 day'") == 2
    assert "'-infinity'" in sql and "'infinity'" in sql
    assert "le.session_id = t.session_id" in sql  # keyed on the cost row's session, not the dims row


@pytest.mark.asyncio
async def test_session_detail_meta_service_name_tiebreak_is_deterministic():
    # Two rows (within or across log_events/spans) for the same session can
    # share the exact same ts. Without a secondary sort key Postgres makes no
    # guarantee which one LIMIT 1 returns, so harness_name could flip between
    # requests. resource_id ASC after ts ASC pins the winner to whichever
    # resource_id sorts first, every time.
    recorder = _SqlRecorder()
    reader = PostgresCliAnalyticsReader(recorder)

    await reader.get_session_detail_meta("s1", scope=DETAIL_SCOPE)

    sql = " ".join(recorder.statements[0].split())
    assert "ORDER BY e.ts ASC, e.resource_id ASC" in sql


def test_window_bounds_are_inclusive_and_millisecond_truncated():
    p = window_params(
        LocalAnalyticsFilter(
            start_dt=datetime(2026, 9, 1, 0, 0, 0, 123999), end_dt=datetime(2026, 9, 2), scope=EXCLUDE_SCOPE
        )
    )

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


_NORMALIZED_MODEL_NAME_SQL = (
    "regexp_replace(regexp_replace(regexp_replace(d.model_name, '^(bedrock/)?(converse|invoke)/', '', 'i'), "
    "'^([a-z0-9-]+\\.)?anthropic\\.', '', 'i'), '(\\d{8})-v\\d+:\\d+$', '\\1')"
)


def test_normalized_model_name_sql_expression_is_pinned():
    assert PostgresCliAnalyticsReader._normalized_model_name("d.model_name") == _NORMALIZED_MODEL_NAME_SQL


@pytest.mark.asyncio
async def test_model_breakdown_groups_by_the_normalized_expression():
    sql, _ = await _sql_of("get_model_breakdown")

    assert f"{_NORMALIZED_MODEL_NAME_SQL} AS model_name" in sql
    assert f"GROUP BY {_NORMALIZED_MODEL_NAME_SQL}" in sql
    assert f"HAVING {_NORMALIZED_MODEL_NAME_SQL} <> ''" in sql
    assert "GROUP BY d.model_name" not in sql
    assert "HAVING d.model_name <> ''" not in sql
    assert f"{_NORMALIZED_MODEL_NAME_SQL} {_C}" in sql


@pytest.mark.asyncio
async def test_model_breakdown_order_by_repeats_the_full_normalized_expression():
    # Regression: ORDER BY ..., model_name COLLATE "C" looks like a reference to the
    # SELECT list's output alias, but attaching COLLATE makes it a general expression,
    # so Postgres resolves the bare identifier against the FROM-clause column
    # (cost_daily.model_name) instead of the alias. That raw column is no longer in
    # GROUP BY (the normalized expression is), so Postgres raises a GroupingError at
    # runtime. ORDER BY must repeat the full normalized expression, not the bare alias.
    sql, _ = await _sql_of("get_model_breakdown")

    assert f"ORDER BY session_count DESC, cost_usd DESC, {_NORMALIZED_MODEL_NAME_SQL} {_C}" in sql


def _apply(raw: str) -> str:
    s = re.sub(r"^(bedrock/)?(converse|invoke)/", "", raw, flags=re.IGNORECASE)
    s = re.sub(r"^([a-z0-9-]+\.)?anthropic\.", "", s, flags=re.IGNORECASE)
    return re.sub(r"(\d{8})-v\d+:\d+$", r"\1", s)


def test_normalization_collapses_known_bug_variants_but_not_distinct_models():
    variants = [
        "converse/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "converse/global.anthropic.claude-haiku-4-5-20251001-v1:0",
        "converse/eu.anthropic.claude-haiku-4-5-20251001-v1:0",
        "invoke/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "us-gov-west-1.anthropic.claude-haiku-4-5-20251001-v1:0",
        "anthropic.claude-haiku-4-5-20251001-v1:0",
        "claude-haiku-4-5-20251001",
    ]
    assert {_apply(v) for v in variants} == {"claude-haiku-4-5-20251001"}
    assert _apply("claude-sonnet-5") == "claude-sonnet-5"
    assert _apply("claude-sonnet-5-5") == "claude-sonnet-5-5"
    assert _apply("claude-sonnet-5") != _apply("claude-sonnet-5-5")


def test_normalization_handles_invoke_routing_prefix_and_hyphenated_digit_region_tokens():
    # CR-001: the InvokeModel API path routes through 'invoke/', not just 'converse/'.
    assert _apply("invoke/anthropic.claude-haiku-4-5-20251001-v1:0") == "claude-haiku-4-5-20251001"
    # CR-002: a GovCloud-style region token has hyphens and digits, not just letters.
    assert _apply("us-gov-west-1.anthropic.claude-haiku-4-5-20251001-v1:0") == "claude-haiku-4-5-20251001"


def test_normalization_does_not_collapse_a_models_own_trailing_version_shape():
    # CR-003: the trailing-suffix strip must only match a recognized Bedrock
    # inference-profile tag, i.e. one immediately preceded by an 8-digit date
    # stamp. A model id that merely happens to end in '-v<digits>:<digits>'
    # without that date stamp in front keeps its own identifier untouched.
    assert _apply("my-custom-model-v1:0") == "my-custom-model-v1:0"
    assert _apply("my-custom-model-v1:0") != _apply("my-custom-model")


def test_normalization_is_case_insensitive():
    # CR-001 (standalone re-review): confirmed on a live PostgreSQL instance that the
    # prior case-sensitive regexp_replace calls left an uppercase-routed variant
    # ("CONVERSE/US.ANTHROPIC....") completely unstripped, so it did not merge with its
    # canonical lowercase sibling. The 'i' flag added to the first two regexp_replace
    # calls fixes this; the Python mirror must match.
    assert _apply("CONVERSE/US.ANTHROPIC.claude-haiku-4-5-20251001-v1:0") == "claude-haiku-4-5-20251001"


def test_normalization_strips_a_nested_bedrock_provider_wrapper():
    # CR-002 (standalone re-review): LiteLLM's own Bedrock provider convention nests the
    # API-path marker behind a 'bedrock/' provider prefix (e.g. 'bedrock/converse/...'),
    # documented in LiteLLM's model-routing strings even though not yet observed in this
    # telemetry source. Confirmed on a live PostgreSQL instance that the prior pattern left
    # 'bedrock/converse/us.anthropic....' completely unstripped; the optional '(bedrock/)?'
    # wrapper fixes this without affecting the already-supported bare 'converse/'/'invoke/'.
    assert _apply("bedrock/converse/us.anthropic.claude-haiku-4-5-20251001-v1:0") == "claude-haiku-4-5-20251001"
    assert _apply("bedrock/invoke/anthropic.claude-haiku-4-5-20251001-v1:0") == "claude-haiku-4-5-20251001"
    # the bare (non-nested) forms must still work unchanged
    assert _apply("converse/us.anthropic.claude-haiku-4-5-20251001-v1:0") == "claude-haiku-4-5-20251001"
    assert _apply("Converse/Us.Anthropic.claude-haiku-4-5-20251001-v1:0") == "claude-haiku-4-5-20251001"


def test_aggregation_merges_session_count_and_cost_across_raw_name_variants():
    # CR-005: no prior test asserted the actual aggregation outcome (session_count/cost_usd/
    # total_tokens) after grouping by the normalized expression, only the SQL shape and the
    # regex mirror in isolation. This simulates get_model_breakdown's grouping semantics
    # (count(DISTINCT session_id), sum(cost_usd)) in Python over cost_daily-shaped rows whose
    # raw model_name varies per row but denotes the same canonical model, mirroring what was
    # verified end-to-end against a live PostgreSQL instance (see docs/superpowers/reviews/
    # 2026-10-20-mr-4489/verify_model_breakdown.py).
    rows = [
        {"session_id": "s1", "model_name": "converse/us.anthropic.claude-haiku-4-5-20251001-v1:0", "cost_usd": 1.0},
        {
            "session_id": "s2",
            "model_name": "converse/global.anthropic.claude-haiku-4-5-20251001-v1:0",
            "cost_usd": 2.0,
        },
        {"session_id": "s3", "model_name": "claude-haiku-4-5-20251001", "cost_usd": 3.0},
        {"session_id": "s4", "model_name": "claude-sonnet-5", "cost_usd": 5.0},
        {"session_id": "s5", "model_name": "claude-sonnet-5-5", "cost_usd": 7.0},
    ]
    by_canonical: dict[str, list[dict]] = {}
    for row in rows:
        by_canonical.setdefault(_apply(row["model_name"]), []).append(row)

    assert set(by_canonical) == {"claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-sonnet-5-5"}

    haiku_rows = by_canonical["claude-haiku-4-5-20251001"]
    assert len({r["session_id"] for r in haiku_rows}) == 3  # COUNT(DISTINCT session_id)
    assert sum(r["cost_usd"] for r in haiku_rows) == 6.0  # sum(cost_usd)

    assert {r["session_id"] for r in by_canonical["claude-sonnet-5"]} == {"s4"}
    assert sum(r["cost_usd"] for r in by_canonical["claude-sonnet-5"]) == 5.0
    assert {r["session_id"] for r in by_canonical["claude-sonnet-5-5"]} == {"s5"}
    assert sum(r["cost_usd"] for r in by_canonical["claude-sonnet-5-5"]) == 7.0


@pytest.mark.asyncio
async def test_session_model_costs_groups_by_session_and_model():
    sql, _ = await _sql_of("get_session_model_costs")
    assert "GROUP BY c.session_id, c.model_name" in sql
    assert "<> ''" not in sql
    for column in (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "cache_creation_5m_tokens",
        "cache_creation_1h_tokens",
    ):
        assert f"sum(c.{column})::bigint AS {column}" in sql
    assert f"{PostgresCliAnalyticsReader._normalized_model_name('c.model_name')} AS normalized_model_name" in sql


@pytest.mark.asyncio
async def test_session_cost_facts_inner_join_sel_without_epoch_defaults():
    sql, _ = await _sql_of("get_session_cost_facts")

    assert "day BETWEEN" not in sql
    assert "1970-01-01" not in sql
    assert "LEFT JOIN sel" not in sql
    assert "JOIN sel" in sql


@pytest.mark.asyncio
async def test_session_model_costs_scope_the_subagent_totals_branch_to_sel():
    sql, _ = await _sql_of("get_session_model_costs")

    assert "FROM cost_daily c WHERE c.session_id IN (SELECT session_id FROM sel)" in sql
    assert (
        "FROM session_usage u WHERE u.attrs->>'token_source' = 'subagent_usage' "
        "AND u.session_id IN (SELECT session_id FROM sel)"
    ) in sql


_TOTALS_SUBQUERY = re.compile(
    r"LEFT JOIN \(SELECT session_id, (?:sum\(coalesce\((\w+), 0\)\) AS \w+(?:, )?){4} FROM session_usage "
    r"WHERE attrs->>'token_source' = 'subagent_usage' AND session_id IN \(SELECT session_id FROM sel\) "
    r"GROUP BY session_id\) t ON t\.session_id = s\.session_id"
)


@pytest.mark.asyncio
async def test_costless_session_facts_join_one_scoped_totals_row_per_session_and_never_sum_it():
    sql, _ = await _sql_of("get_session_cost_facts", include_costless=True)

    # The subagent totals are one row per selected session (GROUP BY session_id, scoped to sel) ...
    assert _TOTALS_SUBQUERY.search(sql)
    # ... so the outer GROUP BY takes them with max, never a sum that would multiply them by the cost rows.
    for column in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
        assert f"max(t.{column})" in sql
    assert "sum(t." not in sql
    # Both the cost rows and the totals are optional, so a session without either is still listed.
    assert re.search(r"sel s JOIN act a ON a\.session_id = s\.session_id LEFT JOIN \(SELECT c\.\*", sql)
    assert ") c ON c.session_id = s.session_id LEFT JOIN (SELECT session_id, sum(coalesce(" in sql
    assert re.findall(r"(?<!LEFT )JOIN \(", sql) == []


class _PageArgRecorder(_PageRecorder):
    def __init__(self) -> None:
        super().__init__()
        self.args: list[tuple] = []

    async def fetch(self, sql, *args):
        self.args.append(args)
        return await super().fetch(sql, *args)


async def _page_query(**kwargs) -> tuple[str, tuple]:
    recorder = _PageArgRecorder()
    await PostgresCliAnalyticsReader(recorder).get_session_page(_flt(), **kwargs)  # type: ignore[arg-type]
    return " ".join(recorder.statements[0].split()), recorder.args[0]


_RATES = [ModelRates(model="m", input=1e-6)]


@pytest.mark.parametrize("sort_by", SESSION_PAGE_SORTS)
@pytest.mark.asyncio
async def test_the_session_page_is_limited_and_offset_in_sql_over_sessions_ordered_by_key_then_id(sort_by):
    sql, args = await _page_query(sort_by=sort_by, page=2, per_page=10, rates=_RATES)

    assert re.search(r"LIMIT \$(\d+)::bigint OFFSET \$(\d+)::bigint", sql)
    assert 10 in args and 20 in args  # per_page and page * per_page, bound, never interpolated
    if sort_by == "start_time":  # cut from the started arm's first rows and the other arms' (the 'candidates' CTE)
        assert f'ORDER BY started_at DESC, session_id {_C} LIMIT' in sql
    else:
        assert f'ORDER BY sort_key DESC, session_id {_C} LIMIT' in sql
    if sort_by != "start_time":  # the start_time count is its own statement (below)
        assert "(SELECT count(*) FROM filtered)" in sql  # the total counts sessions, not the page
    assert "sel s" in sql and "FROM sel" in sql  # the user / project scope stays in force


@pytest.mark.asyncio
async def test_a_huge_session_page_binds_a_clamped_offset_that_fits_bigint():
    sql, args = await _page_query(sort_by="start_time", page=10**18, per_page=50)

    assert re.search(r"LIMIT \$(\d+)::bigint OFFSET \$(\d+)::bigint", sql)
    assert 4611686018427387904 in args  # 2**62, not 5 * 10**19 that overflows int64 in the driver
    assert max(a for a in args if isinstance(a, int)) <= 2**63 - 1


@pytest.mark.asyncio
async def test_the_python_paged_session_facts_carry_no_limit_or_offset():
    sql, _ = await _sql_of("get_session_cost_facts", include_costless=True)

    assert "LIMIT" not in sql
    assert not re.search(r"OFFSET (?!0\b)", sql)  # `OFFSET 0` only fences the per-row activity probes


@pytest.mark.asyncio
async def test_the_session_page_search_is_escaped_and_bound_never_interpolated():
    sql, args = await _page_query(sort_by="start_time", page=0, per_page=5, search="  50%_A\\B ")

    assert sql.count("ILIKE $") == 4 and sql.count("::text ESCAPE '\\'") == 4
    assert "s.session_title ILIKE" in sql and "s.repository ILIKE" in sql and "s.branch ILIKE" in sql
    assert "50" not in sql
    assert "%50\\%\\_a\\\\b%" in args  # trimmed, lower-cased, % _ and \ made literal


@pytest.mark.asyncio
async def test_a_search_with_a_nul_character_matches_nothing_without_a_query():
    recorder = _PageRecorder()

    page = await PostgresCliAnalyticsReader(recorder).get_session_page(  # type: ignore[arg-type]
        _flt(), "start_time", 0, 5, search="a\x00b"
    )

    assert page == (0, []) and recorder.statements == []


@pytest.mark.asyncio
async def test_the_session_page_without_search_binds_none():
    sql, _ = await _page_query(sort_by="start_time", page=0, per_page=5)

    assert "ILIKE" not in sql and "$search" not in sql


@pytest.mark.asyncio
async def test_the_session_page_unattributed_filter_is_applied_before_the_count_and_the_page():
    sql, _ = await _page_query(sort_by="start_time", page=0, per_page=5, is_unattributed=True)

    assert "WHERE true AND s.repository = ''" in sql
    assert sql.index("s.repository = ''") < sql.index("LIMIT $")


@pytest.mark.asyncio
async def test_an_unattributed_start_time_page_is_one_pass_over_the_window_not_an_index_walk():
    """The repository = '' filter is applied after the heap fetch, so an ordered `started_at` walk could read most
    of the window before it finds a page when unattributed sessions are rare: it takes the single-pass statement."""
    narrowed = _PageArgRecorder()
    await PostgresCliAnalyticsReader(narrowed).get_session_page(  # type: ignore[arg-type]
        _flt(), "start_time", 0, 20, is_unattributed=True
    )
    plain = _PageArgRecorder()
    await PostgresCliAnalyticsReader(plain).get_session_page(_flt(), "start_time", 0, 20)  # type: ignore[arg-type]

    assert len(narrowed.statements) == 1 and len(plain.statements) == 2
    assert "s.repository = ''" in narrowed.statements[0]


@pytest.mark.asyncio
async def test_the_start_time_page_and_count_are_separate_lean_statements_without_the_prompt_columns():
    recorder = _PageArgRecorder()
    await PostgresCliAnalyticsReader(recorder).get_session_page(_flt(), "start_time", 1, 20)  # type: ignore[arg-type]
    page_sql, count_sql = (" ".join(s.split()) for s in recorder.statements)

    assert "LIMIT" in page_sql and "LIMIT" not in count_sql and "count(*)" in count_sql
    assert "first_prompt" not in page_sql and "first_prompt" not in count_sql  # lean: no wide columns


@pytest.mark.asyncio
async def test_the_ctx_per_call_page_rounds_to_nine_decimals_and_is_zero_without_calls():
    sql, _ = await _page_query(sort_by="ctx_per_call", page=0, per_page=5)

    assert "WHEN coalesce(c.calls, 0) = 0 THEN 0::numeric" in sql
    assert ", 9) END AS sort_key" in sql
    assert "coalesce(t.cache_read_tokens, 0)" in sql  # the subagent totals count, as in the list row


def _inner_projection(sql: str) -> str:
    """The columns `sel` reads from session_dims (the last SELECT before its FROM session_dims)."""
    head = sql.split("sel AS (", 1)[1].split(" FROM session_dims ", 1)[0]
    return head.rsplit("SELECT ", 1)[1]


@pytest.mark.asyncio
async def test_the_unfiltered_shared_ctx_per_call_page_selects_only_the_ids_so_the_covering_index_can_serve_it():
    sql, _ = await _page_query(sort_by="ctx_per_call", page=0, per_page=5)

    projection = _inner_projection(sql)
    assert projection == "x.session_id"
    for wide in ("repo_remote", "branch", "jwt_email", "project_name", "owner_email", "route_note", "repository"):
        assert wide not in projection
    assert "started_at BETWEEN" in sql  # the window is still decided once, in sel


@pytest.mark.asyncio
async def test_the_ctx_per_call_minimal_projection_keeps_the_order_the_total_and_the_scope_condition():
    sql, _ = await _page_query(sort_by="ctx_per_call", page=1, per_page=5)

    assert f"ORDER BY sort_key DESC, session_id {_C}" in sql and "(SELECT count(*) FROM filtered)" in sql
    sel = sql.split("sel AS (", 1)[1].split("filtered AS", 1)[0]
    assert "x.subscription_type" in sel and "x.project_origin" in sel  # the viewer scope is still applied to sel
    assert "session_id IN (SELECT session_id FROM sel)" in sql


@pytest.mark.parametrize("sort_by", ["start_time", "cost_usd"])
@pytest.mark.asyncio
async def test_the_other_sorts_keep_the_full_lean_projection(sort_by):
    sql, _ = await _page_query(sort_by=sort_by, page=0, per_page=5, rates=_RATES)

    assert "d.repo_remote" in _inner_projection(sql)


@pytest.mark.asyncio
async def test_a_session_page_cannot_sort_by_an_unknown_key():
    with pytest.raises(ValueError, match="models"):
        await PostgresCliAnalyticsReader(_PageRecorder()).get_session_page(  # type: ignore[arg-type]
            _flt(), "models", 0, 5
        )


@pytest.mark.asyncio
async def test_the_cost_sort_needs_the_rates_of_the_window_models():
    with pytest.raises(ValueError, match="rates"):
        await PostgresCliAnalyticsReader(_PageRecorder()).get_session_page(  # type: ignore[arg-type]
            _flt(), "cost_usd", 0, 5
        )


@pytest.mark.asyncio
async def test_the_start_time_page_orders_by_the_stored_start_without_the_activity_range():
    sql, _ = await _page_query(sort_by="start_time", page=0, per_page=5)

    assert "s.started_at AS sort_key" in sql
    assert "act AS" not in sql and "session_events" not in sql
    assert f"ORDER BY started_at DESC, session_id {_C} LIMIT" in sql


@pytest.mark.asyncio
async def test_the_cost_page_orders_by_the_rounded_sql_cost_key_then_session_id():
    sql, args = await _page_query(sort_by="cost_usd", page=0, per_page=5, rates=_RATES)

    assert "k.cost_key" in sql and "round(sum(" in sql  # session_cost_key_sql, the one cost spelling
    assert "coalesce(k.cost_key, 0::numeric) AS sort_key" in sql
    assert f"ORDER BY sort_key DESC, session_id {_C} LIMIT" in sql
    assert ["m"] in args  # the rate models are bound, never interpolated


@pytest.mark.parametrize("sort_by", SESSION_PAGE_SORTS)
@pytest.mark.asyncio
async def test_every_sort_searches_prompt_title_repository_and_branch(sort_by):
    sql, _ = await _page_query(sort_by=sort_by, page=0, per_page=5, search="x", rates=_RATES)

    for column in ("first_prompt", "session_title", "repository", "branch"):
        assert f"s.{column} ILIKE $" in sql


class _FrameworkRecorder(_SqlRecorder):
    """Answers the distinct-name-sets read with two sets and the page statement with one row."""

    async def fetch(self, sql, *args):
        await super().fetch(sql, *args)
        if sql.lstrip().startswith("SELECT names FROM cli_framework_names"):
            return [{"names": ["a"]}, {"names": []}]
        if "FROM filtered fw JOIN cli_framework_names" in sql:
            return [{"session_id": "s1", "total": 7, "flags": None}]
        if "count(*)::bigint AS total FROM cli_framework_names" in sql:
            return [{"total": 9}]
        return []


@pytest.mark.asyncio
async def test_the_framework_page_builds_the_names_once_then_pages_the_sessions_of_the_accepted_sets():
    recorder = _FrameworkRecorder()
    seen: list[list[list[str]]] = []

    def matching(sets):
        seen.append(sets)
        return [["a"]]

    total, ids, flags = await PostgresCliAnalyticsReader(recorder).get_framework_page(  # type: ignore[arg-type]
        _flt(), "cost_usd", 1, 5, matching, _RATES, "x", True
    )
    create, analyze, sets_sql, page_sql, *rest = (" ".join(st.split()) for st in recorder.statements)

    assert (total, ids) == (7, ["s1"]) and seen == [[["a"], []]] and flags == []
    assert create.startswith("CREATE TEMP TABLE cli_framework_names ON COMMIT DROP AS WITH")
    assert " ".join(classifier_names_by_session_sql("sel").split()) in create  # set-wise names, once
    assert "s.repository = ''" in create and "ILIKE" in create  # search and unattributed narrow the window
    assert analyze == "ANALYZE cli_framework_names"
    assert sets_sql == "SELECT names FROM cli_framework_names GROUP BY names"
    assert "to_jsonb(n.names) IN (SELECT jsonb_array_elements(" in page_sql
    assert f"ORDER BY fw.sort_key DESC, fw.session_id {_C} LIMIT" in page_sql and "count(*) OVER ()" in page_sql
    assert "g AS MATERIALIZED (" in page_sql and "bool_or(" in page_sql  # the cost sort's flags come from its groups
    assert rest and any("bool_or(" in st for st in rest)  # an empty flags answer falls back to the window flags


@pytest.mark.asyncio
async def test_a_framework_page_past_the_last_match_counts_the_accepted_sessions():
    class Empty(_FrameworkRecorder):
        async def fetch(self, sql, *args):
            if "FROM filtered fw JOIN cli_framework_names" in sql:
                self.statements.append(sql)
                return []
            return await super().fetch(sql, *args)

    recorder = Empty()
    await PostgresCliAnalyticsReader(recorder).get_framework_page(  # type: ignore[arg-type]
        _flt(), "start_time", 4, 5, lambda sets: sets
    )
    assert any(
        "count(*)::bigint AS total FROM cli_framework_names n WHERE" in " ".join(st.split())
        for st in recorder.statements
    )


@pytest.mark.asyncio
async def test_a_framework_search_with_a_nul_character_matches_nothing_without_the_names_statement():
    recorder = _SqlRecorder()
    page = await PostgresCliAnalyticsReader(recorder).get_framework_page(  # type: ignore[arg-type]
        _flt(), "start_time", 0, 5, lambda sets: sets, search="a\x00b"
    )
    assert page[:2] == (0, [])
    assert not any("cli_framework_names" in st for st in recorder.statements)


def test_restricting_a_filter_to_session_ids_keeps_every_other_scope():
    f = _flt(users=["Dev@X.com"], projects=["p"])
    narrowed = f.restricted_to(["s1", "s2"])
    cte = PostgresCliAnalyticsReader._sessions_cte(narrowed)

    assert "lower(user_email) = ANY($users::text[])" in cte and "project_name = ANY($projects::text[])" in cte
    assert "session_id = ANY($session_ids::text[])" in cte
    assert window_params(narrowed)["session_ids"] == ["s1", "s2"]
    assert (window_params(narrowed)["users"], window_params(narrowed)["projects"]) == (["dev@x.com"], ["p"])
    assert f.session_ids is None and "session_ids" not in window_params(f)  # the original is untouched


def test_session_ids_never_replace_a_filter_that_denies_every_session():
    cte = PostgresCliAnalyticsReader._sessions_cte(_flt(deny_all=True).restricted_to(["s1"]))

    assert "AND false" in cte  # the scope comes first, the denying filter still applies
    assert "session_id = ANY($session_ids" not in cte  # a denied filter never reaches the id narrowing


@pytest.mark.asyncio
async def test_cost_kpis_selects_total_api_calls():
    sql, _ = await _sql_of("get_cost_kpis")
    assert "coalesce(sum(c.api_call_count), 0)::bigint" in sql
    assert "AS total_api_calls" in sql


@pytest.mark.asyncio
async def test_session_cost_facts_selects_api_call_count():
    sql, _ = await _sql_of("get_session_cost_facts")
    assert "sum(c.api_call_count)::bigint" in sql
    assert "AS api_call_count" in sql


@pytest.mark.parametrize("method", ["get_users", "get_session_cost_facts", "get_session_detail_cost"])
@pytest.mark.asyncio
async def test_the_top_model_is_the_largest_row_of_the_original_cost_grain(method: str) -> None:
    # cost_daily holds several rows per (day, session, user, model, query_source) once plugin data adds
    # speed, geo, scope and agent type. Their calls are summed back to that grain, then the largest row
    # wins: ordering by a single row's api_call_count would let a model split over many rows lose.
    recorder = _SqlRecorder()
    reader = PostgresCliAnalyticsReader(recorder)  # type: ignore[arg-type]
    if method == "get_session_detail_cost":
        await reader.get_session_detail_cost("s1", scope=DETAIL_SCOPE)
    else:
        await getattr(reader, method)(_flt())
    (statement,) = recorder.statements
    sql = " ".join(statement.split())

    assert "sum(c.api_call_count) OVER w AS grain_calls" in sql
    assert "WINDOW w AS (PARTITION BY c.day, c.session_id, c.user_email, c.model_name, c.query_source)) c" in sql
    assert "(array_agg(c.model_name ORDER BY c.grain_calls DESC NULLS LAST, c.day, c.session_id" in sql
    assert "ORDER BY c.api_call_count" not in sql
    # cost_daily is read for the grain_calls subquery, again inside sel's own activity-window checks (seven times:
    # the any-time probe of the underived started arm, and the whole-day and window-day probes of each of the
    # three arms that ask `_active_in_window`), and once more per `_DEVELOPER`
    # occurrence for its own cost-email fallback subquery (get_users repeats {_DEVELOPER} in ORDER BY,
    # get_session_cost_facts uses it once); a plain `FROM cost_daily c` has no grain_calls column
    developer_reads = {"get_users": 2, "get_session_cost_facts": 1}.get(method, 0)
    expected_cost_daily_reads = 1 if method == "get_session_detail_cost" else 8 + developer_reads
    assert sql.count("cost_daily") == expected_cost_daily_reads
    assert "(SELECT c.*, sum(c.api_call_count) OVER w AS grain_calls, " in sql


@pytest.mark.asyncio
async def test_session_detail_model_costs_groups_by_model_for_one_session():
    recorder = _SqlRecorder()
    reader = PostgresCliAnalyticsReader(recorder)  # type: ignore[arg-type]
    await reader.get_session_detail_model_costs("s1", scope=DETAIL_SCOPE)
    (statement,) = recorder.statements
    sql = " ".join(statement.split())
    assert "GROUP BY c.model_name" in sql
    assert "FROM cost_daily c WHERE c.session_id = $1" in sql
    assert "FROM session_usage u WHERE u.attrs->>'token_source' = 'subagent_usage' AND u.session_id = $1" in sql


@pytest.mark.asyncio
async def test_users_daily_activity_still_groups_by_the_fact_day():
    sql, _ = await _sql_of("get_users_daily_activity")

    assert "act.day AS day" in sql
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


@pytest.mark.asyncio
async def test_file_facts_changed_only_counts_written_or_edited_files():
    """files_changed must not count a file whose only touches were reads, or a rejected/failed
    Write/Edit — only a file with at least one successful Write or Edit counts (EPMCDME-15583)."""
    sql, _ = await _sql_of("get_file_facts_by_session")

    assert "count(*) FILTER (WHERE written OR edited) AS files_changed" in sql


# ── invocations and tools ──


def _without_sel(sql: str) -> str:
    """`sql` with the body of its `sel` CTE removed (parentheses matched, so nested subqueries stay inside)."""
    start = sql.index("sel AS (") + len("sel AS (")
    depth = 1
    for i in range(start, len(sql)):
        depth += {"(": 1, ")": -1}.get(sql[i], 0)
        if depth == 0:
            return sql[:start] + sql[i:]
    raise AssertionError("unbalanced sel CTE")


@pytest.mark.parametrize("method", ["get_tool_success_by_session", "get_tool_usage", "get_invocations"])
@pytest.mark.asyncio
async def test_invocation_methods_read_the_scoped_hourly_rollup_without_a_window(method: str) -> None:
    sql, _ = await _sql_of(method)
    # sel decides the window, from raw records too; the invocation query itself must not.
    body = _without_sel(sql)

    assert "FROM invocations_hourly" in body
    assert "IN (SELECT session_id FROM sel)" in body
    forbidden = ["hour >=", "ts BETWEEN", "ts >=", "FROM spans", "FROM log_events"]
    if method != "get_invocations":  # its slash commands also read session_dims.commands
        forbidden.append("UNION")
    for fragment in forbidden:
        assert fragment not in body


@pytest.mark.parametrize("method", ["get_tool_success_by_session", "get_tool_usage", "get_invocations"])
@pytest.mark.asyncio
async def test_invocation_methods_bind_only_window_and_filter_parameters(method):
    recorder = _SqlRecorder()
    await getattr(PostgresCliAnalyticsReader(recorder), method)(_flt())  # type: ignore[arg-type]

    if method == "get_invocations":
        # get_invocations binds builtin_commands as $3 parameter
        assert "$3::text[]" in recorder.statements[0]
    else:
        # Tool methods bind only window and filter, no $3 parameter
        assert "$3" not in recorder.statements[0]


# -- audit: the window is decided only in sel --

_ALL_FILTERS = [{}, {"deny_all": True}, {"projects": ["p"]}]
_REMOVED_WINDOWS = ("day BETWEEN", "$d0", "$d1", "$h0", "$h1", "_EDGES", "_INNER_HOURS")


@pytest.mark.parametrize("filters", _ALL_FILTERS, ids=["unfiltered", "deny_all", "projects"])
@pytest.mark.parametrize(("method", "kwargs"), _WINDOWED_CALLS)
@pytest.mark.asyncio
async def test_no_fact_query_carries_its_own_window_and_all_are_scoped_to_sel(method, kwargs, filters):
    statements = await _call(method, kwargs, filters)

    assert statements
    for sql in statements:
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
    """The (lower, upper) the database receives for the `started_at BETWEEN` predicate in sel (the exact-
    admission arm for a session that started inside the window)."""
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
        scope=EXCLUDE_SCOPE,
        start_dt=datetime(2026, 9, 1, 8, 30, 0, 999999),
        end_dt=datetime(2026, 9, 8, 17, 45, 0, 500),
    )

    assert await _bound_window(f) == (
        datetime(2026, 9, 1, 8, 30, 0, 999000, tzinfo=UTC),
        datetime(2026, 9, 8, 17, 45, 0, 0, tzinfo=UTC),
    )


# ── slash commands: one source per session ──

_SCOPED = "AND session_id IN (SELECT session_id FROM sel)"


async def _invocation_branches(**filters: object) -> list[str]:
    """The UNION ALL branches of `get_invocations`, named parameters kept, whitespace collapsed."""
    captured: list[str] = []

    async def capture(sql: str, params: dict[str, object]) -> list[dict[str, object]]:
        captured.append(sql)
        return []

    reader = PostgresCliAnalyticsReader(_SqlRecorder())  # type: ignore[arg-type]
    reader._q = capture  # type: ignore[method-assign]
    await reader.get_invocations(_flt(**filters))
    (sql,) = captured
    start = sql.index("FROM (", sql.index("AS count")) + len("FROM (")
    body = sql[start : sql.rindex(") u")]
    return [" ".join(branch.split()) for branch in body.split("UNION ALL")]


def _no_summary(column: str) -> str:
    return f"NOT EXISTS (SELECT 1 FROM session_dims sd WHERE sd.session_id = {column} AND sd.summary_ts IS NOT NULL)"


@pytest.mark.parametrize("filters", _ALL_FILTERS, ids=["unfiltered", "deny_all", "projects"])
@pytest.mark.asyncio
async def test_invocations_have_three_branches_each_scoped_to_sel(filters: dict[str, object]) -> None:
    branches = await _invocation_branches(**filters)

    assert len(branches) == 3
    assert all(branch.endswith(_SCOPED) for branch in branches)


@pytest.mark.asyncio
async def test_skill_and_agent_invocations_come_from_the_hourly_rollup_alone() -> None:
    branches = await _invocation_branches()

    assert f"SELECT kind, name, calls AS c FROM invocations_hourly WHERE kind IN (2, 3) {_SCOPED}" in branches


@pytest.mark.asyncio
async def test_otel_slash_commands_count_only_for_sessions_without_a_summary() -> None:
    # Missing, a session with a summary would count its commands twice: OTel and session_dims.commands.
    # Built-in command names are excluded via parameter binding.
    from codemie.repository.cli_analytics.vocabulary import BUILTIN_COMMAND_NAMES

    branches = await _invocation_branches()

    assert (
        "SELECT kind, name, calls AS c FROM invocations_hourly WHERE kind = 4 "
        f"AND {_no_summary('invocations_hourly.session_id')} AND name <> ALL($builtin_commands::text[]) {_SCOPED}"
    ) in branches

    # Verify that compact is not excluded by the branch predicate
    assert "compact" not in BUILTIN_COMMAND_NAMES


@pytest.mark.asyncio
async def test_summary_sessions_count_every_string_command_once_by_the_shared_window() -> None:
    from codemie.repository.cli_analytics.vocabulary import BUILTIN_COMMAND_NAMES

    branches = await _invocation_branches()

    summary = [b for b in branches if "jsonb_array_elements" in b]
    assert summary == [
        "SELECT 4, e.cmd #>> '{}', 1 FROM session_dims, jsonb_array_elements(session_dims.commands) AS e(cmd) "
        "WHERE session_dims.summary_ts IS NOT NULL AND jsonb_typeof(e.cmd) = 'string' AND e.cmd #>> '{}' <> '' AND (e.cmd #>> '{}') <> ALL($builtin_commands::text[]) "
        f"{_SCOPED}"
    ]
    # The window is the one of `sel`: the branch has no time predicate of its own.
    assert "started_at" not in summary[0] and "BETWEEN" not in summary[0]

    # Verify that compact is not excluded by the branch predicate
    assert "compact" not in BUILTIN_COMMAND_NAMES


@pytest.mark.asyncio
async def test_an_empty_summary_command_counts_nothing_and_names_are_taken_as_stored() -> None:
    # The rollup skips a blank name; without the filter an empty element of session_dims.commands
    # would count as a blank-named command. Names are not rewritten: commands are stored without
    # a leading slash and the server does not guess.
    (summary,) = [b for b in await _invocation_branches() if "jsonb_array_elements" in b]

    assert "AND jsonb_typeof(e.cmd) = 'string' AND e.cmd #>> '{}' <> '' " in summary
    assert summary.startswith("SELECT 4, e.cmd #>> '{}', 1 FROM session_dims, ")
    assert "ltrim" not in summary


def test_window_params_omit_projects_when_project_unattributed():
    params = window_params(_flt(projects=["p"], project_unattributed=True))

    assert "projects" not in params


def test_window_params_keep_projects_by_default():
    assert window_params(_flt(projects=["p"]))["projects"] == ["p"]


@pytest.mark.asyncio
async def test_scalars_agent_count_has_dedup_correction_like_skill_count():
    """get_session_detail_scalars corrects skill_count for hook-only dispatches not yet in
    tool_facts_daily; agent_count must have the same correction, or a skill-tagged subagent
    dispatch that hasn't rolled up yet undercounts/overcounts agent_count."""
    recorder = _SqlRecorder()

    await PostgresCliAnalyticsReader(recorder).get_session_detail_scalars("s1", scope=DETAIL_SCOPE)

    sql = " ".join(recorder.statements[0].split())
    agent_count_expr = sql.split("AS agent_count")[0].rsplit("AS tool_call_count", 1)[-1]
    assert "FROM hook_events" in agent_count_expr
    assert "agent.subagent.start" in agent_count_expr
    assert "NOT IN (SELECT subagent_type FROM spans" in agent_count_expr
    # a Claude Code skill-tagged start is a skill, not an agent; another client's stays an agent
    assert (
        "AND NOT (coalesce(he.skill_name, '') <> '' AND coalesce(he.attrs->>'platform', 'claude-code') = "
        "'claude-code')" in agent_count_expr
    )
    # a fork of a counted Skill call is a skill, never an agent, with or without skill_name; the Skill end names
    # it by its fork_agent_id, else by the complete agentId in its tool_response
    in_response = tool_response_agent_id("he_e.attrs->>'tool_response'")
    agent_id = f"coalesce(nullif(he_e.attrs->>'fork_agent_id', ''), {in_response})"
    assert (
        "AND NOT (coalesce(he.attrs->>'platform', 'claude-code') = 'claude-code' "
        "AND EXISTS (SELECT 1 FROM hook_events he_e WHERE he_e.session_id = he.session_id "
        "AND he_e.event_type = 'agent.tool.end' AND he_e.tool_name = 'Skill' "
        "AND coalesce(he_e.attrs->>'platform', 'claude-code') = 'claude-code' "
        f"AND {agent_id} = he.agent_id AND he_e.tool_use_id <> '' "
        "AND (EXISTS (SELECT 1 FROM spans he_s WHERE he_s.session_id = he_e.session_id AND he_s.span_kind = 1 "
        "AND he_s.tool_use_id = he_e.tool_use_id AND he_s.skill_name <> '' "
        f"AND {executed_tool_span('he_s', 'Skill')})" in agent_count_expr
    )
    # nor is the fork a slash command pairs with, with or without skill_name
    assert (
        "AND NOT ((he.event_type = 'agent.subagent.start' AND coalesce(he.attrs->>'platform', 'claude-code') = "
        "'claude-code' AND NOT EXISTS (SELECT 1 FROM hook_events he_p WHERE he_p.session_id = he.session_id "
        "AND he_p.event_type = 'agent.subagent.start' AND he.agent_id <> '' AND he_p.agent_id = he.agent_id "
        "AND (he_p.ts, he_p.ingest_seq) < (he.ts, he.ingest_seq)) "
        "AND NOT EXISTS (SELECT 1 FROM hook_events he_s WHERE he_s.session_id = he.session_id" in agent_count_expr
    )
    assert "AND he_d.event_type = 'agent.skill.dispatch'" in agent_count_expr
    # the day with no span is the UTC day, as in the rollups
    assert (
        "AND sp.ts >= date_trunc('day', he.ts, 'UTC') AND sp.ts < date_trunc('day', he.ts, 'UTC') + interval '1 day'"
        in agent_count_expr
    )


@pytest.mark.asyncio
async def test_dispatch_union_branch_treats_subagent_hook_skill_name_as_skill_dispatch():
    """A forked skill's SubagentStart (skill_name, no TOOL span) is a skill row, not an agent, read by its own
    agentId, unless its initiator has a row: a Skill span whose result names its agentId, or the slash-command
    dispatch it pairs with. A dispatch row takes its fork's cost by the fork's agentId."""
    recorder = _SqlRecorder()

    await PostgresCliAnalyticsReader(recorder).get_session_detail_dispatches("s1", scope=DETAIL_SCOPE)

    sql = " ".join(recorder.statements[0].split())
    assert (
        "FROM hook_events h LEFT JOIN stopped st ON st.agent_id = h.agent_id AND h.agent_id <> '' "
        "LEFT JOIN agent_usage au ON au.agent_id = h.agent_id AND h.agent_id <> '' "
        "WHERE h.session_id = $1::text AND h.event_type = 'agent.subagent.start' AND h.skill_name <> '' "
        "AND coalesce(h.attrs->>'platform', 'claude-code') = 'claude-code' "
        "-- one row per fork: a re-sent or resumed start of the same agent_id is not another invocation "
        "AND NOT EXISTS (SELECT 1 FROM hook_events h_p WHERE h_p.session_id = h.session_id "
        "AND h_p.event_type = 'agent.subagent.start' AND h.agent_id <> '' AND h_p.agent_id = h.agent_id "
        "AND (h_p.ts, h_p.ingest_seq) < (h.ts, h.ingest_seq)) "
        "AND NOT EXISTS (SELECT 1 FROM dispatch_fork df WHERE df.agent_id = h.agent_id) "
        "AND NOT EXISTS (SELECT 1 FROM t JOIN skill_fork sf ON sf.tool_use_id = t.tool_use_id "
        "WHERE t.skill_name <> '' AND sf.agent_id = h.agent_id)" in sql
    )
    assert "WHERE d.session_id = $1::text AND d.event_type = 'agent.skill.dispatch' AND d.skill_name <> ''" in sql
    # (b): same prompt_id, the earliest fork candidate at or after the dispatch with its skill name, else the
    # earliest without one
    assert (
        "WHERE f.session_id = d.session_id AND f.agent_id <> '' AND (f.event_type = 'agent.subagent.start' "
        "AND coalesce(f.attrs->>'platform', 'claude-code') = 'claude-code' "
        "AND NOT EXISTS (SELECT 1 FROM hook_events f_p WHERE f_p.session_id = f.session_id "
        "AND f_p.event_type = 'agent.subagent.start' AND f.agent_id <> '' AND f_p.agent_id = f.agent_id "
        "AND (f_p.ts, f_p.ingest_seq) < (f.ts, f.ingest_seq)) "
        "AND NOT EXISTS (SELECT 1 FROM hook_events f_s WHERE f_s.session_id = f.session_id" in sql
    )
    # a dispatch whose prompt_id holds another dispatch pairs with none
    assert (
        "AND f.prompt_id = d.prompt_id AND (f.skill_name = d.skill_name OR coalesce(f.skill_name, '') = '') "
        "AND f.ts >= d.ts "
        "AND coalesce(d.attrs->>'platform', 'claude-code') = 'claude-code' AND d.prompt_id <> '' "
        "AND NOT EXISTS (SELECT 1 FROM hook_events d_o WHERE d_o.session_id = d.session_id "
        "AND d_o.event_type = 'agent.skill.dispatch' "
        "AND coalesce(d_o.attrs->>'platform', 'claude-code') = 'claude-code' "
        "AND d_o.prompt_id = d.prompt_id AND (d_o.ts, d_o.ingest_seq) <> (d.ts, d.ingest_seq)) "
        "ORDER BY coalesce(f.skill_name, '') = '', f.ts, f.ingest_seq LIMIT 1" in sql
    )
    assert "FROM dispatch_fork df LEFT JOIN inline_usage iu ON iu.ts = df.ts" in sql
    assert "LEFT JOIN stopped st ON st.agent_id = df.agent_id" in sql
    # a dispatch without a fork takes the requests attributed to its skill in its own prompt, 0 without any
    assert "CASE WHEN df.agent_id IS NULL THEN coalesce(iu.cost_usd, 0) ELSE au.cost_usd END::float8" in sql
    assert "x.skill_name = w.skill_name" in sql
    # identical re-sent dispatch rows (hook_events has no unique key) share one window, so they cannot double the sums
    assert "SELECT DISTINCT d.ts, d.skill_name, d.prompt_id, df.agent_id, df.ts AS fork_ts" in sql
    assert "FROM paired_requests WHERE scope_kind = 'skill' AND agent_id = ''" in sql
    # a subagent no LLM request names takes its own paired requests, never on top of its span-grouped ones
    assert "WHERE g.agent_id <> '' AND NOT EXISTS (SELECT 1 FROM agent_groups ag WHERE ag.agent_id = g.agent_id)" in sql


@pytest.mark.asyncio
async def test_an_agent_call_is_a_dispatch_only_with_run_evidence_from_an_hour_before_to_a_day_after_it():
    """An Agent or Skill call is a dispatch only through the gate the rollups count it by."""
    recorder = _SqlRecorder()

    await PostgresCliAnalyticsReader(recorder).get_session_detail_dispatches("s1", scope=DETAIL_SCOPE)

    sql = " ".join(recorder.statements[0].split())
    # coalesce: ingest stores an absent subagent_type as NULL, and NULL = '' would drop skill rows
    assert (
        f"AND (CASE WHEN coalesce(s.subagent_type, '') <> '' THEN {executed_tool_span('s', 'Agent')} "
        f"ELSE {executed_tool_span('s', 'Skill')} END)"
    ) in sql


# ── get_session_detail_dispatches: exact agent attribution via the agentId chain ──


async def _dispatches_sql() -> str:
    recorder = _SqlRecorder()
    await PostgresCliAnalyticsReader(recorder).get_session_detail_dispatches("s1", scope=DETAIL_SCOPE)  # type: ignore[arg-type]
    assert len(recorder.statements) == 1
    return " ".join(recorder.statements[0].split())


@pytest.mark.asyncio
async def test_agent_id_is_read_from_the_agent_tool_call_hook_event():
    # The agentId a subagent reports back with is named in its own Agent tool call's hook event, a Skill call's
    # fork in the Skill call's. Matched by identity (the tool_use_id), never by position or timing.
    sql = await _dispatches_sql()
    agent_id = tool_response_agent_id("attrs->>'tool_response'")

    assert (
        f"tool_use_id, ts, {agent_id} AS agent_id FROM hook_events WHERE session_id = $1::text "
        "AND event_type = 'agent.tool.end' AND tool_name = 'Agent'" in sql
    )
    in_response = tool_response_agent_id("hook_events.attrs->>'tool_response'")
    skill_agent_id = f"coalesce(nullif(hook_events.attrs->>'fork_agent_id', ''), {in_response})"
    assert (
        f"tool_use_id, {skill_agent_id} AS agent_id FROM hook_events WHERE session_id = $1::text "
        "AND event_type = 'agent.tool.end' AND tool_name = 'Skill' AND tool_use_id <> '' "
        "AND coalesce(attrs->>'platform', 'claude-code') = 'claude-code'" in sql
    )
    assert "agentId[^a-zA-Z0-9_-]+" not in sql
    assert "LEFT JOIN launched la ON la.tool_use_id = t.tool_use_id" in sql
    assert (
        "LEFT JOIN skill_fork sf ON sf.tool_use_id = t.tool_use_id AND t.tool_use_id <> '' AND t.skill_name <> ''"
        in sql
    )


@pytest.mark.asyncio
async def test_agent_cost_is_summed_from_the_api_requests_of_its_llm_request_group():
    # The subagent's llm_request spans carry its agentId and name, as their parent, the span that
    # groups its api_request events. Its cost is the plain sum over those events, joined by agentId.
    sql = await _dispatches_sql()

    assert "span_kind = 4" in sql and "attrs ? 'agent_id'" in sql
    assert (
        "FROM (SELECT DISTINCT agent_id FROM agent_groups) g JOIN log_events l ON l.span_id IN "
        "(SELECT ag.parent_span_id FROM agent_groups ag WHERE ag.agent_id = g.agent_id) AND l.session_id" in sql
    )
    assert "LEFT JOIN agent_usage au ON au.agent_id = coalesce(la.agent_id, sf.agent_id)" in sql


@pytest.mark.asyncio
async def test_agent_run_time_runs_from_its_subagent_start_to_its_subagent_stop_event():
    # The run time of any agent, background or foreground, is its own SubagentStart hook event (found by the
    # agentId of its call's result) to its own SubagentStop; without the start, the Agent tool call's start
    # (agent.tool.start); the launch ack only when both are missing: it arrives seconds after the call. The
    # tool-execution span closes at launch, so it is not used. A forked skill's run is its fork's.
    sql = await _dispatches_sql()

    assert (
        "SELECT agent_id, min(ts) AS ts FROM hook_events WHERE session_id = $1::text "
        "AND event_type = 'agent.subagent.start' AND agent_id <> '' GROUP BY agent_id" in sql
    )
    assert "coalesce(f.ts, s.ts, e.ts) AS launch_ts" in sql
    assert "LEFT JOIN forked f ON f.agent_id = e.agent_id" in sql
    assert "async_launched" not in sql
    assert "event_type = 'agent.subagent.stop'" in sql
    assert "LEFT JOIN stopped st ON st.agent_id = coalesce(la.agent_id, sf.agent_id)" in sql
    assert (
        "CASE WHEN sf.agent_id IS NOT NULL THEN CASE WHEN st.ended_at >= fs.ts "
        "THEN (extract(epoch FROM st.ended_at - fs.ts) * 1000)::bigint END" in sql
    )
    assert "subagent_duration_ms" in sql


@pytest.mark.asyncio
async def test_an_agent_dispatch_falls_back_to_its_parent_span_only_when_it_has_no_agent_id():
    # The agent_usage join is preferred. Only a dispatch with no agentId at all falls back to its own
    # windowed slice of the parent span's api_requests (see the sibling_window test below: an
    # un-windowed slice would duplicate cost whenever two such dispatches share a parent). A known
    # agentId with no api_request group, an agent's or a forked skill's, never takes the parent span
    # (the main thread, whose cost is not the subagent's): only its own paired requests, else NULL.
    sql = await _dispatches_sql()

    assert "coalesce(au.cost_usd, pu.cost_usd)::float8 AS cost_usd" in sql
    assert (
        "LEFT JOIN parent_usage pu ON pu.fallback_id = t.span_id AND la.agent_id IS NULL AND sf.agent_id IS NULL" in sql
    )
    assert "DISTINCT ON (tool_use_id)" in sql
    # the full even-split/time-window mechanism (row_number dispatch_id,
    # per-event active_n division, an unconditional sibling_count divisor) must still never come back —
    # only the narrower, next-sibling-start-bounded split below, scoped to the no-agentId fallback alone
    # (the paired_requests CTE runs the rollups' request pairing, which is no cost split)
    dispatch_sql = sql.split("paired_requests AS MATERIALIZED")[0] + sql.split("inline_window AS")[1]
    for removed in ("windows AS", "matches AS", "active_n", "sibling_count", "row_number() OVER"):
        assert removed not in dispatch_sql


@pytest.mark.asyncio
async def test_the_no_agent_id_fallback_splits_cost_at_the_next_sibling_s_start():
    """Two dispatches with no resolvable agentId sharing one parent must not each take the parent's
    whole API-request sum: each reads only its own slice, from its own start to the next strictly later
    sibling start under the same parent, unbounded for the last one, split equally among the dispatches
    that start together (otherwise two such dispatches each show the same $1.00 against a $1.00 session)."""
    sql = await _dispatches_sql()

    assert "fallback_id, parent_span_id, start_ts" in sql
    assert (
        "min(start_ts) OVER (PARTITION BY parent_span_id ORDER BY start_ts "
        "RANGE BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING EXCLUDE GROUP) AS next_start, "
        "count(*) FILTER (WHERE takes_parent) OVER (PARTITION BY parent_span_id, start_ts) AS ties" in sql
    )
    # only the dispatches that take the parent's requests share a slice; a dispatch with an agentId takes its own
    assert "(la.agent_id IS NULL AND sf.agent_id IS NULL) AS takes_parent" in sql
    assert "sum(coalesce(l.cost_usd, 0))::numeric / w.ties AS cost_usd" in sql
    assert "sum(coalesce(l.input_tokens, 0))::numeric / w.ties AS input_tokens" in sql
    assert "FROM sibling_window w" in sql
    assert "l.ts >= w.start_ts AND (w.next_start IS NULL OR l.ts < w.next_start)" in sql
    assert "WHERE w.ties > 0 GROUP BY w.fallback_id, w.ties" in sql


@pytest.mark.asyncio
async def test_an_agent_row_starts_at_its_run_start_a_skill_row_at_its_tool_span_in_start_order():
    # An agent's bar starts when its run began (launched: its SubagentStart, else its call start), the same
    # instant its run time is measured from. Only agents take that anchor: a skill's bar stays at its tool span.
    sql = await _dispatches_sql()

    assert "coalesce(CASE WHEN t.subagent_type <> '' THEN la.launch_ts END, t.span_start) AS dispatch_start" in sql
    assert "ORDER BY dispatch_start" in sql


@pytest.mark.asyncio
async def test_invocations_bind_builtin_command_names() -> None:
    """Verify that the $builtin_commands parameter is bound with the expected values."""
    from codemie.repository.cli_analytics.vocabulary import BUILTIN_COMMAND_NAMES

    captured_params: dict[str, object] | None = None

    async def capture_params(sql: str, params: dict[str, object]) -> list[dict[str, object]]:
        nonlocal captured_params
        captured_params = params
        return []

    reader = PostgresCliAnalyticsReader(_SqlRecorder())  # type: ignore[arg-type]
    reader._q = capture_params  # type: ignore[method-assign]
    await reader.get_invocations(_flt())

    assert captured_params is not None
    assert "builtin_commands" in captured_params
    assert captured_params["builtin_commands"] == list(BUILTIN_COMMAND_NAMES)
    assert captured_params["builtin_commands"] == ["model", "exit", "quit"]


# ── viewer scope in the base conditions of _sessions_cte ─────────────────────


def _cte(**kwargs) -> str:
    return PostgresCliAnalyticsReader._sessions_cte(_flt(**kwargs))


def test_exclude_scope_is_in_the_base_conditions():
    cte = _cte(scope=ViewerScope("a@x.com", False, frozenset({"p"}), "exclude"))

    assert f"NOT {sql_private('x')}" in cte
    assert "coalesce(x.project_name, '') = ANY($scope_projects::text[])" in cte
    assert "$scope_viewer" not in cte


def test_own_only_scope_is_in_the_base_conditions():
    cte = _cte(scope=ViewerScope("A@x.com", False, frozenset(), "own_only"))

    assert f"{sql_norm('x.owner_email')} = $scope_viewer::text" in cte


def test_scope_is_the_first_base_condition_so_appended_conditions_cannot_displace_it():
    cte = _cte(
        users=["u"],
        projects=["p"],
        repositories=["r"],
        branch="b",
        scope=ViewerScope("a@x.com", False, frozenset(), "own_only"),
    )
    where = cte.split(") x", 1)[1]

    assert where.index("$scope_viewer") < where.index("$users") < where.index("branch = $branch")
    scope_sql = sql_list_condition(ViewerScope("a@x.com", False, frozenset(), "own_only"))[0]
    assert where.count("WHERE") == 1 and where.lstrip().startswith(f"WHERE {scope_sql} AND ")


@pytest.mark.parametrize("scope", [None, "admin", object(), ViewerScope("a@x.com", True, frozenset(), "bogus")])  # type: ignore[arg-type]
def test_absent_or_malformed_scope_denies_everything(scope):
    cte = _cte(scope=scope)

    assert "WHERE false" in cte
    assert "true" not in cte.split("FROM session_dims d")[1]


def test_scope_params_reach_the_query():
    p = window_params(_flt(scope=ViewerScope("A@x.com", False, frozenset({"p"}), "exclude")))
    q = window_params(_flt(deny_all=True, scope=ViewerScope("A@x.com", False, frozenset({"p"}), "own_only")))

    assert p["scope_projects"] == ["p"]
    assert q["scope_viewer"] == "a@x.com"


def test_sessions_project_the_columns_the_scope_reads():
    cte = _cte(scope=EXCLUDE_SCOPE)

    for column in ("d.owner_email", "d.subscription_type", "d.project_origin"):
        assert column in cte


def test_window_params_carry_subscription_types_and_project_origins():
    p = window_params(_flt(subscription_types=["unknown"], project_origins=["none"]))

    assert (p["subscription_types"], p["project_origins"]) == (["unknown"], ["none"])
    assert "subscription_types" not in window_params(_flt(deny_all=True, subscription_types=["unknown"]))


def test_subscription_and_origin_filters_use_the_normalised_projection_and_stay_anded_with_the_scope():
    cte = PostgresCliAnalyticsReader._sessions_cte(_flt(subscription_types=["unknown"], project_origins=["none"]))
    where = cte.split(") x", 1)[1]

    assert "THEN 'unknown'" in where and "THEN 'none'" in where  # NULL/empty map to unknown / none
    assert f"{sql_norm('x.subscription_type')} = ''" in where
    assert "= ANY($subscription_types::text[])" in where and "= ANY($project_origins::text[])" in where
    # The scope predicate is first and every filter is ANDed after it: a filter value never widens it.
    scope_sql = sql_list_condition(EXCLUDE_SCOPE)[0]
    assert where.split("WHERE ", 1)[1].startswith(scope_sql + " AND ")
    assert " OR lower(btrim" not in where.split(scope_sql, 1)[1]


def test_personal_filter_under_exclude_still_carries_the_scope_that_excludes_personal():
    cte = PostgresCliAnalyticsReader._sessions_cte(_flt(subscription_types=["personal"], project_origins=["personal"]))

    assert f"NOT {sql_private('x')}" in cte  # exclude = not private to an owner only


@pytest.mark.parametrize(
    ("value", "sub", "origin"),
    [
        (None, "unknown", "none"),
        ("  ", "unknown", "none"),
        ("Corporate", "corporate", "other"),
        ("CODEMIE", "codemie", "other"),
        ("Personal", "personal", "personal"),
        ("Linked_Folder", "other", "linked_folder"),
        ("default_project", "other", "default_project"),
        ("weird", "other", "other"),
    ],
)
def test_vocabulary_normalisers(value, sub, origin):
    assert normalise_subscription_type(value) == sub
    assert normalise_project_origin(value) == origin


def test_sql_vocabulary_expression_mirrors_the_python_mapping():
    sql = sql_subscription_type("c")

    assert (
        sql.count("'unknown'") == 2 and "'other'" in sql and "IN ('codemie', 'corporate', 'personal', 'unknown')" in sql
    )
    assert "'none'" in sql_project_origin("c") and "'registered_repository'" in sql_project_origin("c")


@pytest.mark.asyncio
async def test_window_model_flags_read_the_shared_token_grouping_behind_the_viewer_scope():
    sql, _ = await _sql_of("get_window_model_flags")
    assert sql.startswith("WITH sel AS")
    assert "session_id IN (SELECT session_id FROM sel)" in sql  # the viewer scope
    # The flags clamp the cache split on the (session, model) group's sums, exactly like the cost key (CR-004).
    assert " ".join(model_token_groups(normalized=False).split()) in " ".join(sql.split())
    assert "FROM session_usage u WHERE u.attrs->>'token_source' = 'subagent_usage'" in sql
    assert "GROUP BY g.model_name" in sql
    assert sql.count("bool_or(") == 5
    # A subagent totals row without a model reaches the flags as '' (like the Python path), never as NULL (CR-003).
    assert "coalesce(u.model, '')" in sql


@pytest.mark.asyncio
async def test_session_model_costs_and_cost_key_share_one_token_grouping():
    costs, _ = await _sql_of("get_session_model_costs")
    grouping = " ".join(model_token_groups().split())
    spend_grouping = " ".join(model_token_groups(True).split())

    assert grouping in costs
    assert grouping in " ".join(session_cost_key_sql().split())
    assert spend_grouping != grouping
    assert spend_grouping in " ".join(session_cost_key_sql(True).split())


def test_cost_key_sql_is_the_twin_of_the_python_pricer():
    sql = " ".join(session_cost_key_sql().split())

    assert "round(sum(" in sql and ")::numeric, 9) AS cost_key" in sql
    assert sql.count("> 0 THEN") == 5  # a kind with tokens <= 0 is skipped
    assert sql.count("coalesce(r.") == 5  # a missing rate counts as 0
    assert "unnest($rate_models::text[], $rate_input::float8[], $rate_output::float8[]" in sql
    assert "least(coalesce(g.cache_creation_1h_tokens, 0), g.cache_creation_tokens)" in sql  # the cache-split clamp


def test_rates_params_binds_one_float_array_per_rate_column():
    from codemie.repository.cli_analytics.ports import ModelRates

    params = rates_params([ModelRates("a", 1, None, 3), ModelRates("b"), ModelRates("a", 1, None, 3)])

    assert params["rate_models"] == ["a", "b"]
    assert params["rate_input"] == [1.0, None]
    assert params["rate_output"] == [None, None]
    assert params["rate_cache_read"] == [3.0, None]


@pytest.mark.asyncio
async def test_a_searched_start_time_page_is_one_pass_with_a_window_count():
    sql, _ = await _page_query(sort_by="start_time", page=0, per_page=5, search="fix")

    assert "count(*) OVER ()" in sql and "LIMIT" in sql
    assert "last_event_at" not in sql  # the activity columns are not read
    assert "first_prompt" in sql and "session_title" in sql


@pytest.mark.asyncio
async def test_a_searched_page_past_the_last_match_counts_in_a_second_statement():
    recorder = _PageRecorder()
    await PostgresCliAnalyticsReader(recorder).get_session_page(  # type: ignore[arg-type]
        _flt(), "start_time", 3, 5, search="fix"
    )
    assert len(recorder.statements) == 2 and "count(*)::bigint AS total" in recorder.statements[1]


@pytest.mark.asyncio
async def test_the_cost_page_builds_the_token_groups_once_for_the_key_and_the_flags():
    recorder = _PageRecorder()
    await PostgresCliAnalyticsReader(recorder).get_cost_page(_flt(), 0, 20, _RATES)  # type: ignore[arg-type]
    sql = " ".join(recorder.statements[0].split())

    assert len(recorder.statements) == 1
    assert sql.count("g AS MATERIALIZED (") == 1 and "normalized_model_name" not in sql
    assert "FROM g LEFT JOIN unnest(" in sql and "GROUP BY g.model_name" in sql
    assert sql.count("bool_or(") == 5
    assert "(SELECT count(*) FROM filtered)" in sql and f"ORDER BY sort_key DESC, session_id {_C}" in sql


@pytest.mark.asyncio
async def test_the_cost_models_are_read_from_the_filtered_window_and_viewer_scope():
    recorder = _SqlRecorder()
    assert await PostgresCliAnalyticsReader(recorder).get_cost_models(_flt()) == []  # type: ignore[arg-type]
    text = " ".join(recorder.statements[0].split())
    assert "FROM cost_daily c WHERE c.session_id IN (SELECT session_id FROM sel)" in text
    assert "u.session_id IN (SELECT session_id FROM sel)" in text


_OWN_SCOPE = ViewerScope(viewer_email="dev@x.com", unrestricted=False, admin_projects=frozenset(), mode="own_only")
_PROJECT_ADMIN_SCOPE = ViewerScope(
    viewer_email="pa@x.com", unrestricted=False, admin_projects=frozenset({"p"}), mode="exclude"
)


@pytest.mark.parametrize(
    "filters",
    [
        {"users": ["a@x.com"]},
        {"projects": ["plum"]},
        {"project_unattributed": True},
        {"repositories": ["r"]},
        {"branch": "main"},
        {"subscription_types": ["codemie"]},
        {"project_origins": ["profile"]},
        {"scope": _OWN_SCOPE},
        {"scope": _PROJECT_ADMIN_SCOPE},
        {"deny_all": True},
    ],
    ids=lambda f: next(iter(f)),
)
@pytest.mark.asyncio
async def test_a_filtered_start_time_page_is_cut_in_one_pass_not_by_walking_the_start_time_index(filters):
    recorder = _PageRecorder()
    await PostgresCliAnalyticsReader(recorder).get_session_page(_flt(**filters), "start_time", 0, 20)  # type: ignore[arg-type]

    assert len(recorder.statements) == 1
    sql = " ".join(recorder.statements[0].split())
    # the filter is applied to the whole window first: a window count forbids the index-ordered LIMIT walk
    assert "count(*) OVER ()" in sql and f"ORDER BY sort_key DESC, session_id {_C} LIMIT" in sql


@pytest.mark.asyncio
async def test_the_unfiltered_shared_start_time_page_keeps_the_index_ordered_walk_and_the_index_only_count():
    recorder = _PageRecorder()
    await PostgresCliAnalyticsReader(recorder).get_session_page(_flt(), "start_time", 0, 20)  # type: ignore[arg-type]

    page_sql, count_sql = (" ".join(st.split()) for st in recorder.statements)
    assert "count(*) OVER ()" not in page_sql and "LIMIT" in page_sql
    assert "LIMIT" not in count_sql
    # one count per arm group: the started arm is an index-only count, the other arms are few rows
    assert "(SELECT count(*) FROM sel_main) + (SELECT count(*) FROM sel_rest)" in count_sql


@pytest.mark.parametrize(
    "kwargs, filters",
    [
        ({"search": "x"}, {}),
        ({"is_unattributed": True}, {}),
        ({}, {"users": ["a@x.com"]}),
        ({}, {"projects": ["p"]}),
        ({}, {"repositories": ["r"]}),
        ({}, {"branch": "b"}),
        ({}, {"subscription_types": ["codemie"]}),
        ({}, {"project_origins": ["profile"]}),
        ({}, {"scope": _OWN_SCOPE}),
        ({}, {"scope": _PROJECT_ADMIN_SCOPE}),
        ({}, {"deny_all": True}),
    ],
    ids=lambda v: next(iter(v), "unfiltered") if isinstance(v, dict) else None,
)
@pytest.mark.asyncio
async def test_a_filtered_searched_or_other_scope_ctx_per_call_page_keeps_the_full_projection(kwargs, filters):
    recorder = _PageArgRecorder()
    await PostgresCliAnalyticsReader(recorder).get_session_page(  # type: ignore[arg-type]
        _flt(**filters), "ctx_per_call", 0, 5, **kwargs
    )
    sql = " ".join(recorder.statements[0].split())

    projection = _inner_projection(sql)
    assert "d.repo_remote" in projection and "d.owner_email" in projection


_NARROWING = [
    ({"search": "x"}, {}),
    ({"is_unattributed": True}, {}),
    ({}, {"users": ["a@x.com"]}),
    ({}, {"projects": ["p"]}),
    ({}, {"repositories": ["r"]}),
    ({}, {"branch": "b"}),
    ({}, {"subscription_types": ["codemie"]}),
    ({}, {"project_origins": ["profile"]}),
    ({}, {"scope": _OWN_SCOPE}),
    ({}, {"scope": _PROJECT_ADMIN_SCOPE}),
    ({}, {"deny_all": True}),
]


@pytest.mark.parametrize("sort_by", SESSION_PAGE_SORTS)
@pytest.mark.asyncio
async def test_the_unfiltered_framework_request_reads_its_window_index_only_in_every_statement(sort_by):
    recorder = _FrameworkRecorder()
    await PostgresCliAnalyticsReader(recorder).get_framework_page(  # type: ignore[arg-type]
        _flt(), sort_by, 0, 20, lambda sets: sets, _RATES
    )
    statements = [" ".join(st.split()) for st in recorder.statements]
    windows = [st for st in statements if "sel AS (" in st]

    assert len(windows) >= 3  # the names, the page and (start_time, ctx_per_call) the window flags
    for sql in windows:
        # only the ids and the start: both are in `session_dims_active_shared_cov`, so `sel` is an Index Only Scan
        assert _inner_projection(sql) in ("x.session_id, x.started_at", "x.session_id")
    assert _inner_projection(statements[0]) == "x.session_id, x.started_at"  # the names need the start (invocations)
    assert "started_at BETWEEN" in statements[0] and "x.subscription_type" in statements[0]  # the window, the scope


@pytest.mark.parametrize(
    "kwargs, filters", _NARROWING, ids=lambda v: next(iter(v), "unfiltered") if isinstance(v, dict) else None
)
@pytest.mark.asyncio
async def test_a_narrowed_or_searched_framework_request_keeps_the_full_projection_in_the_names_statement(
    kwargs, filters
):
    recorder = _FrameworkRecorder()
    await PostgresCliAnalyticsReader(recorder).get_framework_page(  # type: ignore[arg-type]
        _flt(**filters), "start_time", 0, 20, lambda sets: sets, **kwargs
    )
    create = " ".join(recorder.statements[0].split())

    assert "d.repo_remote" in _inner_projection(create) and "d.owner_email" in _inner_projection(create)


@pytest.mark.asyncio
async def test_the_window_model_flags_of_an_unfiltered_window_read_the_ids_only():
    sql, _ = await _sql_of("get_window_model_flags")

    assert _inner_projection(sql) == "x.session_id"
    assert "x.subscription_type" in sql and "started_at BETWEEN" in sql and sql.count("bool_or(") == 5


@pytest.mark.parametrize("filters", [f for _, f in _NARROWING if f])
@pytest.mark.asyncio
async def test_the_window_model_flags_of_a_narrowed_window_keep_the_full_projection(filters):
    sql, _ = await _sql_of("get_window_model_flags", filters)

    assert "d.repo_remote" in _inner_projection(sql)


# ── funding / personal-billed bucket rows (EPMCDME-16849 item 7) ──


class _ArgsRecorder(_SqlRecorder):
    def __init__(self) -> None:
        super().__init__()
        self.args: list[tuple] = []

    async def fetch(self, sql, *args):
        self.args.append(args)
        return await super().fetch(sql, *args)


async def _sql_and_args(method: str, f: LocalAnalyticsFilter, **kwargs) -> tuple[str, tuple]:
    recorder = _ArgsRecorder()
    await getattr(PostgresCliAnalyticsReader(recorder), method)(f, **kwargs)  # type: ignore[arg-type]
    assert len(recorder.statements) == 1
    return " ".join(recorder.statements[0].split()), recorder.args[0]


@pytest.mark.parametrize(
    "filters", [{}, {"projects": ["p"]}, {"deny_all": True}], ids=["unfiltered", "projects", "deny"]
)
@pytest.mark.asyncio
async def test_funding_rows_read_the_spend_rows_scope_without_the_spend_filter(filters):
    f = _flt(**filters)
    sql, args = await _sql_and_args("get_session_funding_costs", f)
    spend_sql, spend_args = await _sql_and_args("get_session_model_costs", f, spend_only=True)
    # the spend rows read the shared token grouping (`SELECT g.* FROM (...)`), the bucket rows `c.session_id` directly
    head, spend_head = sql.split(" SELECT c.session_id,")[0], spend_sql.split(" SELECT g.* FROM (")[0]

    # the same `sel` (viewer scope, window, filters) and parameters as the spend rows: no read is widened
    assert head.startswith("WITH sel AS (") and "SELECT * FROM (SELECT d.session_id" in head
    assert head == spend_head
    assert args == spend_args
    # the spend rows keep their filter; the bucket rows read every selected session
    assert "NOT IN (SELECT session_id FROM sel WHERE" in spend_sql
    assert "NOT IN (SELECT session_id FROM sel WHERE" not in sql
    assert "coalesce(d.funding_route, 'unknown') AS funding_route" in sql
    assert " ".join(f"{sql_personal_billed('s')} AS personal_billed".split()) in sql
    assert "LEFT JOIN session_dims d ON d.session_id = c.session_id" in sql
    assert "JOIN sel s ON s.session_id = c.session_id" in sql
