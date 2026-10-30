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
- a session is in the window iff its `started_at` is in [start, end], the bounds truncated
  to whole milliseconds; the window is decided once, in `_sessions_cte`, and every fact
  query is scoped to that session set;
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

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import SESSION_PAGE_SORTS, Rows
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.otlp import MAX_KEY_BYTES
from codemie.repository.cli_analytics.postgres.rollups import _attr_ms
from codemie.repository.cli_analytics.postgres.session_derived import CLASSIFIER_NAMES_SQL
from codemie.repository.cli_analytics.postgres.fork_attribution import (
    dispatch_fork_candidate,
    executed_tool_span,
    first_subagent_start,
    fork_of_counted_call,
    fork_of_dispatch,
    is_claude_code,
    skill_end_agent_id,
    sole_dispatch_of_prompt,
    tool_response_agent_id,
)
from codemie.repository.cli_analytics.vocabulary import (
    BUILTIN_COMMAND_NAMES,
    DIMENSION_HOOK_TYPES,
    SKILL_DISPATCH_EVENT,
    EventKind,
    SpanKind,
)

_NAMED_PARAM = re.compile(r"\$([a-z_][a-z0-9_]*)")
_EPOCH = "'1970-01-01 00:00:00+00'::timestamptz"
_TOOL, _EXEC, _INTERACTION = int(SpanKind.TOOL), int(SpanKind.TOOL_EXECUTION), int(SpanKind.INTERACTION)
_API_REQUEST = int(EventKind.API_REQUEST)
_LLM_REQUEST = int(SpanKind.LLM_REQUEST)
_DIM_TYPES = "ARRAY[" + ", ".join(f"'{t}'" for t in DIMENSION_HOOK_TYPES) + "]::text[]"
_C = 'COLLATE "C"'
_MAX_PAGE_OFFSET = 2**62  # far below the int64 bound of `OFFSET $offset::bigint`, far beyond any real row count

_STARTED_IN_WINDOW = "started_at BETWEEN $start_dt::timestamptz AND $end_dt::timestamptz"
# Clips the raw hook events that decide a session's last dimension event (not session membership).
_TS = "ts BETWEEN $start_dt::timestamptz AND $end_dt::timestamptz"
# Slash commands have one source per session: a session with a summary counts only the `commands`
# of its summary, so its OTel `user_prompt` records (kind 4 of the rollup) add nothing.


def _escape_like(term: str) -> str:
    """`term` with the LIKE wildcards and the escape character made literal (for `ESCAPE '\\'`)."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _no_summary(column: str) -> str:
    return f"NOT EXISTS (SELECT 1 FROM session_dims sd WHERE sd.session_id = {column} AND sd.summary_ts IS NOT NULL)"


def _row_email(alias: str) -> str:
    """The smallest non-empty `user_email` of the `alias` rows, byte order; an aggregate, or a window with OVER."""
    return f"min(nullif({alias}.user_email, '') {_C})"


def _session_email(alias: str) -> str:
    return f"{_row_email(alias)} OVER (PARTITION BY {alias}.session_id) AS session_email"


def _developer(row_email: str) -> str:
    """One developer per session: the session's email, else the smallest non-empty email of its rows, so a session
    whose rows carry different emails is never split."""
    return f"coalesce(nullif(s.user_email, ''), {row_email}, 'unknown')"


_DEVELOPER = _developer("c.session_email")
# The smallest non-empty cost_daily email of the session `s`, the first fallback of its developer.
_COST_EMAIL = f"(SELECT {_row_email('c')} FROM cost_daily c WHERE c.session_id = s.session_id)"
# The smallest non-empty lines_daily email of the session `s`.
_LINES_EMAIL = f"(SELECT {_row_email('x')} FROM lines_daily x WHERE x.session_id = s.session_id)"


def _session_developer(lines_email: str) -> str:
    """The developer of session `s`: its email, else its smallest non-empty cost_daily email, else `lines_email`
    (its smallest non-empty lines_daily email)."""
    return _developer(f"coalesce({_COST_EMAIL}, {lines_email})")


# The model of the row with the most calls, over the rows of the original grain of cost_daily:
# (day, session, user, model, query_source). cost_daily now has several rows per
# such key (speed, geo, scope, agent type), so their calls are summed back to that grain first (window
# `w`), then the largest row wins; ties go to the earliest day, then the key columns, as before. On
# days without plugin data cost_daily has one row per such key and the answer is unchanged.
_GRAIN_CALLS = "sum(c.api_call_count) OVER w AS grain_calls"
_GRAIN = "c.day, c.session_id, c.user_email, c.model_name, c.query_source"
_TOP_MODEL = (
    f"(array_agg(c.model_name ORDER BY c.grain_calls DESC, c.day, c.session_id {_C}, c.user_email {_C}, "
    f"c.model_name {_C}, c.query_source {_C}))[1]"
)


def _cost_rows(where: str) -> str:
    """`cost_daily` rows of the selection, each with the calls of its original grain (see _TOP_MODEL) and its
    session's row email (see _DEVELOPER)."""
    return (
        f"(SELECT c.*, {_GRAIN_CALLS}, {_session_email('c')} FROM cost_daily c WHERE {where} "
        f"WINDOW w AS (PARTITION BY {_GRAIN})) c"
    )


_SEL_SESSIONS = ("c.session_id IN (SELECT session_id FROM sel)", "u.session_id IN (SELECT session_id FROM sel)")


