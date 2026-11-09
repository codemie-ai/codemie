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

"""The stored activity facts of a session: `has_activity`, `last_activity_day`, `resumed` (EPMCDME-16427).

They are derived from the five rollup tables the Sessions window rule reads (`reader._ACTIVITY_TABLES`), so the rule
can ask one indexed column instead of five tables for each of ~10^6 sessions:

- `has_activity`: a row exists in any of the tables, on any day;
- `last_activity_day`: the latest such day;
- `resumed`: a row exists on a UTC day other than the session's start day (a continued session, or rows that predate
  the start). Sessions whose rows all sit on the start day are not `resumed`.

Concurrency. Two refreshers never overlap: a refresher batch runs on the connection that holds the session-level
advisory lock "rollup-refresher" (`AnalyticsPgEngine.advisory_lock`, `pg_try_advisory_lock`); another pod gets no
connection and skips the run. So the day rows of one session are written, and its facts derived, by one transaction at
a time. The refresher can overlap the maintenance backfill (lock "maintenance"), and that is safe in both orders: the
backfill locks the rows it takes (FOR UPDATE SKIP LOCKED), so it skips a session whose row the refresher holds, and a
refresher derive that waits for a backfill's row lock then writes facts computed from its own snapshot, which contains
its own uncommitted rows, over the backfill's (`test_live_session_activity.py`, both orders).

Writers. The facts depend on two things only: the day rows of the five tables and `session_dims.started_at` (the start
day decides `resumed`). Both are written by the rollup refresher alone, in `RollupRefresher._recompute`, and the
derive runs there last, for every session of the batch's keys, in the same transaction: so the facts are never stale
for a refresher write. The session binding (ingest) writes only `_BINDING_COLUMNS` (owner, subscription, origin, route
note, project name, email), never `started_at` or a day row, and a row without `started_at` is not in any list. A new
writer of `started_at` or of a day row outside the refresher must derive the facts of its sessions too
(`test_session_activity.py` guards the binding).

NULL means "not derived yet" (a session that existed before revision a1c1a0000011, or one written outside the
refresher): the reader asks the tables for those, so a list is exact whatever has been derived. The refresher derives
the sessions of every batch it recomputes, in the batch's transaction; `backfill_session_activity` derives the NULL
ones, a bounded batch per maintenance run.
"""

from __future__ import annotations

import logging
import time

import asyncpg

logger = logging.getLogger(__name__)

# The five tables of reader._ACTIVITY_TABLES (the reader and this module cannot import each other).
ACTIVITY_TABLES = ("turns_daily", "tool_facts_daily", "cost_daily", "session_usage", "lines_daily")

_DAYS = " UNION ALL ".join(
    f"SELECT session_id, day FROM {t} WHERE session_id = ANY($1::text[])" for t in ACTIVITY_TABLES
)

# One statement for a list of sessions: the activity of each from the tables, written when it differs from the stored
# value. A session with no row is written false (derived, no activity), not left NULL. A session with no start day
# counts every row as resumed (the safe side: it is a candidate for the window arm, which then checks exactly).
DERIVE_SQL = f"""
WITH ids AS (SELECT DISTINCT unnest($1::text[]) AS session_id),
days AS ({_DAYS}),
facts AS (
    SELECT i.session_id,
           max(d.day) AS last_day,
           coalesce(bool_or(d.day IS DISTINCT FROM (sd.started_at AT TIME ZONE 'UTC')::date)
                    FILTER (WHERE d.day IS NOT NULL), false) AS resumed
    FROM ids i
    JOIN session_dims sd ON sd.session_id = i.session_id
    LEFT JOIN days d ON d.session_id = i.session_id
    GROUP BY i.session_id
)
UPDATE session_dims sd
SET has_activity = f.last_day IS NOT NULL, last_activity_day = f.last_day, resumed = f.resumed
FROM facts f
WHERE sd.session_id = f.session_id
  AND (sd.has_activity IS DISTINCT FROM (f.last_day IS NOT NULL)
       OR sd.last_activity_day IS DISTINCT FROM f.last_day
       OR sd.resumed IS DISTINCT FROM f.resumed)"""

# The oldest underived sessions, locked so that two pods never derive the same ones.
_PENDING_SQL = """
SELECT session_id FROM session_dims WHERE has_activity IS NULL
ORDER BY started_at NULLS LAST, session_id LIMIT $1 FOR UPDATE SKIP LOCKED"""


async def derive_session_activity(conn: asyncpg.Connection, session_ids: list[str], timeout: float) -> int:
    """Write the activity facts of `session_ids` (those with a session_dims row); returns how many rows changed."""
    ids = sorted({s for s in session_ids if s})
    if not ids:
        return 0
    status = await conn.execute(DERIVE_SQL, ids, timeout=timeout)
    return int(status.rsplit(" ", 1)[-1])


async def backfill_session_activity(
    conn: asyncpg.Connection, batch_size: int, budget_s: float, statement_timeout_s: float
) -> int:
    """Derive the facts of the sessions still NULL, `batch_size` at a time, until none is left or `budget_s` seconds
    have passed; returns how many sessions were derived. Each batch is its own transaction, so a run that is stopped
    keeps what it did and a later run continues."""
    deadline = time.monotonic() + budget_s
    derived = 0
    while time.monotonic() < deadline:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL statement_timeout = '{int(statement_timeout_s)}s'")
            rows = await conn.fetch(_PENDING_SQL, batch_size, timeout=statement_timeout_s + 30)
            if not rows:
                return derived
            ids = [r["session_id"] for r in rows]
            await derive_session_activity(conn, ids, statement_timeout_s + 30)
        derived += len(ids)
    return derived
