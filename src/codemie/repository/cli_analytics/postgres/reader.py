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

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import Rows
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.vocabulary import DIMENSION_HOOK_TYPES, SKILL_DISPATCH_EVENT, EventKind, SpanKind

_NAMED_PARAM = re.compile(r"\$([a-z_][a-z0-9_]*)")
_EPOCH = "'1970-01-01 00:00:00+00'::timestamptz"
_TOOL, _EXEC, _INTERACTION = int(SpanKind.TOOL), int(SpanKind.TOOL_EXECUTION), int(SpanKind.INTERACTION)
_API_REQUEST = int(EventKind.API_REQUEST)
_DIM_TYPES = "ARRAY[" + ", ".join(f"'{t}'" for t in DIMENSION_HOOK_TYPES) + "]::text[]"
_C = 'COLLATE "C"'

_STARTED_IN_WINDOW = "started_at BETWEEN $start_dt::timestamptz AND $end_dt::timestamptz"
# Clips the raw hook events that decide a session's last dimension event (not session membership).
_TS = "ts BETWEEN $start_dt::timestamptz AND $end_dt::timestamptz"
# Slash commands have one source per session: a session with a summary counts only the `commands`
# of its summary, so its OTel `user_prompt` records (kind 4 of the rollup) add nothing.


def _no_summary(column: str) -> str:
    return f"NOT EXISTS (SELECT 1 FROM session_dims sd WHERE sd.session_id = {column} AND sd.summary_ts IS NOT NULL)"


_DEVELOPER = "coalesce(nullif(s.user_email, ''), nullif(c.user_email, ''), 'unknown')"
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
    """`cost_daily` rows of the selection, each with the calls of its original grain (see _TOP_MODEL)."""
    return f"(SELECT c.*, {_GRAIN_CALLS} FROM cost_daily c WHERE {where} WINDOW w AS (PARTITION BY {_GRAIN})) c"


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
    return params


def _ms(column: str) -> str:
    """Epoch milliseconds, truncated: dateDiff('millisecond', a, b) is _ms(b) - _ms(a)."""
    return f"floor(extract(epoch FROM {column}) * 1000)::bigint"