def _token_rows(session_where: str, usage_where: str) -> str:
    """Token rows of the selected sessions: `cost_daily` plus the subagent totals-only rows of `session_usage`
    (attrs.token_source = 'subagent_usage'), which are priced at query time. The writer guarantees a totals row
    exists only when no request of that agent is kept, so nothing is counted twice. The 5m / 1h sums stay
    NULL when no row carries a split; only the four base counts are coalesced.

    Date, user, project, repository and branch filters need no copy here: both branches are scoped by
    `session_id IN (SELECT session_id FROM sel)`, and `sel` (see `_sessions_cte`) is where the window and
    every filter are decided."""
    return (
        "(SELECT t.session_id, t.model_name, t.input_tokens, t.output_tokens, t.cache_read_tokens,"
        " t.cache_creation_tokens, t.cache_creation_5m_tokens, t.cache_creation_1h_tokens FROM ("
        "SELECT c.session_id, c.model_name, c.input_tokens, c.output_tokens, c.cache_read_tokens,"
        " c.cache_creation_tokens, c.cache_creation_5m_tokens, c.cache_creation_1h_tokens"
        f" FROM cost_daily c WHERE {session_where}"
        " UNION ALL "
        "SELECT u.session_id, u.model, coalesce(u.input_tokens, 0), coalesce(u.output_tokens, 0),"
        " coalesce(u.cache_read_tokens, 0), coalesce(u.cache_creation_tokens, 0),"
        " u.cache_creation_5m_tokens, u.cache_creation_1h_tokens"
        f" FROM session_usage u WHERE u.attrs->>'token_source' = 'subagent_usage' AND {usage_where}"
        ") t) c"
    )


_TOTAL_COLUMNS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")


def _tokens(column: str, include_totals: bool) -> str:
    """Session sum of a token `column` of cost_daily; with `include_totals` also the subagent totals-only rows of
    `session_usage` (see _token_rows), so the list agrees with the detail view. The totals come from the join `t`
    of `_facts_from`, constant per session, so they are taken with max, never summed over the cost rows."""
    base = f"sum(c.{column})"
    if not include_totals:
        return f"{base}::bigint"
    return f"(coalesce({base}, 0) + coalesce(max(t.{column}), 0))::bigint"


