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

"""Rollup refresher: the PostgreSQL job that maintains the rollup tables.

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

from codemie.repository.cli_analytics.postgres.dirty import USAGE_REQUEST_REACH, RollupFamily
from codemie.repository.cli_analytics.postgres.engine import CLIENT_TIMEOUT_MARGIN_S, AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.maintenance import TODAY_UTC_SQL
from codemie.repository.cli_analytics.postgres.otlp import MAX_KEY_BYTES, MAX_PATH_BYTES
from codemie.repository.cli_analytics.postgres.session_derived import update_session_derived
from codemie.repository.cli_analytics.vocabulary import (
    ACTIVE_TIME_METRICS,
    DIMENSION_HOOK_TYPES,
    EDIT_TOOLS,
    LINES_METRICS,
    SENTINEL_EXACT,
    SENTINEL_PREFIX_RAW,
    SENTINEL_PREFIX_TRIMMED,
    SKILL_DISPATCH_EVENT,
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

# Client text the recompute reads from attrs or usage[] never becomes a key or an indexed value when
# it is longer than this: it is skipped ('' = unknown), never cut. A user_email or story_id longer
# than this is no candidate (both columns are indexed). It is the length ingest cuts its own typed
# key columns to, so one bound holds for every key.
_MAX_KEY_BYTES = MAX_KEY_BYTES


def _bounded(value: str) -> str:
    """`value` (a SQL text expression) when it fits a key column, else NULL."""
    return f"CASE WHEN octet_length({value}) <= {_MAX_KEY_BYTES} THEN {value} END"


_INT32_MAX = 2**31 - 1
_INT64_MAX = 2**63 - 1


def _fits(value: str, bits: int = 32) -> str:
    """`value` when it fits a signed integer column of `bits` bits, else NULL (never class 22)."""
    top = _INT32_MAX if bits == 32 else _INT64_MAX
    return f"CASE WHEN {value} BETWEEN {-top - 1} AND {top} THEN {value} END"


# A client number is read as text and cast to numeric, which holds at most 16,383 digits after the
# point and raises on more. No real count or duration has a fraction near this bound.
_MAX_FRACTION_DIGITS = 30


def _whole_number(value: str, digits: int) -> str:
    """The integer part of `value` (a SQL text expression) as bigint, when it is a non-negative
    decimal number of at most `digits` integer digits; else NULL, never an error."""
    number = f"'^[0-9]{{1,{digits}}}([.][0-9]{{1,{_MAX_FRACTION_DIGITS}}})?$'"
    return f"CASE WHEN {value} ~ {number} THEN trunc({value}::numeric)::bigint END"


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _text_array(values: tuple[str, ...]) -> str:
    """A SQL array of code constants (never input)."""
    return "ARRAY[" + ", ".join(_literal(v) for v in values) + "]::text[]"


_KEYS = "_cli_rollup_keys"
_DAY_LO = "(k.day::timestamp AT TIME ZONE 'UTC')"
_DAY_HI = "((k.day + 1)::timestamp AT TIME ZONE 'UTC')"
_LOG, _DIMS, _SPANS, _METRICS, _USAGE, _SESSION = (
    int(f)
    for f in (
        RollupFamily.LOG_FACTS,
        RollupFamily.DIMENSIONS,
        RollupFamily.SPAN_FACTS,
        RollupFamily.METRIC_FACTS,
        RollupFamily.USAGE_FACTS,
        RollupFamily.SESSION,
    )
)
_TOOL, _EXEC, _INTERACTION = int(SpanKind.TOOL), int(SpanKind.TOOL_EXECUTION), int(SpanKind.INTERACTION)
_DIM_TYPES = _text_array(DIMENSION_HOOK_TYPES)

# A prompt that may become the session's first prompt (session_dims.first_prompt).
_PROMPT_OK = (
    f"btrim(h.prompt_body) <> '' AND lower(btrim(h.prompt_body)) <> ALL({_text_array(SENTINEL_EXACT)})"
    + "".join(f" AND NOT starts_with(btrim(h.prompt_body), {_literal(p)})" for p in SENTINEL_PREFIX_TRIMMED)
    + "".join(f" AND NOT starts_with(h.prompt_body, {_literal(p)})" for p in SENTINEL_PREFIX_RAW)
)


def _earliest(column: str, condition: str = "") -> str:
    """The earliest non-empty value of the column by ts, ties to the first ingested."""
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


def _utc(day: str) -> str:
    """The start of a UTC day given as a SQL date expression."""
    return f"(({day})::timestamp AT TIME ZONE 'UTC')"


def _attr_ms(alias: str, key: str) -> str:
    """A non-negative millisecond count read from `<alias>.attrs`, as bigint. Anything else (text, a
    negative or implausible number, a missing key) is NULL, never an error: the bound keeps every sum
    of it far inside bigint's range, as _MAX_COUNTER does for the counters."""
    return _whole_number(f"({alias}.attrs->>{_literal(key)})", len(str(_MAX_COUNTER)) - 1)


# Requests of the claimed keys with bit 1 or 16, one row each, for cost_daily and the
# session_usage tables. Transaction-scoped like _KEYS: rows vanish at commit or rollback.
#   req_kind   matched     a transcript request paired with its OTel record (either tier)
#              transcript  a transcript request without an OTel counterpart
#              service     an unmatched OTel request on a day with plugin data
#              otel_only   an OTel request on a day without plugin data
# Key columns hold their final value per kind ('' = unknown, never NULL): `model` and the scope
# columns as session_usage takes them, `model_name`/`user_email`/`query_source` as
# cost_daily takes them on a plugin day. On an OTel-only day cost_daily keeps its original grain and
# does not use the scope columns. Measures stay NULL when unknown; tokens come from one source only.
_REQUESTS = "_cli_requests"
_CREATE_REQUESTS = (
    f"CREATE TEMP TABLE IF NOT EXISTS {_REQUESTS} (day date, hour timestamptz, ts timestamptz, session_id text, "
    "req_kind text, model text, model_name text, user_email text, query_source text, speed text, inference_geo text, "
    "service_tier text, scope_kind text, scope_name text, agent_id text, agent_type text, input_tokens bigint, "
    "output_tokens bigint, cache_read_tokens bigint, cache_creation_tokens bigint, cache_5m bigint, cache_1h bigint, "
    "thinking_tokens bigint, web_search_requests int, web_fetch_requests int, cost_usd float8, duration_ms bigint, "
    "ttft_ms bigint) ON COMMIT DELETE ROWS"
)
# Two records of one request paired by fingerprint are at most this far apart; a day is widened by
# it on both sides for that tier.
_MATCH = "interval '5 seconds'"
# The identifier tier pairs a transcript request and an OTel record across midnight when both lie
# within this reach of it, so the candidate sets of both reach this far past the day's edges. The
# rows of one transcript request are read this far past those, so a request whose rows straddle an
# edge is still grouped whole and never counted on two days. The same reach as a usage_requests row
# and an api_request with a request_id mark the neighbouring day with (dirty.py).
_GROUP_REACH = f"interval '{int(USAGE_REQUEST_REACH.total_seconds())} seconds'"
_TRANSCRIPT_CACHE_WRITE = (
    "CASE WHEN a.cache_5m IS NULL AND a.cache_1h IS NULL THEN NULL "
    "ELSE coalesce(a.cache_5m, 0) + coalesce(a.cache_1h, 0) END"
)
_TRANSCRIPT_COLUMNS = (
    "coalesce(a.speed, ''), coalesce(a.inference_geo, ''), coalesce(a.service_tier, ''), coalesce(a.scope_kind, ''), "
    "coalesce(a.scope_name, ''), coalesce(a.agent_id, ''), coalesce(a.agent_type, ''), "
    f"a.input_tokens, a.output_tokens, a.cache_read_tokens, {_TRANSCRIPT_CACHE_WRITE}, a.cache_5m, a.cache_1h, "
    "a.thinking_tokens, a.web_search_requests, a.web_fetch_requests"
)
_T_ORDER = (
    "ts, request_id, message_id, model_raw, model, input_tokens, output_tokens, cache_read_tokens, cache_5m, "
    "cache_1h, thinking_tokens, web_search_requests, web_fetch_requests, user_email, speed, inference_geo, "
    "service_tier, scope_kind, scope_name, agent_id, agent_type, row_key"
)

# Key text read from an OTel record's attrs (the typed columns are bounded at ingest).
_OTEL_SPEED = _bounded("l.attrs->>'speed'")

# What the plugin strips from a raw model name to get `model`, in its order: a vendor prefix, a
# region prefix, a date suffix, two version suffixes. An OTel name with no transcript counterpart
# goes through the same steps, so a model has one `model` value whichever source named it.
_MODEL_AFFIXES = (
    "^(anthropic|bedrock|vertex)[/.]",
    "^(us|eu|apac)[.]anthropic[.]",
    "[-_][0-9]{8}$",
    "[-_]v[0-9]+[:@][0-9]+$",
    "@[0-9]+$",
)


def _normalised_model(value: str) -> str:
    """`value` (a SQL text expression, a raw model name) as the plugin normalises it: lower case,
    trimmed, without the affixes. NULL stays NULL."""
    expression = f"regexp_replace(lower({value}), '^[[:space:]]+|[[:space:]]+$', '', 'g')"
    for affix in _MODEL_AFFIXES:
        expression = f"regexp_replace({expression}, {_literal(affix)}, '')"
    return expression


