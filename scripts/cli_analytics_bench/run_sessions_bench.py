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

"""Benchmark of the CLI Analytics Sessions list (EPMCDME-16427). Run it against a SCRATCH database only.

    PG_URL=postgresql://... poetry run python scripts/cli_analytics_bench/run_sessions_bench.py latency \
        --label branch --days 30 --runs 15
    PG_URL=... poetry run python scripts/cli_analytics_bench/run_sessions_bench.py explain --days 30
    PG_URL=... poetry run python scripts/cli_analytics_bench/run_sessions_bench.py connections \
        --concurrency 4 --days 30 --rounds 3 --hog cost

`latency` accepts `--stored on|off` (sets the kill switch of the scratch database), `--cold` (one run, no warm-up: the
caller restarts the server first), `--ingest-rate N` (a concurrent ingest and refresher stand-in, see `ingest_sim.py`:
reports the share of requests with a non-empty S, |S| and the cap-fallback rate) and prints p50/p95 of `get_sessions`
per request type plus the per-statement times of one request
(the reader's `_q` is timed, so the same script measures main and the branch: run it from each checkout).
`explain` reruns the statements of one request under EXPLAIN (ANALYZE, BUFFERS). `connections` runs K concurrent
requests (with a pool hog, a refresher stand-in and an ingest-like probe) and reports the acquire wait p99 per
request type, the pool's occupancy and the ingest 503s; the shipped request holds two pool connections. Only
`--stored`, `--ingest-rate` and `parity` write (the control row, the stand-in's own sessions) and only to the scratch
database.
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
from codemie.repository.cli_analytics.postgres.rates_version import RatesVersionProvider
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader, positional
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.repository.cli_analytics.scope import ViewerScope
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from codemie.service.analytics.handlers.coding_agent_pricing import catalog_rate_fingerprint

SCOPE = ViewerScope(viewer_email="admin@x.com", unrestricted=True, admin_projects=frozenset(), mode="exclude")
# Request types: default order, cost order, ctx_per_call order, search, framework filter (the slow path).
REQUESTS = {
    "default": {"sort_by": "start_time", "search": None, "framework": None},
    "cost": {"sort_by": "cost_usd", "search": None, "framework": None},
    "ctx_per_call": {"sort_by": "ctx_per_call", "search": None, "framework": None},
    "search": {"sort_by": "start_time", "search": "fix", "framework": None},
    "framework": {"sort_by": "start_time", "search": None, "framework": "Superpowers"},
    "framework_ctx": {"sort_by": "ctx_per_call", "search": None, "framework": "Superpowers"},
    "framework_cost": {"sort_by": "cost_usd", "search": None, "framework": "Superpowers"},
    "framework_fallback": {"sort_by": "cost_usd", "search": None, "framework": "Pure chat"},
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
        self.modes: list[tuple[str, int, bool, str | None, int]] = []  # (request, id, stored, reason, |S|)
        self.request: contextvars.ContextVar[str] = contextvars.ContextVar("request", default="-")
        self.request_id: contextvars.ContextVar[int] = contextvars.ContextVar("request_id", default=0)
        self.seq = 0
        self.held_by: dict[int, int] = {}
        self.peak_by_request: dict[int, int] = {}


def instrument(engine: AnalyticsPgEngine, rec: Recorder) -> None:
    original = engine.acquire

    @contextlib.asynccontextmanager
    async def acquire(timeout: float | None = None):
        started = time.perf_counter()
        async with contextlib.AsyncExitStack() as stack:
            conn = await stack.enter_async_context(original(timeout))
            rec.waits.append((rec.request.get(), time.perf_counter() - started))
            rid = rec.request_id.get()
            rec.held += 1
            rec.peak = max(rec.peak, rec.held)
            if rid:
                rec.held_by[rid] = rec.held_by.get(rid, 0) + 1
                rec.peak_by_request[rid] = max(rec.peak_by_request.get(rid, 0), rec.held_by[rid])
            try:
                yield conn
            finally:
                rec.held -= 1
                if rid:
                    rec.held_by[rid] -= 1

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
    original_mode = PostgresCliAnalyticsReader._stored_mode

    async def recorded_mode(self, *args, **kwargs):
        mode = await original_mode(self, *args, **kwargs)
        rec.modes.append((rec.request.get(), rec.request_id.get(), mode.stored, mode.reason, len(mode.stale)))
        return mode

    PostgresCliAnalyticsReader._stored_mode = recorded_mode  # type: ignore[method-assign]
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
    reader = PostgresCliAnalyticsReader(engine, s_cap=int(os.environ.get("BENCH_S_CAP", "5000")))
    reader.set_rates(RatesVersionProvider(catalog_rate_fingerprint).current)  # as start_cli_analytics_runtime does
    return LocalAnalyticsHandler(reader)


async def set_stored(engine: AnalyticsPgEngine, state: str) -> None:
    """The kill switch of the scratch database: `on`, `off` or `keep` (leave it as it is)."""
    if state != "keep":
        async with engine.acquire() as conn:
            await conn.execute("UPDATE session_sort_keys_control SET enabled = $1", state == "on")


async def one_request(handler: LocalAnalyticsHandler, rec: Recorder, name: str, days: int) -> float:
    rec.request.set(name)
    rec.seq += 1
    rec.request_id.set(rec.seq)
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


def stored_summary(rec: Recorder, name: str) -> dict:
    """What the stored path did for the requests of `name`: how many were served stored, how many merged a non-empty S,
    the |S| seen and the share that fell back to today's statements (and why)."""
    by_request: dict[int, list[tuple[bool, str | None, int]]] = {}
    for request, rid, stored, reason, stale in rec.modes:
        if request == name:
            by_request.setdefault(rid, []).append((stored, reason, stale))
    n = len(by_request)
    if not n:
        return {"requests": 0}
    sizes = [max(st for _, _, st in modes) for modes in by_request.values()]
    non_empty = [x for x in sizes if x]
    reasons: dict[str, int] = {}
    for modes in by_request.values():
        for stored, reason, _ in modes:
            if not stored:
                reasons[reason or "?"] = reasons.get(reason or "?", 0) + 1
    over = sum(1 for modes in by_request.values() if any(r == "s_over_cap" for _, r, _ in modes))
    return {
        "requests": n,
        "share_non_empty_S": round(len(non_empty) / n, 3),
        "S_p50": statistics.median(non_empty) if non_empty else 0,
        "S_max": max(sizes),
        "cap_fallback_rate": round(over / n, 3),
        "fallback_reasons": reasons,
    }


