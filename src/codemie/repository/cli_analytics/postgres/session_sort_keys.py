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

"""The stored per-session sort keys of the Sessions list (EPMCDME-16427, part 2).

`session_dims` carries what the three window-wide sorts and the framework filter would otherwise recompute over the
whole window: `cache_read_total` and `api_call_total` (the ctx-per-call key), `classifier_names` (the framework
filter), `models_used` with `kind_masks` (the token kinds each model used) and `cost_key` (today's rounded cost key,
priced with the rate set whose version is `rates_version`). A non-NULL `rates_version` means the session is derived.

The refresher writes them in `RollupRefresher._recompute`, after the day rows, in the same transaction, so a stored
value and the rollups it was computed from commit together. The statement is one set-wise pass over the batch's
sessions through the same token groups (`cost_key_sql.model_token_groups`) and kind masks as the reader's statements,
and it rewrites a row only when a value differs, so an unchanged recompute writes nothing.

Backfill and re-price (the maintenance job, or the `sort_keys_cli` wrapper, both under the "maintenance" advisory
lock). Sessions with `rates_version IS NULL` are derived; derived sessions whose stamp differs from the current
version are re-derived, which rewrites `cost_key` and the stamp in the one UPDATE, so a stored cost key is never
current under another rate set's stamp. A batch is one transaction that locks its rows `FOR UPDATE SKIP LOCKED` and
writes them with the refresher's own statement. Overlap with the refresher is safe in both orders: the batch skips a
row the refresher holds (the refresher derives it, from its own snapshot, at the end of its transaction); a refresher
write that waits for a batch's row lock then writes values computed from its snapshot, which holds its own rows, over
the batch's. Batches walk `session_id` upwards, so a run never rescans what it has done; a skipped row is picked up
by the next run. Totals come from `cost_daily` and `session_usage`, which outlive raw retention.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

import asyncpg

from codemie.repository.cli_analytics.ports import ModelRates, RatesFor
from codemie.repository.cli_analytics.postgres.cost_key_sql import (
    cost_groups_cte,
    cost_key_from_groups_sql,
    model_token_groups,
    normalized_model_name,
    rates_params,
)
from codemie.repository.cli_analytics.postgres.kind_masks import RATE_COLUMNS, kind_mask_sql
from codemie.repository.cli_analytics.postgres.session_derived import read_classifier_names
from codemie.repository.cli_analytics.postgres.settings import LEGACY_APPLICATION_NAME

logger = logging.getLogger(__name__)

# $1 session ids, $2/$3 the (session, name) pairs, $4 the rates version, $5.. the rate arrays (see `rates_params`).
_RATE_ARGS = {"rate_models": "$5", **{f"rate_{column}": f"${6 + i}" for i, column in enumerate(RATE_COLUMNS)}}


def _numbered(sql: str) -> str:
    for name in sorted(_RATE_ARGS, key=len, reverse=True):
        sql = sql.replace(f"${name}", _RATE_ARGS[name])
    return sql


# A btree entry holds about 2.7 kB, and `classifier_names` and `models_used` (INCLUDE columns of `session_dims_fw_win`)
# come from names clients send. A session whose two arrays exceed this many bytes (`pg_column_size`, below the limit
# with room for the other columns of the entry) is not stamped: its arrays are not stored, the marker below stands in
# `classifier_names`, and the readers compute it live like any unstamped session. The backfill, the re-price and the
# automatic enable skip a marked session, so it blocks none of them; a later recompute re-derives it, and a session
# that has shrunk gets its stamp back. It costs only that session's live computation.
MAX_ARRAYS_BYTES = 2300
OVERSIZED_MARK = "~oversized~"
# `session_model_groups` is keyed (session_id, model_name), so a raw model name is a btree key: a name above this many
# bytes (the entry limit is about 2.7 kB) must not abort the stamp transaction. Such a session writes no rows there,
# its `model_groups_rev` is `MODEL_GROUPS_NEVER` and the readers compute its groups live.
MAX_MODEL_NAME_BYTES = 1024
_OVERSIZED_NAMES = f"ARRAY['{OVERSIZED_MARK}']::text[]"

# Revision of the stored `session_model_groups` rows (`session_dims.model_groups_rev`): the rows of a session are
# trusted only when `rates_version IS NOT NULL AND model_groups_rev = MODEL_GROUPS_REV`. A change of the stored format
# (the normalisation expression, the token kinds) bumps it, which makes every stored row untrusted until re-derived.
MODEL_GROUPS_REV = 1
# The `model_groups_rev` of a stamped session that can never hold rows at this revision (a raw model name above
# `MAX_MODEL_NAME_BYTES`): the backfill and the automatic enable skip it, so it neither is re-derived on every run nor
# holds the switch back. Readers still see it as invalid (they trust only `= MODEL_GROUPS_REV`). A bump of the revision
# makes it pending again, as the sign flips with it. An array-oversized session is excluded by its classifier mark.
MODEL_GROUPS_NEVER = -MODEL_GROUPS_REV

# Names are ordered COLLATE "C", as `CLASSIFIER_NAMES_SQL`; a session without names stays NULL. A session without cost
# rows has no group: key 0, zero totals, NULL model arrays. Calls come from `cost_daily` alone (subagent totals-only
# rows carry no calls), cache reads from both sources, exactly as the reader's ctx key reads them.
SORT_KEYS_SQL = _numbered(f"""
WITH sel AS (SELECT DISTINCT unnest($1::text[]) AS session_id),
{cost_groups_cte()},
nm AS (
    SELECT n.session_id, array_agg(DISTINCT n.name COLLATE "C" ORDER BY n.name COLLATE "C") AS names
    FROM unnest($2::text[], $3::text[]) AS n (session_id, name) GROUP BY n.session_id
),
ck AS {cost_key_from_groups_sql().removesuffix(" k")},
mu AS (
    SELECT g.session_id,
           array_agg(g.model_name ORDER BY g.model_name COLLATE "C") AS models,
           array_agg(({kind_mask_sql()})::int ORDER BY g.model_name COLLATE "C") AS masks,
           sum(g.cache_read_tokens)::bigint AS cache_read
    FROM g GROUP BY g.session_id
),
calls AS (
    SELECT c.session_id, sum(c.api_call_count)::bigint AS calls
    FROM cost_daily c WHERE c.session_id IN (SELECT session_id FROM sel) GROUP BY c.session_id
),
raw AS (
    SELECT s.session_id,
           coalesce(mu.cache_read, 0) AS cache_read_total,
           coalesce(calls.calls, 0) AS api_call_total,
           nm.names AS classifier_names,
           mu.models AS models_used,
           mu.masks AS kind_masks,
           coalesce(ck.cost_key, 0::numeric) AS cost_key
    FROM sel s
    LEFT JOIN mu ON mu.session_id = s.session_id
    LEFT JOIN calls ON calls.session_id = s.session_id
    LEFT JOIN nm ON nm.session_id = s.session_id
    LEFT JOIN ck ON ck.session_id = s.session_id
),
facts AS (
    SELECT r.session_id, r.cache_read_total, r.api_call_total, r.cost_key, o.oversized,
           CASE WHEN o.oversized THEN {_OVERSIZED_NAMES} ELSE r.classifier_names END AS classifier_names,
           CASE WHEN o.oversized THEN NULL ELSE r.models_used END AS models_used,
           CASE WHEN o.oversized THEN NULL ELSE r.kind_masks END AS kind_masks,
           CASE WHEN o.oversized THEN NULL ELSE $4::text END AS rates_version,
           CASE WHEN o.oversized THEN NULL WHEN l.long_name THEN {MODEL_GROUPS_NEVER}::smallint
                ELSE {MODEL_GROUPS_REV}::smallint END AS model_groups_rev
    FROM raw r,
    LATERAL (SELECT coalesce(pg_column_size(r.classifier_names), 0) + coalesce(pg_column_size(r.models_used), 0)
                    > {MAX_ARRAYS_BYTES} AS oversized) o,
    LATERAL (SELECT EXISTS (SELECT 1 FROM g WHERE g.session_id = r.session_id
                            AND octet_length(g.model_name) > {MAX_MODEL_NAME_BYTES}) AS long_name) l
),
-- The stored token groups of `session_model_groups`, from the same `g` (built once) as the keys above. A sibling
-- data-modifying CTE runs once, to completion, on the statement's snapshot, whether or not it is referenced. The
-- upsert and the delete touch disjoint rows (a model in `g` against a model not in it), so their order is
-- immaterial. Sessions without a `session_dims` row get no rows; a session whose `model_groups_rev` is not the
-- current revision (oversized, or a long model name) gets none and loses what it had.
-- Same expressions as `priced_groups_cte` (normalised name, has_cost_daily).
mg_upsert AS (
    INSERT INTO session_model_groups AS m
        (session_id, model_name, normalized_model_name, input_tokens, output_tokens, cache_read_tokens,
         cache_creation_tokens, cache_creation_5m_tokens, cache_creation_1h_tokens, has_cost_daily)
    SELECT g.session_id, g.model_name, {normalized_model_name("g.model_name")},
           g.input_tokens, g.output_tokens, g.cache_read_tokens, g.cache_creation_tokens,
           g.cache_creation_5m_tokens, g.cache_creation_1h_tokens,
           EXISTS (SELECT 1 FROM cost_daily cd WHERE cd.session_id = g.session_id)
    FROM g
    JOIN facts f ON f.session_id = g.session_id AND f.model_groups_rev = {MODEL_GROUPS_REV}
    JOIN session_dims d ON d.session_id = g.session_id
    ON CONFLICT (session_id, model_name) DO UPDATE
    SET normalized_model_name = EXCLUDED.normalized_model_name, input_tokens = EXCLUDED.input_tokens,
        output_tokens = EXCLUDED.output_tokens, cache_read_tokens = EXCLUDED.cache_read_tokens,
        cache_creation_tokens = EXCLUDED.cache_creation_tokens,
        cache_creation_5m_tokens = EXCLUDED.cache_creation_5m_tokens,
        cache_creation_1h_tokens = EXCLUDED.cache_creation_1h_tokens, has_cost_daily = EXCLUDED.has_cost_daily
    WHERE m.normalized_model_name IS DISTINCT FROM EXCLUDED.normalized_model_name
       OR m.input_tokens IS DISTINCT FROM EXCLUDED.input_tokens
       OR m.output_tokens IS DISTINCT FROM EXCLUDED.output_tokens
       OR m.cache_read_tokens IS DISTINCT FROM EXCLUDED.cache_read_tokens
       OR m.cache_creation_tokens IS DISTINCT FROM EXCLUDED.cache_creation_tokens
       OR m.cache_creation_5m_tokens IS DISTINCT FROM EXCLUDED.cache_creation_5m_tokens
       OR m.cache_creation_1h_tokens IS DISTINCT FROM EXCLUDED.cache_creation_1h_tokens
       OR m.has_cost_daily IS DISTINCT FROM EXCLUDED.has_cost_daily
),
mg_delete AS (
    DELETE FROM session_model_groups m USING facts f
    WHERE m.session_id = f.session_id
      AND (f.model_groups_rev IS DISTINCT FROM {MODEL_GROUPS_REV}
           OR NOT EXISTS (SELECT 1 FROM g WHERE g.session_id = m.session_id AND g.model_name = m.model_name))
)
UPDATE session_dims sd
SET cache_read_total = f.cache_read_total, api_call_total = f.api_call_total,
    classifier_names = f.classifier_names, models_used = f.models_used, kind_masks = f.kind_masks,
    cost_key = f.cost_key, rates_version = f.rates_version, model_groups_rev = f.model_groups_rev