_BUILD_REQUESTS = f"""INSERT INTO {_REQUESTS} (day, hour, ts, session_id, req_kind, model, model_name, user_email,
    query_source, speed, inference_geo, service_tier, scope_kind, scope_name, agent_id, agent_type, input_tokens,
    output_tokens, cache_read_tokens, cache_creation_tokens, cache_5m, cache_1h, thinking_tokens,
    web_search_requests, web_fetch_requests, cost_usd, duration_ms, ttft_ms)
WITH RECURSIVE
-- Each key's day widened by 5 s (lo, hi: the fingerprint tier) and by 1 h (id_lo, id_hi: the identifier
-- tier); `plugin`: the session has usage_requests rows in the day widened by 5 s (per day).
rk AS (
    SELECT w.day, w.session_id, w.lo, w.hi, w.id_lo, w.id_hi,
           EXISTS (SELECT 1 FROM usage_requests u WHERE u.session_id = w.session_id AND u.ts >= w.lo AND u.ts < w.hi)
               AS plugin
    FROM (SELECT k.day, k.session_id, {_DAY_LO} - {_MATCH} AS lo, {_DAY_HI} + {_MATCH} AS hi,
                 {_DAY_LO} - {_GROUP_REACH} AS id_lo, {_DAY_HI} + {_GROUP_REACH} AS id_hi
          FROM {_KEYS} k WHERE k.kinds & {_LOG | _USAGE} <> 0) w
),
-- Transcript requests: rows grouped by (session, request id or else message id, raw model), the
-- maximum of every field (never the sum), the latest ts. A row with neither id is a group of its own.
-- Those of the day widened by 1 h: only the day's own are written, the rest are pairing candidates.
tr AS (
    SELECT r.day, r.session_id, r.lo, r.hi, max(u.ts) AS ts, max(nullif(u.request_id, '')) AS request_id,
           max(nullif(u.message_id, '')) AS message_id, max(u.model_raw) AS model_raw, max(u.model) AS model,
           max({_bounded("u.user_email")}) AS user_email, max(u.speed) AS speed, max(u.inference_geo) AS inference_geo,
           max(u.service_tier) AS service_tier, max(u.scope_kind) AS scope_kind, max(u.scope_name) AS scope_name,
           max(u.agent_id) AS agent_id, max(u.agent_type) AS agent_type, max(u.input_tokens) AS input_tokens,
           max(u.output_tokens) AS output_tokens, max(u.cache_read_tokens) AS cache_read_tokens,
           max(u.cache_creation_5m_tokens) AS cache_5m, max(u.cache_creation_1h_tokens) AS cache_1h,
           max(u.thinking_tokens) AS thinking_tokens, max(u.web_search_requests) AS web_search_requests,
           max(u.web_fetch_requests) AS web_fetch_requests,
           max(CASE WHEN coalesce(nullif(u.request_id, ''), nullif(u.message_id, '')) IS NULL
                    THEN u.tableoid::text || u.ctid::text END) AS row_key
    FROM rk r
    JOIN usage_requests u ON u.session_id = r.session_id
     AND u.ts >= r.id_lo - {_GROUP_REACH} AND u.ts < r.id_hi + {_GROUP_REACH}
    GROUP BY r.day, r.session_id, r.lo, r.hi, r.id_lo, r.id_hi,
             coalesce(nullif(u.request_id, ''), nullif(u.message_id, '')), coalesce(u.model_raw, ''),
             CASE WHEN coalesce(nullif(u.request_id, ''), nullif(u.message_id, '')) IS NULL
                  THEN u.tableoid::text || u.ctid::text END
    HAVING max(u.ts) >= r.id_lo AND max(u.ts) < r.id_hi
),
-- `tid` orders them by time, then by every stored field: pairing never depends on row order.
t AS (
    SELECT tr.*, (tr.ts AT TIME ZONE 'UTC')::date AS t_day,
           row_number() OVER (PARTITION BY tr.day, tr.session_id ORDER BY {_T_ORDER}) AS tid
    FROM tr
),
-- OTel requests of the day widened by 1 h; `oid` orders them by time, then by every stored column.
o AS (
    SELECT r.day, r.session_id, r.lo, r.hi, l.ts, (l.ts AT TIME ZONE 'UTC')::date AS o_day,
           nullif(l.request_id, '') AS request_id, l.model, l.user_email,
           l.query_source, l.input_tokens, l.output_tokens, l.cache_read_tokens, l.cache_creation_tokens, l.cost_usd,
           CASE WHEN {_OTEL_SPEED} = 'normal' THEN 'standard' ELSE nullif({_OTEL_SPEED}, '') END AS speed,
           nullif({_bounded("l.attrs->>'agent.name'")}, '') AS agent_name,
           {_attr_ms("l", "duration_ms")} AS duration_ms, {_attr_ms("l", "ttft_ms")} AS ttft_ms,
           row_number() OVER (
               PARTITION BY r.day, r.session_id
               ORDER BY l.ts, l.request_id, l.model, l.user_email, l.query_source, l.input_tokens, l.output_tokens,
                        l.cache_read_tokens, l.cache_creation_tokens, l.cost_usd, l.prompt_id, l.attrs::text,
                        l.tableoid, l.ctid
           ) AS oid
    FROM rk r
    JOIN log_events l ON l.session_id = r.session_id AND l.ts >= r.id_lo AND l.ts < r.id_hi
    WHERE l.event_kind = {int(EventKind.API_REQUEST)}
),
-- Tier 1: equal non-empty request ids, the n-th transcript request with the n-th OTel record, each
-- within 1 h of the other one's day: on one day, or on both sides of a midnight within 1 h of it. The
-- test is symmetric, so the recomputes of both days find the same pair.
t1 AS (
    SELECT a.day, a.session_id, a.tid, b.oid
    FROM (SELECT day, session_id, request_id, tid, ts, t_day,
                 row_number() OVER (PARTITION BY day, session_id, request_id ORDER BY tid) AS n
          FROM t WHERE request_id IS NOT NULL) a
    JOIN (SELECT day, session_id, request_id, oid, ts, o_day,
                 row_number() OVER (PARTITION BY day, session_id, request_id ORDER BY oid) AS n
          FROM o WHERE request_id IS NOT NULL) b USING (day, session_id, request_id, n)
    WHERE b.ts >= {_utc("a.t_day")} - {_GROUP_REACH} AND b.ts < {_utc("a.t_day + 1")} + {_GROUP_REACH}
      AND a.ts >= {_utc("b.o_day")} - {_GROUP_REACH} AND a.ts < {_utc("b.o_day + 1")} + {_GROUP_REACH}
),
-- Tier 2 candidates among what tier 1 left, both within the day widened by 5 s: not both ids present,
-- the raw model, three token counts and the cache-write total equal, at most 5 s apart.
c AS (
    SELECT a.day, a.session_id, a.tid, b.oid, greatest(b.ts - a.ts, a.ts - b.ts) AS gap
    FROM t a
    JOIN o b ON b.day = a.day AND b.session_id = a.session_id
     AND b.ts >= a.ts - {_MATCH} AND b.ts <= a.ts + {_MATCH}
    WHERE a.ts >= a.lo AND a.ts < a.hi AND b.ts >= b.lo AND b.ts < b.hi
      AND (a.request_id IS NULL OR b.request_id IS NULL)
      AND b.model = a.model_raw
      AND coalesce(b.input_tokens, 0) = coalesce(a.input_tokens, 0)
      AND coalesce(b.output_tokens, 0) = coalesce(a.output_tokens, 0)
      AND coalesce(b.cache_read_tokens, 0) = coalesce(a.cache_read_tokens, 0)
      AND coalesce(b.cache_creation_tokens, 0) = coalesce(a.cache_5m, 0) + coalesce(a.cache_1h, 0)
      AND NOT EXISTS (SELECT 1 FROM t1 WHERE t1.day = a.day AND t1.session_id = a.session_id AND t1.tid = a.tid)
      AND NOT EXISTS (SELECT 1 FROM t1 WHERE t1.day = b.day AND t1.session_id = b.session_id AND t1.oid = b.oid)
),
d AS (
    SELECT c.*, count(*) OVER (PARTITION BY c.day, c.session_id, c.tid) AS t_edges,
           count(*) OVER (PARTITION BY c.day, c.session_id, c.oid) AS o_edges
    FROM c
),
-- A candidate that shares neither side is a pair as it is. The rest are paired one to one in time
-- order: each transcript request takes the nearest free record, ties to the earlier (lower oid).
q AS (
    SELECT x.day, x.session_id, x.tid, row_number() OVER (PARTITION BY x.day, x.session_id ORDER BY x.tid) AS n
    FROM (SELECT DISTINCT day, session_id, tid FROM d WHERE t_edges > 1 OR o_edges > 1) x
),
walk (day, session_id, n, used, tid, oid) AS (
    SELECT DISTINCT day, session_id, 0::bigint, ARRAY[]::bigint[], NULL::bigint, NULL::bigint FROM q
    UNION ALL
    SELECT w.day, w.session_id, w.n + 1, CASE WHEN f.oid IS NULL THEN w.used ELSE w.used || f.oid END, q.tid, f.oid
    FROM walk w
    JOIN q ON q.day = w.day AND q.session_id = w.session_id AND q.n = w.n + 1
    LEFT JOIN LATERAL (
        SELECT d.oid FROM d
        WHERE d.day = q.day AND d.session_id = q.session_id AND d.tid = q.tid AND d.oid <> ALL (w.used)
        ORDER BY d.gap, d.oid
        LIMIT 1
    ) f ON true
),
p AS (
    SELECT day, session_id, tid, oid FROM t1
    UNION ALL
    SELECT day, session_id, tid, oid FROM d WHERE t_edges = 1 AND o_edges = 1
    UNION ALL
    SELECT day, session_id, tid, oid FROM walk WHERE oid IS NOT NULL
)
-- A matched request takes the day and hour of its transcript time, wherever its OTel record lies.
SELECT a.day, date_trunc('hour', a.ts, 'UTC'), a.ts, a.session_id, 'matched', coalesce(a.model, ''),
       coalesce(b.model, ''), coalesce(b.user_email, ''), coalesce(b.query_source, ''), {_TRANSCRIPT_COLUMNS},
       b.cost_usd, b.duration_ms, b.ttft_ms
FROM p
JOIN t a ON a.day = p.day AND a.session_id = p.session_id AND a.tid = p.tid
JOIN o b ON b.day = p.day AND b.session_id = p.session_id AND b.oid = p.oid
WHERE a.t_day = a.day
UNION ALL
SELECT a.day, date_trunc('hour', a.ts, 'UTC'), a.ts, a.session_id, 'transcript', coalesce(a.model, ''),
       coalesce(a.model_raw, ''), coalesce(a.user_email, ''), '', {_TRANSCRIPT_COLUMNS},
       NULL, NULL, NULL
FROM t a
WHERE a.t_day = a.day
  AND NOT EXISTS (SELECT 1 FROM p WHERE p.day = a.day AND p.session_id = a.session_id AND p.tid = a.tid)
UNION ALL
-- An unmatched OTel record of the day: a service request when the day has plugin data, else an
-- OTel-only request, named after its agent when it carries `agent.name`. A record paired with a
-- transcript request of the neighbour day is in `p` and is written on that day only. `model` is the
-- OTel name normalised as the plugin does it; `model_name` keeps the name as OTel sent it.
SELECT b.day, date_trunc('hour', b.ts, 'UTC'), b.ts, b.session_id,
       CASE WHEN r.plugin THEN 'service' ELSE 'otel_only' END,
       coalesce({_normalised_model("b.model")}, ''), coalesce(b.model, ''), coalesce(b.user_email, ''),
       coalesce(b.query_source, ''),
       coalesce(b.speed, ''), '', '',
       CASE WHEN r.plugin THEN 'service' WHEN b.agent_name IS NOT NULL THEN 'agent' ELSE '' END,
       CASE WHEN r.plugin THEN coalesce(b.query_source, '') ELSE coalesce(b.agent_name, '') END,
       '', '', b.input_tokens, b.output_tokens, b.cache_read_tokens, b.cache_creation_tokens, NULL, NULL, NULL,
       NULL, NULL, b.cost_usd, b.duration_ms, b.ttft_ms
FROM o b
JOIN rk r ON r.day = b.day AND r.session_id = b.session_id
WHERE b.o_day = b.day
  AND NOT EXISTS (SELECT 1 FROM p WHERE p.day = b.day AND p.session_id = b.session_id AND p.oid = b.oid)"""


def _json_count(element: str, key: str, digits: int) -> str:
    """A non-negative count of at most `digits` digits read from the jsonb object `element`, as bigint.
    Anything else (text, a negative number, a missing key, a non-object) is NULL, never an error; the
    bound keeps every sum of it far inside its column's range."""
    return _whole_number(f"({element}->>{_literal(key)})", digits)


def _totals_count(key: str) -> str:
    """A token count of a usage[] row of agent.subagent.usage (bigint columns)."""
    return _json_count("e.item", key, 12)


def _totals_calls(key: str) -> str:
    """A call count of a usage[] row of agent.subagent.usage (int columns)."""
    return _json_count("e.item", key, 6)


_SUBAGENT_TOTALS_EVENT = _literal("agent.subagent.usage")
_TOTALS_ATTRS = "jsonb_build_object('token_source', 'subagent_usage')"


def _otel_usage_exists(session_id: str) -> str:
    """NOT EXISTS over session_usage rows built from OTel records (attrs.otel_requests), on any day and
    for any agent: raw log_events retention may have purged the api_request rows, while these rows stay
    for session retention."""
    return (
        f"NOT EXISTS (SELECT 1 FROM session_usage su2 WHERE su2.session_id = {session_id} "
        "AND su2.attrs ? 'otel_requests')"
    )


def _totals_are_a_source(session_id: str, agent_id: str) -> str:
    """A subagent's totals are a token source only when the session has no usage_requests of
    that agent and no OTel api_request, both on any day. Otherwise they would count requests twice.

    Nor when session_usage holds rows of the agent built from its requests: raw retention may have
    purged the requests, while those rows stay for session retention.

    Nor when the session has session_usage rows built from OTel records (attrs.otel_requests), on any
    day and for any agent: raw log_events retention may have purged the api_request rows."""
    return (
        f"NOT EXISTS (SELECT 1 FROM usage_requests u WHERE u.session_id = {session_id} "
        f"AND coalesce(u.agent_id, '') = {agent_id}) "
        f"AND NOT EXISTS (SELECT 1 FROM log_events l WHERE l.session_id = {session_id} "
        f"AND l.event_kind = {int(EventKind.API_REQUEST)}) "
        f"AND NOT EXISTS (SELECT 1 FROM session_usage su WHERE su.session_id = {session_id} "
        f"AND su.agent_id = {agent_id} AND coalesce(su.attrs->>'token_source', '') <> 'subagent_usage') "
        f"AND {_otel_usage_exists(session_id)}"
    )


def _has_totals(attrs: str) -> str:
    """The event carries totals: its `usage` is an array with at least one object. An event without
    them is no totals event, so it never replaces the rows written from an earlier one."""
    return (
        f"EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof({attrs}->'usage') = 'array' "
        f"THEN {attrs}->'usage' END) i (item) WHERE jsonb_typeof(i.item) = 'object')"
    )


def _latest_totals_day(session_id: str, agent_id: str) -> str:
    """The UTC day of the agent's latest agent.subagent.usage event with totals (NULL when none is kept)."""
    return (
        f"(SELECT (h.ts AT TIME ZONE 'UTC')::date FROM hook_events h WHERE h.session_id = {session_id} "
        f"AND h.event_type = {_SUBAGENT_TOTALS_EVENT} AND coalesce(h.agent_id, '') = {agent_id} "
        f"AND {_has_totals('h.attrs')} "
        "ORDER BY h.ts DESC, h.ingest_seq DESC LIMIT 1)"
    )


