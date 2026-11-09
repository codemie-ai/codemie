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

"""Benchmark of the CLI Analytics Sessions list (EPMCDME-16427). Read-only against the analytics database.

    PG_URL=postgresql://... poetry run python scripts/cli_analytics_bench/run_sessions_bench.py latency \
        --label branch --days 30 --runs 15
    PG_URL=... poetry run python scripts/cli_analytics_bench/run_sessions_bench.py explain --days 30
    PG_URL=... poetry run python scripts/cli_analytics_bench/run_sessions_bench.py connections \
        --concurrency 4 --days 30 --rounds 3 --hog cost

`latency` prints p50/p95 of `get_sessions` per request type plus the per-statement times of one request
(the reader's `_q` is timed, so the same script measures main and the branch: run it from each checkout).
`explain` reruns the statements of one request under EXPLAIN (ANALYZE, BUFFERS). `connections` runs K concurrent
requests (with a pool hog, a refresher stand-in and an ingest-like probe) and reports the acquire wait p99 per
request type, the pool's occupancy and the ingest 503s; the shipped request holds two pool connections. Nothing here
writes to the database.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import contextvars
import json
import os
import statistics
import time
from datetime import datetime, timedelta, timezone

from codemie.configs.config import Config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader, positional
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.repository.cli_analytics.scope import ViewerScope
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler

SCOPE = ViewerScope(viewer_email="admin@x.com", unrestricted=True, admin_projects=frozenset(), mode="exclude")
# Request types: default order, cost order, ctx_per_call order, search, framework filter (the slow path).
REQUESTS = {
    "default": {"sort_by": "start_time", "search": None, "framework": None},
    "cost": {"sort_by": "cost_usd", "search": None, "framework": None},
    "ctx_per_call": {"sort_by": "ctx_per_call", "search": None, "framework": None},
    "search": {"sort_by": "start_time", "search": "fix", "framework": None},
    "framework": {"sort_by": "start_time", "search": None, "framework": "superpowers"},
    "framework_fallback": {"sort_by": "cost_usd", "search": None, "framework": "Unknown"},
}


def make_filter(days: int) -> LocalAnalyticsFilter:
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    return LocalAnalyticsFilter(scope=SCOPE, start_dt=end - timedelta(days=days), end_dt=end)


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


class Recorder:
    """Times every statement and every connection acquire; shared by the readers of one run."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, float]] = []
        self.waits: list[tuple[str, float]] = []
        self.held = 0
        self.peak = 0
        self.request: contextvars.ContextVar[str] = contextvars.ContextVar("request", default="-")


def instrument(engine: AnalyticsPgEngine, rec: Recorder) -> None:
    original = engine.acquire

    @contextlib.asynccontextmanager
    async def acquire(timeout: float | None = None):
        started = time.perf_counter()
        async with contextlib.AsyncExitStack() as stack:
            conn = await stack.enter_async_context(original(timeout))
            rec.waits.append((rec.request.get(), time.perf_counter() - started))
            rec.held += 1
            rec.peak = max(rec.peak, rec.held)
            try:
                yield conn
            finally:
                rec.held -= 1

    engine.acquire = acquire  # type: ignore[method-assign]
    original_q = PostgresCliAnalyticsReader._q

    async def timed_q(self, sql, params):
        started = time.perf_counter()
        try:
            return await original_q(self, sql, params)
        finally:
            rec.statements.append((sql, time.perf_counter() - started))
            rec.last_params = params

    PostgresCliAnalyticsReader._q = timed_q  # type: ignore[method-assign]
    original_fetch = getattr(PostgresCliAnalyticsReader, "_fetch", None)

    if original_fetch is not None:  # the branch's framework page runs on one held connection, outside _q

        async def timed_fetch(self, conn, sql, params):
            started = time.perf_counter()
            try:
                return await original_fetch(self, conn, sql, params)
            finally:
                rec.statements.append((sql, time.perf_counter() - started))

        PostgresCliAnalyticsReader._fetch = timed_fetch  # type: ignore[method-assign]


async def open_engine(rec: Recorder) -> AnalyticsPgEngine:
    url = os.environ["PG_URL"]
    # BENCH_STATEMENT_TIMEOUT_MS raises the pool's client-side limit too (a plan is the point, not the 30 s limit); the
    # `explain` mode needs it because the cost sort's model list is run for real.
    overrides = (
        {"CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS": int(os.environ["BENCH_STATEMENT_TIMEOUT_MS"])}
        if "BENCH_STATEMENT_TIMEOUT_MS" in os.environ
        else {}
    )
    engine = AnalyticsPgEngine(AnalyticsPgSettings.from_config(Config(PG_URL=url, **overrides)))
    instrument(engine, rec)
    return engine


