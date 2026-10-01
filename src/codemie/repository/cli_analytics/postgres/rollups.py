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

"""Rollup refresher: the PostgreSQL counterpart of the ClickHouse materialized views.

Ingest queues `(day, session)` keys in `rollup_dirty` (see dirty.py); this job recomputes
each affected rollup slice from raw rows with DELETE + INSERT ... GROUP BY. Recomputing
is idempotent and absorbs late and out-of-order records, and the same job backfills
(`mark_dirty`).

Versioned claim, so ingest never waits for a recompute:
  1. read a batch of keys with their `version`, taking no lock;
  2. in one transaction, recompute every flagged family for those keys, then delete the
     keys whose `version` is still the one read.
A key marked again while its slice was being recomputed keeps its new version, so the
delete skips it and the next run recomputes it. Nothing committed can be missed:
  - a raw row committed before a recompute statement's snapshot is in that recompute;
  - a later one was committed by a transaction that bumped the key's version after
    step 1, so the key stays queued. If that transaction is still open when the delete
    runs, the delete waits for its row lock and then re-checks the version.
Ingest waits for this job only on that final delete, never on the recompute.
Readers see either the old or the new slice (MVCC); rollups trail raw data by at most
one refresh interval.

A batch that fails on its data is split until the key at fault is alone, and that key
is dropped, so one bad record cannot hold every later batch back. Keys of days whose raw
rows have passed retention are dropped unrecomputed: rebuilding such a slice from the few
late rows left would replace the totals it kept.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, timedelta

import asyncpg

from codemie.repository.cli_analytics.postgres.dirty import RollupFamily
from codemie.repository.cli_analytics.postgres.engine import CLIENT_TIMEOUT_MARGIN_S, AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.maintenance import TODAY_UTC_SQL
from codemie.repository.cli_analytics.vocabulary import (
    ACTIVE_TIME_METRICS,
    DIMENSION_HOOK_TYPES,
    EDIT_TOOLS,
    LINES_METRICS,
    SENTINEL_EXACT,
    SENTINEL_PREFIX_RAW,
    SENTINEL_PREFIX_TRIMMED,
    WRITE_TOOLS,
    EventKind,
    SpanKind,
)

logger = logging.getLogger(__name__)

# Failures that come back whenever the same keys are recomputed, and fast: a value PostgreSQL
# rejects, or one too large for an index row (54000). The batch is split to find the key.
_REJECTED_DATA = (asyncpg.DataError, asyncpg.exceptions.ProgramLimitExceededError)
# A recompute past its time limit (57014) is a key too large to recompute in time, or passing
# load. The keys of a batch that timed out are flagged `alone`: whichever pod refreshes next
# takes them one at a time. A key that times out alone is not due again for _DEFER_STEP_S times
# its timeouts so far (passing load cannot run up its strikes in one go), and is dropped after
# this many timeouts in a row. rollup_dirty holds that state, so every pod sees it and a
# restart keeps it.
_TIMEOUT_STRIKES = 3
_DEFER_STEP_S = 600
# Waiting longer for a lock fails the run like any transient error: the next run retries it.
_LOCK_TIMEOUT_MS = 30_000

# One transaction may recompute thousands of slices; it gets more time than a dashboard
# query. The client-side limit is raised with it, or the pool's would cancel the statement first.
_REFRESH_STATEMENT_TIMEOUT_S = 300
_REFRESH_STATEMENT_TIMEOUT = f"SET LOCAL statement_timeout = '{_REFRESH_STATEMENT_TIMEOUT_S}s'"
_REFRESH_CLIENT_TIMEOUT_S = _REFRESH_STATEMENT_TIMEOUT_S + CLIENT_TIMEOUT_MARGIN_S

# A counter reading that adds more than this to a column counts as 0. No real line count or
# active time (in ms) comes near it, and it keeps every sum of the rollups and the dashboards
# far inside bigint's range, whatever a client sends.
_MAX_COUNTER = 10**9


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _text_array(values: tuple[str, ...]) -> str:
    """A SQL array of code constants (never input)."""
    return "ARRAY[" + ", ".join(_literal(v) for v in values) + "]::text[]"


_KEYS = "_cli_rollup_keys"
_DAY_LO = "(k.day::timestamp AT TIME ZONE 'UTC')"
_DAY_HI = "((k.day + 1)::timestamp AT TIME ZONE 'UTC')"
_LOG, _DIMS, _SPANS, _METRICS = (int(f) for f in RollupFamily)
_TOOL, _EXEC, _INTERACTION = int(SpanKind.TOOL), int(SpanKind.TOOL_EXECUTION), int(SpanKind.INTERACTION)
_DIM_TYPES = _text_array(DIMENSION_HOOK_TYPES)

# A prompt that may become the session's first prompt (mv_session_dims).
_PROMPT_OK = (
    f"btrim(h.prompt_body) <> '' AND lower(btrim(h.prompt_body)) <> ALL({_text_array(SENTINEL_EXACT)})"
    + "".join(f" AND NOT starts_with(btrim(h.prompt_body), {_literal(p)})" for p in SENTINEL_PREFIX_TRIMMED)
    + "".join(f" AND NOT starts_with(h.prompt_body, {_literal(p)})" for p in SENTINEL_PREFIX_RAW)
)


def _earliest(column: str, condition: str = "") -> str:
    """argMinIf(column, ts, column != ''): the earliest non-empty value, ties to the first ingested."""
    where = f"h.event_type = ANY({_DIM_TYPES}) AND h.{column} <> ''" + (f" AND {condition}" if condition else "")
    return f"(array_agg(h.{column} ORDER BY h.ts, h.ingest_seq) FILTER (WHERE {where}))[1]"


def _counter(condition: str, scale: int = 1) -> str:
    """A metric reading times `scale`, as a bigint count. The reading is bounded before it is
    scaled, so the guard itself cannot overflow; NaN compares above every number (counts as 0)."""
    return (
        f"CASE WHEN {condition} AND abs(m.value) <= {_MAX_COUNTER // scale} "
        f"THEN trunc(m.value * {scale})::bigint ELSE 0 END"
    )


_LINES_ADDED = _counter("m.type = 'added'")
_LINES_REMOVED = _counter("m.type = 'removed'")
_ACTIVE_MS_USER = _counter("m.type = 'user'", scale=1000)  # seconds to milliseconds
_ACTIVE_MS_CLI = _counter("m.type = 'cli'", scale=1000)


_EARLIEST_COLUMNS = ("repository", "branch", "repo_remote", "project_name", "developer_name", "first_prompt")


def _earliest_merged(column: str) -> str:
    """The stored earliest value unless the recompute reaches further back (see session_dims)."""
    return (
        f"CASE WHEN sd.started_at < EXCLUDED.started_at THEN coalesce(sd.{column}, EXCLUDED.{column}) "
        f"ELSE coalesce(EXCLUDED.{column}, sd.{column}) END"
    )


def _in_day(alias: str, table: str) -> str:
    return (
        f"FROM {_KEYS} k JOIN {table} {alias} ON {alias}.session_id = k.session_id "
        f"AND {alias}.ts >= {_DAY_LO} AND {alias}.ts < {_DAY_HI}"
    )


def _replace_daily(table: str, family: int, select: str) -> list[str]:
    return [
        f"DELETE FROM {table} t USING {_KEYS} k WHERE k.kinds & {family} <> 0 "
        f"AND t.day = k.day AND t.session_id = k.session_id",
        f"INSERT INTO {table} {select}",
    ]


RECOMPUTE: list[str] = [
    *_replace_daily(
        "cost_daily",
        _LOG,
        f"""(day, session_id, user_email, model_name, query_source, cost_usd, input_tokens, output_tokens,
             cache_read_tokens, cache_creation_tokens, api_call_count)
        SELECT k.day, k.session_id, coalesce(l.user_email, ''), coalesce(l.model, ''), coalesce(l.query_source, ''),
               -- numeric: an exact sum, the same in whatever order the rows are scanned
               sum(coalesce(l.cost_usd, 0)::numeric)::float8,
               sum(coalesce(l.input_tokens, 0)), sum(coalesce(l.output_tokens, 0)),
               sum(coalesce(l.cache_read_tokens, 0)), sum(coalesce(l.cache_creation_tokens, 0)), count(*)
        {_in_day("l", "log_events")}
        WHERE k.kinds & {_LOG} <> 0 AND l.event_kind = {int(EventKind.API_REQUEST)}
        GROUP BY 1, 2, 3, 4, 5""",
    ),
    # SummingMergeTree drops rows whose sums are all zero when parts merge: so do these two.
    *_replace_daily(
        "lines_daily",
        _METRICS,
        f"""(day, session_id, user_email, model_name, lines_added, lines_removed)
        SELECT k.day, k.session_id, coalesce(m.user_email, ''), coalesce(m.model, ''),
               sum({_LINES_ADDED}), sum({_LINES_REMOVED})
        {_in_day("m", "metric_points")}
        WHERE k.kinds & {_METRICS} <> 0 AND m.metric_name = ANY({_text_array(LINES_METRICS)})
        GROUP BY 1, 2, 3, 4
        HAVING sum({_LINES_ADDED}) <> 0 OR sum({_LINES_REMOVED}) <> 0""",
    ),
    *_replace_daily(
        "active_time_daily",
        _METRICS,
        f"""(day, session_id, user_email, active_ms_user, active_ms_cli)
        SELECT k.day, k.session_id, coalesce(m.user_email, ''), sum({_ACTIVE_MS_USER}), sum({_ACTIVE_MS_CLI})
        {_in_day("m", "metric_points")}
        WHERE k.kinds & {_METRICS} <> 0 AND m.metric_name = ANY({_text_array(ACTIVE_TIME_METRICS)})
        GROUP BY 1, 2, 3
        HAVING sum({_ACTIVE_MS_USER}) <> 0 OR sum({_ACTIVE_MS_CLI}) <> 0""",
    ),
    *_replace_daily(
        "turns_daily",
        _SPANS,
        f"""(day, session_id, turns)
        SELECT k.day, k.session_id, count(*)
        {_in_day("s", "spans")}
        WHERE k.kinds & {_SPANS} <> 0 AND k.session_id <> '' AND s.span_kind = {_INTERACTION}
        GROUP BY 1, 2""",
    ),
    *_replace_daily(
        "tool_facts_daily",
        _SPANS,
        f"""(day, session_id, tool_calls, agent_count, skill_count)
        SELECT k.day, k.session_id,
               count(*) FILTER (WHERE s.tool_name <> ''),
               count(*) FILTER (WHERE s.subagent_type <> ''),
               count(*) FILTER (WHERE s.skill_name <> '')
        {_in_day("s", "spans")}
        WHERE k.kinds & {_SPANS} <> 0 AND k.session_id <> '' AND s.span_kind = {_TOOL}
        GROUP BY 1, 2""",
    ),
    *_replace_daily(
        "session_files_daily",
        _SPANS,
        f"""(day, session_id, file_path, is_written, is_edited)
        SELECT k.day, k.session_id, s.file_path,
               bool_or(s.tool_name = ANY({_text_array(WRITE_TOOLS)})),
               bool_or(s.tool_name = ANY({_text_array(EDIT_TOOLS)}))
        {_in_day("s", "spans")}
        WHERE k.kinds & {_SPANS} <> 0 AND k.session_id <> '' AND s.span_kind = {_TOOL} AND s.file_path <> ''
        GROUP BY 1, 2, 3""",
    ),
    # Hourly invocations: kinds 1-3 come from spans, kind 4 (slash commands) from user prompts.
    f"DELETE FROM invocations_hourly t USING {_KEYS} k WHERE k.kinds & {_SPANS} <> 0 AND t.kind IN (1, 2, 3) "
    f"AND t.session_id = k.session_id AND t.hour >= {_DAY_LO} AND t.hour < {_DAY_HI}",
    f"DELETE FROM invocations_hourly t USING {_KEYS} k WHERE k.kinds & {_LOG} <> 0 AND t.kind = 4 "
    f"AND t.session_id = k.session_id AND t.hour >= {_DAY_LO} AND t.hour < {_DAY_HI}",
    # A tool call succeeded when its execution span (any session, as ClickHouse joins on
    # tool_use_id alone) says so; the execution starts after the tool span, within a day.
    f"""INSERT INTO invocations_hourly (hour, session_id, kind, name, calls, success)
    SELECT date_trunc('hour', t.ts, 'UTC'), k.session_id, 1, t.tool_name, count(*),
           count(*) FILTER (WHERE e.success = 'true')
    {_in_day("t", "spans")}
    LEFT JOIN LATERAL (
        SELECT max(x.success) AS success FROM spans x
        WHERE x.span_kind = {_EXEC} AND x.tool_use_id = t.tool_use_id
          AND x.ts >= t.ts - interval '1 hour' AND x.ts < t.ts + interval '1 day'
    ) e ON t.tool_use_id <> ''
    WHERE k.kinds & {_SPANS} <> 0 AND t.span_kind = {_TOOL} AND t.tool_name <> ''
    GROUP BY 1, 2, 4
    UNION ALL
    SELECT date_trunc('hour', t.ts, 'UTC'), k.session_id, 2, t.skill_name, count(*), 0
    {_in_day("t", "spans")}
    WHERE k.kinds & {_SPANS} <> 0 AND t.span_kind = {_TOOL} AND t.skill_name <> ''
    GROUP BY 1, 2, 4
    UNION ALL
    SELECT date_trunc('hour', t.ts, 'UTC'), k.session_id, 3, t.subagent_type, count(*), 0
    {_in_day("t", "spans")}
    WHERE k.kinds & {_SPANS} <> 0 AND t.span_kind = {_TOOL} AND t.subagent_type <> ''
    GROUP BY 1, 2, 4
    UNION ALL
    SELECT date_trunc('hour', l.ts, 'UTC'), k.session_id, 4, l.command_name, count(*), 0
    {_in_day("l", "log_events")}
    WHERE k.kinds & {_LOG} <> 0 AND l.event_kind = {int(EventKind.USER_PROMPT)} AND l.command_name <> ''
    GROUP BY 1, 2, 4""",
    # Per session, from all of the session's rows (not only the claimed day). Skills accumulate,
    # as in ClickHouse's aggregate states: a skill whose raw rows passed retention is kept.
    f"""INSERT INTO session_skills (session_id, skill_name, updated_at)
    SELECT DISTINCT l.session_id, l.skill_name, now()
    FROM (SELECT DISTINCT session_id FROM {_KEYS} WHERE kinds & {_LOG} <> 0 AND session_id <> '') k
    JOIN log_events l ON l.session_id = k.session_id
    WHERE l.event_kind = {int(EventKind.SKILL_ACTIVATED)} AND l.skill_name <> ''
    ON CONFLICT (session_id, skill_name) DO UPDATE SET updated_at = EXCLUDED.updated_at""",
    # Dimensions come from the 13 dimension hook types, identity (JWT email, developer
    # name) from every hook event, as in ClickHouse's mv_session_dims / mv_session_identity.
    # A recompute sees only the raw rows still kept, so it merges into the stored row the way
    # ClickHouse's aggregate states do: when it starts later than the stored start, the earliest
    # rows are gone and the stored earliest values stay.
    f"""INSERT INTO session_dims AS sd (session_id, started_at, last_event_at, repository, branch, repo_remote,
                                       project_name, developer_name, first_prompt, jwt_email, dev_name_max, updated_at)
    SELECT h.session_id,
           min(h.ts) FILTER (WHERE h.event_type = ANY({_DIM_TYPES})),
           max(h.ts) FILTER (WHERE h.event_type = ANY({_DIM_TYPES})),
           {_earliest("cwd")}, {_earliest("git_branch")}, {_earliest("repo_remote")},
           {_earliest("codemie_project_name")}, {_earliest("developer_name")}, {_earliest("prompt_body", _PROMPT_OK)},
           max(h.user_email COLLATE "C") FILTER (WHERE h.user_email <> ''),
           max(h.developer_name COLLATE "C") FILTER (WHERE h.developer_name <> ''),
           now()
    FROM (SELECT DISTINCT session_id FROM {_KEYS} WHERE kinds & {_DIMS} <> 0 AND session_id <> '') k
    JOIN hook_events h ON h.session_id = k.session_id
    GROUP BY h.session_id
    ON CONFLICT (session_id) DO UPDATE SET
        started_at = LEAST(sd.started_at, EXCLUDED.started_at),
        last_event_at = GREATEST(sd.last_event_at, EXCLUDED.last_event_at),
        {", ".join(f"{c} = {_earliest_merged(c)}" for c in _EARLIEST_COLUMNS)},
        jwt_email = GREATEST(sd.jwt_email COLLATE "C", EXCLUDED.jwt_email COLLATE "C"),
        dev_name_max = GREATEST(sd.dev_name_max COLLATE "C", EXCLUDED.dev_name_max COLLATE "C"),
        updated_at = EXCLUDED.updated_at""",
]

# Keys deferred after a timeout carry a marked_at in the future: they are not due before it.
# Keys flagged `alone` (see _TIMEOUT_STRIKES) are never claimed in a batch.
_READ_KEYS = """
SELECT day, session_id, kinds, version, timeouts FROM rollup_dirty
WHERE NOT alone AND marked_at <= clock_timestamp() ORDER BY marked_at LIMIT $1"""
_READ_ALONE_KEY = """
SELECT day, session_id, kinds, version, timeouts FROM rollup_dirty
WHERE alone AND marked_at <= clock_timestamp() ORDER BY marked_at LIMIT 1"""
# Rows locked in the order ingest locks them, as everywhere.
_FLAG_ALONE = """
WITH k AS (
    SELECT d.day, d.session_id FROM rollup_dirty d
    JOIN unnest($1::date[], $2::text[]) AS u(day, session_id) USING (day, session_id)
    ORDER BY d.day, d.session_id FOR UPDATE OF d
)
UPDATE rollup_dirty r SET alone = true FROM k WHERE r.day = k.day AND r.session_id = k.session_id"""
_CREATE_KEYS = (
    f"CREATE TEMP TABLE IF NOT EXISTS {_KEYS} (day date, session_id text, kinds int, version bigint) "
    "ON COMMIT DELETE ROWS"
)
_LOAD_KEYS = f"INSERT INTO {_KEYS} SELECT * FROM unnest($1::date[], $2::text[], $3::int[], $4::int8[])"
# Every claimed row is locked first, in (day, session_id) order: the order in which ingest
# upserts them, so the two cannot deadlock. A row an open ingest has marked again is locked
# once that ingest commits, and the next two statements see its new version.
_LOCK_CLAIMED_KEYS = f"""
SELECT 1 FROM rollup_dirty d JOIN {_KEYS} k USING (day, session_id)
ORDER BY d.day, d.session_id FOR UPDATE OF d"""
_RELEASE_KEYS = f"""
DELETE FROM rollup_dirty r USING {_KEYS} k
WHERE r.day = k.day AND r.session_id = k.session_id AND r.version = k.version"""
# A key marked again during the recompute is stale only since the recompute began.
_RESTAMP_KEYS = f"""
UPDATE rollup_dirty r SET marked_at = now(), timeouts = 0, alone = false FROM {_KEYS} k
WHERE r.day = k.day AND r.session_id = k.session_id AND r.version <> k.version AND r.marked_at < now()"""
# Whatever its version: a key that cannot be recomputed is usually marked again meanwhile.
_DROP_KEY = "DELETE FROM rollup_dirty WHERE day = $1 AND session_id = $2"
_DEFER_KEY = """
UPDATE rollup_dirty
SET timeouts = timeouts + 1, alone = true,
    marked_at = clock_timestamp() + make_interval(secs => (timeouts + 1) * $3::float8)