# The usage[] rows of each agent's latest agent.subagent.usage event with totals (one event per agent,
# so a re-sent event is counted once), for the key of the event's own day, when they are a token source.
# Key text of a usage[] row is bounded; an agent id too long for a key column is no subagent (as in
# subagent_invocations), so its totals are skipped.
_TOTALS_ROWS = f"""SELECT k.session_id, k.day, {_bounded("e.item->>'model'")}, {_bounded("e.item->>'speed'")},
           {_bounded("e.item->>'inference_geo'")}, {_bounded("e.item->>'scope_kind'")},
           {_bounded("e.item->>'scope_name'")}, v.agent_id,
           {_totals_count("input_tokens")}, {_totals_count("cache_creation_5m_tokens")},
           {_totals_count("cache_creation_1h_tokens")}, {_totals_count("cache_read_tokens")},
           {_totals_count("output_tokens")}, {_totals_calls("api_calls")}, NULL::float8,
           CASE WHEN {_totals_count("cache_creation_5m_tokens")} IS NULL
                 AND {_totals_count("cache_creation_1h_tokens")} IS NULL THEN NULL
                ELSE coalesce({_totals_count("cache_creation_5m_tokens")}, 0)
                     + coalesce({_totals_count("cache_creation_1h_tokens")}, 0) END,
           {_totals_count("thinking_tokens")}, {_totals_calls("web_search_requests")},
           {_totals_calls("web_fetch_requests")}, NULL::bigint, NULL::bigint, true, 0
    FROM (
        SELECT DISTINCT ON (h.session_id, coalesce(h.agent_id, '')) h.session_id, coalesce(h.agent_id, '') AS agent_id,
               h.ts, h.attrs
        FROM (SELECT DISTINCT session_id FROM {_KEYS} WHERE kinds & {_USAGE} <> 0) s
        JOIN hook_events h ON h.session_id = s.session_id AND h.event_type = {_SUBAGENT_TOTALS_EVENT}
         AND octet_length(coalesce(h.agent_id, '')) <= {_MAX_KEY_BYTES}
        WHERE {_has_totals("h.attrs")}
        ORDER BY h.session_id, coalesce(h.agent_id, ''), h.ts DESC, h.ingest_seq DESC
    ) v
    JOIN {_KEYS} k ON k.session_id = v.session_id AND v.ts >= {_DAY_LO} AND v.ts < {_DAY_HI}
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(v.attrs->'usage') = 'array' THEN v.attrs->'usage' END
    ) AS e(item)
    WHERE k.kinds & {_USAGE} <> 0 AND jsonb_typeof(e.item) = 'object'
      AND {_totals_are_a_source("v.session_id", "v.agent_id")}"""

# session_usage, per (day, session): the key day's rows are replaced from the
# requests, plus the subagent totals that are a token source. `api_calls` counts request groups, or
# takes usage[].api_calls; thinking_tokens is part of output_tokens and is never added to it. A sum
# that does not fit its int column (api_calls, the two web request counts) is NULL, never an error.
_SESSION_USAGE = [
    # Totals rows that are no longer a token source, or whose agent's latest event with totals lies on
    # another day, are deleted on whatever day they are; nothing is ever inserted on another day.
    f"""DELETE FROM session_usage t USING (SELECT DISTINCT session_id FROM {_KEYS} WHERE kinds & {_USAGE} <> 0) k
    WHERE t.session_id = k.session_id AND t.attrs->>'token_source' = 'subagent_usage'
      AND (NOT ({_totals_are_a_source("t.session_id", "t.agent_id")})
           OR t.day <> {_latest_totals_day("t.session_id", "t.agent_id")})""",
    *_replace_daily(
        "session_usage",
        _USAGE,
        f"""(session_id, day, model, speed, inference_geo, scope_kind, scope_name, agent_id, input_tokens,
             cache_creation_5m_tokens, cache_creation_1h_tokens, cache_read_tokens, output_tokens, api_calls,
             source_cost_usd, cache_creation_tokens, thinking_tokens, web_search_requests, web_fetch_requests,
             api_duration_ms, ttft_ms_sum, attrs)
        SELECT x.session_id, x.day, coalesce(x.model, ''), coalesce(x.speed, ''), coalesce(x.inference_geo, ''),
               coalesce(x.scope_kind, ''), coalesce(x.scope_name, ''), coalesce(x.agent_id, ''),
               sum(x.input_tokens), sum(x.cache_5m), sum(x.cache_1h), sum(x.cache_read_tokens), sum(x.output_tokens),
               {_fits("sum(x.api_calls)")},
               -- numeric: an exact sum, the same in whatever order the rows are scanned
               sum(x.cost_usd::numeric)::float8,
               sum(x.cache_creation_tokens), sum(x.thinking_tokens), {_fits("sum(x.web_search_requests)")},
               {_fits("sum(x.web_fetch_requests)")}, sum(x.duration_ms), sum(x.ttft_ms),
               CASE WHEN bool_and(x.totals) THEN {_TOTALS_ATTRS}
                    WHEN sum(x.otel) > 0 THEN jsonb_build_object('otel_requests', sum(x.otel)) END
        FROM (
            SELECT r.session_id, r.day, r.model, r.speed, r.inference_geo, r.scope_kind, r.scope_name, r.agent_id,
                   r.input_tokens, r.cache_5m, r.cache_1h, r.cache_read_tokens, r.output_tokens, 1 AS api_calls,
                   r.cost_usd, r.cache_creation_tokens, r.thinking_tokens, r.web_search_requests,
                   r.web_fetch_requests, r.duration_ms, r.ttft_ms, false AS totals,
                   CASE WHEN r.req_kind IN ('matched', 'service', 'otel_only') THEN 1 ELSE 0 END AS otel
            FROM {_REQUESTS} r
            JOIN {_KEYS} k ON k.day = r.day AND k.session_id = r.session_id
            WHERE k.kinds & {_USAGE} <> 0
            UNION ALL
            {_TOTALS_ROWS}
        ) x (session_id, day, model, speed, inference_geo, scope_kind, scope_name, agent_id, input_tokens, cache_5m,
             cache_1h, cache_read_tokens, output_tokens, api_calls, cost_usd, cache_creation_tokens, thinking_tokens,
             web_search_requests, web_fetch_requests, duration_ms, ttft_ms, totals, otel)
        GROUP BY x.session_id, x.day, coalesce(x.model, ''), coalesce(x.speed, ''), coalesce(x.inference_geo, ''),
                 coalesce(x.scope_kind, ''), coalesce(x.scope_name, ''), coalesce(x.agent_id, '')""",
    ),
]

# session_usage_hourly: the session's hours of the key day, rebuilt from the same
# requests by their UTC hour. It never reads session_usage, and subagent totals carry no time, so
# they get no hourly row. The hourly rows of a day sum to its request-derived daily rows. The int
# columns are bounded as in session_usage.
_SESSION_USAGE_HOURLY = [
    f"DELETE FROM session_usage_hourly t USING {_KEYS} k WHERE k.kinds & {_USAGE} <> 0 "
    f"AND t.session_id = k.session_id AND t.hour >= {_DAY_LO} AND t.hour < {_DAY_HI}",
    f"""INSERT INTO session_usage_hourly (session_id, hour, model, speed, inference_geo, service_tier, scope_kind,
                                      scope_name, agent_id, input_tokens, cache_creation_tokens,
                                      cache_creation_5m_tokens, cache_creation_1h_tokens, cache_read_tokens,
                                      output_tokens, thinking_tokens, web_search_requests, web_fetch_requests,
                                      api_calls, api_duration_ms, ttft_ms_sum, source_cost_usd)
    SELECT r.session_id, date_trunc('hour', r.ts, 'UTC'), coalesce(r.model, ''), coalesce(r.speed, ''),
           coalesce(r.inference_geo, ''), coalesce(r.service_tier, ''), coalesce(r.scope_kind, ''),
           coalesce(r.scope_name, ''), coalesce(r.agent_id, ''),
           sum(r.input_tokens), sum(r.cache_creation_tokens), sum(r.cache_5m), sum(r.cache_1h),
           sum(r.cache_read_tokens), sum(r.output_tokens), sum(r.thinking_tokens),
           {_fits("sum(r.web_search_requests)")}, {_fits("sum(r.web_fetch_requests)")}, {_fits("count(*)")},
           sum(r.duration_ms), sum(r.ttft_ms), sum(r.cost_usd::numeric)::float8
    FROM {_REQUESTS} r
    JOIN {_KEYS} k ON k.day = r.day AND k.session_id = r.session_id
    WHERE k.kinds & {_USAGE} <> 0
    GROUP BY r.session_id, date_trunc('hour', r.ts, 'UTC'), coalesce(r.model, ''), coalesce(r.speed, ''),
             coalesce(r.inference_geo, ''), coalesce(r.service_tier, ''), coalesce(r.scope_kind, ''),
             coalesce(r.scope_name, ''), coalesce(r.agent_id, '')""",
]


def _cost_key(column: str) -> str:
    """A key column of cost_daily added by the schema extension: '' on an OTel-only day (the original grain)."""
    return f"CASE WHEN r.req_kind = 'otel_only' THEN '' ELSE coalesce(r.{column}, '') END"


# cost_daily, per (day, session), from the requests of the key day. `_cli_requests` already
# holds each kind's identity: OTel model, sender and query source for matched and service requests,
# model_raw, the transcript's sender and '' for transcript-only ones. An OTel-only day has
# only 'otel_only' requests, which are exactly the day's OTel api_request records: grouped by the
# original key, with the five new key columns '' and the 5m/1h split NULL. A missing cost counts as 0.
_COST_DAILY = _replace_daily(
    "cost_daily",
    _LOG,
    f"""(day, session_id, user_email, model_name, query_source, speed, inference_geo, scope_kind, scope_name,
         agent_type, cost_usd, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
         api_call_count, cache_creation_5m_tokens, cache_creation_1h_tokens)
    SELECT r.day, r.session_id, coalesce(r.user_email, ''), coalesce(r.model_name, ''),
           coalesce(r.query_source, ''), {_cost_key("speed")}, {_cost_key("inference_geo")},
           {_cost_key("scope_kind")}, {_cost_key("scope_name")}, {_cost_key("agent_type")},
           -- numeric: an exact sum, the same in whatever order the rows are scanned
           sum(coalesce(r.cost_usd, 0)::numeric)::float8,
           sum(coalesce(r.input_tokens, 0)), sum(coalesce(r.output_tokens, 0)),
           sum(coalesce(r.cache_read_tokens, 0)), sum(coalesce(r.cache_creation_tokens, 0)), count(*),
           sum(r.cache_5m), sum(r.cache_1h)
    FROM {_REQUESTS} r
    JOIN {_KEYS} k ON k.day = r.day AND k.session_id = r.session_id
    WHERE k.kinds & {_LOG} <> 0
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10""",
)


# ── session_dims, family SESSION ────────────────────────────────────────────────
# One row per session with any stored raw record (an OTel-only session included), upserted
# after the rollups and after DIMENSIONS, for the session of every key with bit 1, 4, 8 or 32. It
# writes only the columns of the schema extension, never started_at, last_event_at or another column
# of the initial schema, and copies none of the earliest-value merge. Every rule keeps the stored value
# when the recompute has none.
# The keys whose session the SESSION statements (and the derived update after them) recompute.
_SESSION_KEY_BITS = _LOG | _SPANS | _METRICS | _SESSION
_SESSION_EVENT = "agent.session.start"
_ENV_EVENT = "agent.session.env"
_GIT_EVENT = "agent.git.snapshot"
_SUMMARY_EVENT = "agent.session.summary"
_USAGE_EVENT = "agent.usage.request"  # stored in usage_requests; its common fields are in its attrs

# Latest-value columns filled from a field of the same name in the events' attrs, by the events that
# carry it. Common fields come from any event; the typed hook columns (effort, permission_mode, ...)
# are read from their columns, never from attrs.
_COMMON_TEXT = ("platform", "entrypoint", "client_version", "codemie_cli_version", "team", "environment", "space_id",
                "space_source")  # fmt: skip
_ENV_EVENTS = (_ENV_EVENT, _SESSION_EVENT)
_ENV_TEXT = ("provider", "api_host", "configured_model", "output_style", "os", "arch", "node_version", "timezone",
             "hostname_hash")  # fmt: skip
_GIT_EVENTS = (_SESSION_EVENT, _GIT_EVENT)
_GIT_TEXT = ("git_email", "codemie_cli_email", "os_user", "git_head_start", "git_head_end")
_VALUE_KEYS = (*_COMMON_TEXT, "schema_version", *_ENV_TEXT, "plugins", "mcp_servers", *_GIT_TEXT, "git_commits")

# session_dims.attrs takes the column-less fields of these events (and the client's identity_source,
# which does not decide the rank). A field named like a column of session_dims, an envelope field or a
# key the recalculation owns is never copied into attrs.
_ATTRS_EVENTS = (_SUMMARY_EVENT, _ENV_EVENT, _GIT_EVENT)
_SESSION_DIMS_COLUMNS = (
    "session_id", "started_at", "last_event_at", "repository", "branch", "repo_remote", "project_name",
    "developer_name", "first_prompt", "jwt_email", "dev_name_max", "updated_at",
    "user_email", "identity_source", "git_email", "codemie_cli_email", "claude_account_email", "os_user",
    "platform", "ingest_source", "client_version", "codemie_cli_version", "provider", "branch_dominant", "feature_id",
    "story_id", "story_source", "primary_model", "primary_command", "delivery_framework", "ended_at", "duration_ms",
    "active_ms", "compaction_pre_tokens", "turns", "api_calls", "tool_calls", "tool_errors", "lines_added",
    "lines_removed", "files_changed", "files_written", "files_edited", "compaction_count", "commands", "skills",
    "agents", "tools", "models", "source_cost_usd",
    "title", "team", "environment", "space_id", "space_source", "entrypoint", "hostname_hash",
    "api_host", "configured_model", "effort", "output_style", "os", "arch", "node_version", "timezone",
    "permission_mode", "git_head_start", "git_head_end", "organization_id", "account_uuid", "account_id",
    "otel_user_id", "terminal_type", "sources", "attrs", "client_versions", "compactions", "plugins", "mcp_servers",
    "git_commits", "tool_results", "schema_version", "activity_started_at", "summary_ts",
)  # fmt: skip
_NOT_ATTRS = (*_SESSION_DIMS_COLUMNS, "type", "timestamp", "session_id", "prompt_id", "event_id",
              "counters_source", "identity_ts", "story_ts")  # fmt: skip