def _totals_join() -> str:
    """Subagent totals-only rows of `session_usage` summed to one row per selected session, so the join cannot
    multiply the cost rows of the outer GROUP BY. Scoped to `sel`, otherwise it would scan all of session_usage."""
    sums = ", ".join(f"sum(coalesce({c}, 0)) AS {c}" for c in _TOTAL_COLUMNS)
    return (
        f"(SELECT session_id, {sums} FROM session_usage "
        "WHERE attrs->>'token_source' = 'subagent_usage' AND session_id IN (SELECT session_id FROM sel) "
        "GROUP BY session_id) t"
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


def normalise(value: Any) -> Any:
    """asyncpg values -> the types the port returns (naive UTC datetime, int, float)."""
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


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
    return params


def _ms(column: str) -> str:
    """Epoch milliseconds, truncated: dateDiff('millisecond', a, b) is _ms(b) - _ms(a)."""
    return f"floor(extract(epoch FROM {column}) * 1000)::bigint"


def _model_sums(requests: str) -> str:
    """Token sums per model of the session's API requests `r` matching `requests`, as a JSON array of
    objects with the column names of get_session_detail_model_costs; NULL when none matches."""
    return f"""(SELECT jsonb_agg(m ORDER BY m.model_name {_C}) FROM (
        SELECT coalesce(r.model, '') AS model_name,
               sum(coalesce(r.input_tokens, 0)) AS input_tokens,
               sum(coalesce(r.output_tokens, 0)) AS output_tokens,
               sum(coalesce(r.cache_read_tokens, 0)) AS cache_read_tokens,
               sum(coalesce(r.cache_creation_tokens, 0)) AS cache_creation_tokens,
               sum({_attr_ms("r", "cache_creation_5m_tokens")}) AS cache_creation_5m_tokens,
               sum({_attr_ms("r", "cache_creation_1h_tokens")}) AS cache_creation_1h_tokens
        FROM log_events r
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
    bounds (no session_dims row) every row of the session counts.
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

    def probes(agg: str, bound: str) -> str:
        return ", ".join(
            f"(SELECT {agg}({a}.ts) FROM {table} {a} WHERE {a}.session_id = {alias}.session_id{bound.format(a=a)})"
            for table, a in (("log_events", "le"), ("spans", "sp"))
        )

    return f"least({start}, {probes('min', earlier)})", f"greatest({end}, {probes('max', later)})"


class PostgresCliAnalyticsReader:
    """Read-only PostgreSQL access for the Local Analytics endpoints."""

    def __init__(self, engine: AnalyticsPgEngine) -> None:
        self._engine = engine

    async def _q(self, sql: str, params: dict[str, Any]) -> Rows:
        text, args = positional(sql, params)
        async with self._engine.acquire() as conn:
            records = await conn.fetch(text, *args)
        return [{key: normalise(value) for key, value in record.items()} for record in records]

    # ── shared session selection ─────────────────────────────────────────────

    @staticmethod
    def _sessions_cte(f: LocalAnalyticsFilter) -> str:
        """Every session's dimensions (dimensions plus the session's email).

        Sessions whose started_at is in the window; the only place the window is decided.
        Strings are never NULL in the result, so missing values become '' for the filters.
        """
        if f.deny_all:
            conditions = ["false"]
        else:
            conditions = []
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
        where = ("\n            WHERE " + " AND ".join(conditions)) if conditions else ""
        return f"""
        sel AS (
            SELECT * FROM (
                SELECT d.session_id,
                       coalesce(nullif(d.repo_remote, ''), nullif(d.repository, ''), '') AS repository,
                       coalesce(d.branch, '')       AS branch,
                       coalesce(d.repo_remote, '')  AS repo_remote,
                       coalesce(d.project_name, '') AS project_name,
                       coalesce(nullif(coalesce(nullif(d.jwt_email, ''), nullif(d.dev_name_max, '')), ''),
                                nullif(d.developer_name, ''), '') AS user_email,
                       d.started_at,
                       d.last_event_at,
                       d.activity_started_at,
                       d.ended_at,
                       coalesce(d.first_prompt, '') AS first_prompt,
                       coalesce(d.session_title, '') AS session_title
                FROM session_dims d
                WHERE d.started_at IS NOT NULL AND {_STARTED_IN_WINDOW}
            ) x{where}
        )"""

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
        """Strip an optional leading LiteLLM Bedrock provider wrapper ('bedrock/'), a routing
        prefix ('converse/' or 'invoke/'), an optional region token plus 'anthropic.' provider
        prefix (e.g. 'us.anthropic.', 'us-gov-west-1.anthropic.', 'anthropic.'), and a trailing
        Bedrock inference-profile version tag ('-v1:0'-style) when it immediately follows an
        8-digit date stamp, e.g. '-20251001-v1:0'.
        CR-002: LiteLLM's own Bedrock provider convention nests an API-path marker behind the
        'bedrock/' provider prefix (e.g. 'bedrock/converse/...'); not yet observed in this
        telemetry source, but a documented real pattern, so the wrapper is stripped defensively.
        The routing-prefix and provider-prefix strips are case-insensitive (CR-001: a routing
        layer that emits a different case for these tokens must still normalize the same way).
        Never touches a date stamp on its own, a model's own version dash inside the id,
        or a trailing '-v\\d+:\\d+' shape that is not preceded by that date stamp.
        """
        without_routing_prefix = f"regexp_replace({column}, '^(bedrock/)?(converse|invoke)/', '', 'i')"
        without_prefix = f"regexp_replace({without_routing_prefix}, '^([a-z0-9-]+\\.)?anthropic\\.', '', 'i')"
        return f"regexp_replace({without_prefix}, '(\\d{{8}})-v\\d+:\\d+$', '\\1')"

    # ── cost / token facts (cost_daily) ──────────────────────────────────────

    async def get_cost_kpis(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT count(DISTINCT session_id)          AS total_sessions,
               sum(cost_usd)                       AS total_cost_usd,
               sum(input_tokens)::bigint           AS total_input_tokens,
               sum(output_tokens)::bigint          AS total_output_tokens,
               sum(cache_read_tokens)::bigint      AS total_cache_read_tokens,
               sum(cache_creation_tokens)::bigint  AS total_cache_creation_tokens,
               sum(api_call_count)::bigint         AS total_api_calls
        FROM cost_daily
        WHERE true{self._scope(f)}""",
            window_params(f),
        )

    async def get_model_breakdown(self, f: LocalAnalyticsFilter) -> Rows:
        model_name = self._normalized_model_name("d.model_name")
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
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
        every money value is priced from. The 5m / 1h sums are NULL when no row of the group has a split."""
        model_name = self._normalized_model_name("c.model_name")
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT c.session_id,
               c.model_name                                 AS model_name,
               {model_name}                                 AS normalized_model_name,
               sum(c.input_tokens)::bigint                  AS input_tokens,
               sum(c.output_tokens)::bigint                 AS output_tokens,
               sum(c.cache_read_tokens)::bigint             AS cache_read_tokens,
               sum(c.cache_creation_tokens)::bigint         AS cache_creation_tokens,
               sum(c.cache_creation_5m_tokens)::bigint      AS cache_creation_5m_tokens,
               sum(c.cache_creation_1h_tokens)::bigint      AS cache_creation_1h_tokens
        FROM {_token_rows(*_SEL_SESSIONS)}
        GROUP BY c.session_id, c.model_name""",
            window_params(f),
        )

    async def get_cost_by_user(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT {_DEVELOPER} AS developer_name, array_agg(DISTINCT c.session_id) AS session_ids
        FROM {_cost_rows("c.session_id IN (SELECT session_id FROM sel)")} JOIN sel s ON s.session_id = c.session_id
        GROUP BY 1
        ORDER BY ({_DEVELOPER}) {_C}""",
            window_params(f),
        )

    async def get_users(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT {_DEVELOPER}                             AS developer_name,
               count(DISTINCT c.session_id)             AS session_count,
               sum(c.input_tokens)::bigint              AS input_tokens,
               sum(c.output_tokens)::bigint             AS output_tokens,
               sum(c.cache_read_tokens)::bigint         AS cache_read_tokens,
               sum(c.cache_creation_tokens)::bigint     AS cache_creation_tokens,
               sum(c.cost_usd)                          AS cost_usd,
               {_TOP_MODEL}                             AS top_model,
               array_agg(DISTINCT c.session_id)         AS session_ids
        FROM {_cost_rows("c.session_id IN (SELECT session_id FROM sel)")}
             JOIN sel s ON s.session_id = c.session_id
        GROUP BY 1
        ORDER BY cost_usd DESC, ({_DEVELOPER}) {_C}""",
            window_params(f),
        )

    async def get_users_daily_activity(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT {_DEVELOPER} AS developer_name, c.day AS day, count(DISTINCT c.session_id) AS session_count
        FROM {_cost_rows("c.session_id IN (SELECT session_id FROM sel)")} JOIN sel s ON s.session_id = c.session_id
        GROUP BY 1, 2
        ORDER BY ({_DEVELOPER}) {_C}, c.day""",
            window_params(f),
        )

    async def get_users_last_active(self, f: LocalAnalyticsFilter) -> Rows:
        """Latest dimension hook event in the window per user.

        `session_dims.last_event_at` is already the latest such event, so raw hook events
        are read only for sessions that were active again after the window end.
        """
        params = window_params(f)
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)},
        w AS (
            SELECT {_session_developer(_LINES_EMAIL)}
                       AS developer_name,
                   CASE WHEN d.last_event_at <= $end_dt::timestamptz THEN d.last_event_at
                        ELSE (SELECT max(h.ts) FROM hook_events h
                              WHERE h.session_id = s.session_id AND h.event_type = ANY({_DIM_TYPES})
                                AND h.{_TS})
                   END AS last_in_window
            FROM sel s JOIN session_dims d ON d.session_id = s.session_id
        )
        SELECT developer_name, max(last_in_window) AS last_active
        FROM w
        WHERE last_in_window IS NOT NULL
        GROUP BY 1""",
            params,
        )

    # ── line facts (lines_daily) ─────────────────────────────────────────────

    async def get_lines_totals(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT sum(lines_added)::bigint AS lines_added, sum(lines_removed)::bigint AS lines_removed
        FROM lines_daily
        WHERE true{self._scope(f)}""",
            window_params(f),
        )

    async def get_lines_daily(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT day, (sum(lines_added) - sum(lines_removed))::bigint AS net_lines
        FROM lines_daily
        WHERE true{self._scope(f)}
        GROUP BY day
        ORDER BY day""",
            window_params(f),
        )

    async def get_lines_by_user(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT {_session_developer("l.session_email")} AS developer_name,
               (sum(l.lines_added) - sum(l.lines_removed))::bigint AS net_lines
        FROM (SELECT l.*, {_session_email("l")} FROM lines_daily l
              WHERE l.session_id IN (SELECT session_id FROM sel)) l
             JOIN sel s ON s.session_id = l.session_id
        GROUP BY 1""",
            window_params(f),
        )

    async def get_lines_by_session(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
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
        WITH {self._sessions_cte(f)}
        SELECT session_id, sum(turns)::bigint AS turns
        FROM turns_daily
        WHERE true{self._scope(f)}
        GROUP BY session_id""",
            window_params(f),
        )

    async def get_file_facts_by_session(self, f: LocalAnalyticsFilter) -> Rows:
        """Distinct files across the session's days, counted in two hash steps (not count(DISTINCT))."""
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)},
        t AS (
            SELECT session_id, sum(tool_calls)::bigint AS tool_calls
            FROM tool_facts_daily
            WHERE true{self._scope(f)}
            GROUP BY session_id
        ),
        per_file AS (
            SELECT session_id, file_path, bool_or(is_written) AS written, bool_or(is_edited) AS edited
            FROM session_files_daily
            WHERE true{self._scope(f)}
            GROUP BY session_id, file_path
        ),
        fl AS (
            SELECT session_id,
                   count(*) FILTER (WHERE written OR edited) AS files_changed,
                   count(*) FILTER (WHERE written) AS files_written,
                   count(*) FILTER (WHERE edited)  AS files_edited
            FROM per_file
            GROUP BY session_id
        )
        SELECT t.session_id,
               coalesce(fl.files_changed, 0) AS files_changed,
               coalesce(fl.files_written, 0) AS files_written,
               coalesce(fl.files_edited, 0)  AS files_edited,
               t.tool_calls
        FROM t LEFT JOIN fl ON fl.session_id = t.session_id""",
            window_params(f),
        )

    async def get_tool_success_by_session(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT session_id, sum(calls)::bigint AS tool_calls, sum(success)::bigint AS tool_calls_success
        FROM invocations_hourly
        WHERE kind = 1 AND session_id <> ''{self._scope(f)}
        GROUP BY session_id""",
            window_params(f),
        )

    async def get_tool_usage(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
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
        summary, its OTel user prompts from the rollup. The window is the one of `sel`, as everywhere."""
        params = window_params(f)
        params["builtin_commands"] = list(BUILTIN_COMMAND_NAMES)
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT CASE kind WHEN 2 THEN 'skill' WHEN 3 THEN 'agent' ELSE 'command' END AS kind,
               name,
               sum(c)::bigint AS count
        FROM (
            SELECT kind, name, calls AS c FROM invocations_hourly
            WHERE kind IN (2, 3){self._scope(f)}
            UNION ALL
            SELECT kind, name, calls AS c FROM invocations_hourly
            WHERE
              kind = 4
              AND {_no_summary("invocations_hourly.session_id")}
              AND name <> ALL($builtin_commands::text[]){self._scope(f)}
            UNION ALL
            SELECT 4, e.cmd #>> '{{}}', 1 FROM session_dims, jsonb_array_elements(session_dims.commands) AS e(cmd)
            WHERE session_dims.summary_ts IS NOT NULL AND jsonb_typeof(e.cmd) = 'string'
              AND e.cmd #>> '{{}}' <> '' AND (e.cmd #>> '{{}}') <> ALL($builtin_commands::text[]){self._scope(f)}
        ) u
        GROUP BY 1, 2""",
            params,
        )

    async def get_skill_names_by_session(self, session_ids: list[str]) -> Rows:
        """Distinct skill names and subagent types per session (the `skill_names` key), for any number of
        sessions: the same names the delivery framework is derived from (`CLASSIFIER_NAMES_SQL`), so a session
        known only from hooks or a summary, or one that only dispatched subagents, counts too.
        A session without such a name has an empty list."""
        if not session_ids:
            return []
        return await self._q(
            f"""
        SELECT sd.session_id, coalesce({CLASSIFIER_NAMES_SQL}, ARRAY[]::text[]) AS skill_names
        FROM session_dims sd
        WHERE sd.session_id = ANY($session_ids::text[])
        ORDER BY sd.session_id {_C}""",
            {"session_ids": list(dict.fromkeys(session_ids))},
        )

    # ── session-level aggregates ─────────────────────────────────────────────

    async def get_session_durations(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)},{self._activity_cte()}
        SELECT session_id, duration_ms
        FROM act""",
            window_params(f),
        )

    async def get_active_ms_by_session(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
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
            having.append("nullif(max(s.repository), '') ILIKE $search::text")
        if is_unattributed:
            having.append("nullif(max(s.repository), '') IS NULL")
        having_sql = ("\n        HAVING " + " AND ".join(having)) if having else ""
        params = window_params(f)
        if search:
            params["search"] = f"%{search.strip().lower()}%"
        group_col = session_col = "s.session_id" if include_costless else "c.session_id"
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)},{self._activity_cte()}
        SELECT {session_col} AS session_id,
               {_DEVELOPER}                                 AS developer_name,
               max(s.repository)                            AS repository,
               max(s.branch)                                AS branch,
               max(s.project_name)                          AS project_name,
               max(s.first_prompt)                          AS prompt,
               max(s.session_title)                         AS session_title,
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

    async def get_session_page(
        self,
        f: LocalAnalyticsFilter,
        sort_by: str,
        page: int,
        per_page: int,
        search: str | None = None,
        is_unattributed: bool = False,
    ) -> tuple[int, list[str]]:
        """One page of the session list, decided in SQL: `(total, session_ids)` of the sessions of `sel` that
        pass `search` (case-insensitive substring of the first prompt, repository or branch, `%`, `_` and `\\`
        matched literally) and `is_unattributed`, ordered by the `sort_by` key DESC then session_id ASC.
        `start_time` is the reported session start, `ctx_per_call` the cache-read tokens (totals-only subagent
        rows included) per API call, 0 without calls. `total` counts every such session, not the page."""
        if sort_by not in SESSION_PAGE_SORTS:
            raise ValueError(f"get_session_page cannot sort by {sort_by!r}; sorts: {SESSION_PAGE_SORTS}")
        if search and "\x00" in search:
            return 0, []  # PostgreSQL text cannot hold a NUL, so no session matches; the driver would reject it
        params = window_params(f)
        params["limit"] = per_page
        params["offset"] = min(page * per_page, _MAX_PAGE_OFFSET)
        conditions = ["true"]
        if search:
            conditions.append(
                "(s.first_prompt ILIKE $search::text ESCAPE '\\' OR s.repository ILIKE $search::text ESCAPE '\\'"
                " OR s.branch ILIKE $search::text ESCAPE '\\')"
            )
            params["search"] = "%" + _escape_like(search.strip().lower()) + "%"
        if is_unattributed:
            conditions.append("s.repository = ''")
        if sort_by == "start_time":
            ctes = f"{self._sessions_cte(f)},{self._activity_cte()}"
            joins = "JOIN act a ON a.session_id = s.session_id"
            sort_key = "a.session_start"
        else:
            ctes = self._sessions_cte(f)
            joins = f"""LEFT JOIN (SELECT session_id, sum(cache_read_tokens) AS cache_read, sum(api_call_count) AS calls
                       FROM cost_daily WHERE session_id IN (SELECT session_id FROM sel) GROUP BY session_id) c
                  ON c.session_id = s.session_id
             LEFT JOIN {_totals_join()} ON t.session_id = s.session_id"""
            sort_key = (
                "CASE WHEN coalesce(c.calls, 0) = 0 THEN 0::numeric ELSE "
                "round((coalesce(c.cache_read, 0) + coalesce(t.cache_read_tokens, 0)) / c.calls, 9) END"
            )
        rows = await self._q(
            f"""
        WITH {ctes},
        filtered AS (
            SELECT s.session_id, {sort_key} AS sort_key
            FROM sel s
                 {joins}
            WHERE {" AND ".join(conditions)}
        ),
        page AS (
            SELECT session_id, sort_key FROM filtered
            ORDER BY sort_key DESC, session_id {_C}
            LIMIT $limit::bigint OFFSET $offset::bigint
        )
        SELECT (SELECT count(*) FROM filtered)::bigint AS total,
               coalesce((SELECT array_agg(session_id ORDER BY sort_key DESC, session_id {_C}) FROM page),
                        ARRAY[]::text[]) AS session_ids""",
            params,
        )
        return int(rows[0]["total"]), list(rows[0]["session_ids"])

    async def get_session_start_times(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)},{self._activity_cte()}
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
        WITH {self._sessions_cte(f)}
        SELECT session_id, repository, branch, project_name FROM sel
        WHERE repository <> ''{search_clause}""",
            params,
        )

    # ── session detail ───────────────────────────────────────────────────────

    async def get_session_detail_meta(self, session_id: str) -> Rows:
        # The same range as the list, keyed on the cost row's session so a session without a
        # session_dims row still gets its earliest event as start.
        start, end = _activity_range("t")
        return await self._q(
            f"""
        WITH c AS (
                 -- a session with dimension events but no cost row is listed with cost_available false, so its
                 -- detail must resolve too
                 SELECT session_id FROM cost_daily WHERE session_id = $sid::text
                 UNION
                 SELECT session_id FROM session_dims WHERE session_id = $sid::text AND started_at IS NOT NULL
             ),
             h AS (
                 SELECT session_id, resource_id, ts FROM log_events WHERE session_id = $sid::text
                 UNION ALL
                 SELECT session_id, resource_id, ts FROM spans WHERE session_id = $sid::text
                 ORDER BY ts ASC, resource_id ASC
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
               coalesce(r.session_start, {_EPOCH})                                    AS started_at,  -- or 1970-01-01
               coalesce({_ms("r.session_end")} - {_ms("r.session_start")}, 0)         AS duration_ms,
               nullif(o.service_name, '')                                             AS harness_service_name
        FROM c
        LEFT JOIN session_dims d ON d.session_id = c.session_id
        CROSS JOIN LATERAL (
            SELECT {start} AS session_start, {end} AS session_end
            FROM (SELECT c.session_id, d.started_at, d.activity_started_at, d.last_event_at, d.ended_at) t
        ) r
        LEFT JOIN h ON h.session_id = c.session_id
        LEFT JOIN otel_resources o ON o.resource_id = h.resource_id""",
            {"sid": session_id},
        )

    async def get_session_detail_cost(self, session_id: str) -> Rows:
        return await self._q(
            f"""
        SELECT {_TOP_MODEL}                         AS model_name,
               sum(c.input_tokens)::bigint          AS input_tokens,
               sum(c.output_tokens)::bigint         AS output_tokens,
               sum(c.cache_read_tokens)::bigint     AS cache_read_tokens,
               sum(c.cache_creation_tokens)::bigint AS cache_creation_tokens
        FROM {_cost_rows("c.session_id = $sid::text")}""",
            {"sid": session_id},
        )

    async def get_session_detail_model_costs(self, session_id: str) -> Rows:
        """Token sums per raw model of one session, the empty model kept: the rows its money is priced from.
        The 5m / 1h sums are NULL when no row of the model has a split."""
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
        GROUP BY c.model_name""",
            {"sid": session_id},
        )

    async def get_session_detail_scalars(self, session_id: str) -> Rows:
        return await self._q(
            f"""
        SELECT
            (SELECT sum(lines_added) FROM lines_daily WHERE session_id = $sid::text)::bigint     AS lines_added,
            (SELECT sum(lines_removed) FROM lines_daily WHERE session_id = $sid::text)::bigint   AS lines_removed,
            (SELECT sum(active_ms_user) + sum(active_ms_cli)
               FROM active_time_daily WHERE session_id = $sid::text)::bigint                     AS active_ms,
            (SELECT sum(turns) FROM turns_daily WHERE session_id = $sid::text)::bigint           AS turns,
            (SELECT sum(tool_calls) FROM tool_facts_daily WHERE session_id = $sid::text)::bigint AS tool_call_count,
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
            )::bigint AS agent_count,
            -- tool_facts_daily already counts each skill invocation once; a dispatch adds only while its
            -- name has no kind-2 row, that is before the rollup has caught up with it.
            (coalesce((SELECT sum(skill_count) FROM tool_facts_daily WHERE session_id = $sid::text), 0)
             + (SELECT count(*) FROM hook_events
                WHERE session_id = $sid::text AND event_type = '{SKILL_DISPATCH_EVENT}' AND skill_name <> ''
                  AND octet_length(skill_name) <= {MAX_KEY_BYTES}
                  AND skill_name NOT IN (SELECT name FROM invocations_hourly
                                         WHERE kind = 2 AND session_id = $sid::text)))::bigint  AS skill_count""",
            {"sid": session_id},
        )

    async def get_session_detail_tools(self, session_id: str) -> Rows:
        return await self._q(
            f"""
        SELECT t.tool_name, count(*) AS call_count, count(*) FILTER (WHERE e.success = 'true') AS success_count
        FROM (SELECT tool_name, tool_use_id FROM spans
              WHERE span_kind = {_TOOL} AND session_id = $sid::text AND tool_name <> '') t
        LEFT JOIN (SELECT tool_use_id, max(success) AS success FROM spans
                   WHERE span_kind = {_EXEC} AND session_id = $sid::text AND tool_use_id <> ''
                   GROUP BY tool_use_id) e
               ON t.tool_use_id = e.tool_use_id AND t.tool_use_id <> ''
        GROUP BY t.tool_name
        ORDER BY call_count DESC, t.tool_name {_C}
        LIMIT 20""",
            {"sid": session_id},
        )

    async def get_session_detail_events(self, session_id: str) -> Rows:
        """API requests and tool calls of a session, in time order. A request's 5m / 1h cache-write split is NULL
        when its attributes carry none."""
        return await self._q(
            f"""
        SELECT * FROM (
            SELECT ts AS timestamp, 'api_request' AS event_type, coalesce(model, '') AS model_name,
                   coalesce(input_tokens, 0) AS input_tokens, coalesce(output_tokens, 0) AS output_tokens,
                   coalesce(cache_read_tokens, 0) AS cache_read_tokens,
                   coalesce(cache_creation_tokens, 0) AS cache_creation_tokens,
                   {_attr_ms("log_events", "cache_creation_5m_tokens")} AS cache_creation_5m_tokens,
                   {_attr_ms("log_events", "cache_creation_1h_tokens")} AS cache_creation_1h_tokens,
                   '' AS tool_name
            FROM log_events WHERE session_id = $sid::text AND event_kind = {_API_REQUEST}
            UNION ALL
            SELECT ts, 'tool_call', '', 0, 0, 0, 0, NULL, NULL, tool_name
            FROM spans WHERE session_id = $sid::text AND span_kind = {_TOOL} AND tool_name <> ''
        ) events
        ORDER BY timestamp, event_type {_C}, tool_name {_C}, model_name {_C}""",
            {"sid": session_id},
        )

    async def get_session_detail_dispatches(self, session_id: str) -> Rows:
        """Agent and skill dispatches (tool spans) plus slash-command skills (hook events).

        One row per skill invocation. A forked skill's row is its initiator's: the Skill call's span row
        (its agent.tool.end names the fork's agentId) or the slash command's dispatch (the fork shares its
        prompt_id, and its skill name unless it ran in the foreground without one); that row takes the fork's
        run (its SubagentStart to its SubagentStop) and cost by the fork's agentId, and the fork adds no row
        of its own. A fork with no initiator row is its own `command` row. Another skill's "real" duration
        runs to the end of its interaction.
        An agent's bar starts at its SubagentStart hook event, found by the agentId of its Agent call's
        result (`dispatch_start`); without one, at the call start (agent.tool.start); the launch ack when
        both are missing. Its run time runs from there to its own SubagentStop hook event, matched by
        agentId; it is NULL when that stop never arrived. The handler clamps it to the session end.
        The tool-execution span duration (`duration_ms`) is selected for the row shape but no agent row
        uses it: it closes when a background agent is launched. An agent dispatch's cost and tokens
        are an exact ID chain, not an estimate: the Agent tool call's hook event names the agentId the
        subagent was launched with; the subagent's LLM requests (llm_request spans) carry that agentId
        and are children of the span that groups its api_request events, so those events are summed.
        A dispatch with no agentId at all (an inline skill, or an agent from telemetry without it) falls back
        to its own slice of the parent span's API requests: from its own start to the next strictly later
        sibling start under the same parent (or unbounded for the last one), split equally among the
        dispatches that start together, so dispatches sharing a parent never each show the identical,
        undivided sum. An agent or fork whose
        agentId is known but has no API requests gets NULL: its parent span is the main thread, whose
        cost is not the subagent's.
        Each row carries `models`, the per-model token sums of the API requests it is priced from (empty
        when it has none), and a parent-window share its `ties`: how many dispatches split that money.
        """

        # each predicate selects the API requests `a` that both the token/cost sums and the per-model sums read
        def agent_requests(a: str) -> str:
            return f"{a}.span_id IN (SELECT ag.parent_span_id FROM agent_groups ag WHERE ag.agent_id = g.agent_id)"

        def window_requests(a: str) -> str:
            return (
                f"{a}.span_id = w.parent_span_id AND {a}.ts >= w.start_ts"
                f" AND (w.next_start IS NULL OR {a}.ts < w.next_start)"
            )

        rows = await self._q(
            f"""
        WITH t AS (
            SELECT coalesce(subagent_type, '') AS subagent_type, coalesce(skill_name, '') AS skill_name,
                   tool_use_id, ts AS span_start, parent_span_id, span_id
            FROM spans s
            WHERE span_kind = {_TOOL} AND session_id = $sid::text AND (subagent_type <> '' OR skill_name <> '')
              -- an Agent or Skill call with no evidence it ran is denied, not a dispatch. Ingest stores an absent
              -- subagent_type as NULL, so a skill row is told apart with coalesce, not = ''.
              AND (CASE WHEN coalesce(s.subagent_type, '') <> '' THEN {executed_tool_span("s", "Agent")}
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
            -- each Agent tool call's result: the launch ack of a background agent, or the return of a foreground one
            SELECT DISTINCT ON (tool_use_id) tool_use_id, ts,
                   {tool_response_agent_id("attrs->>'tool_response'")} AS agent_id
            FROM hook_events
            WHERE session_id = $sid::text AND event_type = 'agent.tool.end' AND tool_name = 'Agent'
              AND tool_use_id <> ''
            ORDER BY tool_use_id, ts DESC
        ), agent_start AS (
            -- when each Agent tool call started
            SELECT tool_use_id, min(ts) AS ts
            FROM hook_events
            WHERE session_id = $sid::text AND event_type = 'agent.tool.start' AND tool_name = 'Agent'
              AND tool_use_id <> ''
            GROUP BY tool_use_id
        ), forked AS (
            -- when each subagent started, from its own earliest SubagentStart hook event (the typed agent_id column)
            SELECT agent_id, min(ts) AS ts
            FROM hook_events
            WHERE session_id = $sid::text AND event_type = 'agent.subagent.start' AND agent_id <> ''
            GROUP BY agent_id
        ), launched AS (
            -- the agentId each Agent tool call launched, and when its run began: its SubagentStart, for background
            -- and foreground agents alike; the call start when that is missing; the ack only when both are (a
            -- background agent's launch ack arrives seconds later, a foreground agent's result after its stop)
            SELECT e.tool_use_id, coalesce(f.ts, s.ts, e.ts) AS launch_ts, e.agent_id
            FROM agent_end e
            LEFT JOIN agent_start s ON s.tool_use_id = e.tool_use_id
            LEFT JOIN forked f ON f.agent_id = e.agent_id
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
            -- a dispatch whose prompt_id holds another dispatch pairs with none
            SELECT d.ts, d.skill_name, df.agent_id, df.ts AS fork_ts
            FROM hook_events d
            LEFT JOIN LATERAL (
                SELECT f.agent_id, f.ts
                FROM hook_events f
                WHERE f.session_id = d.session_id AND f.agent_id <> '' AND {dispatch_fork_candidate("f")}
                  AND f.prompt_id = d.prompt_id AND (f.skill_name = d.skill_name OR coalesce(f.skill_name, '') = '')
                  AND f.ts >= d.ts
                  AND {is_claude_code('d')} AND d.prompt_id <> ''
                  AND {sole_dispatch_of_prompt('d')}
                ORDER BY coalesce(f.skill_name, '') = '', f.ts, f.ingest_seq
                LIMIT 1
            ) df ON true
            WHERE d.session_id = $sid::text AND d.event_type = '{SKILL_DISPATCH_EVENT}' AND d.skill_name <> ''
              AND octet_length(d.skill_name) <= {MAX_KEY_BYTES}
        ), idle AS (
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
            -- idle time between its runs, so that its last stop minus its first start is the sum of its runs
            SELECT x.agent_id, max(x.ts) - coalesce(i.idle, interval '0') AS ended_at
            FROM hook_events x
            LEFT JOIN idle i ON i.agent_id = x.agent_id
            WHERE x.session_id = $sid::text AND x.event_type = 'agent.subagent.stop' AND x.agent_id IS NOT NULL
            GROUP BY x.agent_id, i.idle
        ), agent_groups AS (
            -- the span grouping a subagent's api_request events, named by its LLM requests' agentId
            SELECT DISTINCT attrs->>'agent_id' AS agent_id, parent_span_id
            FROM spans
            WHERE session_id = $sid::text AND span_kind = {_LLM_REQUEST}
              AND attrs ? 'agent_id' AND parent_span_id IS NOT NULL
        ), agent_usage AS (
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
        ), fallback_start AS (
            -- every dispatch's own start under its parent, agent or skill alike (same formula the
            -- final SELECT displays as dispatch_start), used only to bound sibling_window below
            SELECT t.span_id AS fallback_id, t.parent_span_id,
                   coalesce(CASE WHEN t.subagent_type <> '' THEN la.launch_ts END, t.span_start) AS start_ts,
                   (la.agent_id IS NULL AND sf.agent_id IS NULL) AS takes_parent
            FROM t
            LEFT JOIN launched la ON la.tool_use_id = t.tool_use_id AND t.tool_use_id <> ''
            LEFT JOIN skill_fork sf ON sf.tool_use_id = t.tool_use_id AND t.tool_use_id <> '' AND t.skill_name <> ''
        ), sibling_window AS (
            -- each dispatch's own slice of its parent's API-request stream: from its own start to the next
            -- strictly later sibling start under the same parent (or unbounded for the last one); the dispatches
            -- with no resolvable agentId that start together split their slice equally (one with an agentId takes
            -- its own requests, not a share), so none shows the identical, undivided sum a sibling shows.
            SELECT fallback_id, parent_span_id, start_ts,
                   min(start_ts) OVER (PARTITION BY parent_span_id ORDER BY start_ts
                                       RANGE BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING EXCLUDE GROUP) AS next_start,
                   count(*) FILTER (WHERE takes_parent) OVER (PARTITION BY parent_span_id, start_ts) AS ties
            FROM fallback_start
        ), parent_usage AS (
            -- dispatches with no agentId: their own windowed slice of the parent's API requests (see sibling_window)
            SELECT w.fallback_id,
                   sum(coalesce(l.cost_usd, 0))::numeric / w.ties              AS cost_usd,
                   sum(coalesce(l.input_tokens, 0))::numeric / w.ties          AS input_tokens,
                   sum(coalesce(l.output_tokens, 0))::numeric / w.ties         AS output_tokens,
                   sum(coalesce(l.cache_read_tokens, 0))::numeric / w.ties     AS cache_read_tokens,
                   sum(coalesce(l.cache_creation_tokens, 0))::numeric / w.ties AS cache_creation_tokens,
                   w.ties,
                   {_model_sums(window_requests("r"))} AS models
            FROM sibling_window w
            JOIN log_events l
                 ON {window_requests("l")} AND l.session_id = $sid::text AND l.event_kind = {_API_REQUEST}
            WHERE w.ties > 0
            GROUP BY w.fallback_id, w.ties, w.parent_span_id, w.start_ts, w.next_start
        )
        SELECT * FROM (
        SELECT t.subagent_type, t.skill_name, 0 AS is_slash_command,
               -- an agent's bar starts when its run began (see launched); a skill's at its tool span
               coalesce(CASE WHEN t.subagent_type <> '' THEN la.launch_ts END, t.span_start) AS dispatch_start,
               coalesce(ex.duration_ms, 0)::bigint AS duration_ms,
               -- a forked skill's: its fork's run, NULL when it never stopped; another skill's: to the end of its
               -- interaction, NULL when it has none
               CASE WHEN sf.agent_id IS NOT NULL
                    THEN CASE WHEN st.ended_at >= fs.ts
                              THEN (extract(epoch FROM st.ended_at - fs.ts) * 1000)::bigint END
                    WHEN ia.ts IS NOT NULL
                    THEN (({_ns("ia.ts")} + coalesce(ia.duration_ns, 0) - {_ns("t.span_start")})
                          / 1000000)::bigint END AS real_duration_ms,
               -- the subagent's run time: from its start to its stop; NULL when it never stopped
               CASE WHEN st.ended_at >= la.launch_ts
                    THEN (extract(epoch FROM st.ended_at - la.launch_ts) * 1000)::bigint END AS subagent_duration_ms,
               coalesce(au.cost_usd, pu.cost_usd)::float8                AS cost_usd,
               coalesce(au.input_tokens, pu.input_tokens)::bigint        AS input_tokens,
               coalesce(au.output_tokens, pu.output_tokens)::bigint      AS output_tokens,
               coalesce(au.cache_read_tokens, pu.cache_read_tokens)::bigint AS cache_read_tokens,
               coalesce(au.cache_creation_tokens, pu.cache_creation_tokens)::bigint AS cache_creation_tokens,
               coalesce(au.models, pu.models) AS models,
               pu.ties
        FROM t
        LEFT JOIN ex ON ex.tool_use_id = t.tool_use_id AND t.tool_use_id <> ''
        LEFT JOIN ia ON ia.span_id = t.parent_span_id
        LEFT JOIN launched la ON la.tool_use_id = t.tool_use_id AND t.tool_use_id <> ''
        LEFT JOIN skill_fork sf ON sf.tool_use_id = t.tool_use_id AND t.tool_use_id <> '' AND t.skill_name <> ''
        LEFT JOIN forked fs ON fs.agent_id = sf.agent_id
        LEFT JOIN stopped st ON st.agent_id = coalesce(la.agent_id, sf.agent_id)
        LEFT JOIN agent_usage au ON au.agent_id = coalesce(la.agent_id, sf.agent_id)
        -- only without an agentId: a known agent or fork with no API requests has no cost, not the main thread's
        LEFT JOIN parent_usage pu ON pu.fallback_id = t.span_id AND la.agent_id IS NULL AND sf.agent_id IS NULL
        UNION ALL
        -- A forked skill (a skill-tagged agent.subagent.start) whose initiator has no row of its own: no
        -- Skill span names it by agentId and no slash-command dispatch paired with it (an older CLI, or a
        -- Skill call seen only as hook events). Its run time and cost come by its typed agent_id, as an
        -- agent's do. real_duration_ms mirrors subagent_duration_ms: the handler reads real_duration_ms
        -- for this row's "command" kind.
        SELECT '', h.skill_name, 1, h.ts, 0,
               CASE WHEN st.ended_at >= h.ts
                    THEN (extract(epoch FROM st.ended_at - h.ts) * 1000)::bigint END,
               CASE WHEN st.ended_at >= h.ts
                    THEN (extract(epoch FROM st.ended_at - h.ts) * 1000)::bigint END AS subagent_duration_ms,
               au.cost_usd::float8 AS cost_usd, au.input_tokens::bigint AS input_tokens,
               au.output_tokens::bigint AS output_tokens, au.cache_read_tokens::bigint AS cache_read_tokens,
               au.cache_creation_tokens::bigint AS cache_creation_tokens, au.models, NULL
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
        -- a skill that ran in the main thread has no cost or duration of its own (0). Never matched against a
        -- TOOL span by name.
        SELECT '', df.skill_name, 1, df.ts, 0,
               CASE WHEN df.agent_id IS NULL THEN 0
                    WHEN st.ended_at >= df.fork_ts THEN (extract(epoch FROM st.ended_at - df.fork_ts) * 1000)::bigint
               END,
               NULL,
               CASE WHEN df.agent_id IS NULL THEN 0 ELSE au.cost_usd END::float8,
               CASE WHEN df.agent_id IS NULL THEN 0 ELSE au.input_tokens END::bigint,
               CASE WHEN df.agent_id IS NULL THEN 0 ELSE au.output_tokens END::bigint,
               CASE WHEN df.agent_id IS NULL THEN 0 ELSE au.cache_read_tokens END::bigint,
               CASE WHEN df.agent_id IS NULL THEN 0 ELSE au.cache_creation_tokens END::bigint,
               au.models, NULL
        FROM dispatch_fork df
        LEFT JOIN stopped st ON st.agent_id = df.agent_id
        LEFT JOIN agent_usage au ON au.agent_id = df.agent_id
        ) dispatches
        ORDER BY dispatch_start, is_slash_command, subagent_type {_C}, skill_name {_C}""",
            {"sid": session_id},
        )
        for row in rows:
            row["models"] = json.loads(row["models"]) if row["models"] is not None else []
        return rows
