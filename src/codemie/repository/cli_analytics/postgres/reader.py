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

"""The PostgreSQL `CliAnalyticsReader`: the analytics queries, answered from PostgreSQL.

Every method returns the row shapes and Python types the port defines, so
the shared handler produces the same JSON. Semantics carried over on purpose:
- a session is in the window iff its `started_at` is in [start, end] (bounds truncated to whole
  milliseconds) AND it has ever produced at least one real activity signal, OR it has a prompt,
  tool call, cost/usage or line-count row on a day wholly inside the window, or on a day the window
  covers in part with a raw activity record stamped inside the window (`_active_in_window`); a
  session that only ever got `session.start`/`session.env` hooks is excluded entirely. The window is
  decided once, in `_sessions_cte`, and every fact query is scoped to that session set;
- string comparisons in ORDER BY and max() use byte order (COLLATE "C");
- a missing LEFT JOIN match reads as '' / 0 / 1970-01-01 in the API where
  PostgreSQL yields NULL; where that reaches the API it is mapped explicitly;
- ties of "the value of the largest / earliest row" go to a stated order instead of scan order.

Tool success, tool usage and invocations read the `invocations_hourly` rollup for the
selected sessions. Two deliberate choices, both documented in the design: `last_active`
falls back to a session's first developer name, and a tool call whose execution span
lands after the window end still counts as successful.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import asyncpg

from codemie.configs.config import config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import (
    G_MODEL,
    G_MODEL_PERSONAL,
    G_MODEL_ROUTE,
    G_PERSONAL,
    G_ROUTE,
    G_TOTAL,
    G_USER,
    G_USER_PERSONAL,
    G_USER_ROUTE,
    SESSION_PAGE_SORTS,
    CostGroup,
    UsersMoneyPage,
    GroupedMoney,
    ModelRates,
    ProjectPage,
    RollupCost,
    RollupOverview,
    RollupUsers,
    Rows,
    StatementTimeout,
)
from codemie.repository.cli_analytics.postgres.cost_key_sql import (
    _SEL_SESSIONS,
    _RATE_COLUMNS,
    _token_rows,
    cost_groups_cte,
    cost_key_from_groups_sql,
    merged_kind_flags_sql,
    model_flags_sql,
    model_token_groups,
    normalized_model_name,
    rates_params,
    session_cost_key_sql,
)
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.agent_call_attribution import (
    agent_call_ids_cte,
    agent_call_scope,
    agent_call_span_end,
    agent_call_tool,
    takes_parent,
)
from codemie.repository.cli_analytics.postgres.grouped_cost_sql import (
    Grouping,
    grouped_cost_sql,
    priced_groups_cte,
)
from codemie.repository.cli_analytics.postgres.fork_attribution import (
    dispatch_fork_candidate,
    executed_agent_call_span,
    executed_tool_span,
    first_subagent_start,
    fork_of_counted_call,
    fork_of_dispatch,
    skill_end_agent_id,
    sole_dispatch_of_prompt,
)
from codemie.repository.cli_analytics.postgres.hook_row_sql import is_claude_code
from codemie.repository.cli_analytics.postgres.inline_skill_attribution import (
    INLINE_SKILL_CTES,
    REQUEST_PAIRS_CTES,
    not_attributed,
    pair_split,
)
from codemie.repository.cli_analytics.postgres.otlp import MAX_KEY_BYTES
from codemie.repository.cli_analytics.postgres.real_work_sql import real_work_filter
from codemie.repository.cli_analytics.postgres.request_routes import REQUEST_ROUTES_CTE, request_route
from codemie.repository.cli_analytics.postgres.rollups import _BLANKS, _attr_ms, _has_totals
from codemie.repository.cli_analytics.postgres.project_page_sql import project_page_sql
from codemie.repository.cli_analytics.postgres.session_derived import CLASSIFIER_NAMES_SQL
from codemie.repository.cli_analytics.postgres.session_page_sql import (
    FRAMEWORK_NAMES_TABLE,
    LIVE_CTE,
    SORT_KEYS_ENABLED_SQL,
    CTX_KEY_COLUMNS,
    COST_KEY_COLUMNS,
    FLAG_COLUMNS,
    STORED_CTX_KEY,
    stored_ctx_key,
    stored_cost_key,
    stored_cost_models_sql,
    cost_page_sql,
    framework_count_sql,
    framework_flags_select,
    framework_names_sql,
    framework_page_sql,
    framework_stored_groups_sql,
    framework_stored_page_sql,
    ordered_page_sql,
    searched_start_time_page_sql,
    live_cte,
    UNSTAMPED_COST_SQL,
    UNSTAMPED_CTX_SQL,
    start_time_count_sql,
    start_time_page_sql,
    window_count_sql,
)
from codemie.repository.cli_analytics.postgres.stored_mode import (
    DEFAULT_S_CAP,
    DISABLED,
    FALLBACK_WARNINGS,
    GROUPS_READ_ERROR,
    NO_RATES_VERSION,
    S_MERGED,
    S_OVER_CAP,
    UNEXPRESSIBLE,
    ModelGroupsMode,
    StoredMode,
    expressible,
    model_groups_mode,
    report_framework_groups,
)
from codemie.repository.cli_analytics.scope import (
    sql_detail_condition,
    sql_list_condition,
    sql_personal_billed,
    sql_private,
    sql_project_origin,
    sql_subscription_type,
)
from codemie.repository.cli_analytics.vocabulary import (
    ACTIVITY_HOOK_TYPES,
    BUILTIN_COMMAND_NAMES,
    SKILL_DISPATCH_EVENT,
    EventKind,
    SpanKind,
)

_NAMED_PARAM = re.compile(r"\$([a-z_][a-z0-9_]*)")
_EPOCH = "'1970-01-01 00:00:00+00'::timestamptz"
_TOOL, _EXEC, _INTERACTION = int(SpanKind.TOOL), int(SpanKind.TOOL_EXECUTION), int(SpanKind.INTERACTION)
_API_REQUEST = int(EventKind.API_REQUEST)
_LLM_REQUEST = int(SpanKind.LLM_REQUEST)

logger = logging.getLogger(__name__)


def _text_array(values: tuple[str, ...]) -> str:
    return "ARRAY[" + ", ".join(f"'{v}'" for v in values) + "]::text[]"


_C = 'COLLATE "C"'
_AND = " AND "
# Whether the latest summary of session_dims `d` reports metric {c} validly (summary_reported, stored with it).
# A row stored before that key existed has none: its non-NULL columns are taken as reported, as before.
_REPORTED = (
    "(d.summary_ts IS NOT NULL AND (d.attrs->'summary_reported' IS NULL "
    "OR d.attrs->'summary_reported' @> to_jsonb('{c}'::text)))"
)
# A whole-session counter of `d`, only when the summary reports it (a fallback or stale stored value is not that).
_SUMMARY_COUNT = "(CASE WHEN " + _REPORTED + " THEN d.{c} END)"
# A summary map (`agents`, `skills`: name -> count) of `d` whose every entry is a whole number: an empty map is a
# real 0, one bad entry makes the map unknown rather than a smaller total.
_VALID_MAP = (
    "(jsonb_typeof(d.{c}) = 'object' AND NOT EXISTS (SELECT 1 FROM jsonb_each(d.{c}) m "
    "WHERE jsonb_typeof(m.value) <> 'number' OR m.value::text !~ '^[0-9]{{1,9}}$'))"
)
# Its sum: NULL unless the summary reports it and the whole map is valid.
_SUMMARY_MAP_TOTAL = (
    "(CASE WHEN " + _REPORTED + _AND + _VALID_MAP + " THEN coalesce((SELECT sum((m.value::text)::bigint) "
    "FROM jsonb_each(d.{c}) m), 0) END)"
)
_SUMMARY_TURNS, _SUMMARY_TOOL_CALLS = (_SUMMARY_COUNT.format(c=c) for c in ("turns", "tool_calls"))
# none without a summary; the persisted state of the latest one; a summary stored before the state existed is not
# proven final, so it is provisional.
_SUMMARY_STATE = (
    "CASE WHEN d.summary_ts IS NULL THEN 'none' ELSE coalesce(d.attrs->>'summary_state', 'provisional') END"
)
_SUMMARY_METRICS = (
    "CASE WHEN d.summary_ts IS NULL THEN ARRAY[]::text[] "
    "WHEN d.attrs->'summary_reported' IS NOT NULL "
    "THEN ARRAY(SELECT jsonb_array_elements_text(d.attrs->'summary_reported')) "
    "ELSE array_remove(ARRAY[CASE WHEN d.turns IS NOT NULL THEN 'turns' END, "
    "CASE WHEN d.tool_calls IS NOT NULL THEN 'tool_calls' END, "
    "CASE WHEN " + _VALID_MAP.format(c="agents") + " THEN 'agents' END, "
    "CASE WHEN " + _VALID_MAP.format(c="skills") + " THEN 'skills' END], NULL) END"
)
_MAX_PAGE_OFFSET = 2**62  # far below the int64 bound of `OFFSET $offset::bigint`, far beyond any real row count


# The response vocabulary of a selected session (`s` = sel); see scope.py.
_SUB = sql_subscription_type("s.subscription_type")
_ORIGIN = sql_project_origin("s.project_origin")
_CLASS_LISTS = f"array_agg(DISTINCT {_SUB}) AS subscription_types, array_agg(DISTINCT {_ORIGIN}) AS project_origins"


def _turns_cte(sessions: str = "sel") -> str:
    """Turns per session of the CTE `sessions`. The fact CTEs join it rather than filter by `IN (SELECT ...)`: the
    planner then hashes the window's sessions against the rollup (0.8 s instead of 6.7 s at 600,000 sessions)."""
    return (
        "turns_t AS (SELECT t.session_id, sum(t.turns)::bigint AS turns FROM turns_daily t "
        f"JOIN {sessions} s ON s.session_id = t.session_id GROUP BY t.session_id)"
    )


# A session's turns: observed turns prove activity, not completeness; a 0 is served only when the summary reports it.
_TURNS_VALUE = f"greatest(nullif(turns_t.turns, 0), {_SUMMARY_TURNS})::bigint"
_TURNS_FROM = (
    "sel JOIN session_dims d ON d.session_id = sel.session_id LEFT JOIN turns_t ON turns_t.session_id = sel.session_id"
)


def _tools_cte(sessions: str = "sel") -> str:
    """Tool calls per session of the CTE `sessions`. A join, not `IN (SELECT ...)`: the planner then hashes the window's
    sessions against the rollup (1 s instead of 18 s at 600,000 sessions)."""
    return (
        "tool_o AS (SELECT i.session_id, sum(i.calls)::bigint AS tool_calls, "
        "sum(i.success)::bigint AS tool_calls_success "
        f"FROM invocations_hourly i JOIN {sessions} s ON s.session_id = i.session_id "
        "WHERE i.kind = 1 AND i.session_id <> '' GROUP BY i.session_id)"
    )


def _files_ctes(sessions: str = "sel") -> str:
    """Distinct files per session of the CTE `sessions`, counted in two hash steps (not count(DISTINCT)). `file_t`
    holds the sessions with tool facts: only those report file counts (`file_l` is read through it)."""
    return f"""file_t AS (
            SELECT t.session_id, sum(t.tool_calls)::bigint AS tool_calls
            FROM tool_facts_daily t JOIN {sessions} s ON s.session_id = t.session_id
            GROUP BY t.session_id
        ),
        per_file AS (
            SELECT f.session_id, f.file_path, bool_or(f.is_written) AS written, bool_or(f.is_edited) AS edited
            FROM session_files_daily f JOIN {sessions} s ON s.session_id = f.session_id
            GROUP BY f.session_id, f.file_path
        ),
        file_l AS (
            SELECT session_id,
                   count(*) FILTER (WHERE written OR edited) AS files_changed,
                   count(*) FILTER (WHERE written) AS files_written,
                   count(*) FILTER (WHERE edited)  AS files_edited
            FROM per_file
            GROUP BY session_id
        )"""


def _lines_cte(sessions: str = "sel") -> str:
    return (
        "lines_s AS (SELECT l.session_id, sum(l.lines_added)::bigint AS lines_added, "
        "sum(l.lines_removed)::bigint AS lines_removed FROM lines_daily l "
        f"JOIN {sessions} s ON s.session_id = l.session_id GROUP BY l.session_id)"
    )


# One row per selected session `s` with the non-money facts every grouped read aggregates. A fact the per-session
# statements leave out for a session (no row) is 0 here, and lines stay NULL (unknown).
_FILE_JOINS = """LEFT JOIN file_t ON file_t.session_id = s.session_id
            LEFT JOIN file_l ON file_l.session_id = file_t.session_id"""
_SESSION_FACTS_FROM = f"""sel s
            JOIN session_dims d ON d.session_id = s.session_id
            LEFT JOIN turns_t ON turns_t.session_id = s.session_id
            {_FILE_JOINS}
            LEFT JOIN lines_s ON lines_s.session_id = s.session_id"""
_TOOL_FACTS_FROM = "LEFT JOIN tool_o ON tool_o.session_id = s.session_id"
_TOOL_FACT_COLUMNS = (
    "coalesce(tool_o.tool_calls, 0) AS tool_calls, coalesce(tool_o.tool_calls_success, 0) AS tool_calls_success"
)
_SESSION_FACT_COLUMNS = f"""s.session_id,
                   coalesce({_TURNS_VALUE}, 0) AS turns,
                   coalesce(file_l.files_changed, 0) AS files_changed,
                   coalesce(file_l.files_written, 0) AS files_written,
                   coalesce(file_l.files_edited, 0) AS files_edited,
                   lines_s.lines_added, lines_s.lines_removed,
                   coalesce(nullif({_SUB}, ''), 'unknown') AS sub,
                   coalesce(nullif({_ORIGIN}, ''), 'none') AS origin"""

# The columns of the dashboard statements; every part of their UNION ALL fills the ones it uses.
_TEXT_ARRAY = "text[]"
_CLIENT_TIMEOUT = "the statement outran the client command timeout"
_PART_COLUMNS = (
    ("part", "text"),
    ("key", "text"),
    ("n", "bigint"),
    ("files_changed", "bigint"),
    ("files_written", "bigint"),
    ("files_edited", "bigint"),
    ("lines_added", "bigint"),
    ("lines_removed", "bigint"),
    ("turns", "bigint"),
    ("tool_calls", "bigint"),
    ("tool_calls_success", "bigint"),
    ("duration_ms", "bigint"),
    ("net_lines", "bigint"),
    ("nonzero", "boolean"),
    ("subscription_types", _TEXT_ARRAY),
    ("project_origins", _TEXT_ARRAY),
    ("ratio", "float8"),
    ("session_id", "text"),
    ("session_title", "text"),
    ("entrypoint", "text"),
    ("prompt", "text"),
    ("project_name", "text"),
    ("repository", "text"),
    ("branch", "text"),
    ("funding_route", "text"),
    ("personal_billed", "boolean"),
    ("subscription_type", "text"),
    ("cost_usd", "numeric"),
    ("cache_read_cost_usd", "numeric"),
    ("session_count", "bigint"),
    ("unpriced_models", _TEXT_ARRAY),
    ("cost_incomplete", "boolean"),
)


def _part(part: str, **values: str) -> str:
    return ", ".join(
        f"{repr(part) if name == 'part' else values.get(name, 'NULL')}::{kind} AS {name}"
        for name, kind in _PART_COLUMNS
    )


# The money of a request's totals: the spend (every selected session, the Personal-paid ones too since EPMCDME-17260;
# its cost, cache re-read cost and unpriced models), and its split by funding route and by personal billing.
_TOTAL_GROUPINGS = (
    Grouping("spend", []),
    Grouping("route", ["funding_route"]),
    Grouping("billed", ["personal_billed"]),
)
# The Overview's totals also hold the cost split by subscription class (`class`: API against the rest).
_OVERVIEW_GROUPINGS = (*_TOTAL_GROUPINGS, Grouping("class", ["subscription_type"]))
_GC_KEY_PARTS = {"project": "project_name", "repository": "repository", "branch": "branch"}


def _used_keys(groupings: Sequence[Grouping]) -> set[str]:
    return {key for grouping in groupings for key in grouping.keys}


def _gc_part(part: str, groupings: Sequence[Grouping], alias: str = "m") -> str:
    """The parts-shape select list over the rows `alias` of `grouped_cost_sql(groupings)` (the key is the grouping)."""
    used = _used_keys(groupings)
    values = {
        "key": f'{alias}."grouping"',
        "cost_usd": f"{alias}.cost_usd",
        "cache_read_cost_usd": f"{alias}.cache_read_cost_usd",
        "session_count": f"{alias}.session_count",
        "unpriced_models": f"{alias}.unpriced_models",
        "cost_incomplete": f"{alias}.cost_incomplete",
    }
    values.update({column: f"{alias}.{key}" for key, column in _GC_KEY_PARTS.items() if key in used})
    values.update(
        {key: f"{alias}.{key}" for key in ("funding_route", "personal_billed", "subscription_type") if key in used}
    )
    return _part(part, **values)


# a session's spend money: what the dead-session predicate reads
_SESSION_COST = "scost AS (SELECT session_id, sum(cost) AS cost FROM priced_groups GROUP BY session_id)"


_STARTED_IN_WINDOW = "started_at BETWEEN $start_dt::timestamptz AND $end_dt::timestamptz"
# `day` columns are always written in UTC (see rollups.py's `AT TIME ZONE 'UTC'` casts); a bare
# `$start_dt::date`/`$end_dt::date` cast of a timestamptz parameter instead follows the session's
# `TimeZone` GUC, which `server_settings()` never pins, so it must go through UTC explicitly here.
_START_DAY_UTC = "($start_dt::timestamptz AT TIME ZONE 'UTC')::date"
_END_DAY_UTC = "($end_dt::timestamptz AT TIME ZONE 'UTC')::date"
_START_DAY_BEGIN = f"({_START_DAY_UTC}::timestamp AT TIME ZONE 'UTC')"
_AFTER_END_DAY = f"(({_END_DAY_UTC} + 1)::timestamp AT TIME ZONE 'UTC')"


# The UTC days whose every instant is inside the window. The bounds are whole milliseconds, so a window
# ending at 23:59:59.999 covers its last day. The arithmetic runs on UTC timestamps, never on timestamptz,
# so a DST change of the session's TimeZone GUC cannot shift it.
_FIRST_WHOLE_DAY_UTC = "(($start_dt::timestamptz AT TIME ZONE 'UTC') - interval '1 millisecond')::date + 1"
_LAST_WHOLE_DAY_UTC = "(($end_dt::timestamptz AT TIME ZONE 'UTC') + interval '1 millisecond')::date - 1"


_ACTIVITY_TABLES = ("turns_daily", "tool_facts_daily", "cost_daily", "session_usage", "lines_daily")


def _activity_exists(alias: str, days: tuple[str, str | None] | None = None, fenced: bool = False) -> str:
    """A prompt, a tool call, a cost/usage row or a line-count row for the session named by
    `alias`.session_id, on a day from `days[0]` to `days[1]` when given (`days[1]` None: with no last day), at any
    time otherwise.
    `fenced` ends each probe with `OFFSET 0`: PostgreSQL then keeps it a per-row index probe, where otherwise it may
    turn it into a hash of every row of the window (right for a million sessions, ruinous for a few thousand)."""
    bound = ""
    if days:
        bound = f" AND ax.day >= {days[0]}" + (f" AND ax.day <= {days[1]}" if days[1] else "")
    fence = " OFFSET 0" if fenced else ""
    clauses = (
        f"EXISTS (SELECT 1 FROM {t} ax WHERE ax.session_id = {alias}.session_id{bound}{fence})"
        for t in _ACTIVITY_TABLES
    )
    return "(" + " OR ".join(clauses) + ")"


# The raw records the activity tables are built from: prompts, tool and subagent calls, skill dispatches,
# API requests and line counts, as `real_work_sql` defines them (the end of a session stops at the same records).
# Lifecycle hooks (session start, env, end, git snapshot, summary), Claude Code's own startup records and a built-in
# command such as `/exit` are not among them, so a resume with nothing typed leaves no such record.
_ACTIVITY_HOOKS = _text_array(ACTIVITY_HOOK_TYPES)
_RAW_ACTIVITY_SOURCES = tuple(
    (table, real_work_filter(table, "r"))
    for table in ("hook_events", "usage_requests", "log_events", "spans", "metric_points")
)


def _raw_activity_in_window(alias: str, fenced: bool = False, open_ended: bool = False) -> str:
    """A raw activity record of the session named by `alias`.session_id, stamped inside the window (`open_ended`: at
    or after its start, however late)."""
    fence = " OFFSET 0" if fenced else ""
    until = "" if open_ended else " AND r.ts <= $end_dt::timestamptz"
    clauses = (
        f"EXISTS (SELECT 1 FROM {table} r WHERE r.session_id = {alias}.session_id "
        f"AND r.ts >= $start_dt::timestamptz{until}{kind}{fence})"
        for table, kind in _RAW_ACTIVITY_SOURCES
    )
    return "(" + " OR ".join(clauses) + ")"


def _active_in_window(alias: str, fenced: bool = False, raw_first: bool = False) -> str:
    """Activity rescue arm: an old session resumed with no new activity in the window stays excluded from
    `sel`, an old session with fresh activity in the window is included. A day row on a UTC day wholly
    inside the window is enough. A day the window covers only in part also needs a raw activity record
    stamped inside the window, so a sub-day window does not admit a session active on the same day at
    another time. Raw records are kept for the raw retention only, which is why whole days keep the
    day-row rule."""
    whole_days = _activity_exists(alias, (_FIRST_WHOLE_DAY_UTC, _LAST_WHOLE_DAY_UTC), fenced)
    window_days = _activity_exists(alias, (_START_DAY_UTC, _END_DAY_UTC), fenced)
    raw = _raw_activity_in_window(alias, fenced)
    if raw_first:
        # The same rule with the selective half first, for sessions whose rows all sit on one partial day.
        return f"(({raw} AND {window_days}) OR {whole_days})"
    return f"({whole_days} OR ({window_days} AND {raw}))"


# The raw records are kept for the raw retention only (read once, like every other setting).
_RAW_RETENTION_DAYS = int(config.CLI_ANALYTICS_RAW_RETENTION_DAYS)


def _no_raw_check_needed() -> str:
    """A raw record is asked for only by a window of a day or less that starts in the middle of a UTC day: every instant
    of the first day of a window that starts at midnight is inside it, and a longer window (Last 7 days, a month) keeps
    the day-granular rule. The second of slack is for bounds that are a few milliseconds off a whole day. A window that
    starts before the raw retention asks for none either: the raw rows it would look for may be purged, and a session
    listed then must not vanish for that (the day rows decide, as they do for a longer window)."""
    return (
        f"($start_dt::timestamptz = {_START_DAY_BEGIN} "
        "OR $end_dt::timestamptz - $start_dt::timestamptz > interval '1 day 1 second' "
        f"OR $start_dt::timestamptz < now() - interval '{_RAW_RETENTION_DAYS} days')"
    )


def _real_work_from_window_start(alias: str, fenced: bool = False) -> str:
    """The session named by `alias`.session_id has real work at or after the window's start, when the stored facts
    say that its last activity day is the window's first day: a day row says only the day, so a window of a day or
    less that does not start at midnight (Last hour, Last 6 hours, Last 24 hours) needs a raw record at or after the
    start to confirm it."""
    return f"({_no_raw_check_needed()} OR {_raw_activity_in_window(alias, fenced, open_ended=True)})"


def _activity_from_window_start(alias: str) -> str:
    """Whether the session named by `alias`.session_id has a prompt, tool call, cost/usage or line-count row on or
    after the window's first UTC day and, when the only such rows are on that first day of a window of a day or less,
    a raw record of real work at or after the window's start. Gates the `_STARTED_IN_WINDOW` admission.

    A session that only ever got `session.start`/`session.env` hooks (a VS Code restart reopening a panel, a Copilot
    session closed without a prompt) must not be listed at all. Neither must a session whose every activity lies
    before the window and whose start was stamped into it by a later reopen, be that on an earlier day or earlier the
    same day. A session that started in the window and has real activity from the window's first day on keeps the
    exact `_STARTED_IN_WINDOW` admission, so sub-day window-boundary precision for recently started, genuinely active
    sessions is unaffected (a day row says only the day, not the time)."""
    after_first_day = _activity_exists(alias, (f"({_START_DAY_UTC} + 1)", None))
    on_first_day = _activity_exists(alias, (_START_DAY_UTC, _START_DAY_UTC))
    return f"({after_first_day} OR ({_real_work_from_window_start(alias)} AND {on_first_day}))"


_SESSION_ADMITTED_IN_WINDOW = (
    f"(({_STARTED_IN_WINDOW} AND {_activity_from_window_start('d')}) OR {_active_in_window('d')})"
)