def handler_for(engine: AnalyticsPgEngine) -> LocalAnalyticsHandler:
    return LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine))


async def one_request(handler: LocalAnalyticsHandler, rec: Recorder, name: str, days: int) -> float:
    rec.request.set(name)
    started = time.perf_counter()
    await handler.get_sessions(make_filter(days), page=0, per_page=20, **REQUESTS[name])
    return time.perf_counter() - started


def disable_parallel(engine: AnalyticsPgEngine) -> None:
    """Session-level only: every connection the engine hands out plans without parallel workers (the container's
    /dev/shm is too small for parallel hash joins). Changes nothing stored in the database."""
    wrapped = engine.acquire

    @contextlib.asynccontextmanager
    async def serial(timeout: float | None = None):
        async with wrapped(timeout) as conn:
            await conn.execute("SET max_parallel_workers_per_gather = 0")
            yield conn

    engine.acquire = serial  # type: ignore[method-assign]


async def latency(args) -> dict:
    rec = Recorder()
    engine = await open_engine(rec)
    if args.no_parallel:
        disable_parallel(engine)
    handler = handler_for(engine)
    out: dict = {
        "label": args.label,
        "days": args.days,
        "runs": args.runs,
        "no_parallel": args.no_parallel,
        "requests": {},
    }
    for name in args.only or REQUESTS:
        rec.statements.clear()
        try:
            await one_request(handler, rec, name, args.days)  # warm the caches
            times = [await one_request(handler, rec, name, args.days) for _ in range(args.runs)]
            rec.statements.clear()
            await one_request(handler, rec, name, args.days)
        except Exception as exc:  # noqa: BLE001 - a request that fails is a result
            out["requests"][name] = {
                "error": f"{type(exc).__name__}: {exc}",
                "statements_ms": [[" ".join(sql.split())[:90], round(t * 1000, 1)] for sql, t in rec.statements],
            }
            continue
        out["requests"][name] = {
            "p50_ms": round(statistics.median(times) * 1000, 1),
            "p95_ms": round(percentile(times, 0.95) * 1000, 1),
            "statements_ms": [[" ".join(sql.split())[:90], round(t * 1000, 1)] for sql, t in rec.statements],
        }
    await engine.close()
    return out


class _PlannedRow(dict):
    """The stand-in result of a planned-only statement: no total, ids or flags, empty for any other column."""

    def __missing__(self, key: str):
        return {"total": 0, "session_ids": [], "flags": "[]"}.get(key, "")


async def explain(args) -> dict:
    rec = Recorder()
    engine = await open_engine(rec)
    wrapped = engine.acquire

    @contextlib.asynccontextmanager
    async def unbounded(timeout: float | None = None):
        async with wrapped(timeout) as conn:
            await conn.execute(
                "SET statement_timeout = 0; SET max_parallel_workers_per_gather = 0"
            )  # a plan is the point, not the 30 s limit
            yield conn

    engine.acquire = unbounded  # type: ignore[method-assign]
    handler = handler_for(engine)
    plans: dict = {}
    for name in args.only or REQUESTS:
        captured: list[tuple[str, dict]] = []
        original = PostgresCliAnalyticsReader._q

        async def spy(self, sql, params, _captured=captured, _original=original):
            _captured.append((sql, dict(params)))
            # the rates of the cost sort come from the real model list (`get_cost_models`)
            if "WHERE c.model_name IS NOT NULL GROUP BY c.model_name" in " ".join(sql.split()):
                return await _original(self, sql, params)
            return [_PlannedRow()]  # only planned below, not run: one empty row any reader of a page statement accepts

        original_fetch = PostgresCliAnalyticsReader._fetch

        async def spy_fetch(self, conn, sql, params, _captured=captured):
            # The framework page runs on one held connection: only its window statement (the SELECT of the temp
            # table, the names of every session) is planned here; the page statement needs that table to exist.
            if "CREATE TEMP TABLE" in sql:
                _captured.append((sql[sql.index("WITH") :], dict(params)))
            return []

        PostgresCliAnalyticsReader._q = spy  # type: ignore[method-assign]
        PostgresCliAnalyticsReader._fetch = spy_fetch  # type: ignore[method-assign]
        try:
            await handler.get_sessions(make_filter(args.days), page=0, per_page=20, **REQUESTS[name])
        finally:
            PostgresCliAnalyticsReader._q = original  # type: ignore[method-assign]
            PostgresCliAnalyticsReader._fetch = original_fetch  # type: ignore[method-assign]
        plans[name] = []
        for sql, params in captured:
            text, values = positional(sql, params)
            async with engine.acquire() as conn, conn.transaction(readonly=True):
                rows = await conn.fetch("EXPLAIN (ANALYZE, BUFFERS) " + text, *values, timeout=900)
            plans[name].append({"sql": " ".join(sql.split())[:160], "plan": [r[0] for r in rows]})
    await engine.close()
    return {"days": args.days, "plans": plans}