WHERE day = $1 AND session_id = $2"""
# Sorted locks, as everywhere ingest may mark the same keys (a late record of an old day).
_DROP_EXPIRED_KEYS = """
DELETE FROM rollup_dirty r USING (
    SELECT day, session_id FROM rollup_dirty WHERE day < $1 ORDER BY day, session_id FOR UPDATE
) expired
WHERE r.day = expired.day AND r.session_id = expired.session_id"""
# Backfill / repair: queue every (day, session) found in raw data for all families.
_MARK_RANGE = """
INSERT INTO rollup_dirty AS r (day, session_id, kinds)
SELECT day, session_id, bit_or(kinds) FROM (
    SELECT (ts AT TIME ZONE 'UTC')::date AS day, session_id, 1 AS kinds FROM log_events
    WHERE ts >= ($1::date::timestamp AT TIME ZONE 'UTC') AND ts < (($2::date + 1)::timestamp AT TIME ZONE 'UTC')
    UNION ALL
    SELECT (ts AT TIME ZONE 'UTC')::date, session_id, 2 FROM hook_events
    WHERE ts >= ($1::date::timestamp AT TIME ZONE 'UTC') AND ts < (($2::date + 1)::timestamp AT TIME ZONE 'UTC')
      AND session_id <> ''
    UNION ALL
    SELECT (ts AT TIME ZONE 'UTC')::date, session_id, 4 FROM spans
    WHERE ts >= ($1::date::timestamp AT TIME ZONE 'UTC') AND ts < (($2::date + 1)::timestamp AT TIME ZONE 'UTC')
    UNION ALL
    SELECT (ts AT TIME ZONE 'UTC')::date, session_id, 8 FROM metric_points
    WHERE ts >= ($1::date::timestamp AT TIME ZONE 'UTC') AND ts < (($2::date + 1)::timestamp AT TIME ZONE 'UTC')
) raw
GROUP BY 1, 2
ORDER BY 1, 2
ON CONFLICT (day, session_id) DO UPDATE
    SET kinds = r.kinds | EXCLUDED.kinds, version = r.version + 1"""


class RollupRefresher:
    def __init__(
        self,
        engine: AnalyticsPgEngine,
        batch_size: int,
        max_run_seconds: float = 120.0,
        raw_retention_days: int | None = None,
        clock: Callable[[], date] | None = None,
        lock_timeout_ms: int = _LOCK_TIMEOUT_MS,
    ) -> None:
        """`raw_retention_days` enables dropping keys past raw retention; `clock` defaults to the database's date."""
        self._engine = engine
        self._batch_size = batch_size
        self._max_run_seconds = max_run_seconds
        self._raw_retention_days = raw_retention_days
        self._clock = clock
        self._lock_timeout = f"SET LOCAL lock_timeout = {int(lock_timeout_ms)}"
        self._flagged = False  # the last step left keys to take one at a time
        self._drained = False  # the last step claimed a short batch: nothing else is due

    async def claim(self, conn: asyncpg.Connection) -> list[asyncpg.Record]:
        """The oldest queued keys with their versions, read without locking them."""
        return await conn.fetch(_READ_KEYS, self._batch_size)

    async def recompute(self, conn: asyncpg.Connection, keys: list[asyncpg.Record]) -> int:
        """Recompute the keys' slices and release the keys not marked again meanwhile.

        Returns how many keys were recomputed. A batch PostgreSQL rejects for its data
        (_REJECTED_DATA) is split until the key at fault is alone, and that key is dropped. A
        batch that times out has its keys flagged to be taken one at a time; a key that times
        out alone is deferred or dropped (_TIMEOUT_STRIKES). Any other failure (connection lost,
        deadlock, lock timeout) propagates and the next run retries.
        """
        try:
            await self._recompute(conn, keys)
        except _REJECTED_DATA as exc:
            if len(keys) == 1:
                await self._drop(conn, keys[0], exc)
                return 0
            middle = len(keys) // 2
            return await self.recompute(conn, keys[:middle]) + await self.recompute(conn, keys[middle:])
        except asyncpg.exceptions.QueryCanceledError as exc:
            if len(keys) == 1:
                await self._timed_out(conn, keys[0], exc)
                return 0
            # One slow key then costs one more timeout, instead of one per halving.
            await conn.execute(_FLAG_ALONE, [k["day"] for k in keys], [k["session_id"] for k in keys])
            self._flagged = True
            return 0
        return len(keys)

    async def _recompute(self, conn: asyncpg.Connection, keys: list[asyncpg.Record]) -> None:
        timeout = _REFRESH_CLIENT_TIMEOUT_S
        async with conn.transaction():
            await conn.execute(_REFRESH_STATEMENT_TIMEOUT, timeout=timeout)
            await conn.execute(self._lock_timeout, timeout=timeout)
            await conn.execute(_CREATE_KEYS, timeout=timeout)
            await conn.execute(
                _LOAD_KEYS,
                [k["day"] for k in keys],
                [k["session_id"] for k in keys],
                [k["kinds"] for k in keys],
                [k["version"] for k in keys],
                timeout=timeout,
            )
            await conn.execute(f"ANALYZE {_KEYS}", timeout=timeout)
            for statement in RECOMPUTE:
                await conn.execute(statement, timeout=timeout)
            for statement in (_LOCK_CLAIMED_KEYS, _RELEASE_KEYS, _RESTAMP_KEYS):
                await conn.execute(statement, timeout=timeout)

    async def _timed_out(self, conn: asyncpg.Connection, key: asyncpg.Record, error: Exception) -> None:
        timeouts = key["timeouts"] + 1  # in a row: a recompute in time resets them
        if timeouts >= _TIMEOUT_STRIKES:
            await self._drop(conn, key, error)
            return
        delay_s = timeouts * _DEFER_STEP_S
        await conn.execute(_DEFER_KEY, key["day"], key["session_id"], float(_DEFER_STEP_S))
        logger.warning(
            f"cli_analytics: recomputing rollup key day={key['day']} session_id={key['session_id']!r} timed out "
            f"({timeouts} of {_TIMEOUT_STRIKES} in a row); it is retried in {delay_s // 60} minutes"
        )

    async def _drop(self, conn: asyncpg.Connection, key: asyncpg.Record, error: Exception) -> None:
        status = await conn.execute(_DROP_KEY, key["day"], key["session_id"])
        if int(status.rsplit(" ", 1)[-1]):
            logger.error(
                f"cli_analytics: dropped rollup key day={key['day']} session_id={key['session_id']!r}; "
                f"its rollups stay as they were because its data cannot be recomputed: {error}"
            )

    async def _drop_expired_keys(self, conn: asyncpg.Connection) -> None:
        if self._raw_retention_days is None:
            return
        today = self._clock() if self._clock else await conn.fetchval(TODAY_UTC_SQL)
        cutoff = today - timedelta(days=self._raw_retention_days)
        dropped = int((await conn.execute(_DROP_EXPIRED_KEYS, cutoff)).rsplit(" ", 1)[-1])
        if dropped:
            logger.warning(
                f"cli_analytics: dropped {dropped} rollup keys of days before {cutoff}, past raw retention; "
                "their rollups are kept as they are"
            )

    async def refresh_batch(self, conn: asyncpg.Connection) -> int:
        """Recompute the next due work: a key to take alone if any, else a batch; returns how
        many keys were recomputed. A key taken alone is released, deferred or dropped, so the
        queue moves even when every recompute runs out of time."""
        self._flagged = False
        key = await conn.fetchrow(_READ_ALONE_KEY)
        if key is not None:
            return await self.recompute(conn, [key])
        keys = await self.claim(conn)
        self._drained = len(keys) < self._batch_size
        return await self.recompute(conn, keys) if keys else 0

    async def refresh(self, conn: asyncpg.Connection | None = None) -> int:
        """Work the queue down for at most `max_run_seconds`; returns how many keys were recomputed.

        Runs on `conn` when given (the connection holding the job's advisory lock), so the
        job needs one pooled connection, not two.
        """
        if conn is None:
            async with self._engine.acquire() as acquired:
                return await self.refresh(acquired)
        started = time.monotonic()
        deadline = started + self._max_run_seconds
        total = 0
        await self._drop_expired_keys(conn)
        while True:
            self._drained = False
            total += await self.refresh_batch(conn)  # at least one step, whatever the time
            if self._drained and not self._flagged:
                break  # a short batch: nothing else is due
            if time.monotonic() > deadline:
                break
        if total:
            logger.debug("cli_analytics: refreshed %d rollup keys in %.2fs", total, time.monotonic() - started)
        return total

    async def mark_dirty(self, first_day: date, last_day: date, conn: asyncpg.Connection | None = None) -> int:
        """Queue every (day, session) with raw data in [first_day, last_day] for recomputation.

        One day per statement, each with the refresh time budget: a multi-week range in one
        statement would outlast a dashboard's limit and queue nothing.
        """
        if conn is None:
            async with self._engine.acquire() as acquired:
                return await self.mark_dirty(first_day, last_day, acquired)
        marked = 0
        day = first_day
        while day <= last_day:
            async with conn.transaction():
                await conn.execute(_REFRESH_STATEMENT_TIMEOUT, timeout=_REFRESH_CLIENT_TIMEOUT_S)
                status = await conn.execute(_MARK_RANGE, day, day, timeout=_REFRESH_CLIENT_TIMEOUT_S)
            marked += int(status.rsplit(" ", 1)[-1])
            day += timedelta(days=1)
        return marked

    async def backlog(self, conn: asyncpg.Connection | None = None) -> tuple[int, float | None]:
        """Queued keys and the age in seconds of the oldest one (for monitoring)."""
        if conn is None:
            async with self._engine.acquire() as acquired:
                return await self.backlog(acquired)
        row = await conn.fetchrow(
            "SELECT count(*) AS n, extract(epoch FROM clock_timestamp() - min(marked_at)) AS age FROM rollup_dirty"
        )
        return int(row["n"]), (float(row["age"]) if row["age"] is not None else None)