# Priority pairs, highest rank first; an unlisted value ranks lowest.
_IDENTITY_RANKS = ("jwt", "git", "codemie_cli", "claude_account", "os")
_STORY_RANKS = ("explicit", "marker", "branch", "mention")
# session_attributes key -> column.
_OTEL_SESSION_COLUMNS = (
    ("organization.id", "organization_id"),
    ("user.account_uuid", "account_uuid"),
    ("user.account_id", "account_id"),
    ("user.id", "otel_user_id"),
    ("terminal.type", "terminal_type"),
)
_PLATFORM_PREFIXES = (("claude_code.", "claude-code"), ("cursor.", "cursor"))


def _rank(value: str, ranks: tuple[str, ...]) -> str:
    whens = " ".join(f"WHEN {_literal(r)} THEN {len(ranks) - i}" for i, r in enumerate(ranks))
    return f"CASE {value} {whens} ELSE 0 END"


def _ts_text(ts: str) -> str:
    """A timestamp as fixed-format UTC text: pair timestamps kept in attrs compare as text, never cast."""
    return f"""to_char({ts} AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')"""


def _is_string(value: str) -> str:
    return f"jsonb_typeof({value}) = 'string'"


def _attr_candidate(source: str, key: str) -> str:
    """The latest non-empty `key` of agent.session.start / agent.git.snapshot as an identity candidate."""
    value = f"(h.attrs->>{_literal(key)})"
    return (
        f"(SELECT {_literal(source)}, {value}, h.ts FROM hook_events h "
        f"WHERE h.session_id = k.session_id AND h.event_type = ANY({_text_array(_GIT_EVENTS)}) "
        f"AND {_is_string(f'h.attrs->{_literal(key)}')} AND {value} <> '' "
        f"AND octet_length({value}) <= {_MAX_KEY_BYTES} "
        f'ORDER BY h.ts DESC, {value} COLLATE "C" DESC LIMIT 1)'
    )


def _identity_wins(new: str = "EXCLUDED", old: str = "sd") -> str:
    """The recomputed (user_email, identity_source) replaces the stored pair: a higher rank, or the same
    rank and a later time (ties to the larger email, as the recompute picks its candidate)."""
    return (
        f"({new}.user_email IS NOT NULL AND ({old}.user_email IS NULL OR "
        f"({_rank(f'{new}.identity_source', _IDENTITY_RANKS)}, ({new}.attrs->>'identity_ts') COLLATE \"C\", "
        f'{new}.user_email COLLATE "C") > '
        f"({_rank(f'{old}.identity_source', _IDENTITY_RANKS)}, coalesce({old}.attrs->>'identity_ts', '') "
        f'COLLATE "C", {old}.user_email COLLATE "C")))'
    )


def _story_wins(new: str = "EXCLUDED", old: str = "sd") -> str:
    """As _identity_wins, for (story_id, story_source)."""
    return (
        f"({new}.story_id IS NOT NULL AND ({old}.story_id IS NULL OR "
        f"({_rank(f'{new}.story_source', _STORY_RANKS)}, ({new}.attrs->>'story_ts') COLLATE \"C\", "
        f'{new}.story_id COLLATE "C", coalesce({new}.story_source, \'\') COLLATE "C") > '
        f"({_rank(f'{old}.story_source', _STORY_RANKS)}, coalesce({old}.attrs->>'story_ts', '') COLLATE \"C\", "
        f'{old}.story_id COLLATE "C", coalesce({old}.story_source, \'\') COLLATE "C")))'
    )


# The latest non-empty value of every field the row takes from the events' attrs (by ts, then
# ingest_seq; a hook event before a usage_requests row of the same time), as one jsonb object.
_LATEST_FIELD = f"""(e.key = ANY({_text_array((*_COMMON_TEXT, "identity_source"))}) AND {_is_string("e.value")})
               OR (e.key = 'schema_version' AND jsonb_typeof(e.value) IN ('number', 'string')
                   AND (e.value #>> '{{}}') ~ '^[0-9]{{1,9}}$')
               OR (r.event_type = ANY({_text_array(_ENV_EVENTS)})
                   AND ((e.key = ANY({_text_array(_ENV_TEXT)}) AND {_is_string("e.value")})
                        OR (e.key = 'plugins' AND jsonb_typeof(e.value) = 'object')
                        OR (e.key = 'mcp_servers' AND jsonb_typeof(e.value) = 'array')))
               OR (r.event_type = ANY({_text_array(_GIT_EVENTS)})
                   AND ((e.key = ANY({_text_array(_GIT_TEXT)}) AND {_is_string("e.value")})
                        OR (e.key = 'git_commits' AND jsonb_typeof(e.value) = 'array')))
               OR (r.event_type = ANY({_text_array(_ATTRS_EVENTS)}) AND e.key <> ALL({_text_array(_NOT_ATTRS)}))"""

_LATEST_VALUES = f"""SELECT jsonb_object_agg(x.key, x.value) AS v
    FROM (
        SELECT DISTINCT ON (e.key) e.key, e.value
        FROM (
            SELECT h.ts, h.ingest_seq, h.event_type, h.attrs FROM hook_events h WHERE h.session_id = k.session_id
            UNION ALL
            SELECT u.ts, NULL, {_literal(_USAGE_EVENT)}, u.attrs FROM usage_requests u WHERE u.session_id = k.session_id
        ) r
        CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(r.attrs) = 'object' THEN r.attrs END) e
        WHERE e.value NOT IN ('null'::jsonb, '""'::jsonb)
          AND ({_LATEST_FIELD})
        ORDER BY e.key, r.ts DESC, r.ingest_seq DESC NULLS LAST, e.value::text COLLATE "C" DESC
    ) x"""


def _latest_email(alias: str, table: str) -> str:
    return (
        f"(SELECT {alias}.user_email AS email, {alias}.ts FROM {table} {alias} "
        f"WHERE {alias}.session_id = k.session_id AND {alias}.user_email <> '' "
        f'ORDER BY {alias}.ts DESC, {alias}.user_email COLLATE "C" DESC LIMIT 1)'
    )


def _jwt_candidate(alias: str, table: str) -> str:
    return (
        f"(SELECT 'jwt', {alias}.user_email, {alias}.ts FROM {table} {alias} "
        f"WHERE {alias}.session_id = k.session_id AND {alias}.user_email <> '' "
        f"AND octet_length({alias}.user_email) <= {_MAX_KEY_BYTES} "
        f'ORDER BY {alias}.ts DESC, {alias}.user_email COLLATE "C" DESC LIMIT 1)'
    )


_UNION_ALL = " UNION ALL "
_OTEL_EMAILS = _UNION_ALL.join(
    _latest_email(alias, table) for alias, table in (("l", "log_events"), ("s", "spans"), ("m", "metric_points"))
)


def _latest_typed(column: str) -> str:
    """The latest non-empty typed hook_events column of the session."""
    return (
        f"(SELECT h.{column} FROM hook_events h WHERE h.session_id = k.session_id AND h.{column} <> '' "
        f'ORDER BY h.ts DESC, h.ingest_seq DESC LIMIT 1)'
    )


def _otel_session_text(key: str) -> str:
    value = f"sa.attrs->{_literal(key)}"
    return f"CASE WHEN jsonb_typeof({value}) IN ('string', 'number') THEN nullif({value} #>> '{{}}', '') END"


_PLATFORM_FROM_NAMES = (
    "(SELECT CASE "
    + " ".join(f"WHEN starts_with(n.name, {_literal(p)}) THEN {_literal(v)}" for p, v in _PLATFORM_PREFIXES)
    + " END FROM ("
    + _UNION_ALL.join(
        f"SELECT {a}.ts, {a}.{c} AS name FROM {t} {a} WHERE {a}.session_id = k.session_id AND ("
        + " OR ".join(f"starts_with({a}.{c}, {_literal(p)})" for p, _ in _PLATFORM_PREFIXES)
        + ")"
        for a, t, c in (("s", "spans", "span_name"), ("m", "metric_points", "metric_name"))
    )
    + ') n ORDER BY n.ts DESC, n.name COLLATE "C" DESC LIMIT 1)'
)

# ── session_dims counters ───────────────────────────────────────────────────────
# The summary columns come from the latest agent.session.summary (by ts, then ingest_seq), written as a
# set when it is not older than the stored summary_ts. Without a summary they fall back to
# the rollups and the raw rows, only while summary_ts is NULL. Over a purged tail (the stored
# activity_started_at is before every raw record left) a raw-sourced fallback keeps its stored value.
# active_ms and source_cost_usd are recomputed from the rollups every time. A NULL never overwrites.
# A client value is read only when it fits its column: a count of at most 9 digits for int, 15 for bigint.
_INT_DIGITS, _BIGINT_DIGITS = 9, 15
# Booleans a client may send as JSON true/false or as a string in any case.
_ATTRS_BOOLEANS = ("is_final",)
_TOOL_END, _TOOL_ERROR = "agent.tool.end", "agent.tool.error"
_COMPACT_EVENT = "agent.session.compact"
_OTEL_COMPACTION = "compaction"  # log_events.event_name of the OTel compaction event (kind OTHER)

# Which rule the recompute applies to the stored row, inside ON CONFLICT DO UPDATE:
# the summary set, the fallback (no summary ever stored), or neither (the stored summary is newer).
_SUMMARY_WINS = "(EXCLUDED.summary_ts IS NOT NULL AND (sd.summary_ts IS NULL OR EXCLUDED.summary_ts >= sd.summary_ts))"
_FALLBACK_RUNS = "(EXCLUDED.summary_ts IS NULL AND sd.summary_ts IS NULL)"
_PURGED_TAIL = "coalesce(sd.activity_started_at < EXCLUDED.activity_started_at, false)"
# The same test inside the SELECT: the fallback sources are read only when the row will take them.
_FALLBACK_DUE = "sm.ts IS NULL AND cur.summary_ts IS NULL"


def _ms_between(later: str, earlier: str) -> str:
    return f"trunc(extract(epoch FROM ({later}) - ({earlier})) * 1000)::bigint"


def _json_typed(element: str, key: str, json_type: str) -> str:
    """`element->key` when it is of `json_type` (object, array), else NULL."""
    value = f"{element}->{_literal(key)}"
    return f"CASE WHEN jsonb_typeof({value}) = {_literal(json_type)} THEN {value} END"


def _json_text(element: str, key: str) -> str:
    return f"CASE WHEN {_is_string(f'{element}->{_literal(key)}')} THEN nullif({element}->>{_literal(key)}, '') END"


def _json_ts(element: str, key: str) -> str:
    """An ISO-8601 UTC time (`2026-09-29T11:20:05.000Z`) read from `element`, as timestamptz. Anything
    else, a date that does not exist included, is NULL, never an error: the nested CASEs keep the
    order of the checks, and the day is rebuilt from the first of its month, which never raises."""
    value = f"({element}->>{_literal(key)})"
    shape = (
        "'^[1-9][0-9]{3}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])"
        "T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]([.][0-9]{1,6})?Z$'"
    )
    day = (
        f"to_char(make_date(substr({value}, 1, 4)::int, substr({value}, 6, 2)::int, 1) "
        f"+ (substr({value}, 9, 2)::int - 1), 'YYYY-MM-DD')"
    )
    return (
        f"CASE WHEN {value} ~ {shape} THEN CASE WHEN {day} = substr({value}, 1, 10) "
        f"THEN {value}::timestamptz END END"
    )


def _booleans(attrs: str) -> str:
    """`attrs` with each of _ATTRS_BOOLEANS sent as a string ("TRUE", "false") stored as a JSON boolean."""
    for key in _ATTRS_BOOLEANS:
        text = f"lower({attrs}->>{_literal(key)})"
        attrs = (
            f"CASE WHEN jsonb_typeof({attrs}->{_literal(key)}) = 'string' AND {text} IN ('true', 'false') "
            f"THEN jsonb_set({attrs}, {_literal('{' + key + '}')}, to_jsonb({text} = 'true')) ELSE {attrs} END"
        )
    return attrs


def _day_of(ts: str) -> tuple[str, str]:
    return f"date_trunc('day', {ts}, 'UTC')", f"date_trunc('day', {ts}, 'UTC') + interval '1 day'"


_DISPATCH_LO, _DISPATCH_HI = _day_of("d.ts")
_ANY_SPAN_KIND = f"({_TOOL}, {_EXEC}, {_INTERACTION})"

# Earliest and latest time over every raw record of the session (the fallback activity range).
_RAW_RANGE = _UNION_ALL.join(
    f"SELECT min({a}.ts) AS lo, max({a}.ts) AS hi FROM {t} {a} WHERE {a}.session_id = k.session_id"
    for a, t in (("h", "hook_events"), ("u", "usage_requests"), ("l", "log_events"), ("s", "spans"),
                 ("m", "metric_points"))
)  # fmt: skip

