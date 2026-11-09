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

"""AC18: time the plain build of the six session_dims indexes while writers run (EPMCDME-16427).

Works on a disposable copy of session_dims in the schema `ac18` (create it first, never the live table):

    PG_URL=postgresql://... poetry run python scripts/cli_analytics_bench/index_build_bench.py --schema ac18

Writers: a binding-like writer (short UPDATEs of 50 sessions every 50 ms, one autocommit statement each) and a
refresher-like writer (one transaction updating 5000 rows, held ~1 s). For each index the script measures the refresher
batch before the build, then builds with the runner's rule (SHARE lock wait bounded by lock_timeout 5 s, a failed
attempt retried), timing the lock wait and the build separately, records the longest wait a writer saw during the build
(an ingest binding blocked that long answers 503 after its acquire timeout), and measures the batch again after.

Finally it measures what the migration really does: all six builds in ONE transaction under ONE SHARE lock
(`all_six`), reporting each build and `lock_hold_ms`, the time from the lock being granted to the commit, which is
the time writers are blocked. The runbook figure must come from `all_six.build.lock_hold_ms`, not from the sum of
the single-index runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time

import asyncpg

_SHARED_PREDICATE = (
    "WHERE started_at IS NOT NULL AND NOT "
    "(lower(btrim(coalesce(project_origin, ''), E' \\t\\n\\r\\f\\013')) = 'personal' OR "
    "(lower(btrim(coalesce(subscription_type, ''), E' \\t\\n\\r\\f\\013')) = 'personal' AND "
    "lower(btrim(coalesce(project_origin, ''), E' \\t\\n\\r\\f\\013')) NOT IN "
    "('linked_folder', 'profile', 'registered_repository', 'default_project', 'legacy_project')))"
)
# The six indexes of revision a1c1a0000011, in the order the migration builds them. The last four are over the
# activity facts that the same revision adds first (`_FACT_COLUMNS`, added to the copy first, all NULL as after it).
_FACT_COLUMNS = (
    "has_activity boolean, ADD COLUMN IF NOT EXISTS last_activity_day date, ADD COLUMN IF NOT EXISTS resumed boolean"
)
INDEXES = {
    "session_dims_started_at": "CREATE INDEX {name} ON {schema}.session_dims (started_at)",
    "session_dims_started_shared": "CREATE INDEX {name} ON {schema}.session_dims (started_at) " + _SHARED_PREDICATE,
    "session_dims_active_started": "CREATE INDEX {name} ON {schema}.session_dims (started_at) WHERE has_activity",
    "session_dims_active_shared_cov": (
        "CREATE INDEX {name} ON {schema}.session_dims (started_at) INCLUDE (session_id) "
        + _SHARED_PREDICATE.replace("WHERE ", "WHERE has_activity AND ", 1)
    ),
    "session_dims_resumed_last": "CREATE INDEX {name} ON {schema}.session_dims (last_activity_day) WHERE resumed",
    "session_dims_activity_pending": (
        "CREATE INDEX {name} ON {schema}.session_dims (started_at) WHERE has_activity IS NULL"
    ),
}
BATCH = 5000


class Writers:
    def __init__(self, dsn: str, schema: str, hold: float) -> None:
        self.dsn, self.schema, self.hold = dsn, schema, hold
        self.stop = asyncio.Event()
        self.binding_latencies: list[tuple[float, float]] = []  # (finished at, seconds)
        self.batch_seconds: list[tuple[float, float, str]] = []  # (finished at, seconds, column)

    async def binding(self) -> None:
        conn = await asyncpg.connect(self.dsn)
        ids = [
            r[0]
            for r in await conn.fetch(
                f"SELECT session_id FROM {self.schema}.session_dims ORDER BY session_id LIMIT 20000"
            )
        ]
        i = 0
        while not self.stop.is_set():
            batch = ids[i : i + 50] or ids[:50]
            i = (i + 50) % len(ids)
            started = time.perf_counter()
            await conn.execute(
                f"UPDATE {self.schema}.session_dims SET updated_at = now() WHERE session_id = ANY($1::text[])", batch
            )
            self.binding_latencies.append((time.perf_counter(), time.perf_counter() - started))
            await asyncio.sleep(0.05)
        await conn.close()

    async def refresher(self, column: str) -> None:
        conn = await asyncpg.connect(self.dsn)
        offset = 100000
        while not self.stop.is_set():
            started = time.perf_counter()
            async with conn.transaction():
                await conn.execute(
                    f"UPDATE {self.schema}.session_dims SET {column} = {column} + interval '1 millisecond' "
                    f"WHERE session_id IN (SELECT session_id FROM {self.schema}.session_dims "
                    f"ORDER BY session_id OFFSET $1 LIMIT {BATCH})",
                    offset,
                )
                await asyncio.sleep(self.hold)  # the batch holds its row locks while it does the rest of its work
            self.batch_seconds.append((time.perf_counter(), time.perf_counter() - started - self.hold, column))
            offset = (offset + BATCH) % 600000 + 100000
        await conn.close()


def summarise(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50_ms": round(statistics.median(values) * 1000, 1) if values else None,
        "max_ms": round(max(values) * 1000, 1) if values else None,
    }


async def window_of(writers: Writers, start: float, end: float, column: str) -> dict:
    batches = [s for t, s, c in writers.batch_seconds if start <= t <= end and c == column]
    binding = [s for t, s in writers.binding_latencies if start <= t <= end]
    return {"refresh_batch": summarise(batches), "binding_update": summarise(binding)}


async def build(dsn: str, schema: str, names: list[str]) -> dict:
    """The runner's rule: SHARE lock under lock_timeout 5 s, then the builds of `names` in one transaction; a failed
    attempt is retried. `lock_hold_ms` is the time from the lock being granted to the commit."""
    conn = await asyncpg.connect(dsn)
    failed = 0
    started_all = time.perf_counter()
    while True:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SET LOCAL lock_timeout = '5s'")
            lock_started = time.perf_counter()
            await conn.execute(f"LOCK TABLE {schema}.session_dims IN SHARE MODE")
            lock_granted = time.perf_counter()
            builds_ms = {}
            for name in names:
                build_started = time.perf_counter()
                await conn.execute(INDEXES[name].format(name=name, schema=schema))
                builds_ms[name] = round((time.perf_counter() - build_started) * 1000, 1)
            await tx.commit()
            lock_released = time.perf_counter()
            break
        except asyncpg.exceptions.LockNotAvailableError:
            failed += 1
            await tx.rollback()
            await asyncio.sleep(1)
    await conn.close()
    return {
        "failed_attempts_at_5s": failed,
        "lock_wait_ms": round((lock_granted - lock_started) * 1000, 1),
        "build_ms": builds_ms,
        "lock_hold_ms": round((lock_released - lock_granted) * 1000, 1),
        "total_ms": round((time.perf_counter() - started_all) * 1000, 1),
    }


async def main(schema: str, hold: float) -> dict:
    dsn = os.environ["PG_URL"]
    result: dict = {"schema": schema, "refresher_hold_s": hold, "rows": None}
    conn = await asyncpg.connect(dsn)
    await conn.execute(f"ALTER TABLE {schema}.session_dims ADD COLUMN IF NOT EXISTS {_FACT_COLUMNS}")
    result["rows"] = await conn.fetchval(f"SELECT count(*) FROM {schema}.session_dims")
    for name in INDEXES:
        await conn.execute(f"DROP INDEX IF EXISTS {schema}.{name}")
    await conn.close()
    column = "started_at"
    scenarios = [(name, [name]) for name in INDEXES] + [("all_six", list(INDEXES))]
    for label, names in scenarios:
        if label == "all_six":  # the single-index runs left their indexes behind; the migration starts without them
            conn = await asyncpg.connect(dsn)
            for name in INDEXES:
                await conn.execute(f"DROP INDEX IF EXISTS {schema}.{name}")
            await conn.close()
        writers = Writers(dsn, schema, hold)
        tasks = [asyncio.create_task(writers.binding()), asyncio.create_task(writers.refresher(column))]
        await asyncio.sleep(max(8, 2 * hold))  # before: batches and bindings without this index
        before_end = time.perf_counter()
        before = await window_of(writers, before_end - max(8, 2 * hold), before_end, column)
        built_from = time.perf_counter()
        built = await build(dsn, schema, names)
        built_to = time.perf_counter()
        during = await window_of(writers, built_from, built_to + 1, column)
        await asyncio.sleep(max(8, 2 * hold))  # after
        after_end = time.perf_counter()
        after = await window_of(writers, after_end - max(8, 2 * hold), after_end, column)
        writers.stop.set()
        await asyncio.gather(*tasks)
        result[label] = {"build": built, "before": before, "during": during, "after": after}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--schema", default="ac18")
    parser.add_argument("--hold", type=float, default=0.8, help="seconds a refresher-like batch holds its row locks")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(main(args.schema, args.hold)), indent=2))  # noqa: T201 - the recorded artifact