FROM facts f
WHERE sd.session_id = f.session_id
  AND (sd.cache_read_total IS DISTINCT FROM f.cache_read_total
       OR sd.api_call_total IS DISTINCT FROM f.api_call_total
       OR sd.classifier_names IS DISTINCT FROM f.classifier_names
       OR sd.models_used IS DISTINCT FROM f.models_used
       OR sd.kind_masks IS DISTINCT FROM f.kind_masks
       OR sd.cost_key IS DISTINCT FROM f.cost_key
       OR sd.rates_version IS DISTINCT FROM f.rates_version
       OR sd.model_groups_rev IS DISTINCT FROM f.model_groups_rev)""")


async def update_session_sort_keys(
    conn: asyncpg.Connection,
    session_ids: Sequence[str],
    names: Mapping[str, Sequence[str]],
    rates: Mapping[str, ModelRates],
    rates_version: str,
    timeout: float | None = None,
) -> int:
    """Write the stored sort keys of `session_ids` (those with a session_dims row); returns how many rows changed.

    `names` are the classifier names per session (a session absent from it has none), `rates` the catalog rates the
    cost key is priced with and `rates_version` the catalog version they were resolved under, stamped on every derived
    row. `derive_session_sort_keys` resolves the rates of the sessions' own raw model names first.
    """
    ids = sorted({s for s in session_ids if s})
    if not ids:
        return 0
    pair_sessions = [s for s in ids for _ in dict.fromkeys(names.get(s) or ())]
    pair_names = [n for s in ids for n in dict.fromkeys(names.get(s) or ())]
    params = rates_params(list(rates.values()))
    status = await conn.execute(
        SORT_KEYS_SQL,
        ids,
        pair_sessions,
        pair_names,
        rates_version,
        params["rate_models"],
        *(params[f"rate_{column}"] for column in RATE_COLUMNS),
        timeout=timeout,
    )
    return int(status.rsplit(" ", 1)[-1])


# The raw model names of the sessions' token groups: the names the cost key prices, from the same grouping.
MODEL_NAMES_SQL = f"""
WITH sel AS (SELECT DISTINCT unnest($1::text[]) AS session_id)
SELECT DISTINCT g.model_name FROM {model_token_groups(normalized=False)}"""


async def session_model_names(
    conn: asyncpg.Connection, session_ids: Sequence[str], timeout: float | None = None
) -> list[str]:
    """The distinct raw model names the token groups of `session_ids` carry."""
    rows = await conn.fetch(MODEL_NAMES_SQL, list(session_ids), timeout=timeout)
    return [r["model_name"] for r in rows]


async def derive_session_sort_keys(
    conn: asyncpg.Connection,
    session_ids: Sequence[str],
    names: Mapping[str, Sequence[str]],
    rates: RatesFor,
    rates_version: str,
    timeout: float | None = None,
) -> int:
    """`update_session_sort_keys` priced with `rates` of the raw model names the sessions actually carry: the rates
    callable resolves each name as a request resolves its window's names, so a stored cost key equals today's.
    `rates_version` is the current catalog version, read before `rates` is called: a catalog change in between leaves
    an older stamp, which the readers distrust and the re-price rewrites, never a new stamp on old prices."""
    ids = sorted({s for s in session_ids if s})
    if not ids:
        return 0
    resolved = rates(await session_model_names(conn, ids, timeout))
    return await update_session_sort_keys(conn, ids, names, resolved, rates_version, timeout=timeout)


async def unstamp_session_sort_keys(
    conn: asyncpg.Connection, session_ids: Sequence[str], timeout: float | None = None
) -> None:
    """Drop the stamp of the sessions: their stored keys are no longer trusted and the readers compute them live (the
    set S) until the next derivation. Used when the keys of a session could not be rewritten along with its rollups."""
    ids = sorted({s for s in session_ids if s})
    if ids:
        await conn.execute(_UNSTAMP, ids, timeout=timeout)  # only the stamped rows: an unstamped one is not rewritten


class SortKeysResult(NamedTuple):
    derived: int  # sessions that had no stamp and got one
    repriced: int  # sessions stamped by another rate set that were re-derived
    batches: int
    duration_s: float
    wal_bytes: int | None  # WAL written by the whole server meanwhile (pg_current_wal_lsn difference), None if unknown


_PENDING_NULL = f"(rates_version IS NULL AND classifier_names IS DISTINCT FROM {_OVERSIZED_NAMES})"
_PENDING_STALE = "rates_version IS NOT NULL AND rates_version <> $3"
_NEXT_BATCH = """
SELECT session_id FROM session_dims WHERE {pending} AND session_id > $2 ORDER BY session_id LIMIT $1
FOR UPDATE SKIP LOCKED"""
_WAL_SQL = "SELECT pg_current_wal_lsn()"
_WAL_DIFF_SQL = "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), $1::pg_lsn)::bigint"
_DEFAULT_TIMEOUT_S = 330.0


async def _wal_start(conn: asyncpg.Connection) -> int | None:
    try:
        return int(await conn.fetchval(_WAL_SQL))
    except asyncpg.PostgresError:  # a standby has no current write position
        return None


async def _wal_since(conn: asyncpg.Connection, start: int | None) -> int | None:
    if start is None:
        return None
    return int(await conn.fetchval(_WAL_DIFF_SQL, start))


_MARK_SKIPPED = (
    f"UPDATE session_dims SET rates_version = NULL, classifier_names = {_OVERSIZED_NAMES} WHERE session_id = $1"
)


async def _mark_skipped(conn: asyncpg.Connection, session_id: str, error: Exception, timeout: float) -> None:
    """Mark a session whose derivation the database refused like an oversized one: unstamped, the readers compute it
    live (set S), and it no longer counts as pending, so it does not hold the automatic enable back. The next rollup
    of the session writes its names again and puts it back in line."""
    logger.warning(
        f"cli_analytics: stored sort keys of session {session_id} skipped and marked, derivation failed: {error!r}"
    )
    try:
        async with conn.transaction():
            await conn.execute(_MARK_SKIPPED, session_id, timeout=timeout)
    except asyncpg.PostgresError as exc:
        logger.warning(f"cli_analytics: session {session_id} could not be marked as skipped: {exc!r}")


async def _derive_isolating(
    conn: asyncpg.Connection,
    ids: Sequence[str],
    names: Mapping[str, Sequence[str]],
    rates: RatesFor,
    version: str,
    timeout: float,
) -> int:
    """Derive `ids` in a savepoint; when the database refuses the batch (a row too large for the btree, any other
    deterministic error of one session), halve it until the failing session stands alone, log, mark and skip that
    one, so a single bad session cannot stall the run, and with it the auto-enable, for every session after it. The
    skipped session is unstamped and marked: the readers compute it live. Returns how many sessions were derived."""
    try:
        async with conn.transaction():
            await derive_session_sort_keys(conn, ids, names, rates, version, timeout=timeout)
        return len(ids)
    except asyncpg.PostgresError as exc:
        if len(ids) == 1:
            await _mark_skipped(conn, ids[0], exc, timeout)
            return 0
        middle = len(ids) // 2
        return await _derive_isolating(conn, ids[:middle], names, rates, version, timeout) + await _derive_isolating(
            conn, ids[middle:], names, rates, version, timeout
        )


async def _derive_pending(
    conn: asyncpg.Connection,
    pending: str,
    version: str,
    rates: RatesFor,
    batch: int,
    budget_s: float | None,
    pause_s: float,
    timeout: float,
    max_batches: int | None = None,
) -> tuple[int, int]:
    """Derive the sessions `pending` selects, `batch` at a time, one transaction each; returns (sessions, batches).
    Stops when none is left, when `budget_s` has passed (checked between batches) or after `max_batches`."""
    deadline = None if budget_s is None else time.monotonic() + budget_s
    sql = _NEXT_BATCH.format(pending=pending)
    args = (version,) if "$3" in pending else ()
    last, sessions, batches = "", 0, 0
    while (deadline is None or time.monotonic() < deadline) and (max_batches is None or batches < max_batches):
        async with conn.transaction():
            rows = await conn.fetch(sql, batch, last, *args, timeout=timeout)
            if not rows:
                break
            ids = [r["session_id"] for r in rows]
            names = await read_classifier_names(conn, ids, timeout)
            derived = await _derive_isolating(conn, ids, names, rates, version, timeout)
        last = ids[-1]
        sessions += derived
        batches += 1
        if pause_s:
            await asyncio.sleep(pause_s)
    return sessions, batches


async def backfill_session_sort_keys(
    conn: asyncpg.Connection,
    rates: RatesFor,
    version: str,
    batch: int,
    budget_s: float | None = None,
    pause_s: float = 0.0,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> SortKeysResult:
    """Derive the stored sort keys of every session whose `rates_version` is NULL, stamping `version` (the
    current catalog version that `rates` resolves under). Idempotent and resumable: a stamped session is never
    selected again."""
    started, wal = time.monotonic(), await _wal_start(conn)
    derived, batches = await _derive_pending(conn, _PENDING_NULL, version, rates, batch, budget_s, pause_s, timeout)
    return SortKeysResult(derived, 0, batches, time.monotonic() - started, await _wal_since(conn, wal))


async def reprice_cost_keys(
    conn: asyncpg.Connection,
    rates: RatesFor,
    version: str,
    batch: int,
    budget_s: float | None = None,
    pause_s: float = 0.0,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> SortKeysResult:
    """Re-derive the sessions stamped with another rate set than `version`: `cost_key` and the stamp are written by
    one statement, so no session shows the new stamp with an old key. Until it runs, the readers compute those
    sessions live (they trust `cost_key` only when the stamp equals the current version)."""
    started, wal = time.monotonic(), await _wal_start(conn)
    repriced, batches = await _derive_pending(conn, _PENDING_STALE, version, rates, batch, budget_s, pause_s, timeout)
    return SortKeysResult(0, repriced, batches, time.monotonic() - started, await _wal_since(conn, wal))


# The sessions whose stored model groups are not (yet) trusted and can become so: no stamp (and not marked
# array-oversized, which no derivation changes), or a stamp with another revision of the groups than the current one
# and than "never valid" (a long raw model name). It implies the predicate of `session_dims_model_groups_pending`
# (`rates_version IS NULL OR model_groups_rev IS DISTINCT FROM 1`), so that index serves it.
PENDING_GROUPS = (
    f"((rates_version IS NULL AND classifier_names IS DISTINCT FROM {_OVERSIZED_NAMES}) "
    f"OR (rates_version IS NOT NULL AND model_groups_rev IS DISTINCT FROM {MODEL_GROUPS_REV} "
    f"AND model_groups_rev IS DISTINCT FROM {MODEL_GROUPS_NEVER}))"
)
# Sessions that can never hold stored groups: the array-oversized mark, or a long raw model name.
_NEVER_VALID_GROUPS = f"(classifier_names = {_OVERSIZED_NAMES} OR model_groups_rev = {MODEL_GROUPS_NEVER})"
_COUNT_PENDING_GROUPS = f"SELECT count(*) FROM session_dims WHERE {PENDING_GROUPS}"
_COUNT_NEVER_VALID_GROUPS = f"SELECT count(*) FROM session_dims WHERE {_NEVER_VALID_GROUPS}"


class ModelGroupsResult(NamedTuple):
    derived: int  # sessions re-derived, which writes their groups
    batches: int
    duration_s: float
    wal_bytes: int | None  # as `SortKeysResult.wal_bytes`


async def count_pending_model_groups(conn: asyncpg.Connection, timeout: float | None = None) -> int:
    """How many sessions the groups backfill would derive."""
    return int(await conn.fetchval(_COUNT_PENDING_GROUPS, timeout=timeout))


async def count_never_valid_model_groups(conn: asyncpg.Connection, timeout: float | None = None) -> int:
    """How many sessions can never hold stored groups (array-oversized, or a raw model name above
    `MAX_MODEL_NAME_BYTES`); the readers always compute them live."""
    return int(await conn.fetchval(_COUNT_NEVER_VALID_GROUPS, timeout=timeout))


async def backfill_model_groups(
    conn: asyncpg.Connection,
    rates: RatesFor,
    version: str,
    batch: int,
    budget_s: float | None = None,
    pause_s: float = 0.0,
    timeout: float = _DEFAULT_TIMEOUT_S,
    max_batches: int | None = None,
) -> ModelGroupsResult:
    """Write the stored model groups of the sessions `PENDING_GROUPS` selects, by re-deriving them through the stamp
    statement (which writes the groups with the Sessions keys, stamping `version`). Idempotent and resumable: a derived
    session is not selected again, and a run stops after `max_batches` batches (the maintenance job does one) or
    `budget_s`. `pause_s` is slept between batches, for a quiet-window rollout."""
    started, wal = time.monotonic(), await _wal_start(conn)
    derived, batches = await _derive_pending(
        conn, PENDING_GROUPS, version, rates, batch, budget_s, pause_s, timeout, max_batches
    )
    return ModelGroupsResult(derived, batches, time.monotonic() - started, await _wal_since(conn, wal))


# A stamp with neither activity column (the facts were never derived for it) cannot be shown old: treated as active.
_ACTIVE_SINCE = (
    "(last_event_at >= $3 OR last_activity_day >= ($3 AT TIME ZONE 'UTC')::date "
    "OR (last_event_at IS NULL AND last_activity_day IS NULL))"
)
_NEXT_ACTIVE = f"""
SELECT session_id FROM session_dims WHERE rates_version IS NOT NULL AND {_ACTIVE_SINCE} AND session_id > $2
ORDER BY session_id LIMIT $1 FOR UPDATE{{skip}}"""
_NEXT_STAMPED = """
SELECT session_id FROM session_dims WHERE rates_version IS NOT NULL AND session_id > $2
ORDER BY session_id LIMIT $1 FOR UPDATE{skip}"""
_SKIP_LOCKED = " SKIP LOCKED"
# The second sweep of an enable waits for the rows a writer held during the first, but not for ever.
_WAIT_LOCK_TIMEOUT = "30s"
_NULL_STAMPS = "UPDATE session_dims SET rates_version = NULL WHERE session_id = ANY($1::text[])"
_UNSTAMP = _NULL_STAMPS + " AND rates_version IS NOT NULL"
_SET_ENABLED = "UPDATE session_sort_keys_control SET enabled = $1, operator_disabled = NOT $1"
# A session whose last event is newer than this has just arrived (ingest writes it unstamped) and is derived by the
# next refresh: it does not hold the automatic enable back, and the readers compute it live until then.
_IN_FLIGHT = "interval '10 minutes'"
_AUTO_ENABLE_ELIGIBLE = (
    "SELECT EXISTS (SELECT 1 FROM session_sort_keys_control WHERE NOT enabled AND NOT operator_disabled) "
    f"AND NOT EXISTS (SELECT 1 FROM session_dims WHERE ({_PENDING_NULL} OR {_PENDING_STALE.replace('$3', '$1')}) "
    f"AND NOT coalesce(last_event_at >= now() - {_IN_FLIGHT}, false)) "
    # No pod of the previous code is still connected: it would go on rewriting rollups without the stored keys.
    "AND NOT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname = current_database() "
    "AND application_name = $2 AND pid <> pg_backend_pid())"
)
_AUTO_ENABLE = "UPDATE session_sort_keys_control SET enabled = true WHERE NOT enabled AND NOT operator_disabled"
# Pods still on the previous code (a rolling deploy) rewrite rollups without touching the stamp; the sessions they
# touched are the recently active ones, and the sweep before the automatic enable nulls their stamps for this long.
_AUTO_ENABLE_SWEEP = timedelta(hours=1)


async def _null_stamps_pass(
    conn: asyncpg.Connection, since: datetime | None, batch: int, timeout: float, wait: bool
) -> int:
    """One sweep over the stamped sessions active since `since` (all of them when None), in key order, a batch to a
    transaction. Without `wait` a row a writer holds is skipped; with it the sweep waits for that writer (up to
    `_WAIT_LOCK_TIMEOUT`, then the statement fails), so no matching row is left behind."""
    skip = "" if wait else _SKIP_LOCKED
    nulled, last = 0, ""
    while True:
        async with conn.transaction():
            if wait:
                await conn.execute(f"SET LOCAL lock_timeout = '{_WAIT_LOCK_TIMEOUT}'")
            if since is None:
                rows = await conn.fetch(_NEXT_STAMPED.format(skip=skip), batch, last, timeout=timeout)
            else:
                rows = await conn.fetch(_NEXT_ACTIVE.format(skip=skip), batch, last, since, timeout=timeout)
            if not rows:
                return nulled
            ids = [r["session_id"] for r in rows]
            await conn.execute(_NULL_STAMPS, ids, timeout=timeout)
        last = ids[-1]
        nulled += len(ids)


async def enable_session_sort_keys(
    conn: asyncpg.Connection, since: datetime | None, batch: int = 5000, timeout: float = _DEFAULT_TIMEOUT_S
) -> int:
    """Turn the stored readers on. Code that ran while the switch was off (older code writes no stored columns) left
    the stored values of the sessions active since then stale, so their stamps are nulled first (they re-derive
    through the backfill and the readers compute them live meanwhile); `since` is when the switch was cleared, and
    None nulls every stamp. A first sweep skips the rows a writer holds, a second waits for them: the readers are
    switched on only once no matching row kept its stamp, and not at all if a lock is not obtained in time. Returns
    how many stamps were nulled."""
    nulled = await _null_stamps_pass(conn, since, batch, timeout, wait=False)
    waited = await _null_stamps_pass(conn, since, batch, timeout, wait=True)
    await conn.execute(_SET_ENABLED, True)
    logger.info(
        f"cli_analytics: stored session sort keys enabled; {nulled + waited} stamps nulled for re-derivation "
        f"({waited} of them held by a writer meanwhile)"
    )
    return nulled + waited


async def auto_enable_session_sort_keys(
    conn: asyncpg.Connection, version: str, timeout: float = _DEFAULT_TIMEOUT_S
) -> bool:
    """Turn the stored readers on once no session lacks a usable stamp: none with a NULL `rates_version` or one that
    differs from the current `version` (the stamp the readers trust, the cost key included), apart from the sessions
    that arrived in the last `_IN_FLIGHT` (under steady ingest there is always one, and the readers compute those
    live). Few, because the benchmark showed 190 unstamped sessions in a 30-day window already multiply the cost and
    framework requests by 3-17. Before the switch the stamps of the sessions active in the last `_AUTO_ENABLE_SWEEP`
    are nulled, as `enable` does, so a stored key an older pod of a rolling deploy left behind is not trusted; a pod
    on the old code that writes after the switch is for the operator (storage guide, step 3).
    Not while a connection of the previous code is open (`LEGACY_APPLICATION_NAME` in `pg_stat_activity`): that is a
    pod of a rolling deploy not yet replaced, and the switch waits for the next run. Not airtight: a pod of the old code
    with no open connection at that moment (the pool keeps none) is not seen, and a pooling proxy may not pass the
    name on.
    Never after an explicit `disable` (`operator_disabled`); idempotent. True when this call turned them on."""
    if not await conn.fetchval(_AUTO_ENABLE_ELIGIBLE, version, LEGACY_APPLICATION_NAME, timeout=timeout):
        return False
    since = datetime.now(timezone.utc) - _AUTO_ENABLE_SWEEP
    await _null_stamps_pass(conn, since, 5000, timeout, wait=False)
    await _null_stamps_pass(conn, since, 5000, timeout, wait=True)
    # Guarded again: an operator's `disable` during the sweep wins.
    status = await conn.execute(_AUTO_ENABLE, timeout=timeout)
    turned_on = status.endswith(" 1")
    if turned_on:
        logger.info("cli_analytics: stored session sort keys enabled automatically: every session is stamped")
    return turned_on


async def disable_session_sort_keys(conn: asyncpg.Connection) -> None:
    """Clear the kill switch and record the operator's intent: every request goes back to the rollup statements, no data
    changes, and the maintenance job never turns the readers on again until `enable_session_sort_keys`."""
    await conn.execute(_SET_ENABLED, False)


# The groups switch is the Sessions switch's twin on its own columns: it governs the readers only (the stamp always
# writes), `disable` records the operator's intent, `enable` clears it, the automatic enable never overrides it.
_SET_GROUPS_ENABLED = (
    "UPDATE session_sort_keys_control SET model_groups_enabled = $1, model_groups_operator_disabled = NOT $1"
)
_GROUPS_AUTO_ENABLE_ELIGIBLE = (
    "SELECT EXISTS (SELECT 1 FROM session_sort_keys_control "
    "WHERE NOT model_groups_enabled AND NOT model_groups_operator_disabled) "
    f"AND NOT EXISTS (SELECT 1 FROM session_dims WHERE {PENDING_GROUPS} "
    f"AND NOT coalesce(last_event_at >= now() - {_IN_FLIGHT}, false)) "
    "AND NOT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname = current_database() "
    "AND application_name = $1 AND pid <> pg_backend_pid())"
)
_GROUPS_AUTO_ENABLE = (
    "UPDATE session_sort_keys_control SET model_groups_enabled = true "
    "WHERE NOT model_groups_enabled AND NOT model_groups_operator_disabled"
)


async def enable_model_groups(conn: asyncpg.Connection) -> None:
    """Turn the stored-groups readers on and clear the operator's intent. The rows are always kept current by the
    stamp, so nothing is nulled (unlike the Sessions `enable`)."""
    await conn.execute(_SET_GROUPS_ENABLED, True)
    logger.info("cli_analytics: stored model groups enabled")


async def disable_model_groups(conn: asyncpg.Connection) -> None:
    """Clear the groups switch and record the operator's intent: the readers compute the groups live, no data
    changes, and the maintenance job never turns the switch on again until `enable_model_groups`."""
    await conn.execute(_SET_GROUPS_ENABLED, False)
    logger.info("cli_analytics: stored model groups disabled by the operator")


async def auto_enable_model_groups(conn: asyncpg.Connection, timeout: float = _DEFAULT_TIMEOUT_S) -> bool:
    """Turn the stored-groups readers on once no session is pending (`PENDING_GROUPS`, apart from the sessions that
    arrived in the last `_IN_FLIGHT`, which the readers compute live) and no connection of the previous code is open.
    Never after `disable_model_groups`; idempotent. True when this call turned them on."""
    if not await conn.fetchval(_GROUPS_AUTO_ENABLE_ELIGIBLE, LEGACY_APPLICATION_NAME, timeout=timeout):
        return False
    status = await conn.execute(_GROUPS_AUTO_ENABLE, timeout=timeout)  # guarded again: a `disable` meanwhile wins
    turned_on = status.endswith(" 1")
    if turned_on:
        logger.info("cli_analytics: stored model groups enabled automatically: every session is derived")
    return turned_on