_OTEL_COMPACTIONS = f"""SELECT l.ts, 0::bigint AS seq, jsonb_strip_nulls(jsonb_build_object(
                'trigger', {_json_text("l.attrs", "trigger")},
                'pre_tokens', {_json_count("l.attrs", "pre_tokens", _BIGINT_DIGITS)},
                'post_tokens', {_json_count("l.attrs", "post_tokens", _BIGINT_DIGITS)},
                'duration_ms', {_json_count("l.attrs", "duration_ms", _BIGINT_DIGITS)})) AS e
            FROM log_events l
            WHERE l.session_id = k.session_id AND l.event_name = {_literal(_OTEL_COMPACTION)}"""
_HOOK_COMPACTIONS = f"""SELECT h.ts, h.ingest_seq, jsonb_strip_nulls(jsonb_build_object(
                'start', {_ts_text("h.ts")}, 'trigger', nullif(h.trigger, ''))) AS e
            FROM hook_events h
            WHERE h.session_id = k.session_id AND h.event_type = {_literal(_COMPACT_EVENT)}
              AND NOT EXISTS (SELECT 1 FROM log_events l WHERE l.session_id = k.session_id
                              AND l.event_name = {_literal(_OTEL_COMPACTION)})"""

# The joins the counters read, after the stored row `cur`:
#   sm  the latest summary;  rd  the rollup-derived columns (every time);
#   fb  the fallback sources (only when due);  fl  file counts;  er  tool errors;  tl  tools.
_COUNTER_JOINS = f"""LEFT JOIN session_dims cur ON cur.session_id = k.session_id
    LEFT JOIN LATERAL (
        SELECT h.ts, h.attrs FROM hook_events h
        WHERE h.session_id = k.session_id AND h.event_type = {_literal(_SUMMARY_EVENT)}
        ORDER BY h.ts DESC, h.ingest_seq DESC LIMIT 1
    ) sm ON true
    CROSS JOIN LATERAL (
        SELECT (SELECT sum(a.active_ms_user + a.active_ms_cli) FROM active_time_daily a
                WHERE a.session_id = k.session_id) AS active_ms,
               (SELECT sum(c.cost_usd) FROM cost_daily c WHERE c.session_id = k.session_id) AS source_cost_usd
    ) rd
    LEFT JOIN LATERAL (
        SELECT r.first_ts, r.last_ts,
               (SELECT sum(t.turns) FROM turns_daily t WHERE t.session_id = k.session_id) AS turns,
               (SELECT sum(c.api_call_count) FROM cost_daily c WHERE c.session_id = k.session_id) AS api_calls,
               (SELECT sum(f.tool_calls) FROM tool_facts_daily f WHERE f.session_id = k.session_id) AS tool_calls,
               (SELECT sum(x.lines_added) FROM lines_daily x WHERE x.session_id = k.session_id) AS lines_added,
               (SELECT sum(x.lines_removed) FROM lines_daily x WHERE x.session_id = k.session_id) AS lines_removed,
               -- Models by sum(api_call_count), ties by name: the first is primary_model (rollup).
               (SELECT jsonb_agg(x.model_name ORDER BY x.calls DESC, x.model_name COLLATE "C")
                FROM (SELECT c.model_name, sum(c.api_call_count) AS calls FROM cost_daily c
                      WHERE c.session_id = k.session_id AND c.model_name <> '' GROUP BY c.model_name) x) AS models,
               (SELECT jsonb_object_agg(x.name, x.calls)
                FROM (SELECT i.name, sum(i.calls) AS calls FROM invocations_hourly i
                      WHERE i.session_id = k.session_id AND i.kind = 3 GROUP BY i.name) x) AS agents,
               -- Skills: kind 2 of invocations_hourly, plus the dispatches of a skill with no span on a
               -- day that has spans (a day without them already counts its dispatches in kind 2).
               (SELECT jsonb_object_agg(x.name, x.calls)
                FROM (SELECT y.name, sum(y.calls) AS calls FROM (
                          SELECT i.name, i.calls::bigint AS calls FROM invocations_hourly i
                          WHERE i.session_id = k.session_id AND i.kind = 2
                          UNION ALL
                          SELECT d.skill_name, 1 FROM hook_events d
                          WHERE d.session_id = k.session_id AND d.event_type = {_literal(SKILL_DISPATCH_EVENT)}
                            AND d.skill_name <> ''
                            AND EXISTS (SELECT 1 FROM spans s WHERE s.session_id = k.session_id
                                        AND s.span_kind IN {_ANY_SPAN_KIND}
                                        AND s.ts >= {_DISPATCH_LO} AND s.ts < {_DISPATCH_HI})
                            AND NOT EXISTS (SELECT 1 FROM spans s WHERE s.session_id = k.session_id
                                            AND s.span_kind = {_TOOL} AND s.skill_name = d.skill_name
                                            AND s.ts >= {_DISPATCH_LO} AND s.ts < {_DISPATCH_HI})
                      ) y GROUP BY y.name) x) AS skills,
               -- OTel tool_result events; without them, the hook ends and errors.
               (SELECT CASE WHEN o.n > 0 THEN o.n
                            ELSE (SELECT count(*) FROM hook_events h WHERE h.session_id = k.session_id
                                  AND h.event_type IN ({_literal(_TOOL_END)}, {_literal(_TOOL_ERROR)})) END
                FROM (SELECT count(*) AS n FROM log_events l
                      WHERE l.session_id = k.session_id AND l.event_kind = {int(EventKind.TOOL_RESULT)}) o)
                   AS tool_results,
               (SELECT h.git_branch FROM hook_events h WHERE h.session_id = k.session_id AND h.git_branch <> ''
                GROUP BY h.git_branch ORDER BY count(*) DESC, max(h.ts) DESC, h.git_branch COLLATE "C" DESC
                LIMIT 1) AS branch_dominant,
               -- OTel compaction events; without them, the hook's (start and trigger only).
               (SELECT jsonb_agg(c.e ORDER BY c.ts, c.seq, c.e::text COLLATE "C") FROM (
                    {_OTEL_COMPACTIONS}
                    UNION ALL
                    {_HOOK_COMPACTIONS}
                ) c) AS compactions
        FROM (SELECT min(x.lo) AS first_ts, max(x.hi) AS last_ts FROM ({_RAW_RANGE}) x) r
        WHERE {_FALLBACK_DUE}
    ) fb ON true
    -- Distinct paths of the session's days (the stored range reaches past a purged tail), per flag.
    LEFT JOIN LATERAL (
        SELECT CASE WHEN count(*) > 0 THEN count(*) FILTER (WHERE p.written OR p.edited) END AS files_changed,
               CASE WHEN count(*) > 0 THEN count(*) FILTER (WHERE p.written) END AS files_written,
               CASE WHEN count(*) > 0 THEN count(*) FILTER (WHERE p.edited) END AS files_edited
        FROM (SELECT f.file_path, bool_or(f.is_written) AS written, bool_or(f.is_edited) AS edited
              FROM session_files_daily f
              WHERE f.session_id = k.session_id
                AND f.day >= (least(cur.activity_started_at, fb.first_ts) AT TIME ZONE 'UTC')::date
                AND f.day <= (greatest(cur.ended_at, fb.last_ts) AT TIME ZONE 'UTC')::date
              GROUP BY f.file_path) p
        WHERE {_FALLBACK_DUE}
    ) fl ON true
    -- Failed tool calls, one per tool_use_id: execution spans with success 'false', named after their
    -- tool span; without execution spans, agent.tool.error events. Never calls - success.
    LEFT JOIN LATERAL (
        SELECT sum(g.n) AS total, jsonb_object_agg(g.name, g.n) FILTER (WHERE g.name <> '') AS by_name
        FROM (
            SELECT c.name, count(*) AS n FROM (
                SELECT max(f.name COLLATE "C") AS name FROM (
                    SELECT CASE WHEN x.tool_use_id <> '' THEN 'u' || x.tool_use_id
                                ELSE 's' || encode(x.span_id, 'hex') END AS call,
                           coalesce(nullif(x.tool_name, ''), t.tool_name, '') AS name
                    FROM spans x
                    LEFT JOIN LATERAL (
                        SELECT t.tool_name FROM spans t
                        WHERE t.session_id = k.session_id AND t.span_kind = {_TOOL}
                          AND t.tool_use_id = x.tool_use_id AND t.tool_name <> ''
                        ORDER BY t.ts, t.tool_name COLLATE "C" LIMIT 1
                    ) t ON x.tool_use_id <> ''
                    WHERE x.session_id = k.session_id AND x.span_kind = {_EXEC} AND x.success = 'false'
                    UNION ALL
                    SELECT CASE WHEN h.tool_use_id <> '' THEN 'u' || h.tool_use_id ELSE 'h' || h.ingest_seq END,
                           coalesce(h.tool_name, '')
                    FROM hook_events h
                    WHERE h.session_id = k.session_id AND h.event_type = {_literal(_TOOL_ERROR)}
                      AND NOT EXISTS (SELECT 1 FROM spans s WHERE s.session_id = k.session_id
                                      AND s.span_kind = {_EXEC})
                ) f GROUP BY f.call
            ) c GROUP BY c.name
        ) g
        WHERE {_FALLBACK_DUE}
    ) er ON true
    -- tools: calls from kind 1 of invocations_hourly, errors as above, by name.
    LEFT JOIN LATERAL (
        SELECT jsonb_object_agg(n.name, jsonb_build_object('calls', n.calls, 'errors', n.errors)) AS tools
        FROM (
            SELECT u.name, sum(u.calls) AS calls, sum(u.errors) AS errors FROM (
                SELECT i.name, i.calls::bigint AS calls, 0::bigint AS errors FROM invocations_hourly i
                WHERE i.session_id = k.session_id AND i.kind = 1
                UNION ALL
                SELECT e.key, 0, (e.value #>> '{{}}')::bigint FROM jsonb_each(er.by_name) e
            ) u GROUP BY u.name
        ) n
        WHERE {_FALLBACK_DUE}
    ) tl ON true"""

_COMPACTION_COUNT = "coalesce(jsonb_array_length(fb.compactions), 0)"
_SUMMARY_ATTRS = "sm.attrs"  # the latest summary's attrs (`sm` in _COUNTER_JOINS)
_FALLBACK_DURATION = _ms_between("fb.last_ts", "fb.first_ts")
_CLIENT_VERSION = f"coalesce(lv.v->>'client_version', {_otel_session_text('app.version')})"


def _summary_or(summary: str, fallback: str) -> str:
    return f"CASE WHEN sm.ts IS NOT NULL THEN {summary} ELSE {fallback} END"


def _summary_count(key: str, digits: int = _INT_DIGITS) -> str:
    return _json_count(_SUMMARY_ATTRS, key, digits)


# (column, value) of the summary columns whose fallback comes from the rollups: recomputed each time.
_COUNTERS_FROM_ROLLUPS: tuple[tuple[str, str], ...] = (
    ("turns", _summary_or(_summary_count("turns"), _fits("fb.turns"))),
    ("api_calls", _summary_or(_summary_count("api_calls"), _fits("fb.api_calls"))),
    ("tool_calls", _summary_or(_summary_count("tool_calls"), _fits("fb.tool_calls"))),
    ("lines_added", _summary_or(_summary_count("lines_added"), _fits("fb.lines_added"))),
    ("lines_removed", _summary_or(_summary_count("lines_removed"), _fits("fb.lines_removed"))),
    ("files_changed", _summary_or(_summary_count("files_changed"), _fits("fl.files_changed"))),
    ("files_written", _summary_or(_summary_count("files_written"), _fits("fl.files_written"))),
    ("files_edited", _summary_or(_summary_count("files_edited"), _fits("fl.files_edited"))),
    ("agents", _summary_or(_json_typed(_SUMMARY_ATTRS, "agents", "object"), "fb.agents")),
    ("models", _summary_or(_json_typed(_SUMMARY_ATTRS, "models", "array"), "fb.models")),
    ("primary_model", _summary_or(_json_text(_SUMMARY_ATTRS, "primary_model"), "fb.models->>0")),
    # No fallback source: only a summary fills them.
    ("commands", _summary_or(_json_typed(_SUMMARY_ATTRS, "commands", "array"), "NULL::jsonb")),
    ("primary_command", _summary_or(_json_text(_SUMMARY_ATTRS, "primary_command"), "NULL::text")),
    ("title", _summary_or(_json_text(_SUMMARY_ATTRS, "title"), "NULL::text")),
)
# ... and those whose fallback comes from raw rows: kept over a purged tail.
_COUNTERS_FROM_RAW: tuple[tuple[str, str], ...] = (
    ("tool_errors", _summary_or(_summary_count("tool_errors"), _fits("coalesce(er.total, 0)"))),
    ("tool_results", _summary_or(_summary_count("tool_results"), _fits("fb.tool_results"))),
    ("tools", _summary_or(_json_typed(_SUMMARY_ATTRS, "tools", "object"), "tl.tools")),
    ("skills", _summary_or(_json_typed(_SUMMARY_ATTRS, "skills", "object"), "fb.skills")),
    ("branch_dominant", _summary_or(_json_text(_SUMMARY_ATTRS, "branch_dominant"), "fb.branch_dominant")),
    ("compactions", _summary_or(_json_typed(_SUMMARY_ATTRS, "compactions", "array"), "fb.compactions")),
    ("compaction_count", _summary_or(_summary_count("compaction_count"), _COMPACTION_COUNT)),
    # OTel pre_tokens is not the transcript's: the fallback keeps it inside compactions only.
    ("compaction_pre_tokens", _summary_or(_summary_count("compaction_pre_tokens", _BIGINT_DIGITS), "NULL::bigint")),
)  # fmt: skip
_COUNTER_VALUES: tuple[tuple[str, str], ...] = (
    *_COUNTERS_FROM_ROLLUPS,
    *_COUNTERS_FROM_RAW,
    ("activity_started_at", _summary_or(_json_ts(_SUMMARY_ATTRS, "started_at"), "fb.first_ts")),
    ("ended_at", _summary_or(_json_ts(_SUMMARY_ATTRS, "ended_at"), "fb.last_ts")),
    ("duration_ms", _summary_or(_summary_count("duration_ms", _BIGINT_DIGITS), _FALLBACK_DURATION)),
    (
        "client_versions",
        _summary_or(
            _json_typed(_SUMMARY_ATTRS, "client_versions", "array"),
            f"CASE WHEN {_CLIENT_VERSION} IS NOT NULL THEN jsonb_build_array({_CLIENT_VERSION}) END",
        ),
    ),
    ("summary_ts", "sm.ts"),
    ("active_ms", _fits("rd.active_ms", 64)),
    ("source_cost_usd", "rd.source_cost_usd"),
)  # fmt: skip