def _started_in_window(alias: str) -> str:
    return f"{alias}.started_at BETWEEN $start_dt::timestamptz AND $end_dt::timestamptz"


def _admitted_arms(alias: str, columns: str, scope: str | None = None) -> tuple[str, ...]:
    """`_SESSION_ADMITTED_IN_WINDOW` as five SELECTs of `session_dims {alias}`, each served by its own index (a
    single OR of them would not be). A session is in exactly one arm. The stored facts of `session_activity`
    (`has_activity`, `last_activity_day`, `resumed`) stand for the rollup-table probes; a session whose facts are
    not derived yet (NULL) is asked of the tables, as the rule itself does, so the result is the rule's at every
    point of the roll-out.

    - started: started in the window and `has_activity`, the last activity day not before the window's first day and,
      when it is that day, real work at or after the window's start (`_real_work_from_window_start`).
    - started, underived: started in the window, `has_activity` NULL: `_activity_from_window_start`, as the rule says.
    - outside, underived: started outside the window, `has_activity` NULL: `_active_in_window`, as the rule says.
    - outside, resumed: started outside the window, `resumed`, last activity day not before the window's first day.
    - outside, one day: started outside the window, not `resumed` (all rows on its start day), and that day is the
      window's first (started before it) or last (started after it): the raw records decide first.
    Both derived arms run `_active_in_window` with per-row probes (`fenced`) on the candidates only.
    `columns` is the select list, `scope` an extra condition on `alias` (the viewer scope of the ids-only form)."""
    a = alias
    base = f"FROM session_dims {a} WHERE {a}.started_at IS NOT NULL" + (f" AND {scope}" if scope else "")
    base += f" AND {_is_listed(a)}"
    started = _started_in_window(a)
    outside = f"NOT ({started}) AND {a}.has_activity"
    on_edge_day = (
        f"(({a}.started_at >= {_START_DAY_BEGIN} AND {a}.started_at < $start_dt::timestamptz)"
        f" OR ({a}.started_at > $end_dt::timestamptz AND {a}.started_at < {_AFTER_END_DAY}))"
    )
    return (
        f"SELECT {columns} {base} AND {started} AND {a}.has_activity AND {a}.last_activity_day >= {_START_DAY_UTC}"
        f" AND ({a}.last_activity_day > {_START_DAY_UTC} OR {_real_work_from_window_start(a, True)})",
        f"SELECT {columns} {base} AND {started} AND {a}.has_activity IS NULL AND {_activity_from_window_start(a)}",
        f"SELECT {columns} {base} AND NOT ({started}) AND {a}.has_activity IS NULL AND {_active_in_window(a)}",
        f"SELECT {columns} {base} AND {outside} AND {a}.resumed AND {a}.last_activity_day >= {_START_DAY_UTC}"
        f" AND {_active_in_window(a, True)}",
        f"SELECT {columns} {base} AND {outside} AND NOT {a}.resumed AND {on_edge_day}"
        f" AND {_active_in_window(a, True, True)}",
    )


def _developer_narrowing(f: LocalAnalyticsFilter, repository_search: bool, page_repositories: bool) -> list[str]:
    """The conditions over `sel`'s developer and repository columns (the form with `with_developer`)."""
    narrowing = ["developer = ANY($developers::text[])"] if f.developers else []
    if repository_search:
        narrowing.append("nullif(repository, '') ILIKE $search::text")
    if page_repositories:
        narrowing.append(
            "(nullif(repository, '') = ANY($page_repositories::text[])"
            " OR ($page_unattributed::boolean AND nullif(repository, '') IS NULL))"
        )
    return narrowing


def _sel_ctes(
    arms: tuple[str, ...],
    where: str = "",
    star: bool = True,
    split: bool = False,
    developer_where: str | None = None,
) -> str:
    """The CTE `sel`: the union of the arms (`where` filters the full projection). With `split` it is followed by
    `sel_main` (the started arm: the start_time page walks it in index order and the count reads it index-only) and
    `sel_rest` (the other arms, few rows). The unfiltered start_time page and count read those two directly, because
    the union hides the index order (the planner sorts every row of the started arm) and slows the count threefold;
    `sel` itself is then not read, so nothing is computed twice.
    `developer_where` (a string, possibly empty; star form only, without `split`) adds the `developer` column, the
    session's `_DEVELOPER` computed once over the rows that passed `where`, and applies that condition to it."""
    main, *others = arms
    union = " UNION ALL ".join(arms)
    body = f"SELECT * FROM ({union}) x{where}" if star else union
    if developer_where is not None:
        body = f"SELECT * FROM (SELECT s.*, {_DEVELOPER} AS developer FROM ({body}) s) s{developer_where}"
    ctes = f"""
        sel AS ({body})"""
    if split:
        rest = " UNION ALL ".join(others)
        if star:
            main, rest = f"SELECT * FROM ({main}) x{where}", f"SELECT * FROM ({rest}) x{where}"
        ctes += f""",
        sel_main AS ({main}),
        sel_rest AS ({rest})"""
    return ctes


# The person of a session_dims row `d`: the JWT email, else the largest developer name seen, else the developer name.
# One expression for the session list and for the framework sessions, so the two cannot name a person differently.
_SESSION_USER_EMAIL = (
    "coalesce(nullif(coalesce(nullif(d.jwt_email, ''), nullif(d.dev_name_max, '')), ''), "
    "nullif(d.developer_name, ''), '')"
)
# Clips the raw hook events that decide a session's last dimension event (not session membership).
_TS = "ts BETWEEN $start_dt::timestamptz AND $end_dt::timestamptz"
# Slash commands have one source per session: a session with a summary counts only the `commands`
# of its summary, so its OTel `user_prompt` records (kind 4 of the rollup) add nothing.


_SEARCH_COLUMNS = ("first_prompt", "session_title", "repository", "branch")

# Claude Desktop's own auto-title is not an explicit rename, so search must not find a session only by that
# hidden title, or a result would show a session the list itself never displays under that name.
# Mirrors cli_analytics_handler._DESKTOP_ENTRYPOINTS; kept local here since the search condition is built
# in SQL, not in the handler that owns the display-side constant — test_the_sql_desktop_entrypoint_list_matches_
# the_handler_constant (test_reader.py) keeps the two lists equal. The literals are already lowercase,
# so trimming and lower-casing `s.entrypoint` matches them case-insensitively without changing them.
_DESKTOP_ENTRYPOINTS_SQL = "('claude-desktop', 'claude-desktop-3p', 'local-agent', 'remote_cowork')"


def _search_arm(column: str) -> str:
    arm = f"s.{column} ILIKE $search::text ESCAPE '\\'"
    return (
        f"({arm} AND lower(btrim(s.entrypoint, {_BLANKS})) NOT IN {_DESKTOP_ENTRYPOINTS_SQL})"
        if column == "session_title"
        else arm
    )


def _escape_like(term: str) -> str:
    """`term` with the LIKE wildcards and the escape character made literal (for `ESCAPE '\\'`)."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _no_summary(column: str) -> str:
    return f"NOT EXISTS (SELECT 1 FROM session_dims sd WHERE sd.session_id = {column} AND sd.summary_ts IS NOT NULL)"


def _not_a_skill_of_session(session_column: str, name_column: str) -> str:
    """A command name the same session also ran as a skill (kind 2) is that skill's invocation, not a slash command.
    It reads the CTE `skill_rows` (the skill and agent rows of `sel`, scanned once for the whole statement), so it is
    scoped like every fact read and probes no partition: a probe of the partitioned table per command row visited every
    partition each time. No bound on the hour is safe: a session that started before the window and was resumed in it
    has its skill row before the window's start."""
    return (
        "NOT EXISTS (SELECT 1 FROM skill_rows sk "
        f"WHERE sk.kind = 2 AND sk.session_id = {session_column} AND sk.name = {name_column})"
    )


def _row_email(alias: str) -> str:
    """The smallest non-empty `user_email` of the `alias` rows, byte order; an aggregate, or a window with OVER."""
    return f"min(nullif({alias}.user_email, '') {_C})"


def _session_email(alias: str, reporting_lines_only: bool = False) -> str:
    """The session's row email as a window column. With `reporting_lines_only`, over the `alias` rows of
    lines_daily that report changed lines: zero-sum rows only say "lines are known" and must not name a developer."""
    only = f" FILTER (WHERE {alias}.lines_added <> 0 OR {alias}.lines_removed <> 0)" if reporting_lines_only else ""
    return f"{_row_email(alias)}{only} OVER (PARTITION BY {alias}.session_id) AS session_email"


def _developer(row_email: str) -> str:
    """One developer per session: the session's email, else the smallest non-empty email of its rows, so a session
    whose rows carry different emails is never split."""
    return f"coalesce(nullif(s.user_email, ''), {row_email}, 'unknown')"


# A lines_daily row (unqualified, or `x.`-qualified by its alias) that reports changed lines. Rows summing to zero
# only say "lines are known", so classification (day class lists, code-change sessions) ignores them.
_LINES_NONZERO = "lines_added <> 0 OR lines_removed <> 0"
# The smallest non-empty cost_daily email of the session `s`, the first fallback of its developer.
_COST_EMAIL = f"(SELECT {_row_email('c')} FROM cost_daily c WHERE c.session_id = s.session_id)"
# The smallest non-empty email of the session `s` among its lines_daily rows that report changed lines. Zero-sum
# rows only say "lines are known" and are kept since the null-vs-zero change, so they must not become a developer
# source the session did not have before.
_LINES_EMAIL = (
    f"(SELECT {_row_email('x')} FROM lines_daily x "
    f"WHERE x.session_id = s.session_id AND (x.lines_added <> 0 OR x.lines_removed <> 0))"
)


def _session_developer(lines_email: str) -> str:
    """The developer of session `s`: its email, else its smallest non-empty cost_daily email, else `lines_email`
    (its smallest non-empty lines_daily email)."""
    return _developer(f"coalesce({_COST_EMAIL}, {lines_email})")


# One developer expression for every query: self-contained correlated subqueries against `s.session_id`
# rather than a borrowed `c`/`d` alias, so every caller agrees on a costless session's developer the same
# way, instead of silently depending on which row alias happens to be in scope.
_DEVELOPER = _session_developer(_LINES_EMAIL)

# The row identity of a log_events record, as the request pairing names it (`okey`).
_OKEY = "log_events.tableoid::text || log_events.ctid::text"


# The model of the row with the most calls, over the rows of the original grain of cost_daily:
# (day, session, user, model, query_source). cost_daily now has several rows per
# such key (speed, geo, scope, agent type), so their calls are summed back to that grain first (window
# `w`), then the largest row wins; ties go to the earliest day, then the key columns, as before. On
# days without plugin data cost_daily has one row per such key and the answer is unchanged. The model is the
# normalised name, so a model run on several routes ranks and is returned as one.
_GRAIN_CALLS = "sum(c.api_call_count) OVER w AS grain_calls"
_GRAIN_MODEL = normalized_model_name("c.model_name")
_GRAIN = f"c.day, c.session_id, c.user_email, {_GRAIN_MODEL}, c.query_source"
_TOP_MODEL = (
    f"(array_agg({_GRAIN_MODEL} ORDER BY c.grain_calls DESC NULLS LAST, c.day, c.session_id {_C}, "
    f"c.user_email {_C}, {_GRAIN_MODEL} {_C}, c.query_source {_C}) FILTER (WHERE c.model_name IS NOT NULL))[1]"
)


def _cost_rows(where: str, joined: bool = False) -> str:
    """`cost_daily` rows of the selection, each with the calls of its original grain (see _TOP_MODEL) and its
    session's row email (see _DEVELOPER). `joined` scopes them to `sel` by a join instead of `where`: `sel` has one
    row per session, so the rows are the same, and at 600K sessions the planner probes every partition's index once
    per session for `IN (SELECT session_id FROM sel)` and hashes `sel` once for the join."""
    source = (
        "cost_daily c JOIN sel x ON x.session_id = c.session_id WHERE true" if joined else f"cost_daily c WHERE {where}"
    )
    return f"(SELECT c.*, {_GRAIN_CALLS}, {_session_email('c')} FROM {source} WINDOW w AS (PARTITION BY {_GRAIN})) c"


_TOTAL_COLUMNS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")


def _tokens(column: str, include_totals: bool) -> str:
    """Session sum of a token `column` of cost_daily; with `include_totals` also the subagent totals-only rows of
    `session_usage` (see _token_rows), so the list agrees with the detail view. The totals come from the join `t`
    of `_facts_from`, constant per session, so they are taken with max, never summed over the cost rows."""
    base = f"sum(c.{column})"
    if not include_totals:
        return f"{base}::bigint"
    return f"(coalesce({base}, 0) + coalesce(max(t.{column}), 0))::bigint"


def _totals_join(only: str = "", scope: str = "sel") -> str:
    """Subagent totals-only rows of `session_usage` summed to one row per selected session, so the join cannot
    multiply the cost rows of the outer GROUP BY. Scoped to the CTE `scope` (`sel`, or `live` for the live keys of a
    stored read), otherwise it would scan all of session_usage; `only` narrows it further (a condition on
    session_id)."""
    sums = ", ".join(f"sum(coalesce({c}, 0)) AS {c}" for c in _TOTAL_COLUMNS)
    return (
        f"(SELECT session_id, {sums} FROM session_usage "
        f"WHERE attrs->>'token_source' = 'subagent_usage' AND session_id IN (SELECT session_id FROM {scope}){only} "
        "GROUP BY session_id) t"
    )


# The cache-read tokens and API calls of the selected sessions, summed from `cost_daily` (one row per session); a
# stored read sums them for the CTE `live` only (`{scope}` is `sel` or `live`).
_CTX_TOKENS = (
    "(SELECT session_id, sum(cache_read_tokens) AS cache_read, sum(api_call_count) AS calls "
    "FROM cost_daily WHERE session_id IN (SELECT session_id FROM {scope}) GROUP BY session_id) c"
)
_CTX_KEY_FROM_TOKENS = (
    "CASE WHEN coalesce(c.calls, 0) = 0 THEN 0::numeric ELSE "
    "round((coalesce(c.cache_read, 0) + coalesce(t.cache_read_tokens, 0)) / c.calls, 9) END"
)


def _facts_from(include_costless: bool) -> str:
    """FROM clause of `get_session_cost_facts`: sessions that have cost rows, or (opt-in) every selected session."""
    cost = _cost_rows("c.session_id IN (SELECT session_id FROM sel)")
    if include_costless:
        return f"""sel s
             JOIN act a ON a.session_id = s.session_id
             LEFT JOIN {cost} ON c.session_id = s.session_id
             LEFT JOIN {_totals_join()} ON t.session_id = s.session_id"""
    return f"""{cost}
             JOIN sel s ON s.session_id = c.session_id
             JOIN act a ON a.session_id = c.session_id"""


# Main's rule decides which sessions have activity (`_has_any_activity`, the stored `has_activity`); this gate adds
# one thing to it: the own id of a sub-agent (a parent's agent.subagent.start names it as agent_id, see
# `hook_row_sql.names_subagent_session`; its work belongs to the parent) is not listed unless it has cost rows of its
# own. `is_subagent` is set at rollup time from that event. Session Detail by id is not gated: a direct link to a
# hidden session still opens. A session that is not a sub-agent short-circuits before the cost probe.
def _is_listed(alias: str) -> str:
    return (
        f"(NOT coalesce({alias}.is_subagent, false) "
        f"OR EXISTS (SELECT 1 FROM cost_daily cd WHERE cd.session_id = {alias}.session_id))"
    )


def detail_gate(scope: Any, sid: str = "$sid::text") -> tuple[str, dict[str, Any]]:
    """Per-session detail authorization (mode `detail`): a LEFT JOIN of session_dims on the session id, so a
    session without a dims row is class M (derived project '', i.e. unattributed) and an existing Personal row
    without a matching owner is denied. Absent or malformed scope -> always false."""
    cond, params = sql_detail_condition(scope)
    gate = (
        f"EXISTS (SELECT 1 FROM (SELECT {sid} AS session_id) g "
        f"LEFT JOIN session_dims d ON d.session_id = g.session_id WHERE {cond})"
    )
    return gate, params


def positional(sql: str, params: dict[str, Any]) -> tuple[str, list[Any]]:
    """`$name` placeholders -> asyncpg's `$1, $2, ...`, with the values in the matching order."""
    names: list[str] = []

    def number(match: re.Match) -> str:
        name = match.group(1)
        if name not in names:
            names.append(name)
        return f"${names.index(name) + 1}"

    text = _NAMED_PARAM.sub(number, sql)
    return text, [params[name] for name in names]


# The groupings of `get_cost_money` (every session's money, the Personal-paid ones too); `user` ones keep the
# `cost_daily` sessions `get_cost_by_user` lists (a subagent-totals-only session is in the total and per model, in
# no user's figure).
_COST_GROUPINGS = (
    Grouping(G_TOTAL, []),
    Grouping(G_USER, ["user"], cost_daily_only=True),
    Grouping(G_MODEL, ["model"]),
    Grouping(G_ROUTE, ["funding_route"]),
    Grouping(G_PERSONAL, ["personal_billed"]),
    Grouping(G_USER_ROUTE, ["user", "funding_route"], cost_daily_only=True),
    Grouping(G_USER_PERSONAL, ["user", "personal_billed"], cost_daily_only=True),
    Grouping(G_MODEL_ROUTE, ["model", "funding_route"]),
    Grouping(G_MODEL_PERSONAL, ["model", "personal_billed"]),
)

# What the Cost tab reads without the estimate buckets: the spend total, per user and per model.
_COST_SPEND_GROUPINGS = _COST_GROUPINGS[:3]

# The groupings of `get_users_money_page`: the same names as /cost, but per user over every session of `sel`.
_USERS_GROUPINGS = (
    Grouping(G_TOTAL, []),
    Grouping(G_USER, ["user"]),
    Grouping(G_ROUTE, ["funding_route"]),
    Grouping(G_PERSONAL, ["personal_billed"]),
    Grouping(G_USER_ROUTE, ["user", "funding_route"]),
    Grouping(G_USER_PERSONAL, ["user", "personal_billed"]),
)


def grouped_money(rows: Rows, groupings: Sequence[Grouping]) -> GroupedMoney:
    """The `gc` rows (a `grouping` column, the key columns, the money columns) as a `GroupedMoney`: every requested
    name is present, money is `float`, so a zero reads back as 0.0."""
    keys = {g.name: g.keys for g in groupings}
    groups: dict[str, list[CostGroup]] = {g.name: [] for g in groupings}
    for row in rows:
        name = row["grouping"]
        if name is None:  # a ranked developer with no money row (users page): no group
            continue
        groups[name].append(
            CostGroup(
                keys={key: row[key] for key in keys[name]},
                cost_usd=float(row["cost_usd"] or 0),
                cache_read_cost_usd=float(row["cache_read_cost_usd"] or 0),
                session_count=int(row["session_count"]),
                unpriced_models=tuple(row["unpriced_models"] or ()),
                cost_incomplete=bool(row["cost_incomplete"]),
            )
        )
    return GroupedMoney(groups)


def normalise(value: Any) -> Any:
    """asyncpg values -> the types the port returns (naive UTC datetime, int, float)."""
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _rows(records: Any, exact: bool) -> Rows:
    """The records as the port's rows; with `exact` a `Decimal` stays one instead of becoming an int or a float."""
    return [
        {key: value if exact and isinstance(value, Decimal) else normalise(value) for key, value in record.items()}
        for record in records
    ]


