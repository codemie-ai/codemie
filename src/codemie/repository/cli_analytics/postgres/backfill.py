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

"""Rebuild the rollups of a date range: backfill after an outage, repair after a fix.

    python -m codemie.repository.cli_analytics.postgres.backfill --from 2026-09-01 --to 2026-09-23 [--now]

First, rows stored before their span or event name was added to the harness vocabulary get
their kind. Then every (day, session) with raw rows in the range is queued, and the
application's refresh job rebuilds them within its interval. With --now this process
rebuilds what it queued itself, holding the refresher's advisory lock so it never runs
alongside a pod's refresh job. Days past raw retention keep their rollups: there is nothing
left to rebuild them from.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
from datetime import date, timedelta
from typing import NamedTuple

import asyncpg

from codemie.configs.config import config
from codemie.repository.cli_analytics.postgres.engine import CLIENT_TIMEOUT_MARGIN_S, AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.repository.cli_analytics.vocabulary import EVENT_KIND, SPAN_KIND

logger = logging.getLogger(__name__)

# A day's rows can be many: the same budget as a rollup refresh, on both sides.
_STATEMENT_TIMEOUT_S = 300
_STATEMENT_TIMEOUT = f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT_S}s'"
_CLIENT_TIMEOUT_S = _STATEMENT_TIMEOUT_S + CLIENT_TIMEOUT_MARGIN_S
_DAY = "($3::date::timestamp AT TIME ZONE 'UTC')"
_NEXT_DAY = "(($3::date + 1)::timestamp AT TIME ZONE 'UTC')"
_RECLASSIFY_SPANS = f"""
UPDATE spans s SET span_kind = m.kind
FROM unnest($1::text[], $2::int2[]) AS m(name, kind)
WHERE s.span_kind = 0 AND s.span_name = m.name AND s.ts >= {_DAY} AND s.ts < {_NEXT_DAY}"""
_RECLASSIFY_LOG_EVENTS = f"""
UPDATE log_events l SET event_kind = m.kind
FROM unnest($1::text[], $2::int2[]) AS m(name, kind)
WHERE l.event_kind = 0 AND l.event_name = m.name AND l.ts >= {_DAY} AND l.ts < {_NEXT_DAY}"""
_QUEUED_SINCE_BEFORE = "SELECT count(*) FROM rollup_dirty WHERE marked_at <= $1"


class BackfillResult(NamedTuple):
    reclassified: int  # raw rows that got the kind the vocabulary now maps them to
    queued: int  # (day, session) keys queued for a rebuild
    rebuilt: int  # keys rebuilt by this process (--now)


def _rows(status: str) -> int:
    return int(status.rsplit(" ", 1)[-1])


async def reclassify(conn: asyncpg.Connection, first_day: date, last_day: date) -> int:
    """Give kind-0 rows whose name the vocabulary now maps their kind, one day at a time."""
    span_names, span_kinds = list(SPAN_KIND), [int(k) for k in SPAN_KIND.values()]
    event_names, event_kinds = list(EVENT_KIND), [int(k) for k in EVENT_KIND.values()]
    reclassified = 0
    day = first_day
    while day <= last_day:
        async with conn.transaction():
            await conn.execute(_STATEMENT_TIMEOUT, timeout=_CLIENT_TIMEOUT_S)
            for sql, names, kinds in (
                (_RECLASSIFY_SPANS, span_names, span_kinds),
                (_RECLASSIFY_LOG_EVENTS, event_names, event_kinds),
            ):
                reclassified += _rows(await conn.execute(sql, names, kinds, day, timeout=_CLIENT_TIMEOUT_S))
        day += timedelta(days=1)
    return reclassified


async def backfill(
    engine: AnalyticsPgEngine, settings: AnalyticsPgSettings, first_day: date, last_day: date, now: bool
) -> BackfillResult:
    refresher = RollupRefresher(
        engine,
        batch_size=settings.rollup_batch_size,
        raw_retention_days=settings.raw_retention_days,
    )
    async with engine.acquire() as conn:
        reclassified = await reclassify(conn, first_day, last_day)
        queued = await refresher.mark_dirty(first_day, last_day, conn)
        started = await conn.fetchval("SELECT clock_timestamp()")
    rebuilt = 0
    if now:
        async with engine.advisory_lock("rollup-refresher") as conn:
            if conn is None:
                logger.info("cli_analytics: a pod's refresh job holds the refresher lock; it rebuilds the queued keys")
            else:
                # Until the keys queued by now are handled; live ingest keeps marking newer ones.
                # Each run moves the queue (keys too slow to recompute go to its back), so two runs
                # in a row without progress only happen if the refresher is broken: stop then.
                remaining, stalled = await conn.fetchval(_QUEUED_SINCE_BEFORE, started), 0
                while remaining and stalled < 2:
                    rebuilt += await refresher.refresh(conn)
                    left = await conn.fetchval(_QUEUED_SINCE_BEFORE, started)
                    stalled = stalled + 1 if left >= remaining else 0
                    remaining = left
    return BackfillResult(reclassified, queued, rebuilt)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--from", dest="first_day", type=date.fromisoformat, required=True, help="first day, YYYY-MM-DD"
    )
    parser.add_argument("--to", dest="last_day", type=date.fromisoformat, required=True, help="last day, YYYY-MM-DD")
    parser.add_argument("--now", action="store_true", help="rebuild in this process instead of the refresh job")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.last_day < args.first_day:
        parser.error("--to is before --from")
    if config.CLI_ANALYTICS_STORAGE_BACKEND != "postgres":
        parser.error("CLI_ANALYTICS_STORAGE_BACKEND is not postgres: ClickHouse keeps no rollup queue")

    settings = AnalyticsPgSettings.from_config(config)

    async def run() -> BackfillResult:
        engine = AnalyticsPgEngine(settings)
        try:
            return await backfill(engine, settings, args.first_day, args.last_day, now=args.now)
        finally:
            await engine.close()

    result = asyncio.run(run())
    # The audit line, and the command's report: the application's logging writes to the console.
    logger.info(
        f"cli_analytics: rollup backfill of {args.first_day}..{args.last_day} by {getpass.getuser()}: "
        f"{result.reclassified} rows reclassified, {result.queued} (day, session) keys queued, "
        f"{result.rebuilt} rebuilt now"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