def _counter_set(column: str) -> str | None:
    """The merge of a counter column, or None for a column that is not one."""
    written = f"coalesce(EXCLUDED.{column}, sd.{column})"
    if column in dict(_COUNTERS_FROM_ROLLUPS):
        return f"{column} = CASE WHEN {_SUMMARY_WINS} OR {_FALLBACK_RUNS} THEN {written} ELSE sd.{column} END"
    if column in dict(_COUNTERS_FROM_RAW):
        return (
            f"{column} = CASE WHEN {_SUMMARY_WINS} OR ({_FALLBACK_RUNS} AND NOT {_PURGED_TAIL}) THEN {written} "
            f"ELSE sd.{column} END"
        )
    fallback = {
        # The tail rule: the earlier start, the later end; without a purged tail these are the new values.
        "activity_started_at": "least(sd.activity_started_at, EXCLUDED.activity_started_at)",
        "ended_at": "greatest(sd.ended_at, EXCLUDED.ended_at)",
        "duration_ms": "coalesce("
        + _ms_between(
            "greatest(sd.ended_at, EXCLUDED.ended_at)", "least(sd.activity_started_at, EXCLUDED.activity_started_at)"
        )
        + ", sd.duration_ms)",
        # Accumulating: the stored versions plus the one found, if new.
        "client_versions": "CASE WHEN EXCLUDED.client_versions IS NULL "
        "OR coalesce(sd.client_versions @> EXCLUDED.client_versions, false) THEN sd.client_versions "
        "ELSE coalesce(sd.client_versions, '[]'::jsonb) || EXCLUDED.client_versions END",
        "summary_ts": "sd.summary_ts",
    }
    if column in fallback:
        return (
            f"{column} = CASE WHEN {_SUMMARY_WINS} THEN {written} WHEN {_FALLBACK_RUNS} THEN {fallback[column]} "
            f"ELSE sd.{column} END"
        )
    return None  # active_ms, source_cost_usd: recomputed each time, under the NULL rule


_SESSION_DIMS_VALUES: tuple[tuple[str, str], ...] = (
    ("user_email", "id.email"),
    ("identity_source", "id.source"),
    ("story_id", "st.story_id"),
    ("story_source", "st.story_source"),
    *((c, f"lv.v->>{_literal(c)}") for c in _GIT_TEXT),
    ("claude_account_email", "ca.email"),
    ("platform", f"coalesce(lv.v->>'platform', {_PLATFORM_FROM_NAMES})"),
    ("ingest_source", "CASE WHEN src.plugin THEN 'plugin' ELSE 'otel' END"),
    ("client_version", f"coalesce(lv.v->>'client_version', {_otel_session_text('app.version')})"),
    ("entrypoint", f"coalesce(lv.v->>'entrypoint', {_otel_session_text('app.entrypoint')})"),
    *((c, f"lv.v->>{_literal(c)}") for c in _COMMON_TEXT if c not in ("platform", "client_version", "entrypoint")),
    ("schema_version", "(lv.v->>'schema_version')::int"),
    *((c, f"lv.v->>{_literal(c)}") for c in _ENV_TEXT),
    ("plugins", "lv.v->'plugins'"),
    ("mcp_servers", "lv.v->'mcp_servers'"),
    ("git_commits", "lv.v->'git_commits'"),
    ("effort", _latest_typed("effort")),
    ("permission_mode", _latest_typed("permission_mode")),
    *((c, _otel_session_text(k)) for k, c in _OTEL_SESSION_COLUMNS),
    (
        "sources",
        "(SELECT jsonb_agg(x.v ORDER BY x.v COLLATE \"C\") FROM "
        "(VALUES ('otel', src.otel), ('plugin', src.plugin)) x (v, present) WHERE x.present)",
    ),
    (
        "attrs",
        f"nullif(coalesce({_booleans(f'(lv.v - {_text_array(_VALUE_KEYS)})')}, '{{}}'::jsonb) "
        f"|| jsonb_strip_nulls(jsonb_build_object("
        f"'identity_ts', {_ts_text('id.ts')}, 'story_ts', {_ts_text('st.ts')}, "
        "'counters_source', CASE WHEN sm.ts IS NOT NULL THEN 'summary' WHEN cur.summary_ts IS NULL THEN 'fallback' END"
        ")), '{}'::jsonb)",
    ),
)


def _session_dims_set(column: str) -> str:
    """The merge of one SESSION column into the stored row; a NULL never overwrites."""
    counter = _counter_set(column)
    if counter is not None:
        return counter
    if column in ("user_email", "identity_source"):
        return f"{column} = CASE WHEN {_identity_wins()} THEN EXCLUDED.{column} ELSE sd.{column} END"
    if column in ("story_id", "story_source"):
        return f"{column} = CASE WHEN {_story_wins()} THEN EXCLUDED.{column} ELSE sd.{column} END"
    if column == "ingest_source":
        return (
            "ingest_source = CASE WHEN 'plugin' IN (sd.ingest_source, EXCLUDED.ingest_source) THEN 'plugin' "
            "ELSE coalesce(EXCLUDED.ingest_source, sd.ingest_source) END"
        )
    if column == "sources":  # a union, sorted, never set by the client
        return (
            "sources = (SELECT jsonb_agg(DISTINCT x.v ORDER BY x.v) FROM (SELECT e.v COLLATE \"C\" AS v "
            "FROM jsonb_array_elements_text(coalesce(sd.sources, '[]'::jsonb) || coalesce(EXCLUDED.sources, "
            "'[]'::jsonb)) e (v)) x)"
        )
    if column == "attrs":  # key by key, newer over older, never removing a key
        return (
            "attrs = nullif(coalesce(sd.attrs, '{}'::jsonb) "
            "|| coalesce(EXCLUDED.attrs - 'identity_ts' - 'story_ts' - 'counters_source', '{}'::jsonb) "
            f"|| CASE WHEN {_SUMMARY_WINS} THEN jsonb_build_object('counters_source', 'summary') "
            f"WHEN {_FALLBACK_RUNS} THEN jsonb_build_object('counters_source', 'fallback') ELSE '{{}}'::jsonb END "
            f"|| CASE WHEN {_identity_wins()} THEN jsonb_build_object('identity_ts', EXCLUDED.attrs->'identity_ts') "
            "ELSE '{}'::jsonb END "
            f"|| CASE WHEN {_story_wins()} THEN jsonb_build_object('story_ts', EXCLUDED.attrs->'story_ts') "
            "ELSE '{}'::jsonb END, '{}'::jsonb)"
        )
    return f"{column} = coalesce(EXCLUDED.{column}, sd.{column})"


_SESSION_VALUES = (*_SESSION_DIMS_VALUES, *_COUNTER_VALUES)
_SESSION_DIMS = f"""INSERT INTO session_dims AS sd ({", ".join(c for c, _ in _SESSION_VALUES)}, session_id)
    SELECT {", ".join(v for _, v in _SESSION_VALUES)}, k.session_id
    FROM (SELECT DISTINCT session_id FROM {_KEYS} WHERE kinds & {_LOG | _SPANS | _METRICS | _SESSION} <> 0
          AND session_id <> '') k
    -- Which sources hold a record of the session: `plugin` hook_events or usage_requests, `otel` the
    -- three OTel tables. A session with neither (e.g. session_attributes only) gets no row.
    CROSS JOIN LATERAL (
        SELECT EXISTS (SELECT 1 FROM hook_events h WHERE h.session_id = k.session_id)
               OR EXISTS (SELECT 1 FROM usage_requests u WHERE u.session_id = k.session_id) AS plugin,
               EXISTS (SELECT 1 FROM log_events l WHERE l.session_id = k.session_id)
               OR EXISTS (SELECT 1 FROM spans s WHERE s.session_id = k.session_id)
               OR EXISTS (SELECT 1 FROM metric_points m WHERE m.session_id = k.session_id) AS otel
    ) src
    LEFT JOIN session_attributes sa ON sa.session_id = k.session_id
    LEFT JOIN LATERAL ({_LATEST_VALUES}) lv ON true
    -- OTel user.email: claude_account_email, the latest over the three OTel tables.
    LEFT JOIN LATERAL (
        SELECT o.email, o.ts FROM (
            {_OTEL_EMAILS}
        ) o ORDER BY o.ts DESC, o.email COLLATE "C" DESC LIMIT 1
    ) ca ON true
    -- Identity: the candidate of the highest rank, then the latest, then the larger email.
    LEFT JOIN LATERAL (
        SELECT c.email, c.source, c.ts FROM (
            {_jwt_candidate("h", "hook_events")}
            UNION ALL {_jwt_candidate("u", "usage_requests")}
            UNION ALL {_attr_candidate("git", "git_email")}
            UNION ALL {_attr_candidate("codemie_cli", "codemie_cli_email")}
            UNION ALL {_attr_candidate("os", "os_user")}
            UNION ALL SELECT 'claude_account', ca.email, ca.ts
                      WHERE ca.email IS NOT NULL AND octet_length(ca.email) <= {_MAX_KEY_BYTES}
        ) c (source, email, ts)
        ORDER BY {_rank("c.source", _IDENTITY_RANKS)} DESC, c.ts DESC, c.email COLLATE "C" DESC LIMIT 1
    ) id ON true
    -- Story: the common fields story_id / story_source of any event, by rank, then the latest.
    LEFT JOIN LATERAL (
        SELECT y.story_id, y.story_source, y.ts FROM (
            SELECT r.attrs->>'story_id' AS story_id,
                   CASE WHEN {_is_string("r.attrs->'story_source'")} THEN nullif(r.attrs->>'story_source', '') END
                       AS story_source,
                   r.ts
            FROM (
                SELECT h.ts, h.attrs FROM hook_events h WHERE h.session_id = k.session_id
                UNION ALL
                SELECT u.ts, u.attrs FROM usage_requests u WHERE u.session_id = k.session_id
            ) r
            WHERE {_is_string("r.attrs->'story_id'")} AND r.attrs->>'story_id' <> ''
              AND octet_length(r.attrs->>'story_id') <= {_MAX_KEY_BYTES}
        ) y
        ORDER BY {_rank("y.story_source", _STORY_RANKS)} DESC, y.ts DESC, y.story_id COLLATE "C" DESC,
                 coalesce(y.story_source, '') COLLATE "C" DESC
        LIMIT 1
    ) st ON true
    {_COUNTER_JOINS}
    WHERE src.plugin OR src.otel
    ON CONFLICT (session_id) DO UPDATE SET
        {", ".join(_session_dims_set(c) for c, _ in _SESSION_VALUES)}"""

# ── subagent_invocations, family SESSION ────────────────────────────────────────────────
# One row per (session, agent_id) named by agent.subagent.usage, agent.subagent.start / stop or a
# usage_requests row; an OTel-only session has none, and no span is ever matched to a subagent. Each
# column takes the first source that has a value; the latest usage event wins (by ts, then ingest_seq,
# as session_usage picks its totals). agent_type and tool_use_id are typed hook columns.
_SUBAGENT_USAGE, _SUBAGENT_START, _SUBAGENT_STOP = "agent.subagent.usage", "agent.subagent.start", "agent.subagent.stop"
_SUBAGENT_HOOK_TYPES = (_SUBAGENT_USAGE, _SUBAGENT_START, _SUBAGENT_STOP)
# The usage event's fields that have a column here, or are envelope fields, never go into attrs;
# everything else does, the usage[] totals included (kept for reconciliation).
_SUBAGENT_COLUMN_FIELDS = (
    "agent_type", "description", "tool_use_id", "workflow_run", "worktree", "spawn_depth", "started_at", "ended_at",
    "duration_ms", "attribution", "model", "api_calls", "tool_calls", "tool_errors", "tool_results", "lines_added",
    "lines_removed", "files_changed", "tools", "skills", "commands", "compactions", "attrs",
)  # fmt: skip
_SUBAGENT_NOT_ATTRS = (*_SUBAGENT_COLUMN_FIELDS, "session_id", "agent_id", "type", "timestamp", "prompt_id", "event_id")
_SUBAGENT_COUNTS = ("tool_calls", "tool_results", "tool_errors", "lines_added", "lines_removed", "files_changed")
_SUBAGENT_USAGE_ATTRS = "ue.attrs"  # the latest usage event's attrs (`ue` in _SUBAGENT_INVOCATIONS)