def _ns(column: str) -> str:
    return f"((extract(epoch FROM {column}) * 1000000)::bigint * 1000)"


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
                       coalesce(d.first_prompt, '') AS first_prompt
                FROM session_dims d
                WHERE d.started_at IS NOT NULL AND {_STARTED_IN_WINDOW}
            ) x{where}
        )"""

    @staticmethod
    def _scope(f: LocalAnalyticsFilter, column: str = "session_id") -> str:
        """Restrict a fact query to the sessions in `sel` (started in the window, passing the filters)."""
        return f"\n              AND {column} IN (SELECT session_id FROM sel)"

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
               sum(cache_creation_tokens)::bigint  AS total_cache_creation_tokens
        FROM cost_daily
        WHERE true{self._scope(f)}""",
            window_params(f),
        )

    async def get_model_breakdown(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT d.model_name                                   AS model_name,
               count(DISTINCT d.session_id)                   AS session_count,
               sum(d.cost_usd)                                AS cost_usd,
               sum(d.cache_read_tokens)::bigint               AS cache_read_tokens,
               sum(d.input_tokens + d.output_tokens + d.cache_read_tokens + d.cache_creation_tokens)::bigint
                                                              AS total_tokens
        FROM cost_daily d
        WHERE true{self._scope(f, "d.session_id")}
        GROUP BY d.model_name
        HAVING d.model_name <> ''
        ORDER BY session_count DESC, cost_usd DESC, d.model_name {_C}""",
            window_params(f),
        )

    async def get_cost_by_user(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT {_DEVELOPER} AS developer_name, sum(c.cost_usd) AS cost_usd
        FROM cost_daily c JOIN sel s ON s.session_id = c.session_id
        GROUP BY 1
        ORDER BY cost_usd DESC, ({_DEVELOPER}) {_C}""",
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
        FROM cost_daily c JOIN sel s ON s.session_id = c.session_id
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
            SELECT s.user_email,
                   d.developer_name,
                   CASE WHEN d.last_event_at <= $end_dt::timestamptz THEN d.last_event_at
                        ELSE (SELECT max(h.ts) FROM hook_events h
                              WHERE h.session_id = s.session_id AND h.event_type = ANY({_DIM_TYPES})
                                AND h.{_TS})
                   END AS last_in_window
            FROM sel s JOIN session_dims d ON d.session_id = s.session_id
        )
        SELECT coalesce(nullif(user_email, ''), nullif(developer_name, ''), 'unknown') AS developer_name,
               max(last_in_window) AS last_active
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
        SELECT coalesce(nullif(s.user_email, ''), nullif(l.user_email, ''), 'unknown') AS developer_name,
               (sum(l.lines_added) - sum(l.lines_removed))::bigint                  AS net_lines
        FROM lines_daily l JOIN sel s ON s.session_id = l.session_id
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
                   count(*)                       AS files_changed,
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
            WHERE kind = 4 AND {_no_summary("invocations_hourly.session_id")}{self._scope(f)}
            UNION ALL
            SELECT 4, e.cmd #>> '{{}}', 1 FROM session_dims, jsonb_array_elements(session_dims.commands) AS e(cmd)
            WHERE session_dims.summary_ts IS NOT NULL AND jsonb_typeof(e.cmd) = 'string'
              AND e.cmd #>> '{{}}' <> ''{self._scope(f)}
        ) u
        GROUP BY 1, 2""",
            window_params(f),
        )

    async def get_skill_names_by_session(self, session_ids: list[str]) -> Rows:
        """Distinct activated skills per session (session_skills), for any number of sessions."""
        if not session_ids:
            return []
        return await self._q(
            f"""
        SELECT session_id, array_agg(skill_name ORDER BY skill_name {_C}) AS skill_names
        FROM session_skills
        WHERE session_id = ANY($session_ids::text[])
        GROUP BY session_id""",
            {"session_ids": list(dict.fromkeys(session_ids))},
        )

    # ── session-level aggregates ─────────────────────────────────────────────

    async def get_session_durations(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT session_id, {_ms("last_event_at")} - {_ms("started_at")} AS duration_ms
        FROM sel""",
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
    ) -> Rows:
        having = []
        if search:
            having.append("nullif(max(s.repository), '') ILIKE $search::text")
        if is_unattributed:
            having.append("nullif(max(s.repository), '') IS NULL")
        having_sql = ("\n        HAVING " + " AND ".join(having)) if having else ""
        params = window_params(f)
        if search:
            params["search"] = f"%{search.strip().lower()}%"
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT c.session_id,
               {_DEVELOPER}                                 AS developer_name,
               max(s.repository)                            AS repository,
               max(s.branch)                                AS branch,
               max(s.project_name)                          AS project_name,
               max(s.first_prompt)                          AS prompt,
               max(s.started_at)                            AS started_at,
               max(s.last_event_at)                         AS last_event_at,
               {_TOP_MODEL}                                 AS model_name,
               sum(c.cost_usd)                              AS cost_usd,
               sum(c.input_tokens)::bigint                  AS input_tokens,
               sum(c.output_tokens)::bigint                 AS output_tokens,
               sum(c.cache_read_tokens)::bigint             AS cache_read_tokens,
               sum(c.cache_creation_tokens)::bigint         AS cache_creation_tokens
        FROM {_cost_rows("c.session_id IN (SELECT session_id FROM sel)")}
             JOIN sel s ON s.session_id = c.session_id
        GROUP BY c.session_id, 2{having_sql}""",
            params,
        )

    async def get_session_start_times(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT started_at FROM sel""",
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
        return await self._q(
            f"""
        WITH c AS (SELECT DISTINCT session_id FROM cost_daily WHERE session_id = $sid::text)
        SELECT c.session_id,
               coalesce(nullif(coalesce(nullif(d.jwt_email, ''), nullif(d.dev_name_max, '')), ''),
                        nullif(CASE WHEN d.started_at IS NOT NULL THEN d.developer_name END, ''),
                        'unknown')                                                    AS developer_name,
               coalesce(nullif(d.repo_remote, ''), nullif(d.repository, ''))           AS repository,
               nullif(d.branch, '')                                                   AS branch,
               nullif(d.project_name, '')                                             AS project_name,
               nullif(d.first_prompt, '')                                             AS prompt,
               coalesce(d.started_at, {_EPOCH})                                       AS started_at,  -- or 1970-01-01
               coalesce({_ms("d.last_event_at")} - {_ms("d.started_at")}, 0)          AS duration_ms
        FROM c LEFT JOIN session_dims d ON d.session_id = c.session_id""",
            {"sid": session_id},
        )

    async def get_session_detail_cost(self, session_id: str) -> Rows:
        return await self._q(
            f"""
        SELECT {_TOP_MODEL}                         AS model_name,
               sum(c.cost_usd)                      AS cost_usd,
               sum(c.input_tokens)::bigint          AS input_tokens,
               sum(c.output_tokens)::bigint         AS output_tokens,
               sum(c.cache_read_tokens)::bigint     AS cache_read_tokens,
               sum(c.cache_creation_tokens)::bigint AS cache_creation_tokens
        FROM {_cost_rows("c.session_id = $sid::text")}""",
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
            (SELECT sum(agent_count) FROM tool_facts_daily WHERE session_id = $sid::text)::bigint AS agent_count,
            (coalesce((SELECT sum(skill_count) FROM tool_facts_daily WHERE session_id = $sid::text), 0)
             + (SELECT count(*) FROM hook_events
                WHERE session_id = $sid::text AND event_type = '{SKILL_DISPATCH_EVENT}' AND skill_name <> ''
                  AND skill_name NOT IN (SELECT skill_name FROM spans
                                         WHERE span_kind = {_TOOL} AND session_id = $sid::text
                                           AND skill_name <> '')))::bigint                     AS skill_count""",
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
        """API requests and tool calls of a session, in time order."""
        return await self._q(
            f"""
        SELECT * FROM (
            SELECT ts AS timestamp, 'api_request' AS event_type, coalesce(model, '') AS model_name,
                   coalesce(input_tokens, 0) AS input_tokens, coalesce(output_tokens, 0) AS output_tokens,
                   coalesce(cache_read_tokens, 0) AS cache_read_tokens, coalesce(cost_usd, 0)::float8 AS cost_usd,
                   '' AS tool_name
            FROM log_events WHERE session_id = $sid::text AND event_kind = {_API_REQUEST}
            UNION ALL
            SELECT ts, 'tool_call', '', 0, 0, 0, 0::float8, tool_name
            FROM spans WHERE session_id = $sid::text AND span_kind = {_TOOL} AND tool_name <> ''
        ) events
        ORDER BY timestamp, event_type {_C}, tool_name {_C}, model_name {_C}""",
            {"sid": session_id},
        )

    async def get_session_detail_dispatches(self, session_id: str) -> Rows:
        """Agent and skill dispatches (tool spans) plus slash-command skills (hook events).

        Duration comes from the paired execution span; a skill's "real" duration runs to the
        end of its interaction; cost and tokens come from the API requests of the dispatch's
        parent span, from the dispatch start on.
        """
        return await self._q(
            f"""
        WITH t AS (
            SELECT coalesce(subagent_type, '') AS subagent_type, coalesce(skill_name, '') AS skill_name,
                   tool_use_id, ts AS span_start, parent_span_id
            FROM spans
            WHERE span_kind = {_TOOL} AND session_id = $sid::text AND (subagent_type <> '' OR skill_name <> '')
        ), ex AS (
            SELECT tool_use_id, max(duration_ns / 1000000) AS duration_ms FROM spans
            WHERE span_kind = {_EXEC} AND session_id = $sid::text AND tool_use_id <> ''
            GROUP BY tool_use_id
        ), ia AS (
            SELECT span_id, max(ts) AS ts, max(duration_ns) AS duration_ns FROM spans
            WHERE span_kind = {_INTERACTION} AND session_id = $sid::text
            GROUP BY span_id
        ), first_start AS (
            SELECT parent_span_id, min(span_start) AS min_span_start FROM t GROUP BY parent_span_id
        ), lr AS (
            -- API requests of the parent span, counted from the first dispatch on
            SELECT l.span_id,
                   sum(coalesce(l.cost_usd, 0))              FILTER (WHERE after) AS cost_usd,
                   sum(coalesce(l.input_tokens, 0))          FILTER (WHERE after) AS input_tokens,
                   sum(coalesce(l.output_tokens, 0))         FILTER (WHERE after) AS output_tokens,
                   sum(coalesce(l.cache_read_tokens, 0))     FILTER (WHERE after) AS cache_read_tokens,
                   sum(coalesce(l.cache_creation_tokens, 0)) FILTER (WHERE after) AS cache_creation_tokens
            FROM log_events l
            JOIN first_start m ON m.parent_span_id = l.span_id
            CROSS JOIN LATERAL (SELECT l.ts >= m.min_span_start AS after) x
            WHERE l.session_id = $sid::text AND l.event_kind = {_API_REQUEST} AND l.span_id IS NOT NULL
            GROUP BY l.span_id
        )
        SELECT * FROM (
        SELECT t.subagent_type, t.skill_name, 0 AS is_slash_command, t.span_start,
               coalesce(ex.duration_ms, 0)::bigint AS duration_ms,
               -- an interaction with no match counts as starting at 1970-01-01 (0 ns) and lasting 0 ns
               ((coalesce({_ns("ia.ts")}, 0) + coalesce(ia.duration_ns, 0) - {_ns("t.span_start")}) / 1000000)::bigint
                                                         AS real_duration_ms,
               coalesce(lr.cost_usd, 0)::float8          AS cost_usd,
               coalesce(lr.input_tokens, 0)::bigint      AS input_tokens,
               coalesce(lr.output_tokens, 0)::bigint     AS output_tokens,
               coalesce(lr.cache_read_tokens, 0)::bigint AS cache_read_tokens,
               coalesce(lr.cache_creation_tokens, 0)::bigint AS cache_creation_tokens
        FROM t
        LEFT JOIN ex ON ex.tool_use_id = t.tool_use_id AND t.tool_use_id <> ''
        LEFT JOIN ia ON ia.span_id = t.parent_span_id
        LEFT JOIN lr ON lr.span_id = t.parent_span_id
        UNION ALL
        SELECT '', skill_name, 1, ts, 0, 0, 0::float8, 0, 0, 0, 0
        FROM hook_events
        WHERE session_id = $sid::text AND event_type = '{SKILL_DISPATCH_EVENT}' AND skill_name <> ''
          AND skill_name NOT IN (SELECT skill_name FROM spans
                                 WHERE span_kind = {_TOOL} AND session_id = $sid::text AND skill_name <> '')
        ) dispatches
        ORDER BY span_start, is_slash_command, subagent_type {_C}, skill_name {_C}""",
            {"sid": session_id},
        )