async def connections(args) -> dict:
    """K concurrent Sessions requests (default / search / ctx_per_call at --days) plus one `--hog` request (cost or
    framework) started first, a refresher stand-in holding one connection for 2 s out of every 4 s, and an
    ingest-like probe acquiring a connection every 100 ms with the ingest acquire timeout (a timeout is a 503)."""
    rec = Recorder()
    engine = await open_engine(rec)
    handler = handler_for(engine)
    mix = ["default", "search", "ctx_per_call"]
    errors = 0
    stop = asyncio.Event()
    ingest_waits: list[float] = []
    ingest_503 = 0
    occupancy: list[int] = []

    async def worker(index: int) -> None:
        nonlocal errors
        for round_no in range(args.rounds):
            try:
                await one_request(handler, rec, mix[(index + round_no) % len(mix)], args.days)
            except Exception:  # noqa: BLE001 - a bench counts failures
                errors += 1

    async def hog() -> None:
        nonlocal errors
        try:
            await one_request(handler, rec, args.hog, args.days)
        except Exception:  # noqa: BLE001
            errors += 1

    async def refresher() -> None:
        while not stop.is_set():
            async with engine.acquire() as conn:
                await conn.execute("SELECT pg_sleep(2)")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), 2)

    async def ingest() -> None:
        nonlocal ingest_503
        timeout = engine.settings.ingest_acquire_timeout_s
        while not stop.is_set():
            started = time.perf_counter()
            try:
                async with engine.acquire(timeout=timeout) as conn:
                    ingest_waits.append(time.perf_counter() - started)
                    await conn.fetchval("SELECT 1")
            except (TimeoutError, asyncio.TimeoutError):
                ingest_503 += 1
                ingest_waits.append(timeout)
            await asyncio.sleep(0.1)

    async def sample() -> None:
        while not stop.is_set():
            occupancy.append(rec.held)
            await asyncio.sleep(0.05)

    background = [asyncio.create_task(t()) for t in (refresher, ingest, sample)]
    hog_task = asyncio.create_task(hog())
    await asyncio.sleep(1)
    await asyncio.gather(*(worker(i) for i in range(args.concurrency)))
    await hog_task
    stop.set()
    await asyncio.gather(*background, return_exceptions=True)
    by_type: dict[str, list[float]] = {}
    for name, wait in rec.waits:
        by_type.setdefault(name, []).append(wait)
    await engine.close()
    return {
        "concurrency": args.concurrency,
        "days": args.days,
        "hog": args.hog,
        "pool_size": engine.settings.pool_size,
        "peak_connections_held": rec.peak,
        "occupancy_p95": percentile([float(o) for o in occupancy], 0.95) if occupancy else 0,
        "request_errors": errors,
        "ingest_probes": len(ingest_waits),
        "ingest_503": ingest_503,
        "ingest_acquire_wait_p99_ms": round(percentile(ingest_waits, 0.99) * 1000, 1) if ingest_waits else None,
        "acquire_wait_p99_ms": {k: round(percentile(v, 0.99) * 1000, 1) for k, v in by_type.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["latency", "explain", "connections"])
    parser.add_argument("--label", default="branch")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--runs", type=int, default=15)
    parser.add_argument("--only", nargs="*", choices=list(REQUESTS), help="request types to run (default: all)")
    parser.add_argument("--hog", choices=["cost", "framework"], default="cost")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--no-parallel", action="store_true", help="latency: plan without parallel workers")
    args = parser.parse_args()
    result = asyncio.run({"latency": latency, "explain": explain, "connections": connections}[args.mode](args))
    print(json.dumps(result, indent=2, default=str))  # noqa: T201 - the recorded artifact


if __name__ == "__main__":
    main()
