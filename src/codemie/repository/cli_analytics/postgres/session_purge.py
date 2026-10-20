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

"""Purge of idle sessions: their session_dims row and their rows in the per-session tables.

Sessions go in batches, each one statement in its own transaction. A session whose rows another
transaction holds is skipped whole, never waited for; a session keeps its session_dims row until every
child row it counted is deleted, so no child row is left without one; a session with more child rows
than one statement may delete is deleted in parts after the batches.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING

import asyncpg

# A module import, read at call time: maintenance imports this module back.
from codemie.repository.cli_analytics.postgres import maintenance

if TYPE_CHECKING:
    from codemie.repository.cli_analytics.postgres.maintenance import MaintenanceReport

logger = logging.getLogger(__name__)

# Child rows one purge statement deletes at most: about a second per statement at the delete rate
# measured on PostgreSQL 14 and 17, over a hundred times under the statement timeout.
ROW_CAP = 100_000

# The purge never waits long for a lock: rows another transaction holds are skipped, and a lock wait
# that is still left fails fast instead of at the statement timeout; the next run retries.
PURGE_LOCK_TIMEOUT_MS = 2000
_PURGE_LOCK_TIMEOUT = f"SET LOCAL lock_timeout = {PURGE_LOCK_TIMEOUT_MS}"

_SKIPPED_IDS_LOGGED = 10


def _idle(utc_midnight: str, tomorrow: str) -> str:
    """The session_dims row has seen no activity since before the cutoff."""
    past = maintenance.PartitionMaintainer._past
    return (
        f"coalesce(greatest({past('last_event_at', tomorrow)}, {past('ended_at', tomorrow)}), updated_at) "
        f"< {utc_midnight}"
    )


def _child_counts(source: str) -> str:
    """The visible rows of each child table per session of `source`, counted through the primary key up to
    $3 + 1: only whether a table holds more than $3 of them matters beyond that."""
    tables = maintenance._SESSION_CHILDREN
    columns = ", ".join(f"{t}_visible.n AS {t}_rows" for t in tables)
    total = " + ".join(f"{t}_visible.n" for t in tables)
    laterals = " ".join(
        f"CROSS JOIN LATERAL (SELECT count(*) AS n FROM (SELECT 1 FROM {t} WHERE {t}.session_id = s.session_id "
        f"LIMIT $3 + 1) x) {t}_visible"
        for t in tables
    )
    return f"SELECT s.session_id, {columns}, {total} AS n FROM {source} s {laterals}"


def batch_statement(utc_midnight: str, tomorrow: str) -> str:
    """One batch of whole idle sessions. Binds: $1 the cutoff date, $2 the most sessions, $3 the most
    child rows, $4 the session ids to leave out. Returns the candidates taken, the sessions deleted, the
    rows deleted, the ids of the held sessions and the ids of the heavy ones.

    The batch CTE takes up to $2 idle sessions in no order and is evaluated once: FOR UPDATE locks them
    and tests a row another transaction changed meanwhile again on its newest version, leaving it out
    when it is no longer idle; SKIP LOCKED leaves out a session_dims row another transaction holds. A
    candidate with more than $3 child rows is heavy and left to be deleted in parts. The others are
    chosen in session_id order while their child rows add up to at most $3, and their child rows are
    locked with SKIP LOCKED: a session that could not lock every row it counted is held and keeps all its
    rows. Every other chosen session loses exactly the child rows locked for it. A child row changed and
    committed after the statement snapshot is locked at a version the snapshot does not see, and its
    delete removes nothing: a session that lost fewer rows than it counted in any child table is torn,
    keeps its session_dims row and the rows left, and is returned among the held ones. Every other chosen
    session loses its session_dims row too.
    """
    tables = maintenance._SESSION_CHILDREN
    held_rows = [
        f"{t}_held AS MATERIALIZED (SELECT l.tid, l.session_id FROM chosen c CROSS JOIN LATERAL "
        f"(SELECT ctid AS tid, session_id FROM {t} WHERE {t}.session_id = c.session_id FOR UPDATE SKIP LOCKED) l)"
        for t in tables
    ]
    locked_counts = " ".join(
        f"LEFT JOIN (SELECT session_id, count(*) AS n FROM {t}_held GROUP BY session_id) {t}_locked "
        f"ON {t}_locked.session_id = c.session_id"
        for t in tables
    )
    short = " OR ".join(f"c.{t}_rows <> coalesce({t}_locked.n, 0)" for t in tables)
    purged = [
        f"{t}_purged AS (DELETE FROM {t} WHERE ctid = ANY(ARRAY(SELECT h.tid FROM {t}_held h "
        f"WHERE NOT EXISTS (SELECT 1 FROM held x WHERE x.session_id = h.session_id))) RETURNING session_id)"
        for t in tables
    ]
    deleted_counts = " ".join(
        f"LEFT JOIN (SELECT session_id, count(*) AS n FROM {t}_purged GROUP BY session_id) {t}_deleted "
        f"ON {t}_deleted.session_id = c.session_id"
        for t in tables
    )
    lost = " OR ".join(f"c.{t}_rows <> coalesce({t}_deleted.n, 0)" for t in tables)
    rows = " + ".join(["(SELECT count(*) FROM idle)", *(f"(SELECT count(*) FROM {t}_purged)" for t in tables)])
    return (
        "WITH batch AS MATERIALIZED (SELECT session_id FROM session_dims "
        f"WHERE {_idle(utc_midnight, tomorrow)} AND NOT EXISTS (SELECT 1 FROM unnest($4::text[]) AS s(id) "
        "WHERE s.id = session_dims.session_id) LIMIT $2 FOR UPDATE SKIP LOCKED), "
        f"visible AS ({_child_counts('batch')}), "
        "heavy AS (SELECT session_id FROM visible WHERE n > $3), "
        "chosen AS (SELECT * FROM (SELECT v.*, sum(v.n) OVER (ORDER BY v.session_id) AS running "
        "FROM visible v WHERE v.n <= $3) w WHERE w.running <= $3), "
        f"{', '.join(held_rows)}, "
        f"held AS (SELECT c.session_id FROM chosen c {locked_counts} WHERE {short}), "
        f"{', '.join(purged)}, "
        f"torn AS (SELECT c.session_id FROM chosen c {deleted_counts} "
        f"WHERE NOT EXISTS (SELECT 1 FROM held h WHERE h.session_id = c.session_id) AND ({lost})), "
        "idle AS (DELETE FROM session_dims d USING chosen c WHERE d.session_id = c.session_id "
        "AND NOT EXISTS (SELECT 1 FROM held h WHERE h.session_id = c.session_id) "
        "AND NOT EXISTS (SELECT 1 FROM torn t WHERE t.session_id = c.session_id) RETURNING d.session_id) "
        f"SELECT (SELECT count(*) FROM batch), (SELECT count(*) FROM idle), {rows}, "
        "ARRAY(SELECT session_id FROM held UNION ALL SELECT session_id FROM torn), ARRAY(SELECT session_id FROM heavy)"
    )


def part_statement(utc_midnight: str, tomorrow: str) -> str:
    """One part of a heavy session. Binds: $1 the cutoff date, $2 the session id, $3 the most child rows.
    Returns whether the session was taken, the rows deleted, whether it is held and whether it is done.

    The session_dims row is taken FOR UPDATE SKIP LOCKED with the idle test again: held, no longer idle
    or gone, nothing is deleted. Otherwise the child tables in turn get the budget of $3 rows: each locks
    up to what is left of it with SKIP LOCKED and loses the rows it locked. Fewer rows locked than the
    table's share of the budget means another transaction holds some; when every visible child row was
    locked and deleted, the session_dims row goes in the same statement. A child row changed and committed
    after the statement snapshot is locked at a version the snapshot does not see, and its delete removes
    nothing: a part that deleted fewer rows than it locked in any table is held too.
    """
    tables = maintenance._SESSION_CHILDREN
    shares = [f"least({tables[0]}_rows, $3) AS {tables[0]}_take"]
    for i, t in enumerate(tables[1:], start=1):
        before = "".join(f" - {prior}_rows" for prior in tables[:i])
        shares.append(f"least({t}_rows, greatest($3{before}, 0)) AS {t}_take")
    held_rows = [
        f"{t}_held AS MATERIALIZED (SELECT l.tid FROM budget b CROSS JOIN LATERAL (SELECT ctid AS tid FROM {t} "
        f"WHERE {t}.session_id = b.session_id LIMIT b.{t}_take FOR UPDATE SKIP LOCKED) l)"
        for t in tables
    ]
    purged = [
        f"{t}_purged AS (DELETE FROM {t} WHERE ctid = ANY(ARRAY(SELECT tid FROM {t}_held)) RETURNING 1)" for t in tables
    ]
    short = " OR ".join(
        [
            *(f"b.{t}_take <> (SELECT count(*) FROM {t}_held)" for t in tables),
            *(f"(SELECT count(*) FROM {t}_purged) <> (SELECT count(*) FROM {t}_held)" for t in tables),
        ]
    )
    whole = " AND ".join(f"b.{t}_take = b.{t}_rows" for t in tables)
    rows = " + ".join(["(SELECT count(*) FROM done)", *(f"(SELECT count(*) FROM {t}_purged)" for t in tables)])
    return (
        "WITH dims AS MATERIALIZED (SELECT session_id FROM session_dims "
        f"WHERE session_id = $2 AND {_idle(utc_midnight, tomorrow)} FOR UPDATE SKIP LOCKED), "
        f"visible AS ({_child_counts('dims')}), "
        f"budget AS (SELECT *, {', '.join(shares)} FROM visible), "
        f"{', '.join(held_rows)}, "
        f"{', '.join(purged)}, "
        f"held AS (SELECT b.session_id FROM budget b WHERE {short}), "
        "done AS (DELETE FROM session_dims d USING budget b WHERE d.session_id = b.session_id "
        f"AND {whole} AND NOT EXISTS (SELECT 1 FROM held) RETURNING 1) "
        f"SELECT (SELECT count(*) FROM dims), {rows}, EXISTS (SELECT 1 FROM held), EXISTS (SELECT 1 FROM done)"
    )


def idle_left_statement(utc_midnight: str, tomorrow: str) -> str:
    """The idle sessions a purge that ended normally left. Binds: $1 the cutoff date, $2 the time the
    purge started, $3 the session ids already logged as held. Returns how many are left and up to ten
    of their ids; takes no lock."""
    # The batches end only on one with no candidate and the cutoff is fixed for the run, so an idle
    # session left and not in $3 is one whose session_dims row SKIP LOCKED passed over, or a heavy
    # session whose part could not take it. The rollup refresher can re-create or update an old
    # session's session_dims row during the run, setting updated_at to its transaction's start: such
    # a row is idle but locked by nobody. The bound on $2 leaves out the rows of refresher
    # transactions that started after the purge did; one already open when the purge started can
    # still put such a row in the count once, and the next run no longer finds it. A row actually
    # held keeps its earlier committed updated_at and is still counted.
    return (
        "SELECT count(*), (array_agg(session_id))[1:10] FROM session_dims "
        f"WHERE {_idle(utc_midnight, tomorrow)} AND updated_at < $2 AND NOT EXISTS (SELECT 1 FROM "
        "unnest($3::text[]) AS s(id) WHERE s.id = session_dims.session_id)"
    )


async def purge_idle_sessions(
    conn: asyncpg.Connection, utc_midnight: str, tomorrow: str, cutoff: date, batch_size: int, report: MaintenanceReport
) -> None:
    """Whole-session batches until one finds no candidate, then the heavy sessions in parts, then one
    count of the idle sessions left.

    The ids of held and heavy sessions leave the later batches of this run. A failing batch or part
    ends the purge: what was deleted before it stays and the next run continues. Sessions whose rows
    another transaction holds are logged once at the end, also after a failure; those whose
    session_dims row is held are found only by the count, run after a purge that ended normally.
    """
    held: list[str] = []
    uncounted = 0  # idle sessions the count found beyond the ids it returned
    try:
        # The count leaves out session_dims rows written by transactions that start after this read, so
        # a row the rollup refresher re-creates during the run is not reported as held.
        started_at = await conn.fetchval("SELECT clock_timestamp()")
        heavy, drained = await _purge_batches(
            conn, batch_statement(utc_midnight, tomorrow), cutoff, batch_size, held, report
        )
        part = part_statement(utc_midnight, tomorrow)
        for session_id in heavy:
            if await _purge_in_parts(conn, part, cutoff, session_id, report):
                held.append(session_id)
    except maintenance._ISOLATED_FAILURES as exc:
        report.failed.append("purge sessions")
        logger.error(f"cli_analytics: purging expired rows of sessions failed: {exc!r}")
    else:
        if drained:
            uncounted = await _count_idle_left(
                conn, idle_left_statement(utc_midnight, tomorrow), cutoff, started_at, held
            )
    finally:
        if held:
            logger.error(
                f"cli_analytics: the session purge skipped {len(held) + uncounted} idle sessions whose rows another "
                f"transaction holds; the next run retries them: {', '.join(held[:_SKIPPED_IDS_LOGGED])}"
            )


async def _purge_batches(
    conn: asyncpg.Connection, sql: str, cutoff: date, batch_size: int, held: list[str], report: MaintenanceReport
) -> tuple[list[str], bool]:
    """Runs the batches, adding the held session ids to `held`; returns the heavy session ids and whether
    the last batch found no candidate (False when a batch that changed nothing stopped them).

    Every candidate of a batch has its child rows counted, but only those within the row cap are chosen;
    the rest are taken and counted again by the next batch. So a batch that left candidates unchosen makes
    the next one take twice as many as it chose, and a batch that decided all its candidates lets the next
    one take twice as many again, never more than `batch_size`.
    """
    heavy: list[str] = []
    skip: list[str] = []
    skipped: set[str] = set()
    size = batch_size
    while True:
        async with conn.transaction():
            await _set_timeouts(conn)
            candidates, sessions, rows, batch_held, batch_heavy = await conn.fetchrow(
                sql, cutoff, size, ROW_CAP, list(skip), timeout=maintenance._RAISED_CLIENT_TIMEOUT_S
            )
        report.purged_rows += int(rows)
        held.extend(batch_held)
        heavy += batch_heavy
        new = [session_id for session_id in (*batch_held, *batch_heavy) if session_id not in skipped]
        skip += new
        skipped.update(new)
        if not candidates:
            break
        if not sessions and not new:
            # A batch that changes nothing would repeat for ever under the maintenance advisory lock.
            logger.error(
                f"cli_analytics: the session purge stopped: a batch of {candidates} idle sessions before "
                f"{cutoff} deleted none and skipped none"
            )
            break
        chosen = sessions + len(batch_held)
        if candidates > chosen + len(batch_heavy):
            size = min(batch_size, max(1, 2 * chosen))
        else:
            size = min(batch_size, 2 * size)
    return heavy, not candidates


async def _purge_in_parts(
    conn: asyncpg.Connection, sql: str, cutoff: date, session_id: str, report: MaintenanceReport
) -> bool:
    """Deletes one heavy session part by part; True when another transaction holds some of its rows."""
    while True:
        async with conn.transaction():
            await _set_timeouts(conn)
            taken, rows, held, done = await conn.fetchrow(
                sql, cutoff, session_id, ROW_CAP, timeout=maintenance._RAISED_CLIENT_TIMEOUT_S
            )
        report.purged_rows += int(rows)
        if not taken or done:
            return False
        if held or not rows:
            return True


async def _count_idle_left(
    conn: asyncpg.Connection, sql: str, cutoff: date, started_at: datetime, held: list[str]
) -> int:
    """Adds up to ten idle sessions left to `held`; returns how many more were left than it added. A
    failing count is logged and adds none."""
    try:
        async with conn.transaction():
            await _set_timeouts(conn)
            count, ids = await conn.fetchrow(
                sql, cutoff, started_at, list(held), timeout=maintenance._RAISED_CLIENT_TIMEOUT_S
            )
    except maintenance._ISOLATED_FAILURES as exc:
        logger.error(f"cli_analytics: could not count the idle sessions the session purge left: {exc!r}")
        return 0
    if not count:
        return 0
    held.extend(ids)
    return count - len(ids)


async def _set_timeouts(conn: asyncpg.Connection) -> None:
    await conn.execute(maintenance._RAISED_STATEMENT_TIMEOUT, timeout=maintenance._RAISED_CLIENT_TIMEOUT_S)
    await conn.execute(_PURGE_LOCK_TIMEOUT, timeout=maintenance._RAISED_CLIENT_TIMEOUT_S)