def _usage_or_stop_text(key: str) -> str:
    return f"coalesce({_json_text(_SUBAGENT_USAGE_ATTRS, key)}, {_json_text('sp.attrs', key)})"


# (column, value); aliases: ue the latest usage event, ss the first start, sp the latest stop, sa the
# latest start/stop with an agent_type, ur the agent's usage_requests, tm the start and end times.
_SUBAGENT_VALUES: tuple[tuple[str, str], ...] = (
    ("agent_type", "coalesce(nullif(ue.agent_type, ''), sa.agent_type, ur.agent_type)"),
    ("description", _usage_or_stop_text("description")),
    ("tool_use_id", "coalesce(nullif(ue.tool_use_id, ''), nullif(sp.tool_use_id, ''))"),
    ("workflow_run", _usage_or_stop_text("workflow_run")),
    ("worktree", _usage_or_stop_text("worktree")),
    (
        "spawn_depth",
        f"coalesce({_json_count(_SUBAGENT_USAGE_ATTRS, 'spawn_depth', _INT_DIGITS)}, "
        f"{_json_count('sp.attrs', 'spawn_depth', _INT_DIGITS)})",
    ),
    ("started_at", "tm.started_at"),
    ("ended_at", "tm.ended_at"),
    (
        "duration_ms",
        # The computed duration only when the end is not before the start: both are client times.
        f"coalesce({_json_count(_SUBAGENT_USAGE_ATTRS, 'duration_ms', _BIGINT_DIGITS)}, "
        f"CASE WHEN tm.ended_at >= tm.started_at THEN {_ms_between('tm.ended_at', 'tm.started_at')} END)",
    ),
    ("model", f"coalesce({_json_text(_SUBAGENT_USAGE_ATTRS, 'model')}, ur.model)"),
    ("api_calls", f"coalesce({_json_count(_SUBAGENT_USAGE_ATTRS, 'api_calls', _INT_DIGITS)}, ur.groups)"),
    *((c, _json_count(_SUBAGENT_USAGE_ATTRS, c, _INT_DIGITS)) for c in _SUBAGENT_COUNTS),
    ("tools", _json_typed(_SUBAGENT_USAGE_ATTRS, "tools", "object")),
    ("skills", _json_typed(_SUBAGENT_USAGE_ATTRS, "skills", "object")),
    ("commands", _json_typed(_SUBAGENT_USAGE_ATTRS, "commands", "array")),
    ("compactions", _json_typed(_SUBAGENT_USAGE_ATTRS, "compactions", "array")),
    # hook: the agent has its own records (a usage event or requests); heuristic: start/stop only.
    ("attribution", "CASE WHEN ue.ts IS NOT NULL OR ur.first_ts IS NOT NULL THEN 'hook' ELSE 'heuristic' END"),
    (
        "attrs",
        f"CASE WHEN jsonb_typeof(ue.attrs) = 'object' "
        f"THEN nullif(ue.attrs - {_text_array(_SUBAGENT_NOT_ATTRS)}, '{{}}'::jsonb) END",
    ),
)


def _subagent_set(column: str) -> str:
    """The merge of one column into the stored row; a NULL never overwrites."""
    if column == "attribution":  # own records seen once stay the row's source, as their tokens do
        return (
            "attribution = CASE WHEN 'hook' IN (si.attribution, EXCLUDED.attribution) THEN 'hook' "
            "ELSE coalesce(EXCLUDED.attribution, si.attribution) END"
        )
    if column == "attrs":  # key by key, newer over older, never removing a key
        return "attrs = nullif(coalesce(si.attrs, '{}'::jsonb) || coalesce(EXCLUDED.attrs, '{}'::jsonb), '{}'::jsonb)"
    return f"{column} = coalesce(EXCLUDED.{column}, si.{column})"


def _subagent_hook(event: str, order: str) -> str:
    """The agent's first agent.subagent.* hook event of type `event` in `order`."""
    return (
        f"SELECT h.ts, h.agent_type, h.tool_use_id, h.attrs FROM hook_events h "
        f"WHERE h.session_id = k.session_id AND h.event_type = {_literal(event)} AND h.agent_id = a.agent_id "
        f"ORDER BY {order} LIMIT 1"
    )


_LATEST_FIRST = "h.ts DESC, h.ingest_seq DESC"
# The request groups: (request id or else message id, raw model); a row with neither id is
# a group of its own.
_REQUEST_GROUP = (
    "coalesce(nullif(u.request_id, ''), nullif(u.message_id, '')), coalesce(u.model_raw, ''), "
    "CASE WHEN coalesce(nullif(u.request_id, ''), nullif(u.message_id, '')) IS NULL "
    "THEN u.tableoid::text || u.ctid::text END"
)

_SUBAGENT_INVOCATIONS = f"""INSERT INTO subagent_invocations AS si ({", ".join(c for c, _ in _SUBAGENT_VALUES)},
                                                session_id, agent_id)
    SELECT {", ".join(v for _, v in _SUBAGENT_VALUES)}, k.session_id, a.agent_id
    FROM (SELECT DISTINCT session_id FROM {_KEYS} WHERE kinds & {_SESSION_KEY_BITS} <> 0 AND session_id <> '') k
    CROSS JOIN LATERAL (
        SELECT h.agent_id FROM hook_events h
        WHERE h.session_id = k.session_id AND h.event_type = ANY({_text_array(_SUBAGENT_HOOK_TYPES)})
          AND h.agent_id <> '' AND octet_length(h.agent_id) <= {_MAX_KEY_BYTES}
        UNION
        SELECT u.agent_id FROM usage_requests u
        WHERE u.session_id = k.session_id AND u.agent_id <> '' AND octet_length(u.agent_id) <= {_MAX_KEY_BYTES}
    ) a
    LEFT JOIN subagent_invocations cur ON cur.session_id = k.session_id AND cur.agent_id = a.agent_id
    LEFT JOIN LATERAL ({_subagent_hook(_SUBAGENT_USAGE, _LATEST_FIRST)}) ue ON true
    LEFT JOIN LATERAL ({_subagent_hook(_SUBAGENT_START, "h.ts, h.ingest_seq")}) ss ON true
    LEFT JOIN LATERAL ({_subagent_hook(_SUBAGENT_STOP, _LATEST_FIRST)}) sp ON true
    LEFT JOIN LATERAL (
        SELECT h.agent_type FROM hook_events h
        WHERE h.session_id = k.session_id AND h.event_type IN ({_literal(_SUBAGENT_START)}, {_literal(_SUBAGENT_STOP)})
          AND h.agent_id = a.agent_id AND h.agent_type <> ''
        ORDER BY {_LATEST_FIRST} LIMIT 1
    ) sa ON true
    -- The agent's requests: the earliest time, the request groups, the model with the most groups.
    CROSS JOIN LATERAL (
        SELECT min(m.first_ts) AS first_ts, sum(m.n)::int AS groups,
               (array_agg(m.model ORDER BY m.n DESC, m.model COLLATE "C") FILTER (WHERE m.model <> ''))[1] AS model,
               (SELECT u.agent_type FROM usage_requests u
                WHERE u.session_id = k.session_id AND u.agent_id = a.agent_id AND u.agent_type <> ''
                ORDER BY u.ts DESC, u.agent_type COLLATE "C" DESC LIMIT 1) AS agent_type
        FROM (
            SELECT g.model, count(*) AS n, min(g.first_ts) AS first_ts
            FROM (SELECT min(u.ts) AS first_ts, max(u.model) AS model FROM usage_requests u
                  WHERE u.session_id = k.session_id AND u.agent_id = a.agent_id
                  GROUP BY {_REQUEST_GROUP}) g
            GROUP BY g.model
        ) m
    ) ur
    -- started_at: the usage event, else the start event, else the earliest request, which only fills
    -- an empty value (it never replaces a stored start with a later request time over a purged tail).
    -- ended_at: the usage event, else the stop event; never a request time.
    CROSS JOIN LATERAL (
        SELECT coalesce({_json_ts(
            _SUBAGENT_USAGE_ATTRS, "started_at")}, ss.ts, least(cur.started_at, ur.first_ts)) AS started_at,
               coalesce({_json_ts(_SUBAGENT_USAGE_ATTRS, "ended_at")}, sp.ts) AS ended_at
    ) tm
    ON CONFLICT (session_id, agent_id) DO UPDATE SET
        {", ".join(_subagent_set(c) for c, _ in _SUBAGENT_VALUES)}"""


# Counter fallback. A (day, session) pair whose UTC day has no span of kind TOOL,
# TOOL_EXECUTION or INTERACTION takes its SPAN_FACTS rows from hook_events. The test is on the
# kind, never on the span name. A pair has one source: each hook branch runs only where
# its span branch finds nothing, so the two never share a key and are never added together.
_NO_SPAN_OF_KIND = (
    f"NOT EXISTS (SELECT 1 FROM spans s WHERE s.session_id = k.session_id AND s.span_kind IN {_ANY_SPAN_KIND} "
    f"AND s.ts >= {_DAY_LO} AND s.ts < {_DAY_HI})"
)
_PROMPT_SUBMIT, _TOOL_START = "agent.prompt.submit", "agent.tool.start"
_IS_TOOL_START = f"h.event_type = {_literal(_TOOL_START)}"
_IS_SUBAGENT_START = f"h.event_type = {_literal(_SUBAGENT_START)}"
_IS_DISPATCH = f"h.event_type = {_literal(SKILL_DISPATCH_EVENT)}"
# file_path of agent.tool.start has no typed hook column: it is kept in attrs.
_HOOK_FILE_PATH = _json_text("h.attrs", "file_path")


def _hook_key(column: str, limit: int = _MAX_KEY_BYTES) -> str:
    """A hook text that becomes part of a rollup key: non-empty and bounded like ingest's span keys
    (never truncated, never an index row too large)."""
    return f"{column} <> '' AND octet_length({column}) <= {limit}"