def truncate_to_ms(dt: datetime) -> datetime:
    """A window bound truncated to whole milliseconds, in UTC; a naive bound is taken as UTC."""
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return dt.replace(microsecond=dt.microsecond // 1000 * 1000)


def window_params(f: LocalAnalyticsFilter) -> dict[str, Any]:
    start, end = truncate_to_ms(f.start_dt), truncate_to_ms(f.end_dt)
    params: dict[str, Any] = {
        "start_dt": start,
        "end_dt": end,
    }
    params.update(sql_list_condition(f.scope)[1])
    if f.developers:  # applied to the `developer` column of `sel` even when `deny_all` empties the other filters
        params["developers"] = list(f.developers)
    if f.deny_all:
        return params
    if f.users:
        params["users"] = [u.lower() for u in f.users]
    if f.projects and not f.project_unattributed:
        params["projects"] = list(f.projects)
    if f.repositories:
        params["repositories"] = list(f.repositories)
    if f.branch:
        params["branch"] = f.branch
    if f.session_ids is not None:
        params["session_ids"] = list(dict.fromkeys(f.session_ids))
    if f.subscription_types:
        params["subscription_types"] = list(f.subscription_types)
    if f.project_origins:
        params["project_origins"] = list(f.project_origins)
    return params


def _ms(column: str) -> str:
    """Epoch milliseconds, truncated: dateDiff('millisecond', a, b) is _ms(b) - _ms(a)."""
    return f"floor(extract(epoch FROM {column}) * 1000)::bigint"


def _model_sums(requests: str, weight: str = "1", extra_join: str = "") -> str:
    """Token sums per price name (the CTE `request_routes`, else the record's model) of the session's API requests
    `r` matching `requests`, each multiplied by `weight` (1 by default; a fallback dispatch's precomputed share of
    a request it splits with overlapping siblings otherwise, read through `extra_join`'s join of
    `request_shares`), as a JSON array of objects with the column names of get_session_detail_model_costs; NULL
    when none matches."""
    return f"""(SELECT jsonb_agg(m ORDER BY m.model_name {_C}) FROM (
        SELECT coalesce({request_route("r.tableoid::text || r.ctid::text")}, r.model, '') AS model_name,
               sum(coalesce(r.input_tokens, 0)::numeric * ({weight})) AS input_tokens,
               sum(coalesce(r.output_tokens, 0)::numeric * ({weight})) AS output_tokens,
               sum(coalesce(r.cache_read_tokens, 0)::numeric * ({weight})) AS cache_read_tokens,
               sum(coalesce(r.cache_creation_tokens, 0)::numeric * ({weight})) AS cache_creation_tokens,
               sum({_attr_ms("r", "cache_creation_5m_tokens")}::numeric * ({weight})) AS cache_creation_5m_tokens,
               sum({_attr_ms("r", "cache_creation_1h_tokens")}::numeric * ({weight})) AS cache_creation_1h_tokens
        FROM log_events r
        {extra_join}
        WHERE r.session_id = $sid::text AND r.event_kind = {_API_REQUEST} AND {requests}
        GROUP BY 1) m)"""


def _ns(column: str) -> str:
    return f"((extract(epoch FROM {column}) * 1000000)::bigint * 1000)"


def _activity_range(alias: str) -> tuple[str, str]:
    """The reported start and end of a session: its hook times, activity columns and OTel rows.

    Hook times are stamped by the client after its own setup work, so an api_request or a tool span
    can precede the first dimension hook or follow the last one. `alias` exposes session_id,
    started_at, activity_started_at, last_event_at and ended_at. Such rows are looked for only within
    a day of the column bounds, which prunes the other weekly partitions at run time; without column
    bounds (no session_dims row, or no stored end yet) every row of the session counts. Only the end looks for real
    activity alone: a startup record or a built-in command (`/exit`) after the stored end is a reopen that did nothing,
    not work.
    """
    start = f"least({alias}.started_at, {alias}.activity_started_at)"
    end = f"greatest({alias}.last_event_at, {alias}.ended_at)"
    earlier = (
        f" AND {{a}}.ts >= coalesce({start} - interval '1 day', '-infinity')"
        f" AND {{a}}.ts < coalesce({start}, 'infinity')"
    )
    later = (
        f" AND {{a}}.ts > coalesce({end}, '-infinity')"
        f" AND {{a}}.ts <= coalesce({end} + interval '1 day', 'infinity')"
    )

    def probes(agg: str, bound: str, real_work_only: bool) -> str:
        return ", ".join(
            f"(SELECT {agg}({a}.ts) FROM {table} {a} "
            f"WHERE {a}.session_id = {alias}.session_id{bound.format(a=a)}"
            f"{real_work_filter(table, a) if real_work_only else ''})"
            for table, a in (("log_events", "le"), ("spans", "sp"))
        )

    return f"least({start}, {probes('min', earlier, False)})", f"greatest({end}, {probes('max', later, True)})"


# A request's second connection is only taken if the pool has one free this soon: waiting longer, while holding the
# first, is how requests that each hold one connection and wait for another would deadlock a nearly exhausted pool.
SECOND_CONNECTION_TIMEOUT_S = 0.5


class _ConnectionPair:
    """The slice of the engine the reader uses over the connections one request holds: `acquire()` hands out a
    free one and takes it back, so at most `len(connections)` statements run at once. `widen()` adds a second
    connection from the engine for a block."""

    def __init__(self, engine: Any, connections: list[Any]) -> None:
        self._engine = engine
        self._free: asyncio.Queue[Any] = asyncio.Queue()
        for conn in connections:
            self._free.put_nowait(conn)

    @contextlib.asynccontextmanager
    async def acquire(self, timeout: float | None = None) -> AsyncIterator[Any]:
        conn = await self._free.get()
        try:
            yield conn
        finally:
            self._free.put_nowait(conn)

    @contextlib.asynccontextmanager
    async def widen(self) -> AsyncIterator[None]:
        """A second connection for the block, if the pool has one free within `SECOND_CONNECTION_TIMEOUT_S`; the block
        runs on the one connection otherwise. (The timeout is swallowed around the acquire only, never around the
        block, so a statement's own timeout is not hidden.) The second connection returns to the pool when the block
        ends, which is after every statement of the block has finished."""
        async with contextlib.AsyncExitStack() as stack:
            second = None
            with contextlib.suppress(TimeoutError):
                second = await stack.enter_async_context(self._engine.acquire(timeout=SECOND_CONNECTION_TIMEOUT_S))
            if second is not None:
                self._free.put_nowait(second)
            try:
                yield
            finally:
                if second is not None:
                    self._withdraw(second)

    def _withdraw(self, conn: Any) -> None:
        kept = []
        while not self._free.empty():
            item = self._free.get_nowait()
            if item is not conn:
                kept.append(item)
        for item in kept:
            self._free.put_nowait(item)


class PostgresCliAnalyticsReader:
    """Read-only PostgreSQL access for the Local Analytics endpoints."""

    def __init__(self, engine: AnalyticsPgEngine, s_cap: int = DEFAULT_S_CAP) -> None:
        self._engine = engine
        self._s_cap = s_cap  # sessions of S beyond which a stored read runs today's statements
        self._rates_version: Callable[[], str] | None = None
        # The request's `sel` kept in a TEMP TABLE of the one held connection (see `materialize_sel`): its name and the
        # key of the filter it was built for. None: every statement builds the `sel` CTE itself.
        self._sel_temp: tuple[str, tuple[Any, ...], tuple[str, ...] | None] | None = None
        self._files_temp: str | None = None  # TEMP table of the request's gated changed-file counts (prototype)

    def set_rates(self, current_version: Callable[[], str] | None) -> None:
        """Keep the current-rates-version callable (the service layer's catalog): the stored cost key of a session
        is read only while its stamp equals what this returns."""
        self._rates_version = current_version

    @contextlib.asynccontextmanager
    async def bounded(self) -> AsyncIterator[PostgresCliAnalyticsReader]:
        """A reader whose statements run on the one pool connection this request holds (statements one after another).

        The connection is waited for like any other. A request takes a second one only around statements that can use
        it (`widened`), so a long single-connection statement never keeps an idle connection from ingest. Both return
        to the pool when their blocks end, so the caller keeps Python work (pricing, row building) outside the block.
        """
        async with self._engine.acquire() as first:
            scoped = PostgresCliAnalyticsReader(_ConnectionPair(self._engine, [first]), s_cap=self._s_cap)
            scoped.set_rates(self._rates_version)
            try:
                yield scoped
            finally:
                # A temp table outlives the pool's reset, so it is dropped before the connection goes back; a
                # connection that cannot drop it (cancelled or broken) is closed instead of being reused.
                await scoped._drop_sel(first)  # noqa: SLF001 - the scoped reader is this method's own

    @contextlib.asynccontextmanager
    async def widened(self) -> AsyncIterator[PostgresCliAnalyticsReader]:
        """This bounded reader with a second connection for the block (at most two statements at once). Waits for
        the second only for `SECOND_CONNECTION_TIMEOUT_S`, so two requests cannot each hold one and wait for another:
        a request that finds the pool nearly exhausted runs the block on the one connection it has. A reader that is
        not bounded is returned as it is."""
        widen = getattr(self._engine, "widen", None)
        if widen is None or self._sel_temp is not None:  # a temp table is visible to its own connection only
            yield self
            return
        async with widen():
            yield self

    @staticmethod
    def _sel_key(f: LocalAnalyticsFilter) -> tuple[Any, ...]:
        """What fixes the rows of `sel` apart from the developers filter: the window, the viewer scope and the other
        filters (their bound values and the scope condition)."""
        params = {k: v for k, v in window_params(f).items() if k != "developers"}
        return (sql_list_condition(f.scope)[0], f.deny_all, f.project_unattributed, sorted(params.items(), key=str))

    async def materialize_sel(self, f: LocalAnalyticsFilter, cohort: str | None = None) -> None:
        """Builds `sel` of `f` once into a TEMP TABLE of the held connection (with the `developer` column and the
        stored flag columns, ANALYZEd), so every later statement of this request that asks for the same selection
        reads the table instead of evaluating the admission arms again. A statement whose filter differs from `f`
        (other ids, other filters) keeps building its own CTE; a developers filter that is a subset of `f`'s is
        applied to the table. Only on a bounded reader (one connection, so the table is visible to every statement);
        `bounded()` drops it before the connection goes back to the pool."""
        if self._sel_temp is not None or not isinstance(self._engine, _ConnectionPair):
            return
        name = f"request_sel_{uuid.uuid4().hex}"
        cte = self._sessions_cte(f, with_developer=True, extra_columns=FLAG_COLUMNS)
        # The user rollup splits the window's sessions by where they started: `started` keeps those that started in the
        # window (what a start-day row holds), `before` those admitted because they were active in it.
        where = {
            None: "",
            "started": " WHERE started_at >= $start_dt::timestamptz",
            "before": " WHERE started_at < $start_dt::timestamptz",
        }[cohort]
        text, args = positional(f"CREATE TEMP TABLE {name} AS WITH {cte} SELECT * FROM sel{where}", window_params(f))
        async with self._engine.acquire() as conn:
            self._sel_temp = (name, self._sel_key(f), tuple(f.developers) if f.developers else None)
            try:
                async with conn.transaction(isolation="repeatable_read"):
                    await conn.execute(text, *args)
                await conn.execute(f"ANALYZE {name}")  # noqa: S608 - a name made here from a UUID
            except TimeoutError as exc:  # the client command timeout, as `_records` reports it
                raise StatementTimeout(_CLIENT_TIMEOUT) from exc

    async def materialize_files(self, f: LocalAnalyticsFilter) -> None:
        """The files_changed of the sessions with tool facts (the `file_t` gate), once, in a TEMP table of the held
        connection; Overview's money and facts read it instead of each building `file_l`. Needs `materialize_sel`."""
        if self._sel_temp is None or self._files_temp is not None:
            return
        name = f"request_files_{uuid.uuid4().hex}"
        text, args = positional(
            f"CREATE TEMP TABLE {name} AS WITH {self._sel_cte(f)}, {_files_ctes()} "
            "SELECT l.session_id, l.files_changed, l.files_written, l.files_edited FROM file_l l "
            "WHERE l.session_id IN (SELECT session_id FROM file_t)",
            window_params(f),
        )
        async with self._engine.acquire() as conn:
            self._files_temp = name
            try:
                async with conn.transaction(isolation="repeatable_read"):
                    await conn.execute(text, *args)
                await conn.execute(f"ANALYZE {name}")  # noqa: S608 - a name made here from a UUID
            except TimeoutError as exc:  # the client command timeout, as `_records` reports it
                raise StatementTimeout(_CLIENT_TIMEOUT) from exc

    async def _priced_q(
        self, f: LocalAnalyticsFilter, sql: str, params: dict[str, Any], *, exact: bool = False
    ) -> Rows:
        """`_q` for a statement over `priced_groups_cte()`: reads the stored groups when `_model_groups_mode` allows
        it (one repeatable-read snapshot with the mode), the live build otherwise."""
        async with self._engine.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
            mode = await self._model_groups_mode(conn, f)
            if mode.stored:
                live = priced_groups_cte()
                assert live in sql
                sql, params = sql.replace(live, priced_groups_cte(stored=True)), {**params, "stale": list(mode.stale)}
            return await self._fetch(conn, sql, params, exact=exact)

    async def _drop_sel(self, conn: Any) -> None:
        temp, self._sel_temp = self._sel_temp, None
        files, self._files_temp = self._files_temp, None
        names = [name for name in (files, temp and temp[0]) if name]
        if not names:
            return
        try:
            await conn.execute("DROP TABLE IF EXISTS " + ", ".join(f"pg_temp.{name}" for name in names))
        except Exception:  # a failed drop must not replace the request's own error (a timeout is a 504)
            with contextlib.suppress(Exception):
                conn.terminate()  # the pool then discards it; a reused connection must not carry the table
            logger.warning("cli_analytics: request temp table not dropped; the connection was closed")
        except BaseException:  # cancelled while dropping: a failing terminate must not replace the CancelledError
            with contextlib.suppress(Exception):
                conn.terminate()
            raise

    @staticmethod
    async def _records(conn: Any, text: str, args: list[Any]) -> list[Any]:
        """The records of a statement; one that outruns the client command timeout is a `StatementTimeout` (a pool or
        connect timeout never reaches here, so it stays a plain TimeoutError)."""
        try:
            return await conn.fetch(text, *args)
        except TimeoutError as exc:
            raise StatementTimeout(_CLIENT_TIMEOUT) from exc

    async def _q(self, sql: str, params: dict[str, Any], *, exact: bool = False) -> Rows:
        """`exact` keeps a `Decimal` as it is instead of the float of `normalise`, for a caller that adds it up."""
        text, args = positional(sql, params)
        async with self._engine.acquire() as conn:
            records = await self._records(conn, text, args)
        return _rows(records, exact)

    # ── shared session selection ─────────────────────────────────────────────

    @staticmethod
    def _filter_conditions(f: LocalAnalyticsFilter) -> list[str]:
        """The request filters over the projected `x`, in a fixed order (the viewer scope is added before them)."""
        conditions: list[str] = []
        if f.users:
            conditions.append("lower(user_email) = ANY($users::text[])")
        if f.project_unattributed:
            conditions.append("project_name = ''")
        elif f.projects:
            conditions.append("project_name = ANY($projects::text[])")
        if f.repositories:
            conditions.append("repository = ANY($repositories::text[])")
        if f.branch:
            conditions.append("branch = $branch::text")
        if f.session_ids is not None:
            conditions.append("session_id = ANY($session_ids::text[])")
        if f.subscription_types:
            conditions.append(f"{sql_subscription_type('x.subscription_type')} = ANY($subscription_types::text[])")
        if f.project_origins:
            conditions.append(f"{sql_project_origin('x.project_origin')} = ANY($project_origins::text[])")
        return conditions

    def _sel_cte(
        self,
        f: LocalAnalyticsFilter,
        lean: bool = False,
        searchable: bool = False,
        ids_only: bool = False,
        with_start: bool = False,
        split_arms: bool = False,
        with_keys: tuple[str, ...] = (),
        with_developer: bool = False,
    ) -> str:
        """`_sessions_cte`, or, once `materialize_sel` ran for the same selection, the same columns read from the
        request's temp table. Rows are the same: the table is the full form with the developer column."""
        temp = self._sel_temp
        if temp is not None and not split_arms and self._reads_temp(f, temp):
            if f.developers and not with_developer:
                raise ValueError("the developers filter needs with_developer=True")
            if not (ids_only and (f.developers or not set(with_keys) <= set(FLAG_COLUMNS))):
                return self._temp_sel_cte(temp[0], f, lean, searchable, ids_only, with_start, with_keys, with_developer)
        return self._sessions_cte(f, lean, searchable, ids_only, with_start, split_arms, with_keys, with_developer)

    def _reads_temp(self, f: LocalAnalyticsFilter, temp: tuple[str, tuple[Any, ...], tuple[str, ...] | None]) -> bool:
        _, key, developers = temp
        if self._sel_key(f) != key:
            return False
        return developers is None or (f.developers is not None and set(f.developers) <= set(developers))

    @staticmethod
    def _temp_sel_cte(
        table: str,
        f: LocalAnalyticsFilter,
        lean: bool,
        searchable: bool,
        ids_only: bool,
        with_start: bool,
        with_keys: tuple[str, ...],
        with_developer: bool,
    ) -> str:
        if ids_only:
            columns = ["session_id"] + (["started_at"] if with_start else []) + list(with_keys)
        else:
            columns = ["session_id", "repository", "branch", "repo_remote", "project_name", "user_email", "started_at"]
            if not lean:
                columns += ["last_event_at", "activity_started_at", "ended_at"]
            if searchable or not lean:
                columns += ["first_prompt", "session_title", "entrypoint"]
            columns += ["owner_email", "subscription_type", "project_origin", "route_note"]
            if with_developer:
                columns.append("developer")
        where = "\n            WHERE developer = ANY($developers::text[])" if f.developers and not ids_only else ""
        return f"""
        sel AS (SELECT {", ".join(columns)} FROM pg_temp.{table}{where})"""

    @staticmethod
    def _sessions_cte(
        f: LocalAnalyticsFilter,
        lean: bool = False,
        searchable: bool = False,
        ids_only: bool = False,
        with_start: bool = False,
        split_arms: bool = False,
        with_keys: tuple[str, ...] = (),
        with_developer: bool = False,
        extra_columns: tuple[str, ...] = (),
        repository_search: bool = False,
        page_repositories: bool = False,
    ) -> str:
        """Every session's dimensions (dimensions plus the session's email).

        Sessions admitted by `_SESSION_ADMITTED_IN_WINDOW`: started in the window and with at least
        one activity signal on or after the window's first day (`_activity_from_window_start`), or with activity in
        the window per `_active_in_window`. A session that started in the window but has never produced a single
        prompt, tool call, cost/usage or line-count row, or whose every such row lies before the window (a later
        reopen stamped its start into it), is excluded entirely, not merely flagged dead.
        This is the only place the window is decided. Strings are never NULL in the result, so missing
        values become '' for the filters.
        `lean` leaves out the columns only the detail statements and the search read (prompt, title, activity and
        end times); `searchable` keeps the prompt and the title, the two long searched columns.
        A window-wide statement that needs just the ids, the filters and the start reads far less heap.
        `ids_only` outputs the session id alone (the viewer scope is applied inside the scan): for the unfiltered shared
        list nothing else reads `sel`, and the planner then serves the other arms from their indexes. Arm 1 reads
        `last_activity_day`, which the covering index `session_dims_active_shared_cov` does not hold, so it is a
        Bitmap Heap Scan on `session_dims_active_started`, not an Index Only Scan.
        Callers use it only when `_narrows_window` is false and nothing reads the other columns.
        `with_start` adds `started_at` to the ids-only output (every arm index keys it, so no extra column is read)
        for the statements that order by the start or bound the invocations by it (the framework filter).
        `with_keys` (ids-only form) adds those stored columns of `session_dims` (the INCLUDE list of
        `session_dims_fw_win`) that the statement reads, so it does not join `session_dims` again.
        `with_developer` (full form) adds `developer text`, the session's `_DEVELOPER`, and applies `f.developers` to
        it; `f.developers` without it raises ValueError. `repository_search` (with `with_developer`) keeps only the
        sessions whose repository matches `$search` (ILIKE), so the search narrows the universe every statement reads;
        `page_repositories` keeps those of `$page_repositories` (and, with `$page_unattributed`, those with none).
        """
        if f.developers and not with_developer:
            raise ValueError("the developers filter needs with_developer=True")
        searched = """
                       coalesce(d.first_prompt, '') AS first_prompt,
                       coalesce(d.session_title, '') AS session_title,
                       coalesce(d.entrypoint, '') AS entrypoint,"""
        activity = """
                       d.last_event_at,
                       d.activity_started_at,
                       d.ended_at,"""
        if ids_only:
            # The scope is applied under the alias `x` inside the scan, so `sel` outputs the id alone.
            # with_keys: stored columns INCLUDEd by `session_dims_fw_win`: the scan stays index-only
            columns = ("x.session_id, x.started_at" if with_start else "x.session_id") + "".join(
                f", x.{column}" for column in with_keys
            )
            return _sel_ctes(_admitted_arms("x", columns, sql_list_condition(f.scope)[0]), star=False)
        wide = ("" if lean else activity) + (searched if searchable or not lean else "")
        # The viewer scope is always the first condition: nothing appended later can displace it.
        conditions = [sql_list_condition(f.scope)[0]]
        conditions.extend(["false"] if f.deny_all else PostgresCliAnalyticsReader._filter_conditions(f))
        where = "\n            WHERE " + _AND.join(conditions)  # the viewer scope is always there
        columns = f"""d.session_id,
                       coalesce(nullif(d.repo_remote, ''), nullif(d.repository, ''), '') AS repository,
                       coalesce(d.branch, '')       AS branch,
                       coalesce(d.repo_remote, '')  AS repo_remote,
                       coalesce(d.project_name, '') AS project_name,
                       {_SESSION_USER_EMAIL} AS user_email,
                       d.started_at,{wide}
                       d.owner_email,
                       d.subscription_type,
                       d.project_origin,
                       d.route_note{"".join(f", d.{column}" for column in extra_columns)}"""
        if with_developer:
            narrowing = _developer_narrowing(f, repository_search, page_repositories)
            by_developer = ("\n            WHERE " + _AND.join(narrowing)) if narrowing else ""
            return _sel_ctes(_admitted_arms("d", columns), where, developer_where=by_developer)
        return _sel_ctes(_admitted_arms("d", columns), where, split=split_arms)

    @staticmethod
    def _activity_cte() -> str:
        """The reported start, end and duration of every session in `sel` (see `_activity_range`)."""
        start, end = _activity_range("s")
        return f"""
        act AS (
            SELECT r.session_id, r.session_start, r.session_end,
                   {_ms("r.session_end")} - {_ms("r.session_start")} AS duration_ms
            FROM (SELECT s.session_id, {start} AS session_start, {end} AS session_end FROM sel s) r
        )"""

    @staticmethod
    def _scope(f: LocalAnalyticsFilter, column: str = "session_id") -> str:
        """Restrict a fact query to the sessions in `sel` (started in the window, passing the filters)."""
        return f"\n              AND {column} IN (SELECT session_id FROM sel)"

    @staticmethod
    def _normalized_model_name(column: str) -> str:
        """The SQL spelling of the canonical model id; see `normalized_model_name`."""
        return normalized_model_name(column)

    # ── cost / token facts (cost_daily) ──────────────────────────────────────

    async def get_class_lists(self, f: LocalAnalyticsFilter, by: str = "all") -> Rows:
        """The distinct normalised subscription types / project origins of the selected sessions, per entity.

        Read-only side query next to the sum queries (never joined into them, so no total can multiply). `by`:
        `all` (one row, key ''), `model` (normalised model name), `day` (lines_daily day), `tool` (tool name),
        `invocation` (kind and name, as get_invocations), `session` (session id).
        Rows are `key`, `kind`, `subscription_types`,
        `project_origins`; the lists are never NULL."""
        classes = _CLASS_LISTS
        name = self._normalized_model_name("d.model_name")
        sources = {
            "all": f"SELECT ''::text AS key, '' AS kind, {classes} FROM sel s",
            "session": f"SELECT s.session_id AS key, '' AS kind, {classes} FROM sel s GROUP BY s.session_id",
            "model": f"SELECT {name} AS key, '' AS kind, {classes} FROM cost_daily d "
            f"JOIN sel s ON s.session_id = d.session_id GROUP BY {name} HAVING {name} <> ''",
            "day": f"SELECT l.day::text AS key, '' AS kind, {classes} FROM lines_daily l "
            f"JOIN sel s ON s.session_id = l.session_id WHERE {_LINES_NONZERO} GROUP BY 1",
            "tool": f"SELECT i.name AS key, '' AS kind, {classes} FROM invocations_hourly i "
            "JOIN sel s ON s.session_id = i.session_id WHERE i.kind = 1 GROUP BY 1",
            "invocation": f"""SELECT u.name AS key,
                   CASE u.kind WHEN 2 THEN 'skill' WHEN 3 THEN 'agent' ELSE 'command' END AS kind, {classes}
            FROM (
                SELECT kind, name, session_id FROM skill_rows
                UNION ALL
                SELECT kind, name, session_id FROM invocations_hourly
                WHERE kind = 4 AND {_no_summary("invocations_hourly.session_id")}
                  AND name <> ALL($builtin_commands::text[])
                  AND {_not_a_skill_of_session("invocations_hourly.session_id", "invocations_hourly.name")}
                UNION ALL
                SELECT 4, e.cmd #>> '{{}}', session_dims.session_id
                FROM session_dims, jsonb_array_elements(session_dims.commands) AS e(cmd)
                WHERE session_dims.summary_ts IS NOT NULL AND jsonb_typeof(e.cmd) = 'string'
                  AND e.cmd #>> '{{}}' <> '' AND (e.cmd #>> '{{}}') <> ALL($builtin_commands::text[])
                  AND {_not_a_skill_of_session("session_dims.session_id", "(e.cmd #>> '{}')")}
            ) u JOIN sel s ON s.session_id = u.session_id GROUP BY 1, 2""",
        }
        params = window_params(f)
        if by == "invocation":
            params["builtin_commands"] = list(BUILTIN_COMMAND_NAMES)
        skill_rows = (
            """, skill_rows AS MATERIALIZED (SELECT kind, name, session_id FROM invocations_hourly
                WHERE kind IN (2, 3) AND session_id IN (SELECT session_id FROM sel))"""
            if by == "invocation"
            else ""
        )
        return await self._q(f"WITH {self._sel_cte(f)}{skill_rows} {sources[by]}", params)

    async def get_cost_kpis(self, f: LocalAnalyticsFilter) -> Rows:
        """Every session of `sel` counts toward `total_sessions`, priced or not: the same
        costless-inclusive universe the Sessions list already uses via `get_session_cost_facts(...,
        include_costless=True)`. A costless session has no `cost_daily` row, so it contributes 0 to every
        token sum (NULL rows are ignored by `sum`), never NULL to the row as a whole. `total_cost_usd` is
        the one exception: it stays NULL, not 0, for a window with no cost row, so "no spend data" (NULL)
        stays distinguishable from "confirmed $0 spend". `spend_sessions` and `total_cost_usd` cover every
        session with a cost row, the Personal-paid ones too (EPMCDME-17260).

        The cost rows are read plain and scoped by a join to `sel`: nothing here needs `_cost_rows`'s per-row window
        columns (grain calls, session email), and its `IN (SELECT session_id FROM sel)` form had the planner probe
        every partition's index once per session."""
        return await self._q(
            f"""
        WITH {self._sel_cte(f)}
        SELECT count(DISTINCT s.session_id)                              AS total_sessions,
               count(DISTINCT c.session_id)                              AS spend_sessions,
               sum(c.cost_usd)                                           AS total_cost_usd,
               coalesce(sum(c.input_tokens), 0)::bigint          AS total_input_tokens,
               coalesce(sum(c.output_tokens), 0)::bigint         AS total_output_tokens,
               coalesce(sum(c.cache_read_tokens), 0)::bigint     AS total_cache_read_tokens,
               coalesce(sum(c.cache_creation_tokens), 0)::bigint AS total_cache_creation_tokens,
               coalesce(sum(c.api_call_count), 0)::bigint        AS total_api_calls
        FROM sel s
             LEFT JOIN (SELECT c.* FROM cost_daily c JOIN sel x ON x.session_id = c.session_id) c
                    ON c.session_id = s.session_id""",
            window_params(f),
        )

    async def get_dashboard_kpis(self, f: LocalAnalyticsFilter) -> Rows:
        """`get_cost_kpis` for Overview and Efficiency: the same one row (`total_sessions` over every selected session,
        priced or not; `spend_sessions`, `total_cost_usd` and the token and call sums over the token rows), read as a
        direct aggregate of `cost_daily` joined to `sel`. The sums need neither the window columns of `_cost_rows` (the
        calls of the original grain, the session's row email) nor the left join from `sel` (3 times the cost)."""
        return await self._q(
            f"""
        WITH {self._sel_cte(f)}
        SELECT (SELECT count(DISTINCT session_id) FROM sel)                   AS total_sessions,
               count(DISTINCT d.session_id)                                   AS spend_sessions,
               sum(d.cost_usd)                                                AS total_cost_usd,
               coalesce(sum(d.input_tokens), 0)::bigint          AS total_input_tokens,
               coalesce(sum(d.output_tokens), 0)::bigint         AS total_output_tokens,
               coalesce(sum(d.cache_read_tokens), 0)::bigint     AS total_cache_read_tokens,
               coalesce(sum(d.cache_creation_tokens), 0)::bigint AS total_cache_creation_tokens,
               coalesce(sum(d.api_call_count), 0)::bigint        AS total_api_calls
        FROM cost_daily d JOIN sel s ON s.session_id = d.session_id""",
            window_params(f),
        )

    async def get_model_breakdown(self, f: LocalAnalyticsFilter) -> Rows:
        model_name = self._normalized_model_name("d.model_name")
        return await self._q(
            f"""
        WITH {self._sel_cte(f)}
        SELECT {model_name}                                   AS model_name,
               count(DISTINCT d.session_id)                   AS session_count,
               sum(d.cost_usd)                                AS cost_usd,
               sum(d.cache_read_tokens)::bigint               AS cache_read_tokens,
               sum(d.input_tokens + d.output_tokens + d.cache_read_tokens + d.cache_creation_tokens)::bigint
                                                              AS total_tokens
        FROM cost_daily d
        WHERE true{self._scope(f, "d.session_id")}
        GROUP BY {model_name}
        HAVING {model_name} <> ''
        ORDER BY session_count DESC, cost_usd DESC, {model_name} {_C}""",
            window_params(f),
        )

    async def get_session_model_costs(self, f: LocalAnalyticsFilter) -> Rows:
        """Token sums per (session, raw model), the empty model kept, with the normalised model name: the rows
        every money value is priced from, every selected session's (the Personal-paid ones too, EPMCDME-17260). The
        5m / 1h sums are NULL when no row of the group has a split."""
        return await self._q(
            f"WITH {self._sel_cte(f)} SELECT g.* FROM {model_token_groups()}",
            window_params(f),
        )

    async def get_window_model_flags(self, f: LocalAnalyticsFilter) -> list[tuple[str, tuple[bool, ...]]]:
        """Per raw model of the filtered window, which priced token kinds (`kind_tokens` order) were used: the
        input of `CatalogPricer.record_unpriced_from_flags` and the model list the cost sort resolves rates for."""
        # The flags read only the window's session ids: an unfiltered window is an Index Only Scan of the cover index.
        ids_only = f.session_ids is None and not self._narrows_window(f)
        params = window_params(f)
        async with self._engine.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
            # The kind masks do not depend on the rates: every stamped session is fresh, S is the unstamped ones.
            mode = await self._stored_mode(conn, f, "model_flags")
            # Stored: the masks ride in the index-only scan of `sel`, `session_dims` is not joined.
            keys = FLAG_COLUMNS if (mode.stored and ids_only) else ()
            sel = self._sel_cte(f, lean=True, ids_only=ids_only, with_keys=keys)
            if mode.stored:
                params["stale"] = list(mode.stale)
                merged = merged_kind_flags_sql(model_flags_sql(live=True), keyed=bool(keys))
                sql = f"WITH {sel},{LIVE_CTE} SELECT * FROM {merged} ORDER BY model_name {_C}"
            else:
                sql = f"WITH {sel} SELECT * FROM {model_flags_sql()} ORDER BY model_name {_C}"
            rows = await self._fetch(conn, sql, params)
        return [(r["model_name"], tuple(bool(r[f"used_{c}"]) for c in _RATE_COLUMNS)) for r in rows]

    async def get_session_funding_costs(self, f: LocalAnalyticsFilter) -> Rows:
        """The `get_session_model_costs` rows of every selected session (no spend filter), each with the session's
        client-asserted `funding_route` (NULL reads 'unknown'), `personal_billed` (the billing class) and normalised
        `subscription_type`: the rows the funding and personal-billed estimate buckets are priced from. Same `sel`,
        so the same read scope."""
        model_name = self._normalized_model_name("c.model_name")
        return await self._q(
            f"""
        WITH {self._sel_cte(f)}
        SELECT c.session_id,
               c.model_name                                 AS model_name,
               {model_name}                                 AS normalized_model_name,
               coalesce(d.funding_route, 'unknown') AS funding_route,
               {sql_personal_billed("s")} AS personal_billed,
               {_SUB} AS subscription_type,
               sum(c.input_tokens)::bigint                  AS input_tokens,
               sum(c.output_tokens)::bigint                 AS output_tokens,
               sum(c.cache_read_tokens)::bigint             AS cache_read_tokens,
               sum(c.cache_creation_tokens)::bigint         AS cache_creation_tokens,
               sum(c.cache_creation_5m_tokens)::bigint      AS cache_creation_5m_tokens,
               sum(c.cache_creation_1h_tokens)::bigint      AS cache_creation_1h_tokens
        FROM {_token_rows(*_SEL_SESSIONS)}
        JOIN sel s ON s.session_id = c.session_id
        LEFT JOIN session_dims d ON d.session_id = c.session_id
        GROUP BY c.session_id, c.model_name, d.funding_route, s.subscription_type""",
            window_params(f),
        )

    async def get_cost_money(
        self, f: LocalAnalyticsFilter, rates: Sequence[ModelRates], buckets: bool = True
    ) -> GroupedMoney:
        """The money of the /cost page from one pricing pass over the window: the spend total, per user (the sessions
        with a `cost_daily` row only, as `get_cost_by_user` lists them) and per model, plus (with `buckets`) the
        funding-route and Personal-billed estimate buckets of the window, per user (`cost_daily` sessions) and per
        model. Without them the six bucket groupings are not built."""
        groupings = _COST_GROUPINGS if buckets else _COST_SPEND_GROUPINGS
        rows = await self._money_rows(
            f,
            rates,
            lambda priced: f"WITH {self._sel_cte(f, with_developer=True)}, {priced} "
            f"SELECT gc.* FROM {grouped_cost_sql(groupings)}",
        )
        return grouped_money(rows, groupings)

    async def _money_rows(self, f: LocalAnalyticsFilter, rates: Sequence[ModelRates], statement: Any) -> Rows:
        """Runs a money statement over `priced_groups` on one connection, in one repeatable-read transaction: the
        stored model groups are read when `_model_groups_mode` allows it (`statement` gets the CTE text, `live` and
        `priced_groups` with `$stale` bound to S) and the statement then runs in the snapshot S was read in;
        otherwise the all-live CTE."""
        params = {**window_params(f), **rates_params(rates)}
        async with self._engine.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
            mode = await self._model_groups_mode(conn, f)
            if mode.stored:
                params["stale"] = list(mode.stale)
            return await self._fetch(conn, statement(priced_groups_cte(stored=mode.stored)), params)

    async def _model_groups_mode(self, conn: Any, f: LocalAnalyticsFilter) -> ModelGroupsMode:
        """`model_groups_mode` for a request: any error reading the switch or S (in a savepoint, so the request's
        transaction stays usable) is a rate-limited WARNING and the all-live form."""
        try:
            async with conn.transaction():
                return await model_groups_mode(conn, f.session_ids, self._s_cap)
        except Exception as exc:
            FALLBACK_WARNINGS.warn_groups(GROUPS_READ_ERROR, f" not read: {exc!r}")
            return ModelGroupsMode(False, GROUPS_READ_ERROR)

    async def get_users_money_page(
        self, f: LocalAnalyticsFilter, rates: Sequence[ModelRates], limit: int | None, offset: int
    ) -> UsersMoneyPage:
        """The ranking and the money of the Users page in one statement. The ranking is the distinct developers of
        `sel` (every session counts, priced or not) LEFT JOIN their spend, ordered by the cost rounded to 9 places and
        then by name in code-point order; LIMIT/OFFSET apply to it. The window groupings are returned whole, the
        per-user ones only for the page's developers. `total_count` is the distinct developers of `sel`, a column
        outside LIMIT/OFFSET, so a page beyond the end still reports it."""
        groupings = _USERS_GROUPINGS
        page = f"LIMIT {int(limit)} OFFSET {int(offset)}" if limit is not None else ""
        rows = await self._money_rows(
            f,
            rates,
            lambda priced: f"""
        WITH {self._sel_cte(f, with_developer=True)}, {priced},
        money AS MATERIALIZED (SELECT gc.* FROM {grouped_cost_sql(groupings)}),
        ranked AS (
            SELECT d.developer,
                   row_number() OVER (ORDER BY round(coalesce(u.cost_usd, 0), 9) DESC, d.developer {_C}) AS pos
            FROM (SELECT DISTINCT developer FROM sel) d
                 LEFT JOIN money u ON u."grouping" = '{G_USER}' AND u."user" = d.developer
            ORDER BY pos
            {page}
        )
        SELECT (SELECT count(DISTINCT developer) FROM sel) AS total_count, x.*
        FROM (SELECT 1) one LEFT JOIN (
            SELECT r.developer AS page_developer, r.pos, m.*
            FROM ranked r FULL JOIN money m ON m."user" = r.developer
            WHERE r.developer IS NOT NULL OR m."user" IS NULL
        ) x ON true""",
        )
        ranked = sorted({(r["pos"], r["page_developer"]) for r in rows if r["page_developer"] is not None})
        total = max((int(r["total_count"]) for r in rows if r["total_count"] is not None), default=0)
        return UsersMoneyPage([name for _, name in ranked], total, grouped_money(rows, groupings))

    async def rollup_users(
        self, f: LocalAnalyticsFilter, rate_of: Callable[[str], ModelRates], max_age_s: float
    ) -> RollupUsers | None:
        """See the port. Never raises for a missing table: a read that fails answers None, so the live path runs."""
        from codemie.repository.cli_analytics.postgres import user_rollup  # the module imports this one

        version = self._rates_version() if self._rates_version is not None else None
        try:
            return await user_rollup.serve(self, f, rate_of, version, max_age_s=max_age_s)
        except asyncpg.QueryCanceledError:
            raise  # as a StatementTimeout or a pool timeout: the live statements would only be slower still
        except (asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            logger.warning(f"cli_analytics: user rollup not read, the live statements run instead: {exc!r}")
            return None

    async def rollup_overview(
        self, f: LocalAnalyticsFilter, rate_of: Callable[[str], ModelRates], max_age_s: float
    ) -> RollupOverview | None:
        """See the port. Never raises for a missing table: a read that fails answers None, so the live path runs."""
        from codemie.repository.cli_analytics.postgres import user_rollup  # the module imports this one

        version = self._rates_version() if self._rates_version is not None else None
        try:
            return await user_rollup.serve_overview(self, f, rate_of, version, max_age_s=max_age_s)
        except asyncpg.QueryCanceledError:
            raise  # as a StatementTimeout or a pool timeout: the live statements would only be slower still
        except (asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            logger.warning(f"cli_analytics: user rollup not read, the live statements run instead: {exc!r}")
            return None

    async def rollup_cost(
        self, f: LocalAnalyticsFilter, rate_of: Callable[[str], ModelRates], max_age_s: float
    ) -> RollupCost | None:
        """See the port. Never raises for a missing table: a read that fails answers None, so the live path runs."""
        from codemie.repository.cli_analytics.postgres import user_rollup  # the module imports this one

        version = self._rates_version() if self._rates_version is not None else None
        try:
            return await user_rollup.serve_cost(self, f, rate_of, version, max_age_s=max_age_s)
        except asyncpg.QueryCanceledError:
            raise  # as a StatementTimeout or a pool timeout: the live statements would only be slower still
        except (asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            logger.warning(f"cli_analytics: user rollup not read, the live statements run instead: {exc!r}")
            return None

    async def get_developer_session_facts(self, f: LocalAnalyticsFilter) -> Rows:
        """Turns, tool calls, tool successes and the mean session duration per developer, over every session of `sel`
        (`f.developers` keeps all of a developer's sessions, so a fallback-email session is counted). Same figures as
        `get_turns_by_session`, `get_tool_success_by_session` and `get_session_durations`, summed per developer: a
        missing value is 0, a session with an unknown duration (NULL) is left out of the mean, a real 0 is kept."""
        return await self._q(
            f"""
        WITH {self._sel_cte(f, with_developer=True)},{self._activity_cte()},
        t AS (
            SELECT x.session_id, sum(x.turns)::bigint AS turns
            FROM turns_daily x JOIN sel ON sel.session_id = x.session_id
            GROUP BY x.session_id
        ),
        o AS (
            SELECT x.session_id, sum(x.calls)::bigint AS tool_calls, sum(x.success)::bigint AS tool_calls_success
            FROM invocations_hourly x JOIN sel ON sel.session_id = x.session_id
            WHERE x.kind = 1 AND x.session_id <> ''
            GROUP BY x.session_id
        )
        SELECT s.developer,
               coalesce(sum(greatest(nullif(t.turns, 0), {_SUMMARY_TURNS})), 0)::bigint AS turns,
               coalesce(sum(o.tool_calls), 0)::bigint                                  AS tool_calls,
               coalesce(sum(o.tool_calls_success), 0)::bigint                          AS tool_successes,
               trunc(avg(act.duration_ms))::bigint                                     AS avg_session_duration_ms,
               count(act.duration_ms)::bigint                                          AS duration_sessions
        FROM sel s
             JOIN session_dims d ON d.session_id = s.session_id
             LEFT JOIN t ON t.session_id = s.session_id
             LEFT JOIN o ON o.session_id = s.session_id
             LEFT JOIN act ON act.session_id = s.session_id
        GROUP BY s.developer""",
            window_params(f),
        )

    async def get_avg_session_duration_ms(self, f: LocalAnalyticsFilter) -> int | None:
        """The mean session duration over every session of `sel`, truncated to whole milliseconds like
        `int(sum / len)`. A session with an unknown duration (NULL) is left out, a real 0 is kept; None when no
        session has a known duration. Window-wide: the Users page reports it whatever page it shows."""
        rows = await self._q(
            f"""
        WITH {self._sel_cte(f)},{self._activity_cte()}
        SELECT trunc(avg(duration_ms))::bigint AS avg_session_duration_ms
        FROM act""",
            window_params(f),
        )
        value = rows[0]["avg_session_duration_ms"] if rows else None
        return None if value is None else int(value)

    async def get_cost_by_user(self, f: LocalAnalyticsFilter, with_classes: bool = True) -> Rows:
        """Per developer of the sessions with at least one `cost_daily` row: the class lists (unless not `with_classes`,
        then the names only). One row per session is grouped by `sel`'s `developer` (the session's `_DEVELOPER`, read
        from the request's temp table), instead of evaluating `_DEVELOPER`'s subqueries and the window columns of
        `_cost_rows` once per cost row."""
        classes = f",\n               {_CLASS_LISTS}" if with_classes else ""
        return await self._q(
            f"""
        WITH {self._sel_cte(f, with_developer=True)}
        SELECT s.developer AS developer_name{classes}
        FROM sel s
        WHERE s.session_id IN (SELECT c.session_id FROM cost_daily c JOIN sel x ON x.session_id = c.session_id)
        GROUP BY s.developer
        ORDER BY s.developer {_C}""",
            window_params(f),
        )

    async def get_users(self, f: LocalAnalyticsFilter) -> Rows:
        """Every session of `sel` counts toward a developer's `session_count`, priced or not, matching the Sessions
        list's costless-inclusive universe. No session ids, no money and no ordering: the page's money and ranking
        come from `get_users_money_page`."""
        return await self._q(
            f"""
        WITH {self._sel_cte(f, with_developer=True)}
        SELECT s.developer                              AS developer_name,
               count(DISTINCT s.session_id)             AS session_count,
               coalesce(sum(c.input_tokens), 0)::bigint              AS input_tokens,
               coalesce(sum(c.output_tokens), 0)::bigint             AS output_tokens,
               coalesce(sum(c.cache_read_tokens), 0)::bigint         AS cache_read_tokens,
               coalesce(sum(c.cache_creation_tokens), 0)::bigint     AS cache_creation_tokens,
               {_TOP_MODEL}                             AS top_model,
               {_CLASS_LISTS}
        FROM {_cost_rows("", joined=True)}
             RIGHT JOIN sel s ON s.session_id = c.session_id
        GROUP BY 1""",
            window_params(f),
        )

    async def get_users_daily_activity(self, f: LocalAnalyticsFilter) -> Rows:
        """Every session of `sel` counts toward a developer's daily activity, priced or not:
        the day bucket comes from any of the five activity tables, not just `cost_daily`, so a costless
        session still appears on the day it was active."""
        days = " UNION ".join(
            f"SELECT x.session_id, x.day FROM {t} x JOIN sel ON sel.session_id = x.session_id" for t in _ACTIVITY_TABLES
        )
        return await self._q(
            f"""
        WITH {self._sel_cte(f, with_developer=True)}
        SELECT s.developer AS developer_name, act.day AS day, count(DISTINCT act.session_id) AS session_count,
               {_CLASS_LISTS}
        FROM ({days}) act
             JOIN sel s ON s.session_id = act.session_id
        GROUP BY 1, 2
        ORDER BY s.developer {_C}, act.day""",
            window_params(f),
        )

    async def get_users_last_active(self, f: LocalAnalyticsFilter) -> Rows:
        """Latest real-work hook event (`ACTIVITY_HOOK_TYPES`; not a lifecycle hook) in the window per user.

        `session_dims.last_event_at` is already the latest such event, so raw hook events
        are read only for sessions that were active again after the window end.
        """
        params = window_params(f)
        return await self._q(
            f"""
        WITH {self._sel_cte(f, with_developer=True)},
        e AS (
            -- one primary-key lookup per session: a join let the planner hash every row of session_dims
            SELECT s.developer, s.session_id,
                   (SELECT d.last_event_at FROM session_dims d WHERE d.session_id = s.session_id) AS last_event_at
            FROM sel s
        ),
        w AS (
            SELECT e.developer AS developer_name,
                   CASE WHEN e.last_event_at <= $end_dt::timestamptz THEN e.last_event_at
                        ELSE (SELECT max(h.ts) FROM hook_events h
                              WHERE h.session_id = e.session_id AND h.event_type = ANY({_ACTIVITY_HOOKS})
                                AND h.{_TS})
                   END AS last_in_window
            FROM e
        )
        SELECT developer_name, max(last_in_window) AS last_active
        FROM w
        WHERE last_in_window IS NOT NULL
        GROUP BY 1""",
            params,
        )

    # ── line facts (lines_daily) ─────────────────────────────────────────────

    async def get_lines_totals(self, f: LocalAnalyticsFilter) -> Rows:
        """Summed lines of the selected sessions; NULL sums when none of them has a lines_daily row (unknown)."""
        return await self._q(
            f"""
        WITH {self._sel_cte(f)}
        SELECT sum(lines_added)::bigint AS lines_added, sum(lines_removed)::bigint AS lines_removed
        FROM lines_daily
        WHERE true{self._scope(f)}""",
            window_params(f),
        )

    async def get_lines_daily(self, f: LocalAnalyticsFilter) -> Rows:
        """Net lines per day with a lines_daily row; `nonzero` tells whether any row of the day reports lines."""
        return await self._q(
            f"""
        WITH {self._sel_cte(f)}
        SELECT day, (sum(lines_added) - sum(lines_removed))::bigint AS net_lines,
               bool_or({_LINES_NONZERO}) AS nonzero
        FROM lines_daily
        WHERE true{self._scope(f)}
        GROUP BY day
        ORDER BY day""",
            window_params(f),
        )

    async def get_lines_by_user(self, f: LocalAnalyticsFilter) -> Rows:
        """Net lines per developer; a developer without any lines_daily row has no row. With `f.developers` the rows
        are restricted on the very value that names the developer here (`_session_developer`)."""
        developer = _session_developer("l.session_email")
        only = f"\n        WHERE {developer} = ANY($developers::text[])" if f.developers else ""
        return await self._q(
            f"""
        WITH {self._sel_cte(f, with_developer=bool(f.developers))}
        SELECT {developer} AS developer_name,
               (sum(l.lines_added) - sum(l.lines_removed))::bigint AS net_lines
        FROM (SELECT l.*, {_session_email("l", reporting_lines_only=True)} FROM lines_daily l
              JOIN sel x ON x.session_id = l.session_id) l
             JOIN sel s ON s.session_id = l.session_id{only}
        GROUP BY 1""",
            window_params(f),
        )

    async def get_lines_by_session(self, f: LocalAnalyticsFilter) -> Rows:
        """One row per selected session that has a lines_daily row on ANY day (lines are known; a zero-sum row
        reports 0). The window is decided by `sel` only, so the sums cover all days of the selected sessions. A
        session with no row at all is absent (unknown)."""
        return await self._q(
            f"""
        WITH {self._sel_cte(f)}
        SELECT session_id, sum(lines_added)::bigint AS lines_added, sum(lines_removed)::bigint AS lines_removed
        FROM lines_daily
        WHERE true{self._scope(f)}
        GROUP BY session_id""",
            window_params(f),
        )

    # ── trace facts: turns, tool calls, files ────────────────────────────────

    async def get_turns_by_session(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sel_cte(f)},
        {_turns_cte()}
        SELECT sel.session_id,
               -- observed turns prove activity, not completeness: a 0 is served only when the summary reports it
               {_TURNS_VALUE} AS turns,
               {_SUMMARY_STATE} AS summary_state, {_SUMMARY_METRICS} AS summary_metrics
        FROM {_TURNS_FROM}""",
            window_params(f),
        )

    async def get_file_facts_by_session(self, f: LocalAnalyticsFilter) -> Rows:
        """Distinct files across the session's days, counted in two hash steps (not count(DISTINCT))."""
        return await self._q(
            f"""
        WITH {self._sel_cte(f)},
        {_files_ctes()}
        SELECT file_t.session_id,
               coalesce(file_l.files_changed, 0) AS files_changed,
               coalesce(file_l.files_written, 0) AS files_written,
               coalesce(file_l.files_edited, 0)  AS files_edited,
               file_t.tool_calls
        FROM file_t LEFT JOIN file_l ON file_l.session_id = file_t.session_id""",
            window_params(f),
        )

    async def get_tool_success_by_session(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sel_cte(f)},
        {_tools_cte()}
        SELECT sel.session_id, coalesce(tool_o.tool_calls, 0)::bigint AS tool_calls,
               coalesce(tool_o.tool_calls_success, 0)::bigint AS tool_calls_success,
               CASE WHEN {_SUMMARY_TOOL_CALLS} IS NOT NULL
                    THEN greatest(coalesce(tool_o.tool_calls, 0), {_SUMMARY_TOOL_CALLS}) END::bigint AS tool_calls_total
        FROM sel JOIN session_dims d ON d.session_id = sel.session_id
             LEFT JOIN tool_o ON tool_o.session_id = sel.session_id
        WHERE tool_o.session_id IS NOT NULL OR {_SUMMARY_TOOL_CALLS} IS NOT NULL""",
            window_params(f),
        )

    async def get_tool_usage(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sel_cte(f)}
        SELECT name AS tool_name, sum(calls)::bigint AS call_count, sum(success)::bigint AS success_count
        FROM invocations_hourly
        WHERE kind = 1{self._scope(f)}
        GROUP BY name
        ORDER BY call_count DESC, name {_C}
        LIMIT 20""",
            window_params(f),
        )

    async def get_invocations(self, f: LocalAnalyticsFilter) -> Rows:
        """Skills and subagent types from the hourly rollup; slash commands from one source per session:
        the summary's `commands` (every non-empty string element counts 1) or, for a session without a
        summary, its OTel user prompts from the rollup. A command name the same session also ran as a skill (kind 2)
        is counted once, under Skills. The window is the one of `sel`, as everywhere."""
        params = window_params(f)
        params["builtin_commands"] = list(BUILTIN_COMMAND_NAMES)
        return await self._q(
            f"""
        WITH {self._sel_cte(f)},
        skill_rows AS MATERIALIZED (SELECT kind, name, session_id, calls AS c FROM invocations_hourly
            WHERE kind IN (2, 3) AND session_id IN (SELECT session_id FROM sel))
        SELECT CASE kind WHEN 2 THEN 'skill' WHEN 3 THEN 'agent' ELSE 'command' END AS kind,
               name,
               sum(c)::bigint AS count
        FROM (
            SELECT kind, name, c FROM skill_rows
            UNION ALL
            SELECT kind, name, calls AS c FROM invocations_hourly
            WHERE
              kind = 4
              AND {_no_summary("invocations_hourly.session_id")}
              AND name <> ALL($builtin_commands::text[])
              AND {_not_a_skill_of_session("invocations_hourly.session_id", "invocations_hourly.name")}{self._scope(f)}
            UNION ALL
            SELECT 4, e.cmd #>> '{{}}', 1 FROM session_dims, jsonb_array_elements(session_dims.commands) AS e(cmd)
            WHERE session_dims.summary_ts IS NOT NULL AND jsonb_typeof(e.cmd) = 'string'
              AND e.cmd #>> '{{}}' <> '' AND (e.cmd #>> '{{}}') <> ALL($builtin_commands::text[])
              AND {_not_a_skill_of_session("session_dims.session_id", "(e.cmd #>> '{}')")}{self._scope(f)}
        ) u
        GROUP BY 1, 2""",
            params,
        )

    async def get_skill_names_by_session(self, session_ids: list[str], *, scope: Any) -> Rows:
        """One row per known session id, `{"session_id", "skill_names"}`, for any number of sessions: the sorted
        distinct names the delivery framework is derived from (`CLASSIFIER_NAMES_SQL`): the session's `session_skills`,
        the hook-dispatch names (`invocations_hourly` kind 2), the keys of the `skills` and `agents` objects of
        `session_dims` and the string elements of its `commands` array. A session without such a name has an empty
        list; an id with no `session_dims` row has no row."""
        if not session_ids:
            return []
        gate, gate_params = detail_gate(scope, "sd.session_id")
        return await self._q(
            f"""
        SELECT sd.session_id, coalesce({CLASSIFIER_NAMES_SQL}, ARRAY[]::text[]) AS skill_names
        FROM session_dims sd
        WHERE sd.session_id = ANY($session_ids::text[]) AND {gate}
        ORDER BY sd.session_id {_C}""",
            {"session_ids": list(dict.fromkeys(session_ids)), **gate_params},
        )

    # ── session-level aggregates ─────────────────────────────────────────────

    async def get_session_durations(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sel_cte(f)},{self._activity_cte()}
        SELECT session_id, duration_ms
        FROM act""",
            window_params(f),
        )

    async def get_overview_facts(self, f: LocalAnalyticsFilter) -> Rows:
        """The Overview's non-money totals over the selected sessions, as one row of `part` `facts`: `turns`,
        `tool_calls`, `tool_calls_success`, `duration_ms` and `files_changed`, each the sum of the per-session
        statement it replaces. A window with no session sums to 0."""
        files = "" if self._files_temp else f",\n        {_files_ctes()}"
        files_changed = (
            f"(SELECT coalesce(sum(files_changed), 0) FROM pg_temp.{self._files_temp})"
            if self._files_temp
            else "(SELECT coalesce(sum(files_changed), 0) FROM file_l "
            "WHERE session_id IN (SELECT session_id FROM file_t))"
        )
        return await self._q(
            f"""
        WITH {self._sel_cte(f)},{self._activity_cte()},
        {_turns_cte()},
        {_tools_cte()}{files}
        SELECT {_part(
            "facts",
            turns=f"(SELECT coalesce(sum({_TURNS_VALUE}), 0) FROM {_TURNS_FROM})",
            tool_calls="(SELECT coalesce(sum(tool_calls), 0) FROM tool_o)",
            tool_calls_success="(SELECT coalesce(sum(tool_calls_success), 0) FROM tool_o)",
            duration_ms="(SELECT coalesce(sum(duration_ms), 0) FROM act)",
            files_changed=files_changed)}""",
            window_params(f),
        )

    async def get_overview_models(self, f: LocalAnalyticsFilter) -> Rows:
        """The Overview's models in one read of the token rows, as rows of `part` `model`: the normalised model
        (`key`, never blank), the sessions that used it (`n`), their cost from `cost_daily` (`cost_usd`, every
        session's, Personal-paid included; the tie-break of the order, an exact `Decimal`: the user rollup stores and
        adds it) and the sorted-distinct class lists of those sessions. In the order of `get_model_breakdown` (sessions,
        then cost, then name) and over the same sessions; the classes are those of `get_class_lists(f, "model")`. The
        token rows join `sel` rather than filter by `IN`. The session classes are computed once per session, and the
        normalisation once per distinct raw name (`names`, materialised: otherwise the planner evaluates it for every
        token row). The raw names are those of the selected sessions' rows (`tok`, the one read of `cost_daily`), not of
        the whole table."""
        name = self._normalized_model_name("x.raw")
        return await self._q(
            f"""
        WITH {self._sel_cte(f)},
        tok AS MATERIALIZED (
            SELECT c.session_id, c.model_name AS raw, sum(c.cost_usd) AS cost_usd,
                   {_SUB} AS sub, {_ORIGIN} AS origin
            FROM cost_daily c JOIN sel s ON s.session_id = c.session_id
            GROUP BY c.session_id, c.model_name, s.subscription_type, s.project_origin
        ),
        names AS MATERIALIZED (SELECT raw, {name} AS nm FROM (SELECT DISTINCT raw FROM tok) x)
        SELECT {_part(
            "model", key="n.nm", n="count(DISTINCT d.session_id)", subscription_types="array_agg(DISTINCT d.sub)",
            project_origins="array_agg(DISTINCT d.origin)", cost_usd="sum(d.cost_usd)")}
        FROM tok d JOIN names n ON n.raw = d.raw
        WHERE n.nm <> ''
        GROUP BY n.nm
        ORDER BY count(DISTINCT d.session_id) DESC, sum(d.cost_usd) DESC, n.nm {_C}""",
            window_params(f),
            exact=True,
        )

    async def get_overview_lines(self, f: LocalAnalyticsFilter) -> Rows:
        """The Overview's lines in one read of `lines_daily`, as rows of `part`: `total` (`lines_added`, `lines_removed`
        summed over the selected sessions; NULL while none has a row, as `get_lines_totals`) and one `day` per day with
        a row (`key` the day, `net_lines`, its `lines_added` and `lines_removed`, `nonzero` whether a row of the day
        reports lines, and the class lists of the sessions whose rows do, as `get_lines_daily` and
        `get_class_lists(f, "day")`). Unordered."""
        return await self._q(
            f"""
        WITH {self._sel_cte(f)},
        per_day AS (
            SELECT l.day, sum(l.lines_added) AS added, sum(l.lines_removed) AS removed,
                   bool_or({_LINES_NONZERO}) AS nonzero,
                   array_agg(DISTINCT {_SUB}) FILTER (WHERE {_LINES_NONZERO}) AS subs,
                   array_agg(DISTINCT {_ORIGIN}) FILTER (WHERE {_LINES_NONZERO}) AS origins
            FROM lines_daily l JOIN sel s ON s.session_id = l.session_id
            GROUP BY l.day
        )
        SELECT {_part("total", lines_added="sum(added)", lines_removed="sum(removed)")} FROM per_day
        UNION ALL
        SELECT {_part(
            "day", key="day::text", net_lines="added - removed", nonzero="nonzero", subscription_types="subs",
            project_origins="origins", lines_added="added", lines_removed="removed")} FROM per_day""",
            window_params(f),
        )

    async def _dashboard_money(
        self,
        f: LocalAnalyticsFilter,
        rates: Sequence[ModelRates],
        classes: bool,
        exact: bool = False,
        groupings: Sequence[Grouping] = _TOTAL_GROUPINGS,
    ) -> Rows:
        """The money of the selected sessions, priced once at `rates` by `priced_groups_cte`, as rows of `part`: `money`
        (`spend`, `route`, `billed`, and with `groupings` more: the rows of `grouped_cost_sql`, `key` is the grouping)
        and `dead` (`n` sessions that cost money and changed no file, `cost_usd` their money, and with `classes` their
        class lists)."""
        class_lists = (
            {
                "subscription_types": f"array_agg(DISTINCT coalesce(nullif({_SUB}, ''), 'unknown'))",
                "project_origins": f"array_agg(DISTINCT coalesce(nullif({_ORIGIN}, ''), 'none'))",
            }
            if classes
            else {}
        )
        changed = (
            f"changed AS MATERIALIZED (SELECT session_id FROM pg_temp.{self._files_temp} WHERE files_changed > 0)"
            if self._files_temp
            else f"""{_files_ctes()},
        changed AS MATERIALIZED (
            SELECT session_id FROM file_l WHERE files_changed > 0 AND session_id IN (SELECT session_id FROM file_t)
        )"""
        )
        return await self._priced_q(
            f,
            f"""
        WITH {self._sel_cte(f, with_developer=True)},
        {changed},
        {priced_groups_cte()},
        {_SESSION_COST}
        SELECT {_gc_part("money", groupings, "gc")} FROM {grouped_cost_sql(groupings)}
        UNION ALL
        SELECT {_part("dead", n="count(*)", cost_usd="sum(c.cost)", **class_lists)}
        FROM scost c JOIN sel s ON s.session_id = c.session_id
        LEFT JOIN changed ch ON ch.session_id = c.session_id
        WHERE c.cost > 0 AND ch.session_id IS NULL""",
            {**window_params(f), **rates_params(rates)},
            exact=exact,
        )

    async def get_overview_money(
        self, f: LocalAnalyticsFilter, rates: Sequence[ModelRates] = (), *, exact: bool = False
    ) -> Rows:
        """The Overview's money, see `_dashboard_money`, with the cost of every subscription class (`class` rows).
        `exact` keeps the money as `Decimal` rather than the float of `normalise`: the user rollup stores and adds it
        over days."""
        return await self._dashboard_money(f, rates, classes=False, exact=exact, groupings=_OVERVIEW_GROUPINGS)

    async def get_efficiency_money(self, f: LocalAnalyticsFilter, rates: Sequence[ModelRates] = ()) -> Rows:
        """The Efficiency's money and dead sessions, see `_dashboard_money`."""
        return await self._dashboard_money(f, rates, classes=True)

    async def get_repository_ranking(
        self,
        f: LocalAnalyticsFilter,
        include_branches: bool = False,
        search: str | None = None,
        rates: Sequence[ModelRates] = (),
        page: int = 0,
        per_page: int | None = None,
    ) -> Rows:
        """The Repositories groups of the sessions that `search` keeps (ILIKE on the repository, applied to the
        selection before anything is priced or paged), priced, ranked and paged, as rows of `part`:

        - `group`: a repository (project_name is its most frequent project, ties to the alphabetically first) or, with
          `include_branches`, a (project, repository, branch). Empty names are NULL. `n` is its session count and
          `cost_usd` its spend; `key` is the rank by session count, then spend, then name. Without `include_branches`
          only the page is returned (`page` of `per_page`; every group when `per_page` is None).
        - `count`: `n` groups before paging.
        - `total`: `spend`, `route` and `billed` over the whole selection (before paging), as in Overview.
        - `bucket`: `b_route` / `b_billed` rows, the funding and personal-billed estimates of the returned groups,
          by repository (with `include_branches`: by project and repository, the level the branches roll up to).

        The counts of the groups (turns, files, lines, tool calls, classes) are `get_repository_facts`."""
        params = {**window_params(f), **rates_params(rates)}
        if search:
            params["search"] = f"%{search.strip().lower()}%"
        if include_branches:
            group_keys, bucket_keys = ["project", "repository", "branch"], ["project", "repository"]
            groups = """SELECT nullif(project_name, '') AS project_name, nullif(repository, '') AS repository,
                   nullif(branch, '') AS branch, count(*) AS session_count FROM sel GROUP BY 1, 2, 3"""
            money_key = (
                "m.project = coalesce(g.project_name, '') AND m.repository = coalesce(g.repository, '')"
                " AND m.branch = coalesce(g.branch, '')"
            )
            paging = ""
        else:
            group_keys = bucket_keys = ["repository"]
            groups = """SELECT top_project.project AS project_name, g.*
            FROM (SELECT nullif(repository, '') AS repository, NULL::text AS branch, count(*) AS session_count
                  FROM sel GROUP BY 1) g
                 LEFT JOIN top_project ON top_project.repository IS NOT DISTINCT FROM g.repository"""
            money_key = "m.repository = coalesce(g.repository, '')"
            paging = ""
            if per_page is not None:
                paging = "WHERE rn > $offset::bigint AND rn <= $offset::bigint + $limit::bigint"
                params["offset"], params["limit"] = page * per_page, per_page
        groupings = [*_TOTAL_GROUPINGS, Grouping("g_spend", group_keys)]
        if include_branches:  # the branches roll up to (project, repository): their estimates come with the groups
            groupings += [
                Grouping("b_route", [*bucket_keys, "funding_route"]),
                Grouping("b_billed", [*bucket_keys, "personal_billed"]),
            ]
        return await self._priced_q(
            f,
            f"""
        WITH {self._sessions_cte(f, with_developer=True, repository_search=bool(search))},
        {priced_groups_cte()},
        project_counts AS (
            SELECT nullif(repository, '') AS repository, nullif(project_name, '') AS project, count(*) AS n
            FROM sel WHERE nullif(project_name, '') IS NOT NULL GROUP BY 1, 2
        ),
        top_project AS (
            SELECT DISTINCT ON (repository) repository, project FROM project_counts
            ORDER BY repository, n DESC, project {_C}
        ),
        grp AS ({groups}),
        money AS (SELECT * FROM {grouped_cost_sql(groupings)}),
        ranked AS (
            SELECT g.*, coalesce(m.cost_usd, 0) AS cost,
                   row_number() OVER (ORDER BY g.session_count DESC, coalesce(m.cost_usd, 0) DESC,
                                      coalesce(g.repository, '') {_C}, coalesce(g.project_name, '') {_C}) AS rn
            FROM grp g LEFT JOIN money m ON m."grouping" = 'g_spend' AND {money_key}
        ),
        page AS (SELECT * FROM ranked {paging})
        SELECT {_part(
            "group", key="rn::text", n="session_count", project_name="project_name", repository="repository",
            branch="branch", cost_usd="cost")} FROM page
        UNION ALL
        SELECT {_part("count", n="count(*)")} FROM grp
        UNION ALL
        SELECT {_gc_part("total", groupings)} FROM money m WHERE m."grouping" IN ('spend', 'route', 'billed')
        UNION ALL
        SELECT {_gc_part("bucket", groupings)} FROM money m WHERE m."grouping" IN ('b_route', 'b_billed')""",
            params,
        )

    async def get_repository_buckets(
        self,
        f: LocalAnalyticsFilter,
        search: str | None = None,
        rates: Sequence[ModelRates] = (),
        repositories: Sequence[str] = (),
        unattributed: bool = False,
    ) -> Rows:
        """The funding-route and personal-billed estimates of the repositories named (`unattributed`: also the sessions
        with none), priced from their sessions only, as rows of `part` `bucket` (`b_route` / `b_billed`, by
        repository). `get_repository_ranking` returns the page; this prices the page, not the whole selection."""
        groupings = [
            Grouping("b_route", ["repository", "funding_route"]),
            Grouping("b_billed", ["repository", "personal_billed"]),
        ]
        params = {**window_params(f), **rates_params(rates)}
        params["page_repositories"], params["page_unattributed"] = list(repositories), unattributed
        if search:
            params["search"] = f"%{search.strip().lower()}%"
        sel = self._sessions_cte(f, with_developer=True, repository_search=bool(search), page_repositories=True)
        return await self._priced_q(
            f,
            f"""
        WITH {sel},
        {priced_groups_cte()}
        SELECT {_gc_part("bucket", groupings, "gc")} FROM {grouped_cost_sql(groupings)}""",
            params,
        )

    async def get_repository_facts(
        self,
        f: LocalAnalyticsFilter,
        include_branches: bool = False,
        search: str | None = None,
        repositories: Sequence[str] = (),
        unattributed: bool = False,
    ) -> Rows:
        """The non-money facts of the Repositories groups, as rows of `part` `group` (same groups and names as
        `get_repository_ranking`): `n` sessions, `turns`, `files_changed`, `lines_added` / `lines_removed` (NULL while
        no session reports lines), `tool_calls`, `tool_calls_success` and the sorted-distinct class lists. Without
        `include_branches` only the sessions of the repositories named (`unattributed`: also those with none) are read,
        so a page costs a page; with it every group is read."""
        params = window_params(f)
        if search:
            params["search"] = f"%{search.strip().lower()}%"
        name = "sel"
        narrow = ""
        if not include_branches:
            name = "selp"
            params["page_repositories"], params["page_unattributed"] = list(repositories), unattributed
            narrow = """,
        selp AS (SELECT * FROM sel WHERE nullif(repository, '') = ANY($page_repositories::text[])
                 OR ($page_unattributed::boolean AND nullif(repository, '') IS NULL))"""
        keys = "project, repository, branch" if include_branches else "repository"
        project, branch = ("project", "branch") if include_branches else ("NULL", "NULL")
        return await self._q(
            f"""
        WITH {self._sessions_cte(f, with_developer=True, repository_search=bool(search))}{narrow},
        {_turns_cte(name)},
        {_tools_cte(name)},
        {_files_ctes(name)},
        {_lines_cte(name)},
        ps AS (
            SELECT nullif(s.repository, '') AS repository, nullif(s.branch, '') AS branch,
                   nullif(s.project_name, '') AS project, {_SESSION_FACT_COLUMNS}, {_TOOL_FACT_COLUMNS}
            FROM {_SESSION_FACTS_FROM.replace("sel s", f"{name} s", 1)}
            {_TOOL_FACTS_FROM}
        )
        SELECT {_part(
            "group", n="count(*)", turns="sum(turns)", files_changed="sum(files_changed)",
            lines_added="sum(lines_added)", lines_removed="sum(lines_removed)", tool_calls="sum(tool_calls)",
            tool_calls_success="sum(tool_calls_success)", subscription_types="array_agg(DISTINCT sub)",
            project_origins="array_agg(DISTINCT origin)", project_name=project,
            repository="repository", branch=branch)}
        FROM ps GROUP BY {keys}""",
            params,
        )

    async def get_efficiency_facts(self, f: LocalAnalyticsFilter) -> Rows:
        """The Efficiency's non-money facts over the selected sessions, as rows of `part`: `worst` (the session with the
        most cache-read tokens per API call, ties to the lowest id, with the columns of its prompt, read for that one
        session only, so `sel` carries no prompt or title), `depth` (one row
        per session-depth bucket in use, as `key`, with its `n` and class lists), `all` (the class lists of every
        selected session) and `code` (the file and line sums and the class lists of the sessions that changed a file
        or a line). Reads the request's file-count table when `materialize_files` built one."""
        files, facts_from = f"{_files_ctes()},\n        ", _SESSION_FACTS_FROM
        if self._files_temp:
            files = ""
            facts_from = facts_from.replace(
                _FILE_JOINS, f"LEFT JOIN pg_temp.{self._files_temp} file_l ON file_l.session_id = s.session_id"
            )
        return await self._q(
            f"""
        WITH {self._sel_cte(f, lean=True)},
        {_turns_cte()},
        {files}{_lines_cte()},
        facts AS (
            SELECT {_SESSION_FACT_COLUMNS}, coalesce(lines_s.lines_added <> 0 OR lines_s.lines_removed <> 0, false)
                   AS lines_changed
            FROM {facts_from}
        ),
        ps AS (
            SELECT facts.*,
                   CASE WHEN turns <= 1 THEN '1' WHEN turns <= 5 THEN '2-5' WHEN turns <= 10 THEN '6-10'
                        WHEN turns <= 25 THEN '11-25' WHEN turns <= 50 THEN '26-50' ELSE '50+' END AS depth
            FROM facts
        ),
        calls AS (
            SELECT d.session_id, sum(d.cache_read_tokens) AS cache_read, sum(d.api_call_count)::bigint AS calls
            FROM cost_daily d JOIN sel s ON s.session_id = d.session_id GROUP BY d.session_id
        ),
        pick AS (
            SELECT s.session_id, s.repository, s.branch,
                   (coalesce(calls.cache_read, 0) + coalesce(t.cache_read_tokens, 0))::float8 / calls.calls AS ratio
            FROM sel s JOIN calls ON calls.session_id = s.session_id
                 LEFT JOIN {_totals_join()} ON t.session_id = s.session_id
            WHERE calls.calls > 0
            ORDER BY ratio DESC, s.session_id {_C}
            LIMIT 1
        ),
        worst AS (
            SELECT p.session_id, coalesce(d.session_title, '') AS session_title,
                   coalesce(d.entrypoint, '') AS entrypoint,
                   coalesce(d.first_prompt, '') AS first_prompt, p.repository, p.branch, p.ratio
            FROM pick p LEFT JOIN session_dims d ON d.session_id = p.session_id
        )
        SELECT {_part(
            "worst", ratio="ratio", session_id="session_id", session_title="session_title",
            entrypoint="entrypoint", prompt="first_prompt", repository="repository", branch="branch")} FROM worst
        UNION ALL
        SELECT {_part(
            "depth", key="depth", n="count(*)", subscription_types="array_agg(DISTINCT sub)",
            project_origins="array_agg(DISTINCT origin)")} FROM ps GROUP BY depth
        UNION ALL
        SELECT {_part(
            "all", n="count(*)", subscription_types="array_agg(DISTINCT sub)",
            project_origins="array_agg(DISTINCT origin)")} FROM ps
        UNION ALL
        SELECT {_part(
            "code", n="count(*)", files_changed="sum(files_changed)", files_written="sum(files_written)",
            files_edited="sum(files_edited)", lines_added="sum(lines_added)", lines_removed="sum(lines_removed)",
            subscription_types="array_agg(DISTINCT sub) FILTER (WHERE files_changed > 0 OR lines_changed)",
            project_origins="array_agg(DISTINCT origin) FILTER (WHERE files_changed > 0 OR lines_changed)")} FROM ps""",
            window_params(f),
        )

    async def get_active_ms_by_session(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sel_cte(f)}
        SELECT session_id, (sum(active_ms_user) + sum(active_ms_cli))::bigint AS active_ms
        FROM active_time_daily
        WHERE true{self._scope(f)}
        GROUP BY session_id""",
            window_params(f),
        )

    async def get_session_cost_facts(
        self,
        f: LocalAnalyticsFilter,
        search: str | None = None,
        is_unattributed: bool = False,
        include_costless: bool = False,
    ) -> Rows:
        """Per-session facts. `include_costless` also lists sessions without any `cost_daily` row (their cost
        columns are NULL); the default inner join keeps only sessions with cost rows."""
        having = []
        if search:
            having.append("nullif(max(s.repository), '') ILIKE $search::text ESCAPE '\\'")
        if is_unattributed:
            having.append("nullif(max(s.repository), '') IS NULL")
        having_sql = ("\n        HAVING " + _AND.join(having)) if having else ""
        params = window_params(f)
        if search:
            # `%` and `_` in the term are literal, as in the project and session searches.
            params["search"] = "%" + _escape_like(search.strip().lower()) + "%"
        group_col = session_col = "s.session_id" if include_costless else "c.session_id"
        return await self._q(
            f"""
        WITH {self._sel_cte(f)},{self._activity_cte()}
        SELECT {session_col} AS session_id,
               {_DEVELOPER}                                 AS developer_name,
               max(s.repository)                            AS repository,
               max(s.branch)                                AS branch,
               max(s.project_name)                          AS project_name,
               max({_SUB})                                  AS subscription_type,
               max({_ORIGIN})                               AS project_origin,
               max(nullif(btrim(s.route_note), ''))         AS route_note,
               max(s.first_prompt)                          AS prompt,
               max(s.session_title)                         AS session_title,
               max(s.entrypoint)                             AS entrypoint,
               max(a.session_start)                         AS started_at,
               max(a.session_end)                           AS last_event_at,
               max(a.duration_ms)                           AS duration_ms,
               {_TOP_MODEL}                                 AS model_name,
               sum(c.cost_usd)                              AS cost_usd,
               {_tokens('input_tokens', include_costless)}                  AS input_tokens,
               {_tokens('output_tokens', include_costless)}                 AS output_tokens,
               {_tokens('cache_read_tokens', include_costless)}             AS cache_read_tokens,
               {_tokens('cache_creation_tokens', include_costless)}         AS cache_creation_tokens,
               sum(c.api_call_count)::bigint                AS api_call_count
        FROM {_facts_from(include_costless)}
        GROUP BY {group_col}, 2{having_sql}""",
            params,
        )

    @staticmethod
    def _sort_clause(
        sort_by: str,
        rates: Sequence[ModelRates] | None,
        with_groups: bool,
        params: dict[str, Any],
        stored: bool = False,
        keyed: bool = False,
    ) -> tuple[str, str]:
        """The joins of `filtered` and the SQL of its sort key; the cost key adds its rates to `params`. `stored`
        (ctx_per_call, and cost_usd over the groups of `live`) reads the stored key of `session_dims` and today's key
        for the sessions of `live`. `keyed` (ctx_per_call): `sel` already carries the stored inputs of the key, so
        `session_dims` is not joined again."""
        if sort_by == "start_time":
            return "", "s.started_at"
        # A stored read computes today's key for the sessions of `live` alone (the CTE `live_keys`, a handful of rows);
        # `filtered` only joins that small CTE to `sel`, so nothing groups the token rows against the whole window.
        stored_joins = """JOIN session_dims sd ON sd.session_id = s.session_id
             LEFT JOIN live_keys lk ON lk.session_id = s.session_id
             """
        if keyed:
            stored_joins = """LEFT JOIN live_keys lk ON lk.session_id = s.session_id
             """
        stored_cost = stored_cost_key("s" if keyed else "sd")
        if sort_by == "cost_usd":
            params.update(rates_params(rates or []))
            if stored:
                return stored_joins, f"CASE WHEN lk.session_id IS NULL THEN {stored_cost} ELSE lk.live_key END"
            key = cost_key_from_groups_sql() if with_groups else session_cost_key_sql()
            return f"LEFT JOIN {key} ON k.session_id = s.session_id", "coalesce(k.cost_key, 0::numeric)"
        if stored:
            stored_key = stored_ctx_key("s") if keyed else STORED_CTX_KEY
            return stored_joins, f"CASE WHEN lk.session_id IS NULL THEN {stored_key} ELSE lk.live_key END"
        joins = f"""LEFT JOIN {_CTX_TOKENS.replace("{scope}", "sel")} ON c.session_id = s.session_id
             LEFT JOIN {_totals_join()} ON t.session_id = s.session_id"""
        return joins, _CTX_KEY_FROM_TOKENS

    @staticmethod
    def _live_keys_cte(cost: bool, with_groups: bool) -> str:
        """The CTE `live_keys(session_id, live_key)` of a stored read: today's sort key of the sessions of `live`, from
        their own token rows (ctx_per_call) or from the cost groups of `live` (cost_usd)."""
        if cost:
            return f"""live_keys AS (
            SELECT l.session_id, coalesce(k.cost_key, 0::numeric) AS live_key
            FROM live l LEFT JOIN {cost_key_from_groups_sql()} ON k.session_id = l.session_id
        )"""
        return f"""live_keys AS (
            SELECT l.session_id, {_CTX_KEY_FROM_TOKENS} AS live_key
            FROM live l
                 LEFT JOIN {_CTX_TOKENS.replace("{scope}", "live")} ON c.session_id = l.session_id
                 LEFT JOIN {_totals_join(scope="live")} ON t.session_id = l.session_id
        )"""

    @staticmethod
    def _window_conditions(search: str | None, is_unattributed: bool, params: dict[str, Any]) -> list[str]:
        """The conditions of `filtered` on `sel` (`true` first, so they can always be joined by AND): the search over
        the searchable columns and the unattributed filter. Binds `$search` in `params`."""
        conditions = ["true"]
        if search:
            conditions.append("(" + " OR ".join(_search_arm(c) for c in _SEARCH_COLUMNS) + ")")
            params["search"] = "%" + _escape_like(search.strip().lower()) + "%"
        if is_unattributed:
            conditions.append("s.repository = ''")
        return conditions

    @staticmethod
    def _live_and_group_ctes(cost: bool, stored: bool, with_groups: bool, conditions: list[str]) -> str:
        """The CTEs after `sel`: `live` of a stored read and the cost groups. The ctx page reads only its own key, so
        only the sessions its search keeps are computed live; the cost page also derives the kind flags of the whole
        window from `live`, which then covers all of it (S was counted so)."""
        live_only = "true" if cost else _AND.join(conditions)
        groups = f",{live_cte(live_only)}" if stored else ""
        if with_groups and cost:
            groups += f",\n        {cost_groups_cte(live=stored)}"
        if stored:
            groups += f",\n        {PostgresCliAnalyticsReader._live_keys_cte(cost, with_groups)}"
        return groups

    def _sel_form(
        self,
        f: LocalAnalyticsFilter,
        sort_by: str,
        search: str | None,
        is_unattributed: bool,
        stored: bool,
        with_groups: bool,
        framework: bool,
    ) -> tuple[bool, tuple[str, ...]]:
        """How `sel` is read: `(ids_only, keys)`. The ctx key reads only session ids from `sel`, and so does the stored
        cost page (its key and flags come from the stored columns or from `live`): the unfiltered shared list can
        scan them index-only. Then `keys` are the stored columns the statement reads, which ride in that same scan
        (the INCLUDE list of `session_dims_fw_win`), so `session_dims` is not joined again. A search, the
        unattributed filter, a session-id filter or a narrowed window need the full projection and the join."""
        narrowed = bool(search or is_unattributed or f.session_ids is not None or self._narrows_window(f))
        cost_stored = sort_by == "cost_usd" and stored and with_groups
        ids_only = (sort_by == "ctx_per_call" or framework or cost_stored) and not narrowed
        if not (ids_only and stored):
            return ids_only, ()
        if sort_by == "ctx_per_call":
            return ids_only, CTX_KEY_COLUMNS
        return ids_only, COST_KEY_COLUMNS if cost_stored else ()

    def _ordered_window(
        self,
        f: LocalAnalyticsFilter,
        sort_by: str,
        rates: Sequence[ModelRates] | None,
        search: str | None,
        is_unattributed: bool,
        with_groups: bool = False,
        framework: bool = False,
        split_arms: bool = False,
        stored: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        """The one order builder of the session list: the CTEs `sel` and `filtered(session_id, sort_key)` (the
        sessions of `sel` that pass `search` and `is_unattributed`, with the key of `sort_by`) and their parameters.
        Callers order by `sort_key DESC, session_id COLLATE "C"`. `start_time` is the stored start, `cost_usd` the
        rounded SQL cost key priced with `rates`, `ctx_per_call` the cache-read tokens (totals-only subagent rows
        included) per API call, 0 without calls. `framework` (the statements of the framework filter) reads `sel` as
        the ids and the start alone, like `ctx_per_call`, whatever the sort. `stored` (ctx_per_call, and cost_usd with
        `with_groups`) adds the CTE `live` (`$stale` binds S, `$rates_version` the current version for the cost key)
        and reads the stored key of every other session; the cost groups are then built for `live` only."""
        if sort_by not in SESSION_PAGE_SORTS:
            raise ValueError(f"cannot order sessions by {sort_by!r}; sorts: {SESSION_PAGE_SORTS}")
        if sort_by == "cost_usd" and rates is None:
            raise ValueError("sorting by cost_usd needs the rates of the window models")
        params = window_params(f)
        conditions = self._window_conditions(search, is_unattributed, params)
        cost = sort_by == "cost_usd"
        stored = stored and (sort_by == "ctx_per_call" or (cost and with_groups))
        ids_only, keys = self._sel_form(f, sort_by, search, is_unattributed, stored, with_groups, framework)
        keyed = bool(keys)
        joins, sort_key = self._sort_clause(sort_by, rates, with_groups, params, stored, keyed)
        groups = self._live_and_group_ctes(cost, stored, with_groups, conditions)
        sel = self._sel_cte(
            f,
            lean=True,
            searchable=bool(search),
            ids_only=ids_only,
            with_start=framework,
            split_arms=split_arms,
            with_keys=keys,
        )
        ctes = f"""{sel}{groups},
        filtered AS (
            SELECT s.session_id, {sort_key} AS sort_key
            FROM sel s
                 {joins}
            WHERE {_AND.join(conditions)}
        )"""
        return ctes, params

    async def get_session_page(
        self,
        f: LocalAnalyticsFilter,
        sort_by: str,
        page: int,
        per_page: int,
        search: str | None = None,
        is_unattributed: bool = False,
        rates: Sequence[ModelRates] | None = None,
    ) -> tuple[int, list[str]]:
        """One page of the session list, decided in SQL: `(total, session_ids)` of the sessions of `sel` that
        pass `search` (case-insensitive substring of the first prompt, title, repository or branch, `%`, `_` and
        `\\` matched literally) and `is_unattributed`, ordered by the `sort_by` key DESC then session_id ASC
        (`_ordered_window`; `cost_usd` needs `rates`). `total` counts every such session, not the page."""
        if search and "\x00" in search:
            return 0, []  # PostgreSQL text cannot hold a NUL, so no session matches; the driver would reject it
        if sort_by == "ctx_per_call":
            return await self._ctx_page(f, page, per_page, search, is_unattributed)
        indexed = sort_by == "start_time" and not (search or is_unattributed or self._narrows_window(f))
        ctes, params = self._ordered_window(f, sort_by, rates, search, is_unattributed, split_arms=indexed)
        params["limit"] = per_page
        params["offset"] = min(page * per_page, _MAX_PAGE_OFFSET)
        if sort_by == "start_time":
            if not indexed:
                return await self._searched_start_time_page(ctes, params, page * per_page > 0)
            return await self._start_time_page(ctes, params)
        rows = await self._q(ordered_page_sql(ctes), params)
        return int(rows[0]["total"]), list(rows[0]["session_ids"])

    async def _ctx_page(
        self, f: LocalAnalyticsFilter, page: int, per_page: int, search: str | None, is_unattributed: bool
    ) -> tuple[int, list[str]]:
        """The ctx_per_call page, on one connection: the stored keys of the fresh sessions merged with today's key of
        the sessions of S, or (`_stored_mode` says why) today's statement over the whole window."""
        async with self._engine.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
            mode = await self._stored_mode(conn, f)
            ctes, params = self._ordered_window(f, "ctx_per_call", None, search, is_unattributed, stored=mode.stored)
            if mode.stored:
                params["stale"] = list(mode.stale)
            params["limit"] = per_page
            params["offset"] = min(page * per_page, _MAX_PAGE_OFFSET)
            rows = await self._fetch(conn, ordered_page_sql(ctes), params)
        return int(rows[0]["total"]), list(rows[0]["session_ids"])

    async def _stored_mode(
        self,
        conn: Any,
        f: LocalAnalyticsFilter,
        sort_by: str = "ctx_per_call",
    ) -> StoredMode:
        """Whether this request reads the stored values of `sort_by` (ctx_per_call, cost_usd or model_flags, the kind
        masks): the scope is expressible, the cost key has a current rates version, the control row (read once) says
        on, and S (`_stale_ids`) has at most `s_cap` sessions. Each fallback and each merge of a non-empty S logs a
        rate-limited WARNING with the reason and |S|."""
        if not expressible(f.scope):
            return StoredMode(False, UNEXPRESSIBLE)
        version = None
        if sort_by == "cost_usd":
            version = self._rates_version() if self._rates_version is not None else None
            if not version:
                FALLBACK_WARNINGS.warn(NO_RATES_VERSION, " not read", sort_by)
                return StoredMode(False, NO_RATES_VERSION)
        enabled = await self._fetch(conn, SORT_KEYS_ENABLED_SQL, {})
        if not (enabled and enabled[0].get("enabled")):
            FALLBACK_WARNINGS.warn(DISABLED, " not read", sort_by)
            return StoredMode(False, DISABLED)
        stale = await self._stale_ids(conn, f, version)
        if len(stale) > self._s_cap:
            FALLBACK_WARNINGS.warn(S_OVER_CAP, f">{self._s_cap}", sort_by)
            return StoredMode(False, S_OVER_CAP)
        if stale:
            FALLBACK_WARNINGS.warn(S_MERGED, f"={len(stale)}", sort_by)
        return StoredMode(True, None, tuple(stale), version)

    async def _stale_ids(self, conn: Any, f: LocalAnalyticsFilter, version: str | None) -> list[str]:
        """S: the sessions without a stamp or, with the cost key's `version`, without a stamp of that version, at most
        `s_cap + 1` of them (one more than the cap tells the request to fall back).

        They are read from `session_dims_rates_version` alone (a few ids, none when every session is stamped), not from
        the window: the page statement keeps only the members of S that are in its own `sel` (`live_cte`), so S may
        hold sessions outside the window or the viewer's scope. More than `s_cap` of them in the whole table fall back
        whatever the window holds."""
        params: dict[str, Any] = {"s_limit": self._s_cap + 1}
        if version is not None:
            params["rates_version"] = version
        candidates_sql = UNSTAMPED_CTX_SQL if version is None else UNSTAMPED_COST_SQL
        candidates = [r["session_id"] for r in await self._fetch(conn, candidates_sql, params)]
        if len(candidates) > self._s_cap:
            return candidates
        if f.session_ids is not None:
            asked = set(f.session_ids)
            candidates = [c for c in candidates if c in asked]
        return candidates

    @staticmethod
    def _narrows_window(f: LocalAnalyticsFilter) -> bool:
        """True when a filter or the viewer scope keeps only some of the window's sessions by a column no index
        orders (user, project, repository, branch, class, owner): the index-ordered walk of `_start_time_page` would
        then read the whole window to find a rare (or nonexistent) match, so the page is cut in one pass instead.
        Only the unrestricted shared list, narrowed by nothing, takes the index-ordered path."""
        scope = f.scope
        return bool(
            f.deny_all
            or f.users
            or f.developers
            or f.projects
            or f.project_unattributed
            or f.repositories
            or f.branch
            or f.subscription_types
            or f.project_origins
            or scope.mode != "exclude"
            or not scope.unrestricted
        )

    async def _searched_start_time_page(
        self, ctes: str, params: dict[str, Any], past_first: bool
    ) -> tuple[int, list[str]]:
        """A searched or filtered start_time page in one pass over the window: a leading-wildcard ILIKE (or a filter on
        a column no index orders) cannot use the start_time index, so the rows it must read are read once, counted by
        a window function and cut to the page by a top-N sort.
        A page past the last match returns no row, so the exact total then comes from a count (`past_first`)."""
        rows = await self._q(
            searched_start_time_page_sql(ctes),
            params,
        )
        if rows:
            return int(rows[0]["total"]), [r["session_id"] for r in rows]
        if not past_first:
            return 0, []
        count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
        count_rows = await self._q(window_count_sql(ctes), count_params)
        return int(count_rows[0]["total"]), []

    async def _start_time_page(self, ctes: str, params: dict[str, Any]) -> tuple[int, list[str]]:
        """The start_time page as two statements: `filtered` is read once by each, so it is inlined (not
        materialised) and the page walks `session_dims_started_at` in order, stopping after offset + limit rows,
        instead of sorting the whole window. The count is the only statement that reads the whole window."""
        page_rows = await self._q(
            start_time_page_sql(ctes),
            params,
        )
        count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
        count_rows = await self._q(start_time_count_sql(ctes), count_params)
        return int(count_rows[0]["total"]), list(page_rows[0]["session_ids"])

    async def get_cost_models(self, f: LocalAnalyticsFilter) -> list[str]:
        """The distinct raw model names that token rows (`cost_daily`, and the subagent totals of `session_usage`)
        carry in the filtered window and viewer scope: the models whose catalog rates the cost sort resolves before
        it reads the window. Scoped by `sel` like every other window statement, so a model used only by sessions
        outside the window or the viewer's scope never reaches rate resolution.

        With the stored columns on (the unfiltered shared list), the models of every stamped session are its stored
        `models_used`, read in the index-only scan of `sel` (a handful of distinct arrays); only the unstamped
        sessions (set S) are read from the token rows."""
        ids_only = f.session_ids is None and not self._narrows_window(f)
        params = window_params(f)
        if ids_only and expressible(f.scope):
            async with self._engine.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
                enabled = await self._fetch(conn, SORT_KEYS_ENABLED_SQL, {})
                if enabled and enabled[0].get("enabled"):
                    stale = await self._stale_ids(conn, f, None)
                    if len(stale) <= self._s_cap:
                        sel = self._sel_cte(f, lean=True, ids_only=True, with_keys=("models_used",))
                        params["stale"] = list(stale)
                        rows = await self._fetch(conn, stored_cost_models_sql(sel), params)
                        return [r["model_name"] for r in rows]
        rows = await self._q(
            f"WITH {self._sel_cte(f, lean=True, ids_only=ids_only)} "
            # Each token source is joined to `sel`, never `IN (SELECT ... FROM sel)`: the planner turns that into one
            # index probe per session and partition (14M probes on a million sessions, ~40 s); the join hashes `sel`.
            f"SELECT m.model_name FROM ("
            f"SELECT c.model_name FROM cost_daily c JOIN sel ON sel.session_id = c.session_id "
            f"WHERE c.model_name IS NOT NULL "
            f"UNION ALL SELECT coalesce(u.model, '') FROM session_usage u JOIN sel ON sel.session_id = u.session_id "
            f"WHERE u.attrs->>'token_source' = 'subagent_usage') m "
            f'GROUP BY m.model_name ORDER BY m.model_name COLLATE "C"',
            params,
        )
        return [r["model_name"] for r in rows]

    async def get_cost_page(
        self,
        f: LocalAnalyticsFilter,
        page: int,
        per_page: int,
        rates: Sequence[ModelRates],
        search: str | None = None,
        is_unattributed: bool = False,
    ) -> tuple[int, list[str], list[tuple[str, tuple[bool, ...]]]]:
        """`get_session_page` for the cost sort plus the kind flags of the whole filtered window, from one build of
        the (session, model) token groups: `(total, session_ids, model_flags)`. The flags are `get_window_model_flags`'
        input of `CatalogPricer.record_unpriced_from_flags` and are not narrowed by `search` / `is_unattributed`.

        On one connection: when `_stored_mode` allows it, the stored key and kind masks of every session stamped with
        the current rates version are read, and the groups, key and flags of S (and of any session without such a
        stamp) are computed by today's statement; otherwise today's statement runs over the whole window."""
        if search and "\x00" in search:
            return 0, [], await self.get_window_model_flags(f)
        async with self._engine.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
            mode = await self._stored_mode(conn, f, "cost_usd")
            ctes, params = self._ordered_window(
                f, "cost_usd", rates, search, is_unattributed, with_groups=True, stored=mode.stored
            )
            if mode.stored:
                params["stale"] = list(mode.stale)
            params["limit"] = per_page
            params["offset"] = min(page * per_page, _MAX_PAGE_OFFSET)
            keyed = bool(self._sel_form(f, "cost_usd", search, is_unattributed, mode.stored, True, False)[1])
            rows = await self._fetch(conn, cost_page_sql(ctes, stored=mode.stored, keyed=keyed), params)
        row = rows[0]
        flags = json.loads(row["flags"]) if isinstance(row["flags"], str) else row["flags"]
        return (
            int(row["total"]),
            list(row["session_ids"]),
            [(entry[0], tuple(bool(v) for v in entry[1:])) for entry in flags],
        )

    async def get_project_page(
        self,
        f: LocalAnalyticsFilter,
        page: int,
        per_page: int,
        rates: Sequence[ModelRates],
        search: str | None = None,
    ) -> ProjectPage:
        """One page of the project breakdown, decided in SQL (`project_page_sql`): the sessions of `sel` whose
        project or repository contains `search` (case-insensitive) are grouped by project ('' reads NULL, the
        unattributed project) and ordered by their spend priced with `rates`, highest first, the unattributed
        project last, then by name. `matched_session_ids` is None without a search (every session of the window)."""
        search = search.strip().lower() if search and search.strip() else None
        if search and "\x00" in search:
            # PostgreSQL text cannot hold a NUL, so no session matches; the driver would reject it.
            return ProjectPage(0, [], [], [], await self.get_window_model_flags(f))
        params = window_params(f)
        params.update(rates_params(rates))
        params["limit"] = per_page
        params["offset"] = min(page * per_page, _MAX_PAGE_OFFSET)
        if search:
            params["search"] = search
        rows = await self._q(project_page_sql(self._sessions_cte(f, lean=True), searched=bool(search)), params)
        row = rows[0]
        flags = json.loads(row["flags"]) if isinstance(row["flags"], str) else row["flags"]
        matched = row["matched_session_ids"]
        return ProjectPage(
            total=int(row["total"]),
            project_names=[name or None for name in row["project_names"]],
            session_ids=list(row["session_ids"]),
            matched_session_ids=None if matched is None else list(matched),
            model_flags=[(entry[0], tuple(bool(v) for v in entry[1:])) for entry in flags],
        )

    async def get_framework_page(
        self,
        f: LocalAnalyticsFilter,
        sort_by: str,
        page: int,
        per_page: int,
        matching: Callable[[list[list[str]]], list[list[str]]],
        rates: Sequence[ModelRates] | None = None,
        search: str | None = None,
        is_unattributed: bool = False,
    ) -> tuple[int, list[str], list[tuple[str, tuple[bool, ...]]]]:
        """One page of the sessions whose classifier names (`CLASSIFIER_NAMES_SQL`) the framework rule accepts, in
        the order of `get_session_page`: `(total, session_ids, model_flags)`. The rule is Python (the customer's
        regex config), so the work is split around it on one connection and one transaction:

        1. the names of every session of the filtered window are built set-wise into a temp table, and the DISTINCT
           name sets (a handful) are read;
        2. `matching(sets)` returns the sets the framework accepts;
        3. the page is cut from the sessions whose set is accepted, ordered by the sort key, with the exact total.

        `model_flags` covers the whole filtered window like `get_window_model_flags` (the cost sort takes them from
        the groups of its key). `matching` must be fast and synchronous: it runs while the connection is held."""
        if search and "\x00" in search:
            return 0, [], await self.get_window_model_flags(f)
        cost = sort_by == "cost_usd"
        # One snapshot for S and the page, as in the ctx and cost pages (see `live_cte`).
        async with self._engine.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
            mode = await self._stored_mode(conn, f, "cost_usd" if cost else "ctx_per_call")
            if mode.stored:
                total, ids = await self._stored_framework_page(
                    conn, mode, f, sort_by, page, per_page, matching, rates, search, is_unattributed
                )
        if mode.stored:
            return total, ids, await self.get_window_model_flags(f)
        return await self._framework_page(f, sort_by, page, per_page, matching, rates, search, is_unattributed)

    async def _stored_framework_page(
        self,
        conn: Any,
        mode: StoredMode,
        f: LocalAnalyticsFilter,
        sort_by: str,
        page: int,
        per_page: int,
        matching: Callable[[list[list[str]]], list[list[str]]],
        rates: Sequence[ModelRates] | None,
        search: str | None,
        is_unattributed: bool,
    ) -> tuple[int, list[str]]:
        """The framework page from the stored classifier names: the fresh sessions' names are read from
        `session_dims`, those of S are built set-wise, the distinct name sets and their counts (the groups) go to
        `matching` (Python, the customer's rule), `total` is the sum of the accepted groups' counts and the page is
        cut from the sessions of the accepted sets in the order of the sort key (stored for ctx_per_call and
        cost_usd, with the live key for S). The stored `delivery_framework` is never read. `conn` is inside the
        caller's repeatable-read transaction, the one `_stored_mode` read S in."""
        names_ctes, params = self._ordered_window(f, "start_time", None, search, is_unattributed, framework=True)
        params["stale"] = list(mode.stale)
        groups = await self._fetch(conn, framework_stored_groups_sql(names_ctes), params)
        accepted = {tuple(names) for names in matching([list(g["names"]) for g in groups])}
        total = sum(int(g["n"]) for g in groups if tuple(g["names"]) in accepted)
        report_framework_groups(len(groups), len(accepted), total)
        if not total or page * per_page >= total:
            return total, []
        ctes, page_params = self._ordered_window(
            f, sort_by, rates, search, is_unattributed, with_groups=sort_by == "cost_usd", framework=True,
            stored=True,
        )  # fmt: skip
        page_params["stale"] = list(mode.stale)
        if mode.version is not None:
            page_params["rates_version"] = mode.version
        page_params["matching"] = json.dumps([list(names) for names in accepted])
        page_params["limit"] = per_page
        page_params["offset"] = min(page * per_page, _MAX_PAGE_OFFSET)
        rows = await self._fetch(conn, framework_stored_page_sql(ctes), page_params)
        return total, [r["session_id"] for r in rows]

    async def _framework_page(
        self,
        f: LocalAnalyticsFilter,
        sort_by: str,
        page: int,
        per_page: int,
        matching: Callable[[list[list[str]]], list[list[str]]],
        rates: Sequence[ModelRates] | None,
        search: str | None,
        is_unattributed: bool,
    ) -> tuple[int, list[str], list[tuple[str, tuple[bool, ...]]]]:
        """Today's statements of the framework filter (the temp table of the names, then the page)."""
        # The names need no sort key: the window is read in the cheap start_time order, whatever the final sort.
        names_ctes, names_params = self._ordered_window(f, "start_time", None, search, is_unattributed, framework=True)
        with_groups = sort_by == "cost_usd"
        ctes, params = self._ordered_window(
            f, sort_by, rates, search, is_unattributed, with_groups=with_groups, framework=True
        )
        params["limit"] = per_page
        params["offset"] = min(page * per_page, _MAX_PAGE_OFFSET)
        flags_select = framework_flags_select(with_groups)
        async with self._engine.acquire() as conn, conn.transaction():
            await self._fetch(
                conn,
                framework_names_sql(names_ctes),
                names_params,
            )
            # A temp table has no statistics for the planner.
            await self._fetch(conn, f"ANALYZE {FRAMEWORK_NAMES_TABLE}", {})
            sets = [
                list(r["names"])
                for r in await self._fetch(conn, f"SELECT names FROM {FRAMEWORK_NAMES_TABLE} GROUP BY names", {})
            ]
            params["matching"] = json.dumps(matching(sets))
            rows = await self._fetch(
                conn,
                framework_page_sql(ctes, flags_select),
                params,
            )
            total = int(rows[0]["total"]) if rows else 0
            raw_flags = rows[0]["flags"] if rows else None
            if not rows and page * per_page > 0:
                count = await self._fetch(
                    conn,
                    framework_count_sql(),
                    {"matching": params["matching"]},
                )
                total = int(count[0]["total"])
        ids = [r["session_id"] for r in rows]
        if raw_flags is None:
            return total, ids, await self.get_window_model_flags(f)
        flags = json.loads(raw_flags) if isinstance(raw_flags, str) else raw_flags
        return total, ids, [(entry[0], tuple(bool(v) for v in entry[1:])) for entry in flags]

    async def _fetch(self, conn: Any, sql: str, params: dict[str, Any], *, exact: bool = False) -> Rows:
        text, args = positional(sql, params)
        records = await self._records(conn, text, args)
        return _rows(records, exact)

    async def get_session_start_times(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sel_cte(f)},{self._activity_cte()}
        SELECT session_start AS started_at FROM act""",
            window_params(f),
        )

    async def get_repository_sessions(self, f: LocalAnalyticsFilter, search: str | None = None) -> Rows:
        params = window_params(f)
        search_clause = ""
        if search:
            search_clause = "\n          AND repository ILIKE $search::text"
            params["search"] = f"%{search.strip().lower()}%"
        return await self._q(
            f"""
        WITH {self._sel_cte(f)}
        SELECT session_id, repository, branch, project_name FROM sel
        WHERE repository <> ''{search_clause}""",
            params,
        )

    async def get_framework_sessions(
        self, start: datetime, end: datetime, framework_labels: Sequence[str]
    ) -> list[tuple[str, str, str, int]]:
        """Per (lower-cased user email, raw repo_remote, raw repository): how many sessions started in [start, end)
        have a delivery framework among `framework_labels` (the current non-fallback labels, so a stored label of a
        renamed or removed rule never counts). The caller normalises the repository.

        The query is global per person and repository, like the Elasticsearch side it is merged into: it counts the
        shared sessions that started in the window whatever their project, with no dashboard filter; owner-private
        Personal sessions are excluded."""
        rows = await self._q(
            f"""
        SELECT user_email, repo_remote, repository, count(*)::bigint AS framework_sessions
        FROM (
            SELECT lower({_SESSION_USER_EMAIL}) AS user_email,
                   coalesce(repo_remote, '') AS repo_remote,
                   coalesce(repository, '') AS repository
            FROM session_dims d
            WHERE started_at IS NOT NULL AND {_STARTED_IN_WINDOW}
              AND delivery_framework = ANY($labels::text[])
              AND NOT {sql_private("d")}
        ) x
        WHERE user_email <> ''
        GROUP BY 1, 2, 3""",
            {
                "start_dt": truncate_to_ms(start),
                # Elasticsearch ranges are [gte, lt); BETWEEN is inclusive, so the end drops one millisecond.
                "end_dt": truncate_to_ms(end) - timedelta(milliseconds=1),
                "labels": list(framework_labels),
            },
        )
        return [(r["user_email"], r["repo_remote"], r["repository"], int(r["framework_sessions"])) for r in rows]

    # ── session detail ───────────────────────────────────────────────────────

    async def authorize_session(self, session_id: str, scope: Any) -> bool:
        """Cheap read: may this viewer open the session? Decided before any payload query."""
        gate, params = detail_gate(scope)
        rows = await self._q(f"SELECT {gate} AS ok", {"sid": session_id, **params})
        return bool(rows and rows[0]["ok"])

    async def get_session_detail_meta(self, session_id: str, *, scope: Any) -> Rows:
        # The same range as the list, keyed on the cost row's session so a session without a
        # session_dims row still gets its earliest event as start.
        start, end = _activity_range("t")
        gate, gate_params = detail_gate(scope)
        return await self._q(
            f"""
        WITH c AS (
                 -- a session with dimension events but no cost row is listed as a known $0.00 (cost_available
                 -- true, EPMCDME-17138), so its detail must resolve too
                 SELECT session_id FROM cost_daily WHERE session_id = $sid::text
                 UNION
                 SELECT session_id FROM session_dims WHERE session_id = $sid::text AND started_at IS NOT NULL
             ),
             h AS (
                 -- the earliest log / span record whose resource has a usable service name
                 SELECT e.session_id, e.resource_id, e.ts
                 FROM (
                     SELECT session_id, resource_id, ts FROM log_events WHERE session_id = $sid::text
                     UNION ALL
                     SELECT session_id, resource_id, ts FROM spans WHERE session_id = $sid::text
                 ) e
                 JOIN otel_resources ho ON ho.resource_id = e.resource_id
                 WHERE nullif(btrim(ho.service_name, {_BLANKS}), '') IS NOT NULL
                 ORDER BY e.ts ASC, e.resource_id ASC
                 LIMIT 1
             )
        SELECT c.session_id,
               coalesce(nullif(coalesce(nullif(d.jwt_email, ''), nullif(d.dev_name_max, '')), ''),
                        nullif(CASE WHEN d.started_at IS NOT NULL THEN d.developer_name END, ''),
                        'unknown')                                                    AS developer_name,
               coalesce(nullif(d.repo_remote, ''), nullif(d.repository, ''))           AS repository,
               nullif(d.branch, '')                                                   AS branch,
               nullif(d.project_name, '')                                             AS project_name,
               nullif(d.first_prompt, '')                                             AS prompt,
               nullif(d.session_title, '')                                            AS session_title,
               nullif(d.entrypoint, '')                                               AS entrypoint,
               coalesce(r.session_start, {_EPOCH})                                    AS started_at,  -- or 1970-01-01
               coalesce({_ms("r.session_end")} - {_ms("r.session_start")}, 0)         AS duration_ms,
               -- the OTel service name of the earliest resource that has one; else the session `platform` (the hook
               -- `platform`, or derived from span / metric names), used when no resource has a usable `service.name`
               coalesce(nullif(btrim(o.service_name, {_BLANKS}), ''), nullif(btrim(d.platform, {_BLANKS}), ''))
                                                                                      AS harness_service_name,
               {sql_subscription_type("d.subscription_type")}                          AS subscription_type,
               {sql_project_origin("d.project_origin")}                                AS project_origin,
               nullif(btrim(d.route_note), '')                                        AS route_note
        FROM c
        LEFT JOIN session_dims d ON d.session_id = c.session_id
        CROSS JOIN LATERAL (
            SELECT {start} AS session_start, {end} AS session_end
            FROM (SELECT c.session_id, d.started_at, d.activity_started_at, d.last_event_at, d.ended_at) t
        ) r
        LEFT JOIN h ON h.session_id = c.session_id
        LEFT JOIN otel_resources o ON o.resource_id = h.resource_id
        WHERE {gate}""",
            {"sid": session_id, **gate_params},
        )

    async def get_session_detail_cost(self, session_id: str, *, scope: Any) -> Rows:
        gate, gate_params = detail_gate(scope)
        return await self._q(
            f"""
        SELECT {_TOP_MODEL}                         AS model_name,
               sum(c.input_tokens)::bigint          AS input_tokens,
               sum(c.output_tokens)::bigint         AS output_tokens,
               sum(c.cache_read_tokens)::bigint     AS cache_read_tokens,
               sum(c.cache_creation_tokens)::bigint AS cache_creation_tokens
        FROM {_cost_rows(f"c.session_id = $sid::text AND {gate}")}""",
            {"sid": session_id, **gate_params},
        )

    async def get_session_detail_model_costs(self, session_id: str, *, scope: Any) -> Rows:
        """Token sums per raw model of one session, the empty model kept: the rows its money is priced from.
        The 5m / 1h sums are NULL when no row of the model has a split."""
        gate, gate_params = detail_gate(scope)
        return await self._q(
            f"""
        SELECT c.model_name                                 AS model_name,
               sum(c.input_tokens)::bigint                  AS input_tokens,
               sum(c.output_tokens)::bigint                 AS output_tokens,
               sum(c.cache_read_tokens)::bigint             AS cache_read_tokens,
               sum(c.cache_creation_tokens)::bigint         AS cache_creation_tokens,
               sum(c.cache_creation_5m_tokens)::bigint      AS cache_creation_5m_tokens,
               sum(c.cache_creation_1h_tokens)::bigint      AS cache_creation_1h_tokens
        FROM {_token_rows("c.session_id = $sid::text", "u.session_id = $sid::text")}
        WHERE {gate}
        GROUP BY c.model_name""",
            {"sid": session_id, **gate_params},
        )

    async def get_session_detail_scalars(self, session_id: str, *, scope: Any) -> Rows:
        gate, gate_params = detail_gate(scope)
        return await self._q(
            f"""
        SELECT
            (SELECT sum(lines_added) FROM lines_daily WHERE session_id = $sid::text)::bigint     AS lines_added,
            (SELECT sum(lines_removed) FROM lines_daily WHERE session_id = $sid::text)::bigint   AS lines_removed,
            (SELECT sum(active_ms_user) + sum(active_ms_cli)
               FROM active_time_daily WHERE session_id = $sid::text)::bigint                     AS active_ms,
            -- observed turns prove activity, not completeness: a 0 is served only when the summary reports it
            greatest(nullif((SELECT sum(turns) FROM turns_daily WHERE session_id = $sid::text), 0),
                     (SELECT {_SUMMARY_TURNS} FROM session_dims d WHERE d.session_id = $sid::text)
            )::bigint AS turns,
            (SELECT {_SUMMARY_STATE} FROM session_dims d WHERE d.session_id = $sid::text) AS summary_state,
            (SELECT {_SUMMARY_METRICS} FROM session_dims d WHERE d.session_id = $sid::text) AS summary_metrics,
            -- all tools, not the 20 listed: the same rollup rows as get_tool_success_by_session
            (SELECT sum(calls) FROM invocations_hourly WHERE kind = 1 AND session_id = $sid::text)::bigint
                AS tool_call_count,
            (SELECT sum(success) FROM invocations_hourly WHERE kind = 1 AND session_id = $sid::text)::bigint
                AS tool_calls_success,
            -- the latest summary's whole-session count, never below what the rollup observed; NULL without one
            (SELECT CASE WHEN {_SUMMARY_TOOL_CALLS} IS NOT NULL THEN greatest({_SUMMARY_TOOL_CALLS}, coalesce(
                    (SELECT sum(calls) FROM invocations_hourly WHERE kind = 1 AND session_id = $sid::text), 0)) END
               FROM session_dims d WHERE d.session_id = $sid::text)::bigint AS tool_calls_total,
            greatest(nullif(
            (coalesce((SELECT sum(agent_count) FROM tool_facts_daily WHERE session_id = $sid::text), 0)
             -- a hook event not yet reflected by a matching span OR by an already-rolled-up
             -- invocations_hourly row (once the rollup catches up, it already counts this
             -- agent_type via tool_facts_daily above, so this must not double it).
             -- Restricted to a day with no TOOL/TOOL_EXECUTION/INTERACTION span at all, same as the
             -- rollup's own hook fallback (_NO_SPAN_OF_KIND in rollups.py): the kind-3 hook branch
             -- there never runs on a day that has spans, so on such a day the rollup can never
             -- "catch up" and this correction would otherwise add the same +1 forever.
             + (SELECT count(*) FROM hook_events he
                WHERE he.session_id = $sid::text AND he.event_type = 'agent.subagent.start' AND he.agent_type <> ''
                  AND octet_length(he.agent_type) <= {MAX_KEY_BYTES} AND {first_subagent_start("he")}
                  AND NOT (coalesce(he.skill_name, '') <> '' AND {is_claude_code('he')})
                  AND NOT {fork_of_counted_call("he")}
                  AND NOT {fork_of_dispatch("he")}
                  AND he.agent_type NOT IN (SELECT subagent_type FROM spans
                                            WHERE span_kind = {_TOOL} AND session_id = $sid::text
                                              AND subagent_type <> '')
                  AND he.agent_type NOT IN (SELECT name FROM invocations_hourly
                                            WHERE kind = 3 AND session_id = $sid::text)
                  AND NOT EXISTS (SELECT 1 FROM spans sp
                                  WHERE sp.session_id = he.session_id
                                    AND sp.span_kind IN ({_TOOL}, {_EXEC}, {_INTERACTION})
                                    AND sp.ts >= date_trunc('day', he.ts, 'UTC')
                                    AND sp.ts < date_trunc('day', he.ts, 'UTC') + interval '1 day'))
            ), 0),
             (SELECT {_SUMMARY_MAP_TOTAL.format(c="agents")} FROM session_dims d WHERE d.session_id = $sid::text)
            )::bigint AS agent_count,
            -- tool_facts_daily already counts each skill invocation once; a dispatch adds only while its
            -- name has no kind-2 row, that is before the rollup has caught up with it.
            greatest(nullif((coalesce((SELECT sum(skill_count) FROM tool_facts_daily WHERE session_id = $sid::text), 0)
             + (SELECT count(*) FROM hook_events
                WHERE session_id = $sid::text AND event_type = '{SKILL_DISPATCH_EVENT}' AND skill_name <> ''
                  AND octet_length(skill_name) <= {MAX_KEY_BYTES}
                  AND skill_name NOT IN (SELECT name FROM invocations_hourly
                                         WHERE kind = 2 AND session_id = $sid::text))), 0),
             (SELECT {_SUMMARY_MAP_TOTAL.format(c="skills")} FROM session_dims d WHERE d.session_id = $sid::text)
            )::bigint AS skill_count
        WHERE {gate}""",
            {"sid": session_id, **gate_params},
        )

    async def get_session_detail_tools(self, session_id: str, *, scope: Any) -> Rows:
        """The 20 most called tools of a session with their successes, from the hourly rollup the aggregates read
        (OTel spans, else the hooks fallback), so a hooks-only session has them too."""
        gate, gate_params = detail_gate(scope)
        return await self._q(
            f"""
        SELECT name AS tool_name, sum(calls)::bigint AS call_count, sum(success)::bigint AS success_count
        FROM invocations_hourly
        WHERE kind = 1 AND session_id = $sid::text AND {gate}
        GROUP BY name
        ORDER BY call_count DESC, name {_C}
        LIMIT 20""",
            {"sid": session_id, **gate_params},
        )

    async def get_session_detail_events(self, session_id: str, *, scope: Any) -> Rows:
        """API requests and tool calls of a session, in time order. A request's 5m / 1h cache-write split is NULL
        when its attributes carry none.

        The requests are the session's OTel api_request records, plus the transcript requests (`usage_requests`,
        grouped as the cost rollup groups them: by request id, else message id, and raw model; the largest value of
        every field, the latest time; a row with neither id alone) that no OTel record is paired with. The pairing is
        the cost rollup's own, run over the whole session (`REQUEST_PAIRS`), so each request is listed once, as
        cost_daily counts it: a transcript request with an OTel partner is that record (it is not listed again), and
        a transcript request with none is a request of its own, whatever else its day holds (an OTel record that
        reached the server for some requests of a day does not hide the others). A paired record takes the 5m / 1h
        cache-write split of its transcript request when that has one, as the cost rollup prices it, since the OTel
        record carries no split of its own: left unsplit, a write is priced at the 5m rate. A request's split is NULL
        when neither of the two carries one. A session without OTel records (hooks only: GitHub Copilot, Claude
        Code without telemetry) lists all its transcript requests.
        Every request's `model_name` is the name cost_daily keys its tokens by, so each request is priced as
        cost_daily prices it: the route of a transcript request run on a route of its own model, one that normalises
        to its raw model up to a trailing date (`routed_predicate`) (an OTel record takes the route of the transcript
        request the rollup pairs it with, see `request_routes`), else the OTel name, or the raw model of a transcript
        request. `reasoning_tokens` are the transcript's thinking tokens; an OTel api_request carries none (NULL).

        Tool calls are listed from the spans, and from the `agent.tool.start` hooks for a UTC day with no TOOL,
        TOOL_EXECUTION or INTERACTION span, with no widening around midnight: the rollup's own day rule
        (`_NO_SPAN_OF_KIND`), so the listed calls are the ones the tools table and the session list count, a hook
        before midnight and a span after it counted by both, as there.

        A subagent known only by its totals (a session_usage row with attrs.token_source = 'subagent_usage', which
        the cost rollup writes only when no request of that agent is kept) has no request: each such row is one
        `subagent_totals` event with the row's tokens, at the time of the agent's latest agent.subagent.usage
        event with totals (the event the row was built from), else at the start of the row's UTC day once raw
        retention has purged it. Priced like the session's money rows, so for a session without OTel api_request
        records the costs of all usage events (requests and subagent_totals together, not the requests alone) add up
        to the session cost."""
        gate, gate_params = detail_gate(scope)
        return await self._q(
            f"""
        WITH {REQUEST_ROUTES_CTE}, {REQUEST_PAIRS_CTES}
        SELECT * FROM (
            SELECT ts AS timestamp, 'api_request' AS event_type,
                   coalesce({request_route(_OKEY)}, model, '') AS model_name,
                   coalesce(input_tokens, 0) AS input_tokens, coalesce(output_tokens, 0) AS output_tokens,
                   coalesce(cache_read_tokens, 0) AS cache_read_tokens,
                   coalesce({pair_split(_OKEY, 0)}, cache_creation_tokens, 0) AS cache_creation_tokens,
                   coalesce({pair_split(_OKEY, 1)}, {_attr_ms("log_events", "cache_creation_5m_tokens")})
                       AS cache_creation_5m_tokens,
                   coalesce({pair_split(_OKEY, 2)}, {_attr_ms("log_events", "cache_creation_1h_tokens")})
                       AS cache_creation_1h_tokens,
                   NULL::bigint AS reasoning_tokens, '' AS tool_name
            FROM log_events WHERE session_id = $sid::text AND event_kind = {_API_REQUEST} AND {gate}
            UNION ALL
            SELECT coalesce(h.ts, u.day::timestamp AT TIME ZONE 'UTC'), 'subagent_totals', u.model,
                   coalesce(u.input_tokens, 0), coalesce(u.output_tokens, 0), coalesce(u.cache_read_tokens, 0),
                   coalesce(u.cache_creation_tokens, 0), u.cache_creation_5m_tokens, u.cache_creation_1h_tokens,
                   NULL, ''
            FROM session_usage u
            LEFT JOIN LATERAL (
                SELECT x.ts FROM hook_events x
                WHERE x.session_id = u.session_id AND x.event_type = 'agent.subagent.usage'
                  AND coalesce(x.agent_id, '') = u.agent_id AND {_has_totals("x.attrs")}
                ORDER BY x.ts DESC, x.ingest_seq DESC LIMIT 1
            ) h ON true
            WHERE u.session_id = $sid::text AND u.attrs->>'token_source' = 'subagent_usage' AND {gate}
            UNION ALL
            SELECT ts, 'tool_call', '', 0, 0, 0, 0, NULL, NULL, NULL, tool_name
            FROM spans WHERE session_id = $sid::text AND span_kind = {_TOOL} AND tool_name <> '' AND {gate}
            UNION ALL
            SELECT h.ts, 'tool_call', '', 0, 0, 0, 0, NULL, NULL, NULL, h.tool_name
            FROM hook_events h
            WHERE h.session_id = $sid::text AND h.event_type = 'agent.tool.start' AND h.tool_name <> '' AND {gate}
              AND octet_length(h.tool_name) <= {MAX_KEY_BYTES}
              AND NOT EXISTS (SELECT 1 FROM spans s
                              WHERE s.session_id = h.session_id
                                AND s.span_kind IN ({_TOOL}, {_EXEC}, {_INTERACTION})
                                AND s.ts >= date_trunc('day', h.ts, 'UTC')
                                AND s.ts < date_trunc('day', h.ts, 'UTC') + interval '1 day')
            UNION ALL
            -- A transcript request no OTel record is paired with (`unpaired` of REQUEST_PAIRS), grouped and paired as
            -- _BUILD_REQUESTS does, with its cache-write rule (5m + 1h, NULL split kept). The handler prices the row at
            -- the catalog from these tokens, like every other request. model_name is the name cost_daily keys the
            -- request by: its route when it ran on one of its own model (routed_predicate), else model_raw.
            SELECT p.ts, 'api_request', p.model_name, coalesce(p.input_tokens, 0), coalesce(p.output_tokens, 0),
                   coalesce(p.cache_read_tokens, 0), p.cache_creation_tokens, p.cache_5m, p.cache_1h,
                   p.thinking_tokens, ''
            FROM request_pairs p
            WHERE p.part = 'unpaired' AND {gate}
        ) events
        ORDER BY timestamp, event_type {_C}, tool_name {_C}, model_name {_C}""",
            {"sid": session_id, **gate_params},
        )

    async def get_session_detail_dispatches(self, session_id: str, *, scope: Any) -> Rows:
        """Agent and skill dispatches (tool spans) plus slash-command skills (hook events).

        One row per skill invocation. A forked skill's row is its initiator's: the Skill call's span row
        (its agent.tool.end names the fork's agentId) or the slash command's dispatch (the fork shares its
        prompt_id, and its skill name unless it ran in the foreground without one); that row takes the fork's
        run (its SubagentStart to its SubagentStop) and cost by the fork's agentId, and the fork adds no row
        of its own. A fork with no initiator row is its own skill row. An inline slash-started skill (no fork)
        takes the requests of its own prompt that usage_requests attributes to its skill, from the dispatch to
        the next start of the same skill; its run ends at the last of them. Another skill's "real" duration
        runs to the end of its interaction.
        An agent's bar starts at its SubagentStart hook event, found by the agentId its Agent call
        started (`dispatch_start`); without one, at the call start (agent.tool.start); the launch ack when
        both are missing. Its run time (`subagent_duration_ms`) is the `duration_ms` of its latest SubagentStop hook
        event, matched by agentId, less the idle time between a continued agent's runs: the CLI rereads the whole
        transcript on every stop, so that duration is already ended_at minus the agent's first line, idle included,
        and only the latest stop's value is read (an earlier stop missing it, from an older CLI, does not matter).
        The stop row lags the end of the run by seconds, since Claude Code waits for the SubagentStop hooks before
        the Agent call returns. When that duration is known, the bar starts that many milliseconds before the stop
        instead of at SubagentStart, so its drawn end always lands exactly on the stop and never overruns it,
        whatever the real gap between SubagentStart and the subagent's own clock turns out to be. Without a
        duration_ms on the latest stop (an older CLI) the bar keeps starting at SubagentStart and the run time is
        from there to the stop's timestamp, less the same idle time: both ends come from the same clock there, so
        they already agree. It is NULL when the stop never arrived. The handler clamps it to the
        session end. A forked skill's or command's run stays its fork's start to its stop timestamp.
        An inline Skill call (no fork) takes, like an inline slash command, the main-thread requests the plugin
        attributed to its skill in its own prompt, from the call to the next start of the same skill, each request
        once (`skill_matched`); its run ends at the last of them. With none, it keeps the sibling-window fallback
        below, which never prices a request an inline dispatch or Skill call owns (`attributed_okeys`).
        The tool-execution span duration (`duration_ms`) is selected for the row shape but no agent row
        uses it: it closes when a background agent is launched. An agent dispatch's cost and tokens
        are an exact ID chain, not an estimate: the Agent call's hook rows name the agentId the subagent was
        launched with, by the agent-call rule of agent_call_attribution (the end's `fork_agent_id`, else its
        subagent stop's agent_id, else the complete agentId in the result text, which a long prompt cuts off);
        the subagent's LLM requests (llm_request spans) carry that agentId
        and are children of the span that groups its api_request events, so those events are summed.
        A subagent none of whose LLM requests carries its agentId (a foreground fork of a slash command) is
        priced from the API requests paired with the transcript requests the plugin sent under its agentId.
        A dispatch with no agentId at all (an inline skill, or an agent from telemetry without it) falls back
        to its own slice of the parent span's API requests: from its own start to the next strictly later
        sibling start under the same parent (or unbounded for the last one), each request in it split by
        `request_share` among every sibling window its own timestamp falls in, so dispatches sharing a parent
        never each show the identical, undivided sum, and no two siblings' shares of the same request ever sum
        to more than that request. A Claude Code Agent call also ends its slice at its own end or error (a request the
        parent logged after the call returned is the parent's). One that never ended and names no agent, and one
        whose agentId went to another call (a resumed agent), take no parent requests at all, and so no share of a
        tied sibling's slice (`takes_parent` of fork_attribution): their row is empty.
        Rows outside that scope (Task, no tool_use_id, another client, Skill) keep the open slice.
        An agent or fork whose agentId is known but has no API requests gets NULL: its parent span is the main
        thread, whose cost is not the subagent's.
        Each row carries `models`, the per-model token sums of the API requests it is priced from (empty
        when it has none), already split by `request_share` among every sibling window a request falls in.
        """

        # each predicate selects the API requests `a` that both the token/cost sums and the per-model sums read
        def agent_requests(a: str) -> str:
            return f"{a}.span_id IN (SELECT ag.parent_span_id FROM agent_groups ag WHERE ag.agent_id = g.agent_id)"

        def window_requests(a: str) -> str:
            return (
                f"{a}.span_id = w.parent_span_id AND {a}.ts >= w.start_ts"
                f" AND (w.window_end IS NULL OR {a}.ts < w.window_end) AND {not_attributed(a)}"
            )

        gate, gate_params = detail_gate(scope)

        rows = await self._q(
            f"""
        WITH t AS (
            SELECT coalesce(subagent_type, '') AS subagent_type, coalesce(skill_name, '') AS skill_name,
                   tool_use_id, ts AS span_start, parent_span_id, span_id,
                   -- a Claude Code Agent call, and its end (latest agent.tool.end or .error, NULL when it has none):
                   -- the bound of its parent-request fallback; no bound outside that scope
                   {agent_call_scope("s")} AS in_scope,
                   CASE WHEN {agent_call_scope("s")} THEN {agent_call_span_end("s")} END AS bound_ts
            FROM spans s
            WHERE span_kind = {_TOOL} AND session_id = $sid::text AND (subagent_type <> '' OR skill_name <> '')
              -- an Agent or Skill call with no evidence it ran is denied, not a dispatch. Ingest stores an absent
              -- subagent_type as NULL, so a skill row is told apart with coalesce, not = ''.
              AND (CASE WHEN coalesce(s.subagent_type, '') <> '' THEN {executed_agent_call_span("s")}
                        ELSE {executed_tool_span("s", "Skill")} END)
        ), ex AS (
            SELECT tool_use_id, max(duration_ns / 1000000) AS duration_ms FROM spans
            WHERE span_kind = {_EXEC} AND session_id = $sid::text AND tool_use_id <> ''
            GROUP BY tool_use_id
        ), ia AS (
            SELECT span_id, max(ts) AS ts, max(duration_ns) AS duration_ns FROM spans
            WHERE span_kind = {_INTERACTION} AND session_id = $sid::text
            GROUP BY span_id
        ), agent_end AS (
            -- each Agent tool call's result time: the launch ack of a background agent, or the return (or error) of a
            -- foreground one. The agent it started is not read from the result (a foreground result is cut before its
            -- agentId): `launched` names it by the agent-call rule
            SELECT DISTINCT ON (tool_use_id) tool_use_id, ts
            FROM hook_events
            WHERE session_id = $sid::text AND event_type IN ('agent.tool.end', 'agent.tool.error')
              AND {agent_call_tool("tool_name")}
              AND tool_use_id <> ''
            ORDER BY tool_use_id, ts DESC
        ), agent_start AS (
            -- when each Agent tool call started
            SELECT tool_use_id, min(ts) AS ts
            FROM hook_events
            WHERE session_id = $sid::text AND event_type = 'agent.tool.start' AND {agent_call_tool("tool_name")}
              AND tool_use_id <> ''
            GROUP BY tool_use_id
        ), forked AS (
            -- when each subagent started, from its own earliest SubagentStart hook event (the typed agent_id column)
            SELECT agent_id, min(ts) AS ts
            FROM hook_events
            WHERE session_id = $sid::text AND event_type = 'agent.subagent.start' AND agent_id <> ''
            GROUP BY agent_id
        ), {agent_call_ids_cte("$sid::text")}, launched AS (
            -- the agentId each Agent tool call launched, and when its run began: its SubagentStart, for background
            -- and foreground agents alike; the call start when that is missing; the ack only when both are (a
            -- background agent's launch ack arrives seconds later, a foreground agent's result after its stop).
            -- Both ids come from call_ids, which resolves every call of the session once (the agent-call rule of
            -- fork_attribution): claimed_id is the id the call resolves to before the one-call-per-agent rule: set
            -- with no agent_id, the call lost its agent to another call. A call is known by its own Agent hook rows
            -- (start, end, error), a stop alone makes none; one with a start but no end or error is listed only when
            -- a stop names its agent. begin_ts is the instant a call is known to have begun (NULL when neither
            -- the SubagentStart nor the call start arrived): the ack is when it ended, not began.
            SELECT c.tool_use_id, coalesce(f.ts, s.ts, e.ts) AS launch_ts, coalesce(f.ts, s.ts) AS begin_ts,
                   c.agent_id, c.claimed_id
            FROM (SELECT x.tool_use_id, ci.agent_id, ci.claimed_id
                  FROM (SELECT tool_use_id FROM agent_end UNION SELECT tool_use_id FROM agent_start) x
                  LEFT JOIN call_ids ci ON ci.tool_use_id = x.tool_use_id) c
            LEFT JOIN agent_end e ON e.tool_use_id = c.tool_use_id
            LEFT JOIN agent_start s ON s.tool_use_id = c.tool_use_id
            LEFT JOIN forked f ON f.agent_id = c.agent_id
            WHERE e.tool_use_id IS NOT NULL OR c.agent_id IS NOT NULL
        ), skill_fork AS (
            -- the fork each Claude Code Skill call started, named by the agentId of its latest result; NULL for a
            -- skill that ran inline
            SELECT DISTINCT ON (tool_use_id) tool_use_id,
                   {skill_end_agent_id("hook_events")} AS agent_id
            FROM hook_events
            WHERE session_id = $sid::text AND event_type = 'agent.tool.end' AND tool_name = 'Skill'
              AND tool_use_id <> '' AND {is_claude_code()}
            ORDER BY tool_use_id, ts DESC
        ), dispatch_fork AS (
            -- each slash-command dispatch and the fork it started, by the rollups' rule: the earliest fork
            -- candidate at or after it with its prompt_id and skill name, else, when none has the name, the earliest
            -- one without a skill name (a foreground fork); none for another client's dispatch or one without a
            -- prompt_id; a dispatch whose skill name is too long to count is left out, as the counts leave it out;
            -- a dispatch whose prompt_id holds another dispatch pairs with none; DISTINCT: hook_events has no
            -- unique key, so rows identical in these columns are one dispatch (one timeline row, one usage window)
            SELECT DISTINCT d.ts, d.skill_name, d.prompt_id, df.agent_id, df.ts AS fork_ts
            FROM hook_events d
            LEFT JOIN LATERAL (
                SELECT f.agent_id, f.ts
                FROM hook_events f
                WHERE f.session_id = d.session_id AND f.agent_id <> '' AND {dispatch_fork_candidate("f", "call_ids")}
                  AND f.prompt_id = d.prompt_id AND (f.skill_name = d.skill_name OR coalesce(f.skill_name, '') = '')
                  AND f.ts >= d.ts
                  AND {is_claude_code('d')} AND d.prompt_id <> ''
                  AND {sole_dispatch_of_prompt('d')}
                ORDER BY coalesce(f.skill_name, '') = '', f.ts, f.ingest_seq
                LIMIT 1
            ) df ON true
            WHERE d.session_id = $sid::text AND d.event_type = '{SKILL_DISPATCH_EVENT}' AND d.skill_name <> ''
              AND octet_length(d.skill_name) <= {MAX_KEY_BYTES}
        ), {INLINE_SKILL_CTES}, idle AS (
            -- an agent continued by SendMessage starts again with the same agent_id: the time from a stop to the
            -- next start of the same agent is no run, once a later stop closes that run; each stop's gap counts once
            SELECT agent_id, sum(gap) AS idle
            FROM (
                SELECT DISTINCT ON (s.agent_id, p.stopped_at) s.agent_id, s.ts - p.stopped_at AS gap
                FROM hook_events s
                JOIN LATERAL (
                    SELECT max(x.ts) AS stopped_at FROM hook_events x
                    WHERE x.session_id = s.session_id AND x.event_type = 'agent.subagent.stop'
                      AND x.agent_id = s.agent_id AND x.ts < s.ts
                ) p ON p.stopped_at IS NOT NULL
                WHERE s.session_id = $sid::text AND s.event_type = 'agent.subagent.start' AND s.agent_id <> ''
                  AND EXISTS (SELECT 1 FROM hook_events y
                              WHERE y.session_id = s.session_id AND y.event_type = 'agent.subagent.stop'
                                AND y.agent_id = s.agent_id AND y.ts > s.ts)
                ORDER BY s.agent_id, p.stopped_at, s.ts
            ) gaps
            GROUP BY agent_id
        ), stopped AS (
            -- when each subagent stopped, from its own SubagentStop hook event (the typed agent_id column), less the
            -- idle time between its runs, so that its last stop minus its first start is the sum of its runs.
            -- stop_duration_ms: the LATEST stop's own duration_ms, the run time the Timeline shows when it is known:
            -- the CLI reads the whole agent transcript on every stop, so that duration is already ended_at minus the
            -- agent's very first line, idle time between runs included; subtracting idle leaves just the runs, and
            -- an earlier stop lacking a duration_ms (an older CLI) does not matter, only the latest one is read. NULL
            -- when the latest stop has none; the stop's timestamp lags the subagent's end, so it is only the
            -- fallback
            SELECT last.agent_id, last.ts - coalesce(i.idle, interval '0') AS ended_at,
                   CASE WHEN last.duration_ms IS NOT NULL
                        THEN last.duration_ms - coalesce({_ms("i.idle")}, 0) END AS stop_duration_ms
            FROM (
                SELECT DISTINCT ON (x.agent_id) x.agent_id, x.ts, {_attr_ms("x", "duration_ms")} AS duration_ms
                FROM hook_events x
                WHERE x.session_id = $sid::text AND x.event_type = 'agent.subagent.stop' AND x.agent_id IS NOT NULL
                ORDER BY x.agent_id, x.ts DESC
            ) last
            LEFT JOIN idle i ON i.agent_id = last.agent_id
        ), agent_groups AS (
            -- the span grouping a subagent's api_request events, named by its LLM requests' agentId
            SELECT DISTINCT attrs->>'agent_id' AS agent_id, parent_span_id
            FROM spans
            WHERE session_id = $sid::text AND span_kind = {_LLM_REQUEST}
              AND attrs ? 'agent_id' AND parent_span_id IS NOT NULL
        ), span_agent_usage AS (
            SELECT g.agent_id,
                   sum(coalesce(l.cost_usd, 0)) AS cost_usd,
                   sum(coalesce(l.input_tokens, 0)) AS input_tokens,
                   sum(coalesce(l.output_tokens, 0)) AS output_tokens,
                   sum(coalesce(l.cache_read_tokens, 0)) AS cache_read_tokens,
                   sum(coalesce(l.cache_creation_tokens, 0)) AS cache_creation_tokens,
                   {_model_sums(agent_requests("r"))} AS models
            FROM (SELECT DISTINCT agent_id FROM agent_groups) g
            JOIN log_events l
                 ON {agent_requests("l")} AND l.session_id = $sid::text AND l.event_kind = {_API_REQUEST}
            GROUP BY g.agent_id
        ), paired_agent_usage AS (
            -- a subagent none of whose LLM requests names it: Claude Code stamps agent_id only on the requests of a
            -- subagent started by a tool call, so a fork a slash command runs in the foreground has its requests
            -- under the interaction span, among the main thread's. Its own are the API requests paired with the
            -- transcript requests the plugin sent under its agentId (see paired_requests); never both sources
            SELECT g.agent_id,
                   sum(coalesce(g.cost_usd, 0)) AS cost_usd,
                   sum(coalesce(g.input_tokens, 0)) AS input_tokens,
                   sum(coalesce(g.output_tokens, 0)) AS output_tokens,
                   sum(coalesce(g.cache_read_tokens, 0)) AS cache_read_tokens,
                   sum(coalesce(g.cache_creation_tokens, 0)) AS cache_creation_tokens,
                   (SELECT jsonb_agg(m ORDER BY m.model_name {_C}) FROM (
                        SELECT coalesce({request_route("r.okey")}, r.model, '') AS model_name,
                               sum(coalesce(r.input_tokens, 0)) AS input_tokens,
                               sum(coalesce(r.output_tokens, 0)) AS output_tokens,
                               sum(coalesce(r.cache_read_tokens, 0)) AS cache_read_tokens,
                               sum(coalesce(r.cache_creation_tokens, 0)) AS cache_creation_tokens,
                               sum({_attr_ms("r", "cache_creation_5m_tokens")}) AS cache_creation_5m_tokens,
                               sum({_attr_ms("r", "cache_creation_1h_tokens")}) AS cache_creation_1h_tokens
                        FROM paired_requests r
                        WHERE r.agent_id = g.agent_id
                        GROUP BY 1) m) AS models
            FROM paired_requests g
            WHERE g.agent_id <> '' AND NOT EXISTS (SELECT 1 FROM agent_groups ag WHERE ag.agent_id = g.agent_id)
            GROUP BY g.agent_id
        ), agent_usage AS (
            SELECT agent_id, cost_usd, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, models
            FROM span_agent_usage
            UNION ALL
            SELECT agent_id, cost_usd, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, models
            FROM paired_agent_usage
        ), fallback_start AS (
            -- every dispatch's own start under its parent, agent or skill alike (the final SELECT displays the same
            -- formula as dispatch_start; inside the Claude Code Agent scope a call with no start hook and no
            -- SubagentStart begins at its tool span here, not at its end, which would leave its window empty), the
            -- end of its call (bound_ts, used only to bound sibling_window below: NULL for a row outside that scope,
            -- whose window stays open and starts as dispatch_start does) and whether it takes a slice of its
            -- parent's requests at all (takes_parent): not with an agent of its own, and, in that scope, not when
            -- its call never ended or lost its agent to another call
            SELECT t.span_id AS fallback_id, t.parent_span_id,
                   coalesce(CASE WHEN t.subagent_type <> '' THEN
                                 CASE WHEN t.in_scope THEN la.begin_ts ELSE la.launch_ts END END,
                            t.span_start) AS start_ts,
                   t.bound_ts,
                   {takes_parent("t.in_scope", "t.bound_ts", "la.agent_id", "la.claimed_id", "sf.agent_id")}
                       AS takes_parent
            FROM t
            LEFT JOIN launched la ON la.tool_use_id = t.tool_use_id AND t.tool_use_id <> ''
            LEFT JOIN skill_fork sf ON sf.tool_use_id = t.tool_use_id AND t.tool_use_id <> '' AND t.skill_name <> ''
        ), sibling_window AS (
            -- each dispatch's own slice of its parent's API-request stream: from its own start to the next
            -- strictly later sibling start under the same parent or the end of its own call, whichever comes first
            -- (open for a row outside the Claude Code Agent scope that has no later sibling; least() skips the NULL
            -- bound); a request inside more than one sibling's slice at once (see request_shares below) is split
            -- among every one of them, not only a sibling that starts and ends with it, so none shows the
            -- identical, undivided sum a sibling shows, and no two siblings' shares of the same request ever sum
            -- to more than that request.
            SELECT fallback_id, parent_span_id, start_ts, takes_parent, least(next_start, bound_ts) AS window_end
            FROM (
                SELECT fallback_id, parent_span_id, start_ts, bound_ts, takes_parent,
                       min(start_ts) OVER (PARTITION BY parent_span_id ORDER BY start_ts
                           RANGE BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING EXCLUDE GROUP) AS next_start
                FROM fallback_start
            ) windowed
        ), request_shares AS MATERIALIZED (
            -- how many of a request's own siblings (their own takes_parent window under the same parent, see
            -- sibling_window) hold its timestamp too, computed once per distinct (parent, timestamp) - not once
            -- per dispatch, and not once per summed column below - so a fallback-heavy session with many
            -- overlapping siblings prices in one pass over its own requests, not eleven correlated scans of
            -- sibling_window per request (a per-row, per-column repeat of this subquery took 14 s for 150
            -- siblings splitting 300 requests). Every (parent, ts) parent_usage or its models join to below
            -- satisfies window_requests for the row that put them here, so siblings is never 0 where it is read.
            SELECT l.span_id AS parent_span_id, l.ts,
                   (SELECT count(*) FROM sibling_window w2 WHERE w2.parent_span_id = l.span_id AND w2.takes_parent
                        AND l.ts >= w2.start_ts AND (w2.window_end IS NULL OR l.ts < w2.window_end)) AS siblings
            FROM log_events l
            WHERE l.session_id = $sid::text AND l.event_kind = {_API_REQUEST} AND {not_attributed("l")}
                AND EXISTS (SELECT 1 FROM sibling_window w3 WHERE w3.parent_span_id = l.span_id AND w3.takes_parent)
            GROUP BY l.span_id, l.ts
        ), parent_usage AS (
            -- dispatches that take the parent's requests: their own windowed slice of them (see sibling_window),
            -- each request split by its precomputed share (request_shares) among every sibling window it falls in
            SELECT w.fallback_id,
                   sum(coalesce(l.cost_usd, 0)::numeric / rs.siblings)              AS cost_usd,
                   sum(coalesce(l.input_tokens, 0)::numeric / rs.siblings)          AS input_tokens,
                   sum(coalesce(l.output_tokens, 0)::numeric / rs.siblings)         AS output_tokens,
                   sum(coalesce(l.cache_read_tokens, 0)::numeric / rs.siblings)     AS cache_read_tokens,
                   sum(coalesce(l.cache_creation_tokens, 0)::numeric / rs.siblings) AS cache_creation_tokens,
                   {
        _model_sums(
            window_requests("r"),
            weight="(1.0 / rs2.siblings)",
            extra_join="JOIN request_shares rs2 ON rs2.parent_span_id = r.span_id AND rs2.ts = r.ts",
        )
    } AS models
            FROM sibling_window w
            JOIN log_events l
                 ON {window_requests("l")} AND l.session_id = $sid::text AND l.event_kind = {_API_REQUEST}
            JOIN request_shares rs ON rs.parent_span_id = l.span_id AND rs.ts = l.ts
            WHERE w.takes_parent
            GROUP BY w.fallback_id, w.parent_span_id, w.start_ts, w.window_end
        )
        SELECT * FROM (
        SELECT t.subagent_type, t.skill_name, 0 AS is_slash_command,
               -- an agent's bar starts when its run began (see launched); a skill's at its tool span. When the
               -- stop's own duration_ms is what subagent_duration_ms below shows, the bar starts that many ms
               -- before the stop instead, so the two numbers always describe the same span and the bar never
               -- draws past the stop it is anchored to.
               coalesce(CASE WHEN t.subagent_type <> '' THEN
                             CASE WHEN la.agent_id IS NOT NULL AND st.stop_duration_ms IS NOT NULL
                                  THEN st.ended_at - (st.stop_duration_ms * interval '1 millisecond')
                                  ELSE la.launch_ts END
                        END, t.span_start) AS dispatch_start,
               coalesce(ex.duration_ms, 0)::bigint AS duration_ms,
               -- a forked skill's: its fork's run, NULL when it never stopped; an inline skill with attributed
               -- requests: to the last of them; another skill's: to the end of its interaction, NULL when it has none
               CASE WHEN sf.agent_id IS NOT NULL
                    THEN CASE WHEN st.ended_at >= fs.ts
                              THEN (extract(epoch FROM st.ended_at - fs.ts) * 1000)::bigint END
                    WHEN su.span_id IS NOT NULL
                    THEN (extract(epoch FROM su.last_ts - t.span_start) * 1000)::bigint
                    WHEN ia.ts IS NOT NULL
                    THEN (({_ns("ia.ts")} + coalesce(ia.duration_ns, 0) - {_ns("t.span_start")})
                          / 1000000)::bigint END AS real_duration_ms,
               -- the subagent's run time: the stop's own duration_ms when it has one (the stop row lags the end of
               -- the run: Claude Code waits for the SubagentStop hooks before the Agent call returns), else from its
               -- start to its stop; NULL when it never stopped. An agent only: a forked skill's run is real_duration_ms
               CASE WHEN la.agent_id IS NOT NULL AND st.stop_duration_ms IS NOT NULL THEN st.stop_duration_ms
                    WHEN st.ended_at >= la.launch_ts
                    THEN (extract(epoch FROM st.ended_at - la.launch_ts) * 1000)::bigint END AS subagent_duration_ms,
               coalesce(au.cost_usd, su.cost_usd, pu.cost_usd)::float8                AS cost_usd,
               coalesce(au.input_tokens, su.input_tokens, pu.input_tokens)::bigint    AS input_tokens,
               coalesce(au.output_tokens, su.output_tokens, pu.output_tokens)::bigint AS output_tokens,
               coalesce(au.cache_read_tokens, su.cache_read_tokens, pu.cache_read_tokens)::bigint
                   AS cache_read_tokens,
               coalesce(au.cache_creation_tokens, su.cache_creation_tokens, pu.cache_creation_tokens)::bigint
                   AS cache_creation_tokens,
               coalesce(au.models, su.models, pu.models) AS models
        FROM t
        LEFT JOIN ex ON ex.tool_use_id = t.tool_use_id AND t.tool_use_id <> ''
        LEFT JOIN ia ON ia.span_id = t.parent_span_id
        LEFT JOIN launched la ON la.tool_use_id = t.tool_use_id AND t.tool_use_id <> ''
        LEFT JOIN skill_fork sf ON sf.tool_use_id = t.tool_use_id AND t.tool_use_id <> '' AND t.skill_name <> ''
        LEFT JOIN forked fs ON fs.agent_id = sf.agent_id
        LEFT JOIN stopped st ON st.agent_id = coalesce(la.agent_id, sf.agent_id)
        LEFT JOIN agent_usage au ON au.agent_id = coalesce(la.agent_id, sf.agent_id)
        -- only a row that takes the parent's requests (see takes_parent): a known agent or fork with no API
        -- requests has no cost, not the main thread's, and a call that never ended or lost its agent has none
        LEFT JOIN parent_usage pu ON pu.fallback_id = t.span_id
        -- an inline Skill call takes its own prompt's attributed requests (see skill_matched), the sibling-window
        -- fallback only when it has none
        LEFT JOIN skill_usage su ON su.span_id = t.span_id
        UNION ALL
        -- A forked skill (a skill-tagged agent.subagent.start) whose initiator has no row of its own: no
        -- Skill span names it by agentId and no slash-command dispatch paired with it (an older CLI, or a
        -- Skill call seen only as hook events). Its run time and cost come by its typed agent_id, as an
        -- agent's do. real_duration_ms mirrors subagent_duration_ms: the handler reads real_duration_ms
        -- for this skill row.
        SELECT '', h.skill_name, 1, h.ts, 0,
               CASE WHEN st.ended_at >= h.ts
                    THEN (extract(epoch FROM st.ended_at - h.ts) * 1000)::bigint END,
               CASE WHEN st.ended_at >= h.ts
                    THEN (extract(epoch FROM st.ended_at - h.ts) * 1000)::bigint END AS subagent_duration_ms,
               au.cost_usd::float8 AS cost_usd, au.input_tokens::bigint AS input_tokens,
               au.output_tokens::bigint AS output_tokens, au.cache_read_tokens::bigint AS cache_read_tokens,
               au.cache_creation_tokens::bigint AS cache_creation_tokens, au.models
        FROM hook_events h
        LEFT JOIN stopped st ON st.agent_id = h.agent_id AND h.agent_id <> ''
        LEFT JOIN agent_usage au ON au.agent_id = h.agent_id AND h.agent_id <> ''
        WHERE h.session_id = $sid::text AND h.event_type = 'agent.subagent.start' AND h.skill_name <> ''
          AND {is_claude_code('h')}
          -- one row per fork: a re-sent or resumed start of the same agent_id is not another invocation
          AND {first_subagent_start("h")}
          AND NOT EXISTS (SELECT 1 FROM dispatch_fork df WHERE df.agent_id = h.agent_id)
          AND NOT EXISTS (SELECT 1 FROM t JOIN skill_fork sf ON sf.tool_use_id = t.tool_use_id
                          WHERE t.skill_name <> '' AND sf.agent_id = h.agent_id)
        UNION ALL
        -- A slash-command skill dispatch. With a fork it takes the fork's run and cost by the fork's agentId;
        -- a skill that ran in the main thread takes the requests the plugin attributed to it in its own prompt (see
        -- inline_window), up to the last of them; 0 when it has none. Never matched against a TOOL span by name.
        SELECT '', df.skill_name, 1, df.ts, 0,
               CASE WHEN df.agent_id IS NULL
                    THEN coalesce((extract(epoch FROM iu.last_ts - df.ts) * 1000)::bigint, 0)
                    WHEN st.ended_at >= df.fork_ts THEN (extract(epoch FROM st.ended_at - df.fork_ts) * 1000)::bigint
               END,
               NULL,
               CASE WHEN df.agent_id IS NULL THEN coalesce(iu.cost_usd, 0) ELSE au.cost_usd END::float8,
               CASE WHEN df.agent_id IS NULL THEN coalesce(iu.input_tokens, 0) ELSE au.input_tokens END::bigint,
               CASE WHEN df.agent_id IS NULL THEN coalesce(iu.output_tokens, 0) ELSE au.output_tokens END::bigint,
               CASE WHEN df.agent_id IS NULL THEN coalesce(iu.cache_read_tokens, 0)
                    ELSE au.cache_read_tokens END::bigint,
               CASE WHEN df.agent_id IS NULL THEN coalesce(iu.cache_creation_tokens, 0)
                    ELSE au.cache_creation_tokens END::bigint,
               coalesce(au.models, iu.models)
        FROM dispatch_fork df
        LEFT JOIN inline_usage iu ON iu.ts = df.ts AND iu.skill_name = df.skill_name
                                 AND iu.prompt_id = df.prompt_id
        LEFT JOIN stopped st ON st.agent_id = df.agent_id
        LEFT JOIN agent_usage au ON au.agent_id = df.agent_id
        ) dispatches
        WHERE {gate}
        ORDER BY dispatch_start, is_slash_command, subagent_type {_C}, skill_name {_C}""",  # noqa: S608 - only constants and SQL builders are interpolated; the values are bound
            {"sid": session_id, **gate_params},
        )
        for row in rows:
            row["models"] = json.loads(row["models"]) if row["models"] is not None else []
        return rows
