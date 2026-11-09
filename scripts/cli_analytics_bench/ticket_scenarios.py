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

"""The scenarios the ticket EPMCDME-16427 reported, measured with the handler the API calls. Read-only.

    PG_URL=postgresql://... poetry run python scripts/cli_analytics_bench/ticket_scenarios.py [--schema S] [--runs 10]

Scenarios (a page of 20 sessions, the default sort, windows of the UI presets, which start at 00:00 UTC):
  7 days            time of `last_7_days`
  30 days           time of `last_30_days`
  30 days, rows     rows the database statements of one `last_30_days` request return to the application, and how many
                    statements ran (the ticket: 366,724 rows to render 20 sessions)
  search, 30 days   time of a search over `last_30_days` and the number of matching sessions
  two concurrent    two Sessions requests (`last_30_days`) at once, rounds after rounds, while an ingest-like probe
                    acquires a pool connection every 100 ms with the ingest acquire timeout (a timeout is an ingest 503)
The first request of each scenario is run once before timing (a cold plan and cache are a different measurement); the
timings are the `--runs` runs after it: min, median and max.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time

from codemie.configs.config import Config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.repository.cli_analytics.scope import ViewerScope
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from codemie.service.analytics.time_parser import TimeParser

SCOPE = ViewerScope(viewer_email="admin@x.com", unrestricted=True, admin_projects=frozenset(), mode="exclude")
SEARCH_TERMS = ("fix", "add")  # on the dev data: few matches and several thousand


def preset(period: str) -> LocalAnalyticsFilter:
    start, end = TimeParser.parse(period, None, None)
    return LocalAnalyticsFilter(scope=SCOPE, start_dt=start.replace(tzinfo=None), end_dt=end.replace(tzinfo=None))


class RowCounter:
    """Counts the statements and the rows the reader's statements return to the application."""

    def __init__(self) -> None:
        self.statements = 0
        self.rows = 0

    def install(self) -> None:
        counter = self
        original_q = PostgresCliAnalyticsReader._q

        async def counted_q(self, sql, params):
            rows = await original_q(self, sql, params)
            counter.statements += 1
            counter.rows += len(rows)
            return rows

        PostgresCliAnalyticsReader._q = counted_q  # type: ignore[method-assign]
        original_fetch = getattr(PostgresCliAnalyticsReader, "_fetch", None)
        if original_fetch is not None:

            async def counted_fetch(self, conn, sql, params):
                rows = await original_fetch(self, conn, sql, params)
                counter.statements += 1
                counter.rows += len(rows)
                return rows

            PostgresCliAnalyticsReader._fetch = counted_fetch  # type: ignore[method-assign]


def summary(seconds: list[float]) -> dict:
    return {
        "runs": len(seconds),
        "min_s": round(min(seconds), 2),
        "median_s": round(statistics.median(seconds), 2),
        "max_s": round(max(seconds), 2),
    }


async def timed(handler: LocalAnalyticsHandler, period: str, runs: int, search: str | None = None) -> tuple[dict, dict]:
    async def once() -> tuple[float, dict]:
        started = time.perf_counter()
        result = await handler.get_sessions(
            preset(period), page=0, per_page=20, sort_by="start_time", search=search, framework=None
        )
        return time.perf_counter() - started, result

    cold, result = await once()
    seconds = [(await once())[0] for _ in range(runs)]
    return {"first_run_s": round(cold, 2), **summary(seconds)}, result


def total_of(result) -> int | None:
    """`total` of the page: the handler returns (data, pricer, data_as_of); data has sessions, page, per_page, total."""
    data = result[0] if isinstance(result, tuple) else result
    return data.get("total") if isinstance(data, dict) else None


async def concurrent(engine: AnalyticsPgEngine, handler: LocalAnalyticsHandler, rounds: int) -> dict:
    stop = asyncio.Event()
    probes = timeouts = 0
    timeout = engine.settings.ingest_acquire_timeout_s

    async def ingest() -> None:
        nonlocal probes, timeouts
        while not stop.is_set():
            probes += 1
            try:
                async with engine.acquire(timeout=timeout) as conn:
                    await conn.fetchval("SELECT 1")
            except (TimeoutError, asyncio.TimeoutError):
                timeouts += 1
            await asyncio.sleep(0.1)

    async def request() -> float:
        started = time.perf_counter()
        await handler.get_sessions(
            preset("last_30_days"), page=0, per_page=20, sort_by="start_time", search=None, framework=None
        )
        return time.perf_counter() - started

    probe = asyncio.create_task(ingest())
    await asyncio.sleep(0.5)
    seconds: list[float] = []
    for _ in range(rounds):
        seconds += await asyncio.gather(request(), request())
    stop.set()
    await probe
    return {
        "rounds": rounds,
        "requests": len(seconds),
        "request_s": summary(seconds),
        "ingest_probes": probes,
        "ingest_timeouts": timeouts,
        "pool_size": engine.settings.pool_size,
        "ingest_acquire_timeout_s": timeout,
    }


async def run(args) -> dict:
    cfg = Config(PG_URL=os.environ["PG_URL"], CLI_ANALYTICS_PG_SCHEMA=args.schema)
    engine = AnalyticsPgEngine(AnalyticsPgSettings.from_config(cfg))
    counter = RowCounter()
    counter.install()
    handler = LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine))
    out: dict = {"schema": args.schema, "runs": args.runs}
    async with engine.acquire() as conn:
        out["sessions"] = await conn.fetchval("SELECT count(*) FROM session_dims")
        for name, days in (("sessions_in_7d", 7), ("sessions_in_30d", 30)):
            start, _ = TimeParser.parse(f"last_{days}_days", None, None)
            out[name] = await conn.fetchval(
                "SELECT count(*) FROM session_dims WHERE started_at >= $1", start.replace(tzinfo=None)
            )
    out["seven_days"], _ = await timed(handler, "last_7_days", args.runs)
    out["thirty_days"], _ = await timed(handler, "last_30_days", args.runs)
    counter.statements = counter.rows = 0
    await handler.get_sessions(
        preset("last_30_days"), page=0, per_page=20, sort_by="start_time", search=None, framework=None
    )
    out["thirty_days_rows"] = {"statements": counter.statements, "rows_returned": counter.rows}
    out["search_thirty_days"] = {}
    for term in args.search:
        timing, result = await timed(handler, "last_30_days", args.runs, search=term)
        out["search_thirty_days"][term] = {**timing, "matching_sessions": total_of(result)}
    out["two_concurrent_requests"] = await concurrent(engine, handler, args.rounds)
    await engine.close()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--schema", default="codemie_analytics")
    parser.add_argument("--runs", type=int, default=10, help="timed runs per scenario, after one cold run")
    parser.add_argument("--rounds", type=int, default=6, help="rounds of two concurrent requests")
    parser.add_argument("--search", nargs="*", default=list(SEARCH_TERMS), help="search terms for the 30-day search")
    print(json.dumps(asyncio.run(run(parser.parse_args())), indent=2))  # noqa: T201 - the result is the output


if __name__ == "__main__":
    main()