async def latency(args) -> dict:
    rec = Recorder()
    engine = await open_engine(rec)
    if args.no_parallel:
        disable_parallel(engine)
    handler = handler_for(engine)
    await set_stored(engine, args.stored)
    sim = None
    if args.ingest_rate:
        from ingest_sim import IngestSim

        sim = IngestSim(os.environ["PG_URL"], args.ingest_rate, args.refresh_s, step=not args.no_step)
        await sim.start()
    out: dict = {
        "label": args.label,
        "days": args.days,
        "runs": args.runs,
        "stored": args.stored,
        "cold": args.cold,
        "ingest_rate": args.ingest_rate,
        "no_parallel": args.no_parallel,
        "requests": {},
    }
    for name in args.only or REQUESTS:
        rec.statements.clear()
        rec.modes.clear()
        try:
            if args.cold:  # the first request after a server restart; the caller restarts it
                times = [await one_request(handler, rec, name, args.days)]
            else:
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
            "mean_ms": round(statistics.fmean(times) * 1000, 1),
            "min_ms": round(min(times) * 1000, 1),
            "times_ms": [round(t * 1000, 1) for t in times],
            "p50_ms": round(statistics.median(times) * 1000, 1),
            "p95_ms": round(percentile(times, 0.95) * 1000, 1),
            "max_ms": round(max(times) * 1000, 1),
            "n": len(times),
            "stored_path": stored_summary(rec, name),
            "statements_ms": [[" ".join(sql.split())[:90], round(t * 1000, 1)] for sql, t in rec.statements],
        }
    if sim is not None:
        out["ingest"] = await sim.finish()
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


async def parity(args) -> dict:
    """Each request type with the stored path on and off over the same data: ids, order, total and money must match
    (the quiescent database; the stand-in must not run)."""
    rec = Recorder()
    engine = await open_engine(rec)
    handler = handler_for(engine)
    out: dict = {"days": args.days, "requests": {}}
    for name in args.only or REQUESTS:
        results = {}
        for state in ("on", "off"):
            await set_stored(engine, state)
            body, _pricer, _as_of = await handler.get_sessions(
                make_filter(args.days), page=0, per_page=20, **REQUESTS[name]
            )
            results[state] = body
        on, off = results["on"], results["off"]
        out["requests"][name] = {
            "same_ids_order": [s["trace_id"] for s in on["sessions"]] == [s["trace_id"] for s in off["sessions"]],
            "same_total": on["total"] == off["total"],
            "same_rows": on["sessions"] == off["sessions"],
            "total": on["total"],
        }
    await set_stored(engine, "on")
    await engine.close()
    return out


async def connections(args) -> dict:
    """K concurrent Sessions requests (default / search / ctx_per_call at --days) plus one `--hog` request (cost or
    framework) started first, a refresher stand-in holding one connection for 2 s out of every 4 s, and an
    ingest-like probe acquiring a connection every 100 ms with the ingest acquire timeout (a timeout is a 503)."""
    rec = Recorder()
    engine = await open_engine(rec)
    handler = handler_for(engine)
    await set_stored(engine, args.stored)
    sim = None
    if args.ingest_rate:
        from ingest_sim import IngestSim

        sim = IngestSim(os.environ["PG_URL"], args.ingest_rate, args.refresh_s, step=not args.no_step)
        await sim.start()
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
    ingest_report = await sim.finish() if sim is not None else None
    return {
        "stored": args.stored,
        "ingest_stand_in": ingest_report,
        "max_connections_held_by_one_request": max(rec.peak_by_request.values(), default=0),
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
    parser.add_argument("mode", choices=["latency", "explain", "connections", "parity"])
    parser.add_argument("--label", default="branch")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--runs", type=int, default=15)
    parser.add_argument("--only", nargs="*", choices=list(REQUESTS), help="request types to run (default: all)")
    parser.add_argument("--hog", choices=["cost", "framework"], default="cost")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--no-parallel", action="store_true", help="latency: plan without parallel workers")
    parser.add_argument("--stored", choices=["on", "off", "keep"], default="keep", help="the kill switch (scratch db)")
    parser.add_argument("--cold", action="store_true", help="latency: one run per request type, no warm-up")
    parser.add_argument("--ingest-rate", type=float, default=0, help="latency: new sessions per second (stand-in)")
    parser.add_argument("--refresh-s", type=float, default=30, help="latency: refresher stand-in cycle in seconds")
    parser.add_argument(
        "--no-step", action="store_true", help="latency: the stand-in refresher skips the sort-key step"
    )
    args = parser.parse_args()
    result = asyncio.run(
        {"latency": latency, "explain": explain, "connections": connections, "parity": parity}[args.mode](args)
    )
    print(json.dumps(result, indent=2, default=str))  # noqa: T201 - the recorded artifact


if __name__ == "__main__":
    main()