RECOMPUTE: list[str] = [
    # First: cost_daily and the session_usage tables read the requests it builds.
    _BUILD_REQUESTS,
    *_COST_DAILY,
    *_SESSION_USAGE,
    *_SESSION_USAGE_HOURLY,
    # Rows whose sums are all zero are not stored, in these two rollups.
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
        GROUP BY 1, 2
        UNION ALL
        SELECT k.day, k.session_id, count(*)
        {_in_day("h", "hook_events")}
        WHERE k.kinds & {_SPANS} <> 0 AND k.session_id <> '' AND h.event_type = {_literal(_PROMPT_SUBMIT)}
          AND {_NO_SPAN_OF_KIND}
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
        GROUP BY 1, 2
        UNION ALL
        SELECT k.day, k.session_id,
               count(*) FILTER (WHERE {_IS_TOOL_START} AND h.tool_name <> ''),
               count(*) FILTER (WHERE {_IS_SUBAGENT_START}),
               count(*) FILTER (WHERE ({_IS_TOOL_START} OR {_IS_DISPATCH}) AND {_hook_key("h.skill_name")})
        {_in_day("h", "hook_events")}
        WHERE k.kinds & {_SPANS} <> 0 AND k.session_id <> ''
          AND h.event_type IN ({_literal(_TOOL_START)}, {_literal(_SUBAGENT_START)}, {_literal(SKILL_DISPATCH_EVENT)})
          AND {_NO_SPAN_OF_KIND}
        GROUP BY 1, 2""",
    ),
    *_replace_daily(
        "session_files_daily",
        _SPANS,
        f"""(day, session_id, file_path, is_written, is_edited)
        SELECT k.day, k.session_id, s.file_path,
               coalesce(bool_or(s.tool_name = ANY({_text_array(WRITE_TOOLS)})), false),
               coalesce(bool_or(s.tool_name = ANY({_text_array(EDIT_TOOLS)})), false)
        {_in_day("s", "spans")}
        WHERE k.kinds & {_SPANS} <> 0 AND k.session_id <> '' AND s.span_kind = {_TOOL} AND s.file_path <> ''
        GROUP BY 1, 2, 3
        UNION ALL
        SELECT k.day, k.session_id, {_HOOK_FILE_PATH},
               coalesce(bool_or(h.tool_name = ANY({_text_array(WRITE_TOOLS)})), false),
               coalesce(bool_or(h.tool_name = ANY({_text_array(EDIT_TOOLS)})), false)
        {_in_day("h", "hook_events")}
        WHERE k.kinds & {_SPANS} <> 0 AND k.session_id <> '' AND {_IS_TOOL_START}
          AND {_hook_key(_HOOK_FILE_PATH, MAX_PATH_BYTES)} AND {_NO_SPAN_OF_KIND}
        GROUP BY 1, 2, 3""",
    ),
    # Hourly invocations: kinds 1-3 come from spans (hooks on a fallback pair), kind 4 (slash commands)
    # from user prompts.
    f"DELETE FROM invocations_hourly t USING {_KEYS} k WHERE k.kinds & {_SPANS} <> 0 AND t.kind IN (1, 2, 3) "
    f"AND t.session_id = k.session_id AND t.hour >= {_DAY_LO} AND t.hour < {_DAY_HI}",
    f"DELETE FROM invocations_hourly t USING {_KEYS} k WHERE k.kinds & {_LOG} <> 0 AND t.kind = 4 "
    f"AND t.session_id = k.session_id AND t.hour >= {_DAY_LO} AND t.hour < {_DAY_HI}",
    # A tool call succeeded when its execution span (any session: the join is on
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
    -- Fallback: a tool call succeeded when an agent.tool.end of the session carries its tool_use_id
    -- (one per start, counted in the start's hour, within the reach the span join above uses).
    SELECT date_trunc('hour', h.ts, 'UTC'), k.session_id, 1, h.tool_name, count(*), count(e.ended)
    {_in_day("h", "hook_events")}
    LEFT JOIN LATERAL (
        SELECT true AS ended FROM hook_events x
        WHERE x.session_id = k.session_id AND x.event_type = {_literal(_TOOL_END)} AND x.tool_use_id = h.tool_use_id
          AND x.ts >= h.ts - interval '1 hour' AND x.ts < h.ts + interval '1 day'
        LIMIT 1
    ) e ON h.tool_use_id <> ''
    WHERE k.kinds & {_SPANS} <> 0 AND {_IS_TOOL_START} AND {_hook_key("h.tool_name")} AND {_NO_SPAN_OF_KIND}
    GROUP BY 1, 2, 4
    UNION ALL
    SELECT date_trunc('hour', t.ts, 'UTC'), k.session_id, 2, t.skill_name, count(*), 0
    {_in_day("t", "spans")}
    WHERE k.kinds & {_SPANS} <> 0 AND t.span_kind = {_TOOL} AND t.skill_name <> ''
    GROUP BY 1, 2, 4
    UNION ALL
    SELECT date_trunc('hour', h.ts, 'UTC'), k.session_id, 2, h.skill_name, count(*), 0
    {_in_day("h", "hook_events")}
    WHERE k.kinds & {_SPANS} <> 0 AND ({_IS_TOOL_START} OR {_IS_DISPATCH}) AND {_hook_key("h.skill_name")}
      AND {_NO_SPAN_OF_KIND}
    GROUP BY 1, 2, 4
    UNION ALL
    SELECT date_trunc('hour', t.ts, 'UTC'), k.session_id, 3, t.subagent_type, count(*), 0
    {_in_day("t", "spans")}
    WHERE k.kinds & {_SPANS} <> 0 AND t.span_kind = {_TOOL} AND t.subagent_type <> ''
    GROUP BY 1, 2, 4
    UNION ALL
    SELECT date_trunc('hour', h.ts, 'UTC'), k.session_id, 3, h.agent_type, count(*), 0
    {_in_day("h", "hook_events")}
    WHERE k.kinds & {_SPANS} <> 0 AND {_IS_SUBAGENT_START} AND {_hook_key("h.agent_type")} AND {_NO_SPAN_OF_KIND}
    GROUP BY 1, 2, 4
    UNION ALL
    SELECT date_trunc('hour', l.ts, 'UTC'), k.session_id, 4, l.command_name, count(*), 0
    {_in_day("l", "log_events")}
    WHERE k.kinds & {_LOG} <> 0 AND l.event_kind = {int(EventKind.USER_PROMPT)} AND l.command_name <> ''
    GROUP BY 1, 2, 4""",
    # Per session, from all of the session's rows (not only the claimed day). Skills accumulate,
    # so a skill whose raw rows passed retention is kept.
    f"""INSERT INTO session_skills (session_id, skill_name, updated_at)
    SELECT DISTINCT l.session_id, l.skill_name, now()
    FROM (SELECT DISTINCT session_id FROM {_KEYS} WHERE kinds & {_LOG} <> 0 AND session_id <> '') k
    JOIN log_events l ON l.session_id = k.session_id
    WHERE l.event_kind = {int(EventKind.SKILL_ACTIVATED)} AND l.skill_name <> ''
    ON CONFLICT (session_id, skill_name) DO UPDATE SET updated_at = EXCLUDED.updated_at""",
    # Dimensions come from the 13 dimension hook types, identity (JWT email, developer
    # name) from every hook event.
    # A recompute sees only the raw rows still kept, so it merges into the stored row: when it
    # starts later than the stored start, the earliest rows are gone and the stored earliest
    # values stay.
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
    # SESSION last: the session columns read the rollups above.
    _SESSION_DIMS,
    _SUBAGENT_INVOCATIONS,
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
# Backfill / repair: queue every (day, session) found in raw data, with the bits its records mark at
# ingest, intersected with the requested mask ($3). Bit 32 only for a non-empty session_id.
_RANGE = "ts >= ($1::date::timestamp AT TIME ZONE 'UTC') AND ts < (($2::date + 1)::timestamp AT TIME ZONE 'UTC')"
_RANGE_SHIFTED = (
    "ts >= (($1::date::timestamp + interval '1 hour') AT TIME ZONE 'UTC') "
    "AND ts < ((($2::date + 1)::timestamp + interval '1 hour') AT TIME ZONE 'UTC')"
)
_DAY_OF_TS = "(ts AT TIME ZONE 'UTC')::date"
_SESSION_IF_ANY = f"CASE WHEN session_id <> '' THEN {_SESSION} ELSE 0 END"
_FALLBACK_HOOKS = ", ".join(
    _literal(t)
    for t in (
        "agent.prompt.submit",
        "agent.tool.start",
        "agent.tool.end",
        "agent.tool.error",
        "agent.skill.dispatch",
        "agent.subagent.start",
    )
)
_ALL_BITS = _LOG | _DIMS | _SPANS | _METRICS | _USAGE | _SESSION


def _neighbour_day(reach: str) -> str:
    """The day on the other side of the nearer midnight when a row's `ts` lies within `reach` of it (bounds
    inclusive), else NULL: the rule of dirty._mark_near_midnight."""
    start = f"({_DAY_OF_TS}::timestamp AT TIME ZONE 'UTC')"
    return (
        f"CASE WHEN ts - {start} <= {reach} THEN {_DAY_OF_TS} - 1 "
        f"WHEN {start} + interval '1 day' - ts <= {reach} THEN {_DAY_OF_TS} + 1 END"
    )


# An api_request marks the neighbouring day within 1 h with a request_id (the identifier tier pairs across
# midnight that far), within 5 s without one (the fingerprint window), as at ingest.
_API_REQUEST_REACH = f"CASE WHEN request_id <> '' THEN {_GROUP_REACH} ELSE {_MATCH} END"
_USAGE_ROW_BITS = f"{_LOG} | {_USAGE} | {_SESSION_IF_ANY}"
# The neighbouring days are queued with the bits of the row's own day, and no day older than $4
# (today - raw retention; NULL without raw retention), like the days of the range itself.
_MARK_RANGE = f"""
INSERT INTO rollup_dirty AS r (day, session_id, kinds)
SELECT day, session_id, bit_or(kinds) & $3 FROM (
    SELECT {_DAY_OF_TS} AS day, session_id, {_USAGE_ROW_BITS} AS kinds FROM usage_requests
    WHERE {_RANGE}
    UNION ALL
    SELECT {_neighbour_day(_GROUP_REACH)}, session_id, {_USAGE_ROW_BITS} FROM usage_requests
    WHERE {_RANGE}
    UNION ALL
    SELECT {_DAY_OF_TS}, session_id,
           {_LOG} | {_SESSION_IF_ANY} | CASE WHEN event_kind = {int(EventKind.API_REQUEST)} THEN {_USAGE} ELSE 0 END
    FROM log_events
    WHERE {_RANGE}
    UNION ALL
    SELECT {_neighbour_day(_API_REQUEST_REACH)}, session_id, {_USAGE_ROW_BITS} FROM log_events
    WHERE {_RANGE} AND event_kind = {int(EventKind.API_REQUEST)}
    UNION ALL
    SELECT {_DAY_OF_TS}, session_id,
           {_DIMS} | {_SESSION}
             | CASE WHEN event_type IN ({_FALLBACK_HOOKS}) THEN {_SPANS} ELSE 0 END
             | CASE WHEN event_type = {_literal("agent.subagent.usage")} THEN {_USAGE} ELSE 0 END
    FROM hook_events
    WHERE {_RANGE} AND session_id <> ''
    UNION ALL
    SELECT ((ts - interval '1 hour') AT TIME ZONE 'UTC')::date, session_id, {_SPANS} FROM hook_events
    WHERE {_RANGE_SHIFTED} AND session_id <> '' AND event_type IN ({_FALLBACK_HOOKS})
    UNION ALL
    SELECT {_DAY_OF_TS}, session_id, {_SPANS} | {_SESSION_IF_ANY} FROM spans
    WHERE {_RANGE}
    UNION ALL
    SELECT ((ts - interval '1 hour') AT TIME ZONE 'UTC')::date, session_id, {_SPANS} FROM spans
    WHERE {_RANGE_SHIFTED} AND span_kind IN {_ANY_SPAN_KIND}
    UNION ALL
    SELECT {_DAY_OF_TS}, session_id, {_METRICS} | {_SESSION_IF_ANY} FROM metric_points
    WHERE {_RANGE}
) raw
WHERE raw.day IS NOT NULL AND ($4::date IS NULL OR raw.day >= $4::date)
GROUP BY 1, 2
HAVING bit_or(kinds) & $3 <> 0
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
        classify: Callable[[list[str]], str] | None = None,
    ) -> None:
        """`raw_retention_days` enables dropping keys past raw retention; `clock` defaults to the database's date.

        `classify` labels a session's delivery framework from its skill names (the service layer's
        classify_delivery_framework); without it session_dims.delivery_framework is not written.
        """
        self._engine = engine
        self._batch_size = batch_size
        self._max_run_seconds = max_run_seconds
        self._raw_retention_days = raw_retention_days
        self._clock = clock
        self._lock_timeout = f"SET LOCAL lock_timeout = {int(lock_timeout_ms)}"
        self._classify = classify
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
            await conn.execute(_CREATE_REQUESTS, timeout=timeout)
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
            # Last: feature_id and delivery_framework derive from what the SESSION statements wrote.
            sessions = sorted({k["session_id"] for k in keys if k["kinds"] & _SESSION_KEY_BITS and k["session_id"]})
            await update_session_derived(conn, sessions, self._classify, timeout)
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

    async def mark_dirty(
        self, first_day: date, last_day: date, conn: asyncpg.Connection | None = None, mask: int = _ALL_BITS
    ) -> int:
        """Queue every (day, session) with raw data in [first_day, last_day] for recomputation.

        Only the bits of `mask` (a plain int) are queued, and no day older than today - raw retention:
        the refresher would drop such keys with a WARNING, and rebuild the long-term tables from nothing.

        One day per statement, each with the refresh time budget: a multi-week range in one
        statement would outlast a dashboard's limit and queue nothing.
        """
        if conn is None:
            async with self._engine.acquire() as acquired:
                return await self.mark_dirty(first_day, last_day, acquired, mask)
        marked = 0
        day = first_day
        earliest: date | None = None  # neither a day of the range nor a neighbouring day is older
        if self._raw_retention_days is not None:
            today = self._clock() if self._clock else await conn.fetchval(TODAY_UTC_SQL)
            earliest = today - timedelta(days=self._raw_retention_days)
            day = max(day, earliest)
        while day <= last_day:
            async with conn.transaction():
                await conn.execute(_REFRESH_STATEMENT_TIMEOUT, timeout=_REFRESH_CLIENT_TIMEOUT_S)
                status = await conn.execute(
                    _MARK_RANGE, day, day, int(mask), earliest, timeout=_REFRESH_CLIENT_TIMEOUT_S
                )
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


# The dirty bits that trigger each RECOMPUTE statement (not the family that owns the table), keyed by
# target table. A table with differently gated statements has one entry per statement,
# `<table>/<qualifier>`. test_rollups.py checks every statement's gate against its entry.
_TRIGGER_BITS: dict[str, int] = {
    "_cli_requests": _LOG | _USAGE,  # the per-recompute requests of cost_daily and session_usage*
    "cost_daily": _LOG,
    "session_usage": _USAGE,
    "session_usage_hourly": _USAGE,
    "lines_daily": _METRICS,
    "active_time_daily": _METRICS,
    "turns_daily": _SPANS,
    "tool_facts_daily": _SPANS,
    "session_files_daily": _SPANS,
    "invocations_hourly/spans": _SPANS,  # the DELETE of kinds 1-3
    "invocations_hourly/commands": _LOG,  # the DELETE of kind 4
    "invocations_hourly": _SPANS | _LOG,  # the INSERT of all four kinds
    "session_skills": _LOG,
    "session_dims/dimensions": _DIMS,  # the DIMENSIONS upsert (writes started_at)
    "session_dims/session": _LOG | _SPANS | _METRICS | _SESSION,  # the SESSION upsert
    "subagent_invocations": _LOG | _SPANS | _METRICS | _SESSION,  # the SESSION upsert
}
