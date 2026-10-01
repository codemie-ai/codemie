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

"""ClickHouse queries backing the Local Analytics endpoints (the ClickHouse `CliAnalyticsReader`).

Source mapping (this backend, NOT the reference implementation's tables):

    cost / tokens / models      -> codemie_analytics.coding_agent_cost_daily
    lines added / removed       -> codemie_analytics.coding_agent_lines_daily
                                   (native claude_code.lines_of_code.count metric)
    active time                 -> codemie_analytics.coding_agent_active_time_daily
    turns / tools / files       -> codemie_analytics.coding_agent_traces
                                   (claude_code.interaction / .tool / .tool.execution spans)
    per-API-call events         -> codemie_analytics.coding_agent_logs (event_name='api_request')
    repo / branch / project /
    prompt / session identity   -> codemie_analytics.v_session_dimensions (+ v_session_email)

Design decisions (see ANALYTICS_DISCOVERY/local_analytics_implementation_plan.md §3):
  D1 session universe = sessions that made a priced API call (coding_agent_cost_daily).
  D2 repository key   = the git remote (owner/repo) when available, otherwise the
                        directory the session STARTED in.
  D3 turns            = claude_code.interaction spans.

Kept on purpose (PostgreSQL parity, public port/API): the `is_unattributed` repository filter of
get_session_cost_facts and the coalesce(..., 'unknown') developer fallbacks, which still cover a
windowed session whose resolved email is empty. Sessions without dimensions drop out via inner joins.

Window contract: a session is in scope iff v_session_dimensions.started_at is in [start_dt, end_dt]
(inclusive). The predicate lives once, in `_sessions_cte` (`sel`); every windowed fact query is
scoped or inner-joined to `sel` and carries no day/timestamp predicate, so an in-window session
contributes all its data. This matches the PostgreSQL reader. Tool/invocation queries scan the raw
traces/logs tables for those sessions (no ClickHouse hourly rollup).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter

QueryFn = Callable[[str, dict], Awaitable[list[dict]]]

# How long the raw tables keep rows (their TTL in config/clickhouse/schema.sql); the rollups keep 365 days.
RAW_TTL_DAYS = 90

# Collapses a stored `cwd` to a bare folder name. Handles both separators and the
# historical full-path rows written before ingest_router started normalising cwd
_TOOL_SPAN = "SpanName IN ('claude_code.tool', 'cursor.tool')"
_EXEC_SPAN = "SpanName IN ('claude_code.tool.execution', 'cursor.tool.execution')"
_INTERACTION_SPAN = "SpanName IN ('claude_code.interaction', 'cursor.interaction')"

# A session is in scope iff it started inside the window (bounds inclusive). Applied once, in `sel`.
_STARTED_IN_WINDOW = "started_at BETWEEN {start_dt:DateTime64(3)} AND {end_dt:DateTime64(3)}"

# clickhouse-connect sends query parameters as HTTP form fields, and ClickHouse rejects a
# field above http_max_field_value_size (131,072 bytes by default). A window's session ids
# are therefore sent in parts that stay well below it (defect D10 of the PostgreSQL storage
# design, which measured that about 3,250 ids fit).
SESSION_IDS_PARAM_BUDGET_BYTES = 100_000


def _session_id_parts(session_ids: list[str]) -> list[list[str]]:
    """Split ids so each part, rendered as ['id1','id2',...], fits the parameter budget.

    An id too long to fit even alone is left out: sent, it would fail its whole part.
    """
    parts: list[list[str]] = []
    current: list[str] = []
    size = 2  # the brackets
    for sid in dict.fromkeys(session_ids):
        cost = len(sid.encode()) + 3 + sid.count("'") + sid.count("\\")  # quotes, comma, escapes
        if 2 + cost > SESSION_IDS_PARAM_BUDGET_BYTES:
            continue
        if current and size + cost > SESSION_IDS_PARAM_BUDGET_BYTES:
            parts.append(current)
            current, size = [], 2
        current.append(sid)
        size += cost
    if current:
        parts.append(current)
    return parts


class ClickHouseCliAnalyticsReader:
    """Read-only ClickHouse access for the Local Analytics endpoints."""

    def __init__(self, query_fn: QueryFn) -> None:
        self._q = query_fn

    # ── Shared session-selection CTE ─────────────────────────────────────────

    @staticmethod
    def _sessions_cte(f: LocalAnalyticsFilter) -> str:
        """Sessions whose started_at is in the window; the only place the window is decided."""
        conditions = [_STARTED_IN_WINDOW]
        if f.deny_all:
            conditions.append("1 = 0")
        if f.users:
            conditions.append("lower(user_email) IN {users:Array(String)}")
        if f.projects:
            conditions.append("project_name IN {projects:Array(String)}")
        if f.repositories:
            conditions.append("repository IN {repositories:Array(String)}")
        if f.branch:
            conditions.append("branch = {branch:String}")
        having = "\n            WHERE " + "\n              AND ".join(conditions)
        return f"""
        sel AS (
            SELECT
                session_id,
                repository,
                branch,
                repo_remote,
                project_name,
                user_email,
                started_at,
                last_event_at,
                first_prompt
            FROM (
                SELECT
                    d.session_id                                                  AS session_id,
                    coalesce(nullIf(d.repo_remote, ''), nullIf(d.repository, '')) AS repository,
                    d.branch                                                      AS branch,
                    d.repo_remote                                                 AS repo_remote,
                    d.project_name                                                AS project_name,
                    coalesce(nullIf(e.resolved_email, ''), nullIf(d.developer_name, ''), '') AS user_email,
                    d.started_at                                                  AS started_at,
                    d.last_event_at                                               AS last_event_at,
                    d.first_prompt                                                AS first_prompt
                FROM codemie_analytics.v_session_dimensions d
                LEFT JOIN codemie_analytics.v_session_email e ON d.session_id = e.session_id
            ){having}
        )"""

    @staticmethod
    def _session_scope(f: LocalAnalyticsFilter, column: str = "session_id") -> str:
        """Restrict a fact query to the sessions selected by `sel` (always applied)."""
        return f"\n              AND {column} IN (SELECT session_id FROM sel)"

    # ── Cost / token facts (coding_agent_cost_daily) ─────────────────────────

    async def get_cost_kpis(self, f: LocalAnalyticsFilter) -> list[dict]:
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            uniqExact(session_id)          AS total_sessions,
            sum(cost_usd)                  AS total_cost_usd,
            sum(input_tokens)              AS total_input_tokens,
            sum(output_tokens)             AS total_output_tokens,
            sum(cache_read_tokens)         AS total_cache_read_tokens,
            sum(cache_creation_tokens)     AS total_cache_creation_tokens
        FROM codemie_analytics.coding_agent_cost_daily
        WHERE 1 = 1
              {self._session_scope(f)}
        """
        return await self._q(sql, f.params())

    async def get_model_breakdown(self, f: LocalAnalyticsFilter) -> list[dict]:
        """Per-model session counts, tokens and cache-read tokens (the pricing input)."""
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            d.model_name                                                                 AS model_name,
            uniqExact(d.session_id)                                                      AS session_count,
            sum(d.cost_usd)                                                              AS cost_usd,
            sum(d.cache_read_tokens)                                                     AS cache_read_tokens,
            sum(d.input_tokens + d.output_tokens + d.cache_read_tokens + d.cache_creation_tokens)
                                                                                         AS total_tokens
        FROM codemie_analytics.coding_agent_cost_daily d
        WHERE 1 = 1
              {self._session_scope(f, "d.session_id")}
        GROUP BY d.model_name
        HAVING model_name != ''
        ORDER BY session_count DESC, cost_usd DESC, model_name
        """
        return await self._q(sql, f.params())

    async def get_cost_by_user(self, f: LocalAnalyticsFilter) -> list[dict]:
        """Cost per user. The v_session_email join is mandatory: 40% of cost rows carry an
        empty user_email (proxy sessions) and are only attributable through it."""
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            coalesce(nullIf(s.user_email, ''), nullIf(c.user_email, ''), 'unknown') AS developer_name,
            sum(c.cost_usd)                                                         AS cost_usd
        FROM codemie_analytics.coding_agent_cost_daily c
        JOIN sel s ON s.session_id = c.session_id
        GROUP BY developer_name
        ORDER BY cost_usd DESC, developer_name
        """
        return await self._q(sql, f.params())

    async def get_users(self, f: LocalAnalyticsFilter) -> list[dict]:
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            coalesce(nullIf(s.user_email, ''), nullIf(c.user_email, ''), 'unknown') AS developer_name,
            uniqExact(c.session_id)                                                 AS session_count,
            sum(c.input_tokens)                                                     AS input_tokens,
            sum(c.output_tokens)                                                    AS output_tokens,
            sum(c.cache_read_tokens)                                                AS cache_read_tokens,
            sum(c.cache_creation_tokens)                                            AS cache_creation_tokens,
            sum(c.cost_usd)                                                         AS cost_usd,
            argMax(c.model_name, c.api_call_count)                                  AS top_model,
            groupUniqArray(c.session_id)                                            AS session_ids
        FROM codemie_analytics.coding_agent_cost_daily c
        JOIN sel s ON s.session_id = c.session_id
        GROUP BY developer_name
        ORDER BY cost_usd DESC, developer_name
        """
        return await self._q(sql, f.params())

    async def get_users_daily_activity(self, f: LocalAnalyticsFilter) -> list[dict]:
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            coalesce(nullIf(s.user_email, ''), nullIf(c.user_email, ''), 'unknown') AS developer_name,
            c.day                                                                   AS day,
            uniqExact(c.session_id)                                                 AS session_count
        FROM codemie_analytics.coding_agent_cost_daily c
        JOIN sel s ON s.session_id = c.session_id
        GROUP BY developer_name, day
        ORDER BY developer_name, day
        """
        return await self._q(sql, f.params())

    async def get_users_last_active(self, f: LocalAnalyticsFilter) -> list[dict]:
        """Timestamp-granular last activity per user.

        Source: coding_agent_hook_events (9.7K rows) rather than coding_agent_logs
        (39K rows). Hook events cover all session activity types and the daily rollup
        only carries a Date, so a timestamp-granular source is still required.
        Verified live: max(Timestamp) is identical across both sources for every user.
        """
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            coalesce(nullIf(s.user_email, ''), nullIf(h.developer_name, ''), 'unknown') AS developer_name,
            max(h.Timestamp)                                                             AS last_active
        FROM codemie_analytics.coding_agent_hook_events h
        JOIN sel s ON s.session_id = h.session_id
        WHERE h.Timestamp <= {{end_dt:DateTime64(3)}}
              AND h.session_id != ''
        GROUP BY developer_name
        """
        return await self._q(sql, f.params())

    # ── Line facts (coding_agent_lines_daily) ────────────────────────────────

    async def get_lines_totals(self, f: LocalAnalyticsFilter) -> list[dict]:
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            sum(lines_added)   AS lines_added,
            sum(lines_removed) AS lines_removed
        FROM codemie_analytics.coding_agent_lines_daily
        WHERE 1 = 1
              {self._session_scope(f)}
        """
        return await self._q(sql, f.params())

    async def get_lines_daily(self, f: LocalAnalyticsFilter) -> list[dict]:
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            day                                     AS day,
            sum(lines_added) - sum(lines_removed)   AS net_lines
        FROM codemie_analytics.coding_agent_lines_daily
        WHERE 1 = 1
              {self._session_scope(f)}
        GROUP BY day
        ORDER BY day
        """
        return await self._q(sql, f.params())

    async def get_lines_by_user(self, f: LocalAnalyticsFilter) -> list[dict]:
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            coalesce(nullIf(s.user_email, ''), nullIf(l.user_email, ''), 'unknown') AS developer_name,
            sum(l.lines_added) - sum(l.lines_removed)                               AS net_lines
        FROM codemie_analytics.coding_agent_lines_daily l
        JOIN sel s ON s.session_id = l.session_id
        GROUP BY developer_name
        """
        return await self._q(sql, f.params())

    async def get_lines_by_session(self, f: LocalAnalyticsFilter) -> list[dict]:
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            session_id                              AS session_id,
            sum(lines_added)                        AS lines_added,
            sum(lines_removed)                      AS lines_removed
        FROM codemie_analytics.coding_agent_lines_daily
        WHERE 1 = 1
              {self._session_scope(f)}
        GROUP BY session_id
        """
        return await self._q(sql, f.params())

    # ── Trace facts: turns, tool calls, files (coding_agent_traces) ──────────

    async def get_turns_by_session(self, f: LocalAnalyticsFilter) -> list[dict]:
        """Interaction turns per session from the pre-aggregated daily rollup.

        Uses coding_agent_turns_daily (SummingMergeTree, ORDER BY (day, session_id))
        instead of scanning the raw coding_agent_traces table (~15 K rows).
        Pattern mirrors get_cost_kpis / get_lines_by_session which already read daily
        rollups scoped to the sessions selected by `sel`.
        """
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            session_id          AS session_id,
            sum(turns)          AS turns
        FROM codemie_analytics.coding_agent_turns_daily
        WHERE 1 = 1
              {self._session_scope(f)}
        GROUP BY session_id
        """
        return await self._q(sql, f.params())

    async def get_file_facts_by_session(self, f: LocalAnalyticsFilter) -> list[dict]:
        """Per-session distinct file counts from the pre-aggregated daily rollup.

        Uses coding_agent_file_facts_daily (AggregatingMergeTree) instead of scanning
        coding_agent_traces.  uniqExactMerge correctly accumulates state across days and
        across AggregatingMergeTree parts so cross-day sessions produce consistent counts.
        All of a windowed session's days are included (the window is decided in `sel`).
        tool_calls is a SimpleAggregateFunction(sum) so a plain sum() suffices.
        """
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            session_id,
            uniqExactMerge(files_changed)  AS files_changed,
            uniqExactMerge(files_written)  AS files_written,
            uniqExactMerge(files_edited)   AS files_edited,
            sum(tool_calls)                AS tool_calls
        FROM codemie_analytics.coding_agent_file_facts_daily
        WHERE 1 = 1
              {self._session_scope(f)}
        GROUP BY session_id
        """
        return await self._q(sql, f.params())

    async def get_tool_success_by_session(self, f: LocalAnalyticsFilter) -> list[dict]:
        """claude_code.tool joined to claude_code.tool.execution on tool_use_id.

        Cannot be precomputed by a materialized view: the two spans arrive in
        different insert blocks, so the join has to happen at read time.
        """
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            t.session_id                 AS session_id,
            count()                      AS tool_calls,
            countIf(e.success = 'true')  AS tool_calls_success
        FROM (
            SELECT session_id, tool_name, tool_use_id
            FROM codemie_analytics.coding_agent_traces
            WHERE {_TOOL_SPAN}
                  AND tool_name != ''
                  AND session_id != ''
                  {self._session_scope(f)}
        ) t
        LEFT JOIN (
            SELECT tool_use_id, anyLast(SpanAttributes['success']) AS success
            FROM codemie_analytics.coding_agent_traces
            WHERE {_EXEC_SPAN}
                  AND tool_use_id != ''
                  {self._session_scope(f)}
            GROUP BY tool_use_id
        ) e ON t.tool_use_id = e.tool_use_id AND t.tool_use_id != ''
        GROUP BY t.session_id
        """
        return await self._q(sql, f.params())

    async def get_tool_usage(self, f: LocalAnalyticsFilter) -> list[dict]:
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            t.tool_name                  AS tool_name,
            count()                      AS call_count,
            countIf(e.success = 'true')  AS success_count
        FROM (
            SELECT session_id, tool_name, tool_use_id
            FROM codemie_analytics.coding_agent_traces
            WHERE {_TOOL_SPAN}
                  AND tool_name != ''
                  {self._session_scope(f)}
        ) t
        LEFT JOIN (
            SELECT tool_use_id, anyLast(SpanAttributes['success']) AS success
            FROM codemie_analytics.coding_agent_traces
            WHERE {_EXEC_SPAN}
                  AND tool_use_id != ''
                  {self._session_scope(f)}
            GROUP BY tool_use_id
        ) e ON t.tool_use_id = e.tool_use_id AND t.tool_use_id != ''
        GROUP BY t.tool_name
        ORDER BY call_count DESC, tool_name
        LIMIT 20
        """
        return await self._q(sql, f.params())

    async def get_invocations(self, f: LocalAnalyticsFilter) -> list[dict]:
        """Skills, subagent types and slash commands.

        - skills  : claude_code.tool spans carrying skill_name (cross-checked live against
                    the skill_activated log; both sources agree exactly).
        - agents  : claude_code.tool spans carrying subagent_type (richer than the
                    agent.subagent.start hook events).
        - commands: LogAttributes['command_name'] on user_prompt logs. NOTE this captures
                    Claude Code built-ins (resume/clear/compact), not user-authored slash
                    commands - no prompt in live data begins with '/'.
        """
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT 'skill' AS kind, span_skill_name AS name, count() AS count
        FROM codemie_analytics.coding_agent_traces
        WHERE {_TOOL_SPAN}
              AND span_skill_name != ''
              {self._session_scope(f)}
        GROUP BY name

        UNION ALL

        SELECT 'agent' AS kind, subagent_type AS name, count() AS count
        FROM codemie_analytics.coding_agent_traces
        WHERE {_TOOL_SPAN}
              AND subagent_type != ''
              {self._session_scope(f)}
        GROUP BY name

        UNION ALL

        SELECT 'command' AS kind, LogAttributes['command_name'] AS name, count() AS count
        FROM codemie_analytics.coding_agent_logs
        WHERE event_name = 'user_prompt'
              AND LogAttributes['command_name'] != ''
              {self._session_scope(f)}
        GROUP BY name
        """
        return await self._q(sql, f.params())

    async def get_skill_names_by_session(self, session_ids: list[str]) -> list[dict]:
        """Distinct skill names per session from coding_agent_logs (skill_activated events).

        Uses coding_agent_logs.skill_name on skill_activated events — the only source
        that records framework-specific skill names (e.g. superpowers:brainstorming,
        sdlc-factory:sdlc-standard). Covers both slash-command activations and
        in-session Skill tool invocations. The api_request event carries generic names
        (third-party, codemie-sdk) used for cost attribution only.
        coding_agent_traces.span_skill_name has ~0.4% session coverage and is not used.

        Accepts already-filtered session IDs from cost_facts — no date filter needed.
        Works for both bulk (sessions list) and single-session (detail) callers;
        pass [session_id] for the detail endpoint. Large id lists are queried in parts
        (see SESSION_IDS_PARAM_BUDGET_BYTES); a session's rows never span two parts.
        """
        if not session_ids:
            return []
        sql = """
        SELECT session_id, groupUniqArray(skill_name) AS skill_names
        FROM codemie_analytics.coding_agent_logs
        WHERE session_id IN {session_ids:Array(String)}
          AND event_name = 'skill_activated'
          AND skill_name != ''
        GROUP BY session_id
        """
        parts = await asyncio.gather(*(self._q(sql, {"session_ids": part}) for part in _session_id_parts(session_ids)))
        return [row for rows in parts for row in rows]

    # ── Session-level aggregates ─────────────────────────────────────────────

    async def get_session_durations(self, f: LocalAnalyticsFilter) -> list[dict]:
        """Wall-clock span per session, for the sessions that started in the window."""
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            session_id                                                     AS session_id,
            dateDiff('millisecond', started_at, last_event_at)             AS duration_ms
        FROM sel
        """
        return await self._q(sql, f.params())

    async def get_active_ms_by_session(self, f: LocalAnalyticsFilter) -> list[dict]:
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            session_id                                     AS session_id,
            sum(active_ms_user) + sum(active_ms_cli)       AS active_ms
        FROM codemie_analytics.coding_agent_active_time_daily
        WHERE 1 = 1
              {self._session_scope(f)}
        GROUP BY session_id
        """
        return await self._q(sql, f.params())

    async def get_session_cost_facts(
        self,
        f: LocalAnalyticsFilter,
        search: str | None = None,
        is_unattributed: bool = False,
    ) -> list[dict]:
        """Per-session cost/token facts plus dimensions - the base for /sessions and
        the dead-session and context-bloat derivations."""
        having_parts = []
        if search:
            having_parts.append("ilike(nullIf(any(s.repository), ''), {search:String})")
        if is_unattributed:
            having_parts.append("nullIf(any(s.repository), '') IS NULL")
        having = ("\n        HAVING " + "\n          AND ".join(having_parts)) if having_parts else ""
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            c.session_id                                                            AS session_id,
            coalesce(nullIf(s.user_email, ''), nullIf(c.user_email, ''), 'unknown') AS developer_name,
            any(s.repository)                                                       AS repository,
            any(s.branch)                                                           AS branch,
            any(s.project_name)                                                     AS project_name,
            any(s.first_prompt)                                                     AS prompt,
            any(s.started_at)                                                       AS started_at,
            any(s.last_event_at)                                                    AS last_event_at,
            argMax(c.model_name, c.api_call_count)                                  AS model_name,
            sum(c.cost_usd)                                                         AS cost_usd,
            sum(c.input_tokens)                                                     AS input_tokens,
            sum(c.output_tokens)                                                    AS output_tokens,
            sum(c.cache_read_tokens)                                                AS cache_read_tokens,
            sum(c.cache_creation_tokens)                                            AS cache_creation_tokens
        FROM codemie_analytics.coding_agent_cost_daily c
        JOIN sel s ON s.session_id = c.session_id
        GROUP BY c.session_id, developer_name{having}
        """
        params = f.params()
        if search:
            params["search"] = f"%{search.strip().lower()}%"
        return await self._q(sql, params)

    async def get_session_start_times(self, f: LocalAnalyticsFilter) -> list[dict]:
        """One start timestamp per session - the activity heat-map input."""
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT started_at AS started_at
        FROM sel
        """
        return await self._q(sql, f.params())

    # ── Repository roll-up ───────────────────────────────────────────────────

    async def get_repository_sessions(self, f: LocalAnalyticsFilter, search: str | None = None) -> list[dict]:
        """session -> (repository, branch, project) map for the sessions in range."""
        search_clause = "\n          AND ilike(repository, {search:String})" if search else ""
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            session_id      AS session_id,
            repository      AS repository,
            branch          AS branch,
            project_name    AS project_name
        FROM sel
        WHERE repository != ''{search_clause}
        """
        params = f.params()
        if search:
            params["search"] = f"%{search.strip().lower()}%"
        return await self._q(sql, params)

    # ── Session detail ───────────────────────────────────────────────────────

    async def get_session_detail_meta(self, session_id: str) -> list[dict]:
        sql = """
        SELECT
            c.session_id                                                   AS session_id,
            coalesce(nullIf(e.resolved_email, ''), nullIf(d.developer_name, ''), 'unknown') AS developer_name,
            coalesce(nullIf(d.repo_remote, ''), nullIf(d.repository, ''))   AS repository,
            nullIf(d.branch, '')                                           AS branch,
            nullIf(d.project_name, '')                                     AS project_name,
            nullIf(d.first_prompt, '')                                     AS prompt,
            coalesce(d.started_at, costs.min_day)                          AS started_at,
            coalesce(dateDiff('millisecond', d.started_at, d.last_event_at), 0) AS duration_ms
        FROM (
            SELECT DISTINCT session_id
            FROM codemie_analytics.coding_agent_cost_daily
            WHERE session_id = {session_id:String}
        ) c
        CROSS JOIN (
            SELECT toDateTime(min(day)) AS min_day
            FROM codemie_analytics.coding_agent_cost_daily
            WHERE session_id = {session_id:String}
        ) costs
        LEFT JOIN codemie_analytics.v_session_dimensions d ON d.session_id = c.session_id
        LEFT JOIN codemie_analytics.v_session_email e ON e.session_id = c.session_id
        """
        return await self._q(sql, {"session_id": session_id})

    async def get_session_detail_cost(self, session_id: str) -> list[dict]:
        sql = """
        SELECT
            argMax(model_name, api_call_count)  AS model_name,
            sum(cost_usd)                       AS cost_usd,
            sum(input_tokens)                   AS input_tokens,
            sum(output_tokens)                  AS output_tokens,
            sum(cache_read_tokens)              AS cache_read_tokens,
            sum(cache_creation_tokens)          AS cache_creation_tokens
        FROM codemie_analytics.coding_agent_cost_daily
        WHERE session_id = {session_id:String}
        """
        return await self._q(sql, {"session_id": session_id})

    async def get_session_detail_scalars(self, session_id: str) -> list[dict]:
        """Per-session scalar facts for the session detail view.

        lines_added/removed and active_ms already read from pre-aggregated daily tables.
        turns, tool_call_count, agent_count, and skill_count previously triggered 5 raw
        scans of coding_agent_traces (15 K+ rows each); they now read from
        coding_agent_turns_daily and coding_agent_file_facts_daily (~89 row reads).
        """
        sql = """
        SELECT
            (SELECT sum(lines_added)
             FROM codemie_analytics.coding_agent_lines_daily
             WHERE session_id = {session_id:String})                          AS lines_added,
            (SELECT sum(lines_removed)
             FROM codemie_analytics.coding_agent_lines_daily
             WHERE session_id = {session_id:String})                          AS lines_removed,
            (SELECT sum(active_ms_user) + sum(active_ms_cli)
             FROM codemie_analytics.coding_agent_active_time_daily
             WHERE session_id = {session_id:String})                          AS active_ms,
            (SELECT sum(turns)
             FROM codemie_analytics.coding_agent_turns_daily
             WHERE session_id = {session_id:String})                          AS turns,
            (SELECT sum(tool_calls)
             FROM codemie_analytics.coding_agent_file_facts_daily
             WHERE session_id = {session_id:String})                          AS tool_call_count,
            (SELECT sum(agent_count)
             FROM codemie_analytics.coding_agent_file_facts_daily
             WHERE session_id = {session_id:String})                          AS agent_count,
            (SELECT sum(skill_count)
             FROM codemie_analytics.coding_agent_file_facts_daily
             WHERE session_id = {session_id:String})
            + (SELECT count()
               FROM codemie_analytics.coding_agent_logs
               WHERE session_id = {session_id:String}
                 AND event_type = 'agent.skill.dispatch'
                 AND LogAttributes['skill_name'] != ''
                 AND LogAttributes['skill_name'] NOT IN (
                     SELECT span_skill_name
                     FROM codemie_analytics.coding_agent_traces
                     WHERE SpanName IN ('claude_code.tool', 'cursor.tool')
                       AND session_id = {session_id:String}
                       AND span_skill_name != ''
                 ))                                                            AS skill_count
        """
        return await self._q(sql, {"session_id": session_id})

    async def get_session_detail_tools(self, session_id: str) -> list[dict]:
        sql = """
        SELECT
            t.tool_name                  AS tool_name,
            count()                      AS call_count,
            countIf(e.success = 'true')  AS success_count
        FROM (
            SELECT tool_name, tool_use_id
            FROM codemie_analytics.coding_agent_traces
            WHERE SpanName IN ('claude_code.tool', 'cursor.tool')
              AND session_id = {session_id:String}
              AND tool_name != ''
        ) t
        LEFT JOIN (
            SELECT tool_use_id, anyLast(SpanAttributes['success']) AS success
            FROM codemie_analytics.coding_agent_traces
            WHERE SpanName IN ('claude_code.tool.execution', 'cursor.tool.execution')
              AND session_id = {session_id:String}
              AND tool_use_id != ''
            GROUP BY tool_use_id
        ) e ON t.tool_use_id = e.tool_use_id AND t.tool_use_id != ''
        GROUP BY t.tool_name
        ORDER BY call_count DESC, tool_name
        LIMIT 20
        """
        return await self._q(sql, {"session_id": session_id})

    async def get_session_detail_events(self, session_id: str) -> list[dict]:
        """Merged per-call event stream: api_request logs UNION tool spans, time-ordered.

        The union is a subquery because ClickHouse applies an ORDER BY written after
        UNION ALL to the last SELECT only.
        """
        sql = """
        SELECT *
        FROM (
        SELECT
            Timestamp                                          AS timestamp,
            'api_request'                                      AS event_type,
            LogAttributes['model']                             AS model_name,
            toInt64(toUInt64OrZero(LogAttributes['input_tokens']))       AS input_tokens,
            toInt64(toUInt64OrZero(LogAttributes['output_tokens']))      AS output_tokens,
            toInt64(toUInt64OrZero(LogAttributes['cache_read_tokens']))  AS cache_read_tokens,
            toFloat64OrZero(LogAttributes['cost_usd'])         AS cost_usd,
            ''                                                 AS tool_name
        FROM codemie_analytics.coding_agent_logs
        WHERE session_id = {session_id:String}
          AND event_name = 'api_request'

        UNION ALL

        SELECT
            Timestamp        AS timestamp,
            'tool_call'      AS event_type,
            ''               AS model_name,
            toInt64(0)       AS input_tokens,
            toInt64(0)       AS output_tokens,
            toInt64(0)       AS cache_read_tokens,
            toFloat64(0)     AS cost_usd,
            tool_name        AS tool_name
        FROM codemie_analytics.coding_agent_traces
        WHERE session_id = {session_id:String}
          AND SpanName IN ('claude_code.tool', 'cursor.tool')
          AND tool_name != ''
        )
        ORDER BY timestamp, event_type, tool_name, model_name
        """
        return await self._q(sql, {"session_id": session_id})

    async def get_session_detail_dispatches(self, session_id: str) -> list[dict]:
        """Agent/skill dispatch timeline.

        Duration comes from the paired claude_code.tool.execution span (the
        claude_code.tool span's own duration is ~0). Cost/tokens are attributed through
        api_request.SpanId = claude_code.tool.ParentSpanId - verified live: 30/30
        agent+skill spans matched an api_request span in the trailing 7 days.

        The union is a subquery because ClickHouse applies an ORDER BY written after
        UNION ALL to the last SELECT only.
        """
        sql = """
        SELECT *
        FROM (
        SELECT
            t.subagent_type                                                      AS subagent_type,
            t.skill_name                                                         AS skill_name,
            toUInt8(0)                                                           AS is_slash_command,
            t.span_start                                                         AS span_start,
            ifNull(ex.duration_ms, 0)                                            AS duration_ms,
            ifNull(intDiv(dateDiff('nanosecond', t.span_start,
                          dateAdd(NANOSECOND, ia.Duration, ia.Timestamp)),
                          1000000), 0)                                           AS real_duration_ms,
            ifNull(lr.cost_usd, 0.0)                                             AS cost_usd,
            ifNull(lr.input_tokens, 0)                                           AS input_tokens,
            ifNull(lr.output_tokens, 0)                                          AS output_tokens,
            ifNull(lr.cache_read_tokens, 0)                                      AS cache_read_tokens,
            ifNull(lr.cache_creation_tokens, 0)                                  AS cache_creation_tokens
        FROM (
            SELECT
                subagent_type                    AS subagent_type,
                span_skill_name                  AS skill_name,
                tool_use_id                      AS tool_use_id,
                Timestamp                        AS span_start,
                ParentSpanId                     AS parent_span_id
            FROM codemie_analytics.coding_agent_traces
            WHERE SpanName IN ('claude_code.tool', 'cursor.tool')
              AND session_id = {session_id:String}
              AND (subagent_type != '' OR span_skill_name != '')
        ) t
        LEFT JOIN (
            SELECT tool_use_id, anyLast(intDiv(Duration, 1000000)) AS duration_ms
            FROM codemie_analytics.coding_agent_traces
            WHERE SpanName IN ('claude_code.tool.execution', 'cursor.tool.execution')
              AND session_id = {session_id:String}
              AND tool_use_id != ''
            GROUP BY tool_use_id
        ) ex ON ex.tool_use_id = t.tool_use_id AND t.tool_use_id != ''
        LEFT JOIN (
            SELECT SpanId, anyLast(Timestamp) AS Timestamp, anyLast(Duration) AS Duration
            FROM codemie_analytics.coding_agent_traces
            WHERE SpanName IN ('claude_code.interaction', 'cursor.interaction')
              AND session_id = {session_id:String}
            GROUP BY SpanId
        ) ia ON ia.SpanId = t.parent_span_id
        LEFT JOIN (
            SELECT
                l.SpanId                                                              AS span_id,
                sumIf(toFloat64OrZero(l.LogAttributes['cost_usd']),
                      l.Timestamp >= ts.min_span_start)                              AS cost_usd,
                sumIf(toUInt64OrZero(l.LogAttributes['input_tokens']),
                      l.Timestamp >= ts.min_span_start)                              AS input_tokens,
                sumIf(toUInt64OrZero(l.LogAttributes['output_tokens']),
                      l.Timestamp >= ts.min_span_start)                              AS output_tokens,
                sumIf(toUInt64OrZero(l.LogAttributes['cache_read_tokens']),
                      l.Timestamp >= ts.min_span_start)                              AS cache_read_tokens,
                sumIf(toUInt64OrZero(l.LogAttributes['cache_creation_tokens']),
                      l.Timestamp >= ts.min_span_start)                              AS cache_creation_tokens
            FROM codemie_analytics.coding_agent_logs l
            JOIN (
                SELECT ParentSpanId, min(Timestamp) AS min_span_start
                FROM codemie_analytics.coding_agent_traces
                WHERE SpanName IN ('claude_code.tool', 'cursor.tool')
                  AND session_id = {session_id:String}
                  AND (subagent_type != '' OR span_skill_name != '')
                GROUP BY ParentSpanId
            ) ts ON ts.ParentSpanId = l.SpanId
            WHERE l.session_id = {session_id:String}
              AND l.event_name = 'api_request'
              AND l.SpanId != ''
            GROUP BY l.SpanId
        ) lr ON lr.span_id = t.parent_span_id

        UNION ALL

        -- Hook-sourced slash-command skill dispatches via UserPromptExpansion.
        -- OTel emits no Skill tool span for the slash-command path:
        --   https://github.com/anthropics/claude-code/issues/83944
        -- This branch is suppressed when an OTel span already covers the same
        -- skill (NOT IN guard) so it becomes a no-op once that bug is fixed.
        --
        -- Note: hook events store skill name as LogAttributes['skill_name']
        -- (underscore); the materialized skill_name column is empty for these
        -- rows. Query the raw map key directly.
        --
        -- Known gaps:
        --   duration_ms = 0: no PostCommand hook exists today.
        --     https://github.com/anthropics/claude-code/issues/68663
        --   real_duration_ms = 0: no interaction span exists for hook-sourced rows.
        SELECT
            ''             AS subagent_type,
            skill_name,
            toUInt8(1)     AS is_slash_command,
            span_start,
            toInt64(0)     AS duration_ms,
            toInt64(0)     AS real_duration_ms,
            toFloat64(0)   AS cost_usd,
            toUInt64(0)    AS input_tokens,
            toUInt64(0)    AS output_tokens,
            toUInt64(0)    AS cache_read_tokens,
            toUInt64(0)    AS cache_creation_tokens
        FROM (
            SELECT
                LogAttributes['skill_name']   AS skill_name,
                Timestamp                     AS span_start
            FROM codemie_analytics.coding_agent_logs
            WHERE session_id = {session_id:String}
              AND event_type = 'agent.skill.dispatch'
              AND LogAttributes['skill_name'] != ''
              AND LogAttributes['skill_name'] NOT IN (
                  SELECT span_skill_name
                  FROM codemie_analytics.coding_agent_traces
                  WHERE SpanName IN ('claude_code.tool', 'cursor.tool')
                    AND session_id = {session_id:String}
                    AND span_skill_name != ''
              )
        )
        )
        ORDER BY span_start, is_slash_command, subagent_type, skill_name
        """
        return await self._q(sql, {"session_id": session_id})
