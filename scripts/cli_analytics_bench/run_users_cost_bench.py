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

"""Baseline recorder and comparator for /users, /user-charts and /cost (EPMCDME-17051). SCRATCH database only.

    PG_URL=postgresql://.../scratch poetry run python scripts/cli_analytics_bench/run_users_cost_bench.py baseline \
        --out scripts/cli_analytics_bench/results/17051-baseline --days 90 --runs 3
    PG_URL=... poetry run python scripts/cli_analytics_bench/run_users_cost_bench.py compare \
        --baseline scripts/cli_analytics_bench/results/17051-baseline \
        --out scripts/cli_analytics_bench/results/17051-new
    PG_URL=... poetry run python scripts/cli_analytics_bench/run_users_cost_bench.py compare \
        --baseline DIR --candidate DIR2 --out DIR3        # diff two recordings that already exist

`baseline` calls the handlers directly, as the routes do: `get_users(f, 0, 50)` (/users), `get_users(f, None, None)`
with `total_count` set to None (/user-charts) and `get_cost(f)` (/cost), each `--runs` times. It writes `users.json`,
`user_charts.json` and `cost.json` (`data` plus `metadata` with `unpriced`, `unpriced_models` and `cost_incomplete`),
`timings.json` (wall time per run, per-statement times of the first run) and `meta.json` (the pinned window), and
`zero_rounding.json` (sessions whose cost rounds to 0 while the Python cost is above 0). Record it on the commit
BEFORE a handler changes: it is the reference for every later comparison. It only reads.

`compare` records the same three outputs from the current code into `--out` over the baseline's pinned window (or takes
`--candidate`), then checks acceptance criteria 6-7 against `--baseline` and writes `compare.json` there: a dollar
figure (a key with cost, usd or spend) within $0.01; counts, membership and every other field exact (`0` equals `0.0`);
the order of the ranked lists exact, except rows whose costs differ by less than $0.000001; `unpriced`,
`unpriced_models` and `cost_incomplete` exact. Exit status 1 when anything differs.

`load` (Task 10, volume): the new handlers on one engine (the shipped pool) with the ingest stand-in
(`ingest_sim.py`), two background-job stand-ins (each holds a pool connection 2 s of every 4 s) and an ingest probe (a
pool acquire with the ingest timeout plus `SELECT 1` every 100 ms; a timeout is an ingest 503). Phase A is that
background alone (no dashboard), phase B adds `--k` Users tabs (`/users` + `/user-charts`) and `--k` Cost tabs per
round, `--rounds` rounds. Reports per request: wall time, status (200 / 504 / error), peak pool connections held,
per-statement times (criterion 14); ingest p99 of phase B minus phase A against `--ingest-bound-ms`; every request
against its baseline wall time (`--baseline`).

    PG_URL=... poetry run python scripts/cli_analytics_bench/run_users_cost_bench.py load --k 3 \
        --baseline scripts/cli_analytics_bench/results/17051-baseline-600k \
        --out scripts/cli_analytics_bench/results/17051-load-600k

`explain`: EXPLAIN (ANALYZE, BUFFERS) of the Cost money statement and the Users money-page statement (temp blocks,
`pg_stat_database.temp_files`, `temp_file_limit`, sequential scans of cost_daily / session_usage) and the token read in
the `IN (SELECT session_id FROM sel)` form against the form joined to `sel`. `lag`: the largest `asyncio.sleep(0.01)`
overshoot while one `/user-charts` request runs (handler plus the router's `_respond` body and ETag).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader, positional, window_params
from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer, model_rates_for
from codemie.service.analytics.handlers.session_money import token_counts

from run_sessions_bench import (  # the same folder: this script's own helpers
    SCOPE,
    Recorder,
    handler_for,
    open_engine,
    percentile,
)

ENDPOINTS = ("users", "user_charts", "cost")
DOLLAR_TOL = 0.01
ORDER_TOL = 1e-6
ROUND_DIGITS = 9
# The ranked lists (key field per list path): their order is checked with the near-tie allowance.
RANKED = {"data.rows": "developer_name", "data.cost_by_user": "developer_name", "data.cost_by_model": "model_name"}
MAX_FINDINGS = 200


def pinned_filter(days: int, end: datetime) -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(scope=SCOPE, start_dt=end - timedelta(days=days), end_dt=end)


def metadata_of(pricer: CatalogPricer) -> dict[str, Any]:
    """What the routes put in `metadata` for these requests (`_metadata` in the router)."""
    return {
        "unpriced": pricer.unpriced,
        "unpriced_models": pricer.unpriced,
        "cost_incomplete": bool(pricer.unpriced),
    }


async def call(handler: Any, name: str, f: LocalAnalyticsFilter) -> dict[str, Any]:
    if name == "users":
        data, pricer = await handler.get_users(f, 0, 50)
    elif name == "user_charts":
        data, pricer = await handler.get_users(f, None, None)
        data["total_count"] = None  # unpaginated by contract, as the route does
    else:
        data, pricer = await handler.get_cost(f)
    return {"data": data, "metadata": metadata_of(pricer)}


def dump(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=1, default=str, sort_keys=False) + "\n")


async def zero_rounding(handler: Any, f: LocalAnalyticsFilter) -> dict[str, Any]:
    """Sessions whose cost, summed from per-(session, model) values rounded to 9 digits, is 0 while the unrounded
    Python cost is above 0 (spec, Cost above zero): a count that depends on cost > 0 would differ for them."""
    rows = await handler._repo.get_session_model_costs(f, spend_only=True)  # noqa: SLF001 - a bench reads the reader
    pricer = CatalogPricer()
    python_cost: dict[str, float] = {}
    rounded_cost: dict[str, float] = {}
    for row in rows:
        sid, model = str(row.get("session_id")), str(row.get("model_name") or "")
        cost = pricer.price(model, token_counts(row))
        python_cost[sid] = python_cost.get(sid, 0.0) + cost
        rounded_cost[sid] = rounded_cost.get(sid, 0.0) + round(cost, ROUND_DIGITS)
    hits = sorted(sid for sid, c in python_cost.items() if c > 0 and rounded_cost[sid] == 0)
    return {
        "priced_session_model_rows": len(rows),
        "sessions": len(python_cost),
        "sessions_rounded_to_zero_but_python_above_zero": len(hits),
        "session_ids": hits[:100],
    }


async def record(out: Path, f: LocalAnalyticsFilter, runs: int, label: str) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    rec = Recorder()
    engine = await open_engine(rec)
    handler = handler_for(engine)
    timings: dict[str, Any] = {"label": label, "runs": runs, "endpoints": {}}
    try:
        for name in ENDPOINTS:
            wall: list[float] = []
            first: dict[str, Any] | None = None
            statements: list[list[Any]] = []
            for i in range(runs):
                rec.statements.clear()
                started = time.perf_counter()
                result = await call(handler, name, f)
                wall.append(time.perf_counter() - started)
                if i == 0:
                    first = result
                    statements = [[" ".join(sql.split())[:90], round(t * 1000, 1)] for sql, t in rec.statements]
            dump(out / f"{name}.json", first)
            timings["endpoints"][name] = {
                "wall_s": [round(t, 3) for t in wall],
                "min_s": round(min(wall), 3),
                "median_s": round(statistics.median(wall), 3),
                "max_s": round(max(wall), 3),
                "statements_ms_first_run": statements,
            }
            print(f"{name}: {timings['endpoints'][name]['wall_s']} s", flush=True)  # noqa: T201 - the artifact
            dump(out / "timings.json", timings)  # after each endpoint, so a killed run keeps what it measured
        dump(out / "zero_rounding.json", await zero_rounding(handler, f))
    finally:
        await engine.close()
    dump(out / "timings.json", timings)
    return timings


def meta_of(args: argparse.Namespace, end: datetime) -> dict[str, Any]:
    return {"days": args.days, "end_dt": end.isoformat(), "pg_target": os.environ["PG_URL"].rsplit("@", 1)[-1]}


async def baseline(args: argparse.Namespace) -> int:
    os.environ.setdefault("BENCH_STATEMENT_TIMEOUT_MS", "900000")  # the current handlers may run for minutes
    out = Path(args.out)
    end = datetime.fromisoformat(args.end) if args.end else make_end()
    out.mkdir(parents=True, exist_ok=True)
    dump(out / "meta.json", meta_of(args, end))
    await record(out, pinned_filter(args.days, end), args.runs, "baseline")
    print(f"baseline written to {out}")  # noqa: T201
    return 0


def make_end() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


# ── comparison ───────────────────────────────────────────────────────────────


def is_dollar(key: str) -> bool:
    return any(part in key for part in ("cost", "usd", "spend"))


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class Diff:
    def __init__(self) -> None:
        self.findings: list[str] = []
        self.tolerated: list[str] = []
        self.checked = 0

    def add(self, path: str, message: str) -> None:
        if len(self.findings) < MAX_FINDINGS:
            self.findings.append(f"{path}: {message}")

    def walk(self, b: Any, c: Any, path: str, key: str = "") -> None:
        self.checked += 1
        if isinstance(b, dict) and isinstance(c, dict):
            for missing in sorted(set(b) ^ set(c)):
                self.add(path, f"field {missing!r} only in {'baseline' if missing in b else 'candidate'}")
            for k in b.keys() & c.keys():
                self.walk(b[k], c[k], f"{path}.{k}" if path else k, k)
        elif isinstance(b, list) and isinstance(c, list):
            if path in RANKED and all(isinstance(r, dict) for r in b + c):
                self.ranked(b, c, path, RANKED[path])
            else:
                self.positional(b, c, path)
        elif is_number(b) and is_number(c):
            if is_dollar(key):
                if abs(b - c) > DOLLAR_TOL:
                    self.add(path, f"dollar {b!r} vs {c!r} (diff {abs(b - c):.9f} > {DOLLAR_TOL})")
            elif b != c:  # 0 == 0.0 is equal
                self.add(path, f"{b!r} vs {c!r}")
        elif b != c:
            self.add(path, f"{b!r} vs {c!r}")

    def positional(self, b: list, c: list, path: str) -> None:
        if len(b) != len(c):
            self.add(path, f"length {len(b)} vs {len(c)}")
        for i, (x, y) in enumerate(zip(b, c, strict=False)):
            self.walk(x, y, f"{path}[{i}]")

    def ranked(self, b: list[dict], c: list[dict], path: str, key_field: str) -> None:
        """Rows by identity; order exact except rows whose costs differ by < ORDER_TOL (also across a page edge)."""
        bk, ck = [r[key_field] for r in b], [r[key_field] for r in c]
        paged = path == "data.rows" and len(b) == 50 and len(c) == 50
        bcost = {r[key_field]: r.get("cost_usd", 0) for r in b}
        ccost = {r[key_field]: r.get("cost_usd", 0) for r in c}
        if len(b) != len(c):
            self.add(path, f"row count {len(b)} vs {len(c)}")
        for i, (x, y) in enumerate(zip(bk, ck, strict=False)):
            if x == y:
                continue
            near = abs(bcost[x] - ccost[y]) < ORDER_TOL and abs(bcost.get(y, bcost[x]) - bcost[x]) < ORDER_TOL
            if near:
                self.tolerated.append(f"{path}[{i}]: {x!r} vs {y!r} (costs within {ORDER_TOL})")
            else:
                self.add(f"{path}[{i}]", f"order {x!r} vs {y!r} (costs {bcost[x]!r} vs {ccost[y]!r})")
        for who, names, costs, last in (
            ("baseline", set(bk) - set(ck), bcost, min(bcost.values(), default=0)),
            ("candidate", set(ck) - set(bk), ccost, min(bcost.values(), default=0)),
        ):
            for name in sorted(names):
                if paged and abs(costs[name] - last) < ORDER_TOL:  # a near-tie across the page edge (D11)
                    self.tolerated.append(f"{path}: {name!r} only in {who}, tied with the page's last row")
                else:
                    self.add(path, f"row {name!r} only in {who}")
        cmap = {r[key_field]: r for r in c}
        for r in b:
            if r[key_field] in cmap:
                self.walk(r, cmap[r[key_field]], f"{path}[{r[key_field]}]")


def load(directory: Path, name: str) -> Any:
    return json.loads((directory / f"{name}.json").read_text())


async def compare(args: argparse.Namespace) -> int:
    os.environ.setdefault("BENCH_STATEMENT_TIMEOUT_MS", "900000")
    base, out = Path(args.baseline), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.candidate:
        cand = Path(args.candidate)
    else:
        meta = json.loads((base / "meta.json").read_text())
        await record(out, pinned_filter(meta["days"], datetime.fromisoformat(meta["end_dt"])), args.runs, "candidate")
        cand = out
    result: dict[str, Any] = {"baseline": str(base), "candidate": str(cand), "endpoints": {}, "equal": True}
    for name in ENDPOINTS:
        diff = Diff()
        diff.walk(load(base, name), load(cand, name), "")
        result["endpoints"][name] = {
            "equal": not diff.findings,
            "checked_nodes": diff.checked,
            "differences": diff.findings,
            "tolerated_near_ties": diff.tolerated[:MAX_FINDINGS],
            "tolerated_count": len(diff.tolerated),
        }
        result["equal"] &= not diff.findings
    zero = {}
    for label, d in (("baseline", base), ("candidate", cand)):
        path = d / "zero_rounding.json"
        zero[label] = json.loads(path.read_text()) if path.exists() else None
    result["zero_rounding"] = zero  # reported, never hidden
    dump(out / "compare.json", result)
    for name, r in result["endpoints"].items():
        print(  # noqa: T201
            f"{name}: {'all equal' if r['equal'] else str(len(r['differences'])) + ' difference(s)'}"
            f" ({r['checked_nodes']} nodes, {r['tolerated_count']} tolerated near-tie moves)"
        )
        for line in r["differences"][:20]:
            print(f"  {line}")  # noqa: T201
    for label, z in zero.items():
        if z:
            print(f"zero rounding ({label}): {z['sessions_rounded_to_zero_but_python_above_zero']} session(s)")  # noqa: T201
    print("RESULT:", "all-equal" if result["equal"] else "DIFFERENT")  # noqa: T201
    return 0 if result["equal"] else 1


# ── volume: k tabs with ingest and the background jobs (Task 10) ──────────────


def status_of(exc: BaseException) -> str:
    """What the route would answer: a budget or statement timeout is a 504, anything else an error."""
    name = type(exc).__name__
    return "504" if isinstance(exc, TimeoutError) or "QueryCanceled" in name or "Timeout" in name else f"error:{name}"


async def background_jobs(engine: Any, stop: asyncio.Event) -> list[asyncio.Task]:
    """Two stand-ins of the refresher and the maintenance job: each holds one pool connection for 2 s of every 4 s."""

    async def job() -> None:
        while not stop.is_set():
            async with engine.acquire() as conn:
                await conn.execute("SELECT pg_sleep(2)")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), 2)

    return [asyncio.create_task(job()) for _ in range(2)]


async def probe_ingest(engine: Any, stop: asyncio.Event, out: dict[str, Any]) -> None:
    """An ingest-like request every 100 ms: acquire with the ingest timeout, then `SELECT 1`. A timeout is a 503."""
    timeout = engine.settings.ingest_acquire_timeout_s
    while not stop.is_set():
        started = time.perf_counter()
        try:
            async with engine.acquire(timeout=timeout) as conn:
                await conn.fetchval("SELECT 1")
            out["latency"].append(time.perf_counter() - started)
        except (TimeoutError, asyncio.TimeoutError):
            out["503"] += 1
            out["latency"].append(timeout)
        await asyncio.sleep(0.1)


def probe_summary(probe: dict[str, Any]) -> dict[str, Any]:
    lat = probe["latency"]
    return {
        "probes": len(lat),
        "ingest_503": probe["503"],
        "p50_ms": round(percentile(lat, 0.5) * 1000, 1) if lat else None,
        "p99_ms": round(percentile(lat, 0.99) * 1000, 1) if lat else None,
        "max_ms": round(max(lat) * 1000, 1) if lat else None,
    }


async def one_dashboard(handler: Any, rec: Recorder, per_request: dict[int, list], kind: str, f: Any) -> dict[str, Any]:
    rec.request.set(kind)
    rec.seq += 1
    rid = rec.seq
    rec.request_id.set(rid)
    per_request[rid] = []
    started = time.perf_counter()
    status = "200"
    try:
        await call(handler, kind, f)
    except Exception as exc:  # noqa: BLE001 - a bench counts failures
        status = status_of(exc)
        print(f"  {kind} #{rid} failed: {type(exc).__name__}: {str(exc)[:200]}", flush=True)  # noqa: T201
    wall = time.perf_counter() - started
    return {
        "kind": kind,
        "id": rid,
        "status": status,
        "wall_s": round(wall, 3),
        "peak_connections": rec.peak_by_request.get(rid, 0),
        "statements_ms": [[" ".join(sql.split())[:90], round(t * 1000, 1)] for sql, t in per_request[rid]],
    }


async def load_phase(args: argparse.Namespace, f: Any, dashboards: bool) -> dict[str, Any]:
    """One phase on a fresh engine: ingest stand-in + background jobs + probe, with or without the dashboard tabs."""
    from ingest_sim import IngestSim

    rec = Recorder()
    engine = await open_engine(rec)
    handler = handler_for(engine)
    per_request: dict[int, list] = {}
    original_q = PostgresCliAnalyticsReader._q

    async def timed(self: Any, sql: str, params: dict) -> Any:
        started = time.perf_counter()
        try:
            return await original_q(self, sql, params)
        finally:
            per_request.setdefault(rec.request_id.get(), []).append((sql, time.perf_counter() - started))

    PostgresCliAnalyticsReader._q = timed  # type: ignore[method-assign]
    stop = asyncio.Event()
    probe: dict[str, Any] = {"latency": [], "503": 0}
    sim = IngestSim(os.environ["PG_URL"], args.ingest_rate, args.refresh_s)
    results: list[dict[str, Any]] = []
    occupancy: list[int] = []

    async def sample() -> None:
        while not stop.is_set():
            occupancy.append(rec.held)
            await asyncio.sleep(0.05)

    started = time.perf_counter()
    try:
        await sim.start()
        background = [
            *await background_jobs(engine, stop),
            asyncio.create_task(probe_ingest(engine, stop, probe)),
            asyncio.create_task(sample()),
        ]
        await asyncio.sleep(args.settle_s)  # the probe and the jobs run alone first
        if dashboards:
            for round_no in range(args.rounds):
                kinds = ["users", "user_charts"] * args.k + ["cost"] * args.k
                print(f"round {round_no + 1}: {len(kinds)} requests", flush=True)  # noqa: T201
                results += await asyncio.gather(*(one_dashboard(handler, rec, per_request, k, f) for k in kinds))
        else:
            await asyncio.sleep(args.idle_s)
        await asyncio.sleep(args.settle_s)
        stop.set()
        await asyncio.gather(*background, return_exceptions=True)
    finally:
        PostgresCliAnalyticsReader._q = original_q  # type: ignore[method-assign]
        ingest = await sim.finish()
        await engine.close()
    waits: dict[str, list[float]] = {}
    for name, wait in rec.waits:
        waits.setdefault(name, []).append(wait)
    return {
        "dashboards": dashboards,
        "duration_s": round(time.perf_counter() - started, 1),
        "pool_size": engine.settings.pool_size,
        "ingest_acquire_timeout_s": engine.settings.ingest_acquire_timeout_s,
        "probe": probe_summary(probe),
        "ingest_stand_in": ingest,
        "requests": results,
        "peak_connections_held": rec.peak,
        "occupancy_p95": percentile([float(o) for o in occupancy], 0.95) if occupancy else 0,
        "acquire_wait_max_s": {k: round(max(v), 3) for k, v in waits.items() if k in ENDPOINTS},
    }


async def load_test(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = Path(args.baseline)
    meta = json.loads((base / "meta.json").read_text())
    base_wall = {n: json.loads((base / "timings.json").read_text())["endpoints"][n]["wall_s"][0] for n in ENDPOINTS}
    f = pinned_filter(meta["days"], make_end())  # a live window: the stand-in writes now
    result: dict[str, Any] = {"k": args.k, "rounds": args.rounds, "days": meta["days"], "baseline_wall_s": base_wall}
    print("phase A: no dashboard", flush=True)  # noqa: T201
    result["no_dashboard"] = await load_phase(args, f, dashboards=False)
    dump(out / "load.json", result)
    print("phase B: dashboards", flush=True)  # noqa: T201
    result["with_dashboard"] = await load_phase(args, f, dashboards=True)
    a, b = result["no_dashboard"]["probe"], result["with_dashboard"]["probe"]
    reqs = result["with_dashboard"]["requests"]
    checks = {
        "ingest_no_503": a["ingest_503"] == 0 and b["ingest_503"] == 0,
        "no_dashboard_503_504": all(r["status"] == "200" for r in reqs),
        "every_request_not_slower_than_baseline": all(r["wall_s"] <= base_wall[r["kind"]] for r in reqs),
        "ingest_p99_delta_ms": round(b["p99_ms"] - a["p99_ms"], 1),
        "ingest_p99_within_bound": b["p99_ms"] - a["p99_ms"] <= args.ingest_bound_ms,
        "peak_connections_per_request": max((r["peak_connections"] for r in reqs), default=0),
        "peak_connections_per_request_is_1": all(r["peak_connections"] == 1 for r in reqs),
    }
    result["checks"] = checks
    dump(out / "load.json", result)
    print(json.dumps({"checks": checks, "probe_a": a, "probe_b": b}, indent=1))  # noqa: T201
    for r in reqs:
        print(f"{r['kind']:12s} #{r['id']:<3d} {r['status']:6s} {r['wall_s']:8.1f} s  peak={r['peak_connections']}")  # noqa: T201
    ok = all(v for k, v in checks.items() if isinstance(v, bool))
    print("RESULT:", "PASS" if ok else "FAIL")  # noqa: T201
    return 0 if ok else 1


# ── event-loop lag ───────────────────────────────────────────────────────────


async def lag(args: argparse.Namespace) -> int:
    """Largest overshoot of `asyncio.sleep(0.01)` while one /user-charts request runs: the handler, then what the route
    does after it (`_respond`: validation, `model_dump`, the JSON body and the ETag)."""
    import hashlib

    from codemie.rest_api.models.cli_analytics import LocalAnalyticsUsersResponse
    from codemie.rest_api.routers.cli_analytics import _metadata, _respond

    rec = Recorder()
    engine = await open_engine(rec)
    handler = handler_for(engine)
    f = pinned_filter(args.days, make_end())
    ticks: list[tuple[float, float]] = []  # (time since start, overshoot)
    stop = asyncio.Event()

    async def ticker(t0: float) -> None:
        while not stop.is_set():
            before = time.perf_counter()
            await asyncio.sleep(0.01)
            ticks.append((before - t0, time.perf_counter() - before - 0.01))

    marks: dict[str, float] = {}
    t0 = time.perf_counter()
    spans: list[tuple[float, float, int, str]] = []  # (start, end, rows, sql) of each reader statement
    original_q = PostgresCliAnalyticsReader._q

    async def spanned(self: Any, sql: str, params: dict) -> Any:
        started = time.perf_counter() - t0
        rows = await original_q(self, sql, params)
        spans.append((started, time.perf_counter() - t0, len(rows), " ".join(sql.split())[:60]))
        return rows

    PostgresCliAnalyticsReader._q = spanned  # type: ignore[method-assign]
    task = asyncio.create_task(ticker(t0))
    try:
        await asyncio.sleep(0.5)
        data, pricer = await handler.get_users(f, None, None)
        marks["handler_done"] = time.perf_counter() - t0
        data["total_count"] = None
        metadata = _metadata(0, None, pricer.unpriced, pricer.catalog_source, f.end_dt)
        payload = {"data": data, "metadata": metadata}

        def respond() -> tuple[Any, str]:  # the route's step, run in a worker thread as the route does
            response = _respond(payload, LocalAnalyticsUsersResponse)
            etag = (
                response.headers.get("ETag")
                or hashlib.sha256(json.dumps(json.loads(response.body), sort_keys=True).encode()).hexdigest()
            )  # a local config sets no ETag: compute it as the non-local route does
            return response, etag

        response, etag = await asyncio.to_thread(respond)
        marks["respond_done"] = time.perf_counter() - t0
        await asyncio.sleep(0.5)
    finally:
        PostgresCliAnalyticsReader._q = original_q  # type: ignore[method-assign]
        stop.set()
        await task
        await engine.close()
    during = [(t, o) for t, o in ticks if t >= 0.5]
    handler_ticks = [o for t, o in during if t < marks["handler_done"]]
    respond_ticks = [o for t, o in during if marks["handler_done"] <= t <= marks["respond_done"] + 0.05]
    top = sorted(during, key=lambda x: -x[1])[:5]
    result = {
        "rows": len(data["rows"]),
        "body_bytes": len(response.body),
        "etag": etag[:12],
        "handler_s": round(marks["handler_done"] - 0.5, 3),
        "respond_s": round(marks["respond_done"] - marks["handler_done"], 3),
        "max_overshoot_ms_total": round(max(o for _, o in during) * 1000, 1),
        "max_overshoot_ms_handler": round(max(handler_ticks, default=0) * 1000, 1),
        "max_overshoot_ms_respond": round(max(respond_ticks, default=0) * 1000, 1),
        "top5_ms_at_s": [[round(o * 1000, 1), round(t, 2)] for t, o in top],
        "statements": [[round(a, 1), round(b, 1), n, q] for a, b, n, q in spans],
        "blocked_spans_ms": [  # a tick that started at t and overshot by o blocked the loop from about t+0.01 to t+o
            [round(o * 1000, 1), round(t + 0.01, 2), round(t + 0.01 + o, 2)] for t, o in top
        ],
        "bound_ms": args.lag_bound_ms,
        "within_bound": max(o for _, o in during) * 1000 <= args.lag_bound_ms,
    }
    Path(args.out).mkdir(parents=True, exist_ok=True)
    dump(Path(args.out) / "lag.json", result)
    print(json.dumps(result, indent=1))  # noqa: T201
    return 0 if result["within_bound"] else 1


# ── EXPLAIN and the token read forms ─────────────────────────────────────────

_TEMP = re.compile(r"temp read=(\d+) written=(\d+)")
_SEQ = re.compile(r"Seq Scan on (\S+)")
_STATS = "SELECT temp_files, temp_bytes FROM pg_stat_database WHERE datname = current_database()"


class _Captured(Exception):
    pass


async def capture(reader: PostgresCliAnalyticsReader, coro_factory: Any) -> tuple[str, dict]:
    """The SQL and parameters a reader method would run, without running it."""
    seen: list[tuple[str, dict]] = []

    async def spy(self: Any, sql: str, params: dict) -> Any:
        seen.append((sql, dict(params)))
        raise _Captured

    async def spy_fetch(self: Any, conn: Any, sql: str, params: dict) -> Any:
        # The money statements run on one held connection (`_money_rows`) and, with the stored model groups on, bind
        # `stale` (S) from a mode read first; the spy keeps the statement and its parameters.
        seen.append((sql, dict(params)))
        raise _Captured

    original, original_fetch = PostgresCliAnalyticsReader._q, PostgresCliAnalyticsReader._fetch
    PostgresCliAnalyticsReader._q = spy  # type: ignore[method-assign]
    PostgresCliAnalyticsReader._fetch = spy_fetch  # type: ignore[method-assign]
    try:
        with contextlib.suppress(_Captured):
            await coro_factory()
    finally:
        PostgresCliAnalyticsReader._q = original  # type: ignore[method-assign]
        PostgresCliAnalyticsReader._fetch = original_fetch  # type: ignore[method-assign]
    return seen[0]


def join_form(sql: str) -> str:
    """The token read with `sel` joined instead of `IN (SELECT session_id FROM sel)` (sel's session_id is unique)."""
    a = "FROM cost_daily c WHERE c.session_id IN (SELECT session_id FROM sel)"
    b = (
        "FROM session_usage u WHERE u.attrs->>'token_source' = 'subagent_usage' "
        "AND u.session_id IN (SELECT session_id FROM sel)"
    )
    assert a in sql and b in sql, "token read text changed"
    return sql.replace(a, "FROM cost_daily c JOIN sel x ON x.session_id = c.session_id WHERE true").replace(
        b,
        "FROM session_usage u JOIN sel x ON x.session_id = u.session_id "
        "WHERE u.attrs->>'token_source' = 'subagent_usage'",
    )


async def explain(args: argparse.Namespace) -> int:
    from codemie.repository.cli_analytics.postgres.cost_key_sql import model_token_groups

    rec = Recorder()
    engine = await open_engine(rec)
    reader = PostgresCliAnalyticsReader(engine)
    f = pinned_filter(args.days, make_end())
    out: dict[str, Any] = {"days": args.days, "statement_timeout_ms_shipped": engine.settings.statement_timeout_ms}
    try:
        rates = [model_rates_for(m) for m in await reader.get_cost_models(f)]
        # The money statements as the requests run them: on one held connection with the window's `sel` in a temp table.
        held = contextlib.AsyncExitStack()
        repo = await held.enter_async_context(reader.bounded())
        started = time.perf_counter()
        await repo.materialize_sel(f)
        out["sel_materialize_s"] = round(time.perf_counter() - started, 3)
        print(f"sel temp table: {out['sel_materialize_s']} s", flush=True)  # noqa: T201
        cost_sql = await capture(repo, lambda: repo.get_cost_money(f, rates))
        users_sql = await capture(repo, lambda: repo.get_users_money_page(f, rates, 50, 0))
        sel_cte = reader._sessions_cte(f, with_developer=False)  # noqa: SLF001 - a bench reads the reader
        token_in = (
            f"WITH {sel_cte} SELECT count(*), sum(g.input_tokens) FROM {model_token_groups(normalized=False)}",
            window_params(f),
        )
        token_join = (join_form(token_in[0]), token_in[1])
        async with engine.acquire() as conn:
            out["temp_file_limit"] = await conn.fetchval("SHOW temp_file_limit")
            out["work_mem"] = await conn.fetchval("SHOW work_mem")
            out["max_parallel_workers_per_gather"] = await conn.fetchval("SHOW max_parallel_workers_per_gather")
            stats0 = dict(await conn.fetchrow(_STATS))
        out["pg_stat_database_before"] = stats0
        statements = {"cost_money": cost_sql, "users_money_page": users_sql}
        for name, (sql, params) in statements.items():
            text, values = positional(sql, params)
            async with repo._engine.acquire() as conn, conn.transaction(readonly=True):  # noqa: SLF001
                await conn.execute(f"SET LOCAL statement_timeout = {int(args.timeout_s) * 1000}")
                if args.work_mem_mb:
                    await conn.execute(f"SET LOCAL work_mem = '{int(args.work_mem_mb)}MB'")
                rows = await conn.fetch("EXPLAIN (ANALYZE, BUFFERS) " + text, *values, timeout=args.timeout_s + 60)
            plan = [r[0] for r in rows]
            exec_ms = next((float(x.split()[2]) for x in plan if x.startswith("Execution Time")), None)
            out[name] = {
                "execution_ms": exec_ms,
                "over_30s": exec_ms is not None and exec_ms > engine.settings.statement_timeout_ms,
                "seq_scans": sorted(set(_SEQ.findall("\n".join(plan)))),
                "temp_blocks_read_written": [list(map(int, m)) for m in _TEMP.findall("\n".join(plan))],
                "plan": plan,
            }
            print(f"{name}: {exec_ms} ms, seq scans {out[name]['seq_scans']}", flush=True)  # noqa: T201
        await held.aclose()
        async with engine.acquire() as conn:
            out["pg_stat_database_after"] = dict(await conn.fetchrow(_STATS))
        forms = {}
        for label, (sql, params) in (("in_sel", token_in), ("join_sel", token_join)):
            text, values = positional(sql, params)
            times = []
            for _ in range(args.token_runs):
                async with engine.acquire() as conn, conn.transaction(readonly=True):
                    await conn.execute(f"SET LOCAL statement_timeout = {int(args.timeout_s) * 1000}")
                    started = time.perf_counter()
                    row = await conn.fetchrow(text, *values, timeout=args.timeout_s + 60)
                    times.append(time.perf_counter() - started)
            forms[label] = {"wall_s": [round(t, 3) for t in times], "rows_sum": [int(row[0]), int(row[1] or 0)]}
        forms["join_faster"] = min(forms["join_sel"]["wall_s"]) < min(forms["in_sel"]["wall_s"])
        out["token_read"] = forms
        print(json.dumps(forms), flush=True)  # noqa: T201
    finally:
        await engine.close()
    Path(args.out).mkdir(parents=True, exist_ok=True)
    dump(Path(args.out) / "explain.json", out)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    b = sub.add_parser("baseline", help="record /users, /user-charts and /cost from the current handlers")
    b.add_argument("--out", required=True)
    b.add_argument("--days", type=int, default=90)
    b.add_argument("--runs", type=int, default=3)
    b.add_argument("--end", help="ISO window end (UTC, naive); default now")
    c = sub.add_parser("compare", help="criteria 6-7: the current code (or --candidate) against a baseline")
    c.add_argument("--baseline", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--candidate", help="an existing recording to compare instead of running the handlers")
    c.add_argument("--runs", type=int, default=1)
    ld = sub.add_parser("load", help="k Users tabs and k Cost tabs with ingest and the background jobs (volume)")
    ld.add_argument("--k", type=int, default=3)
    ld.add_argument("--rounds", type=int, default=1)
    ld.add_argument("--baseline", required=True, help="the baseline dir: meta.json (days) and timings.json")
    ld.add_argument("--out", required=True)
    ld.add_argument("--ingest-rate", type=float, default=50, help="new sessions per second of the ingest stand-in")
    ld.add_argument("--refresh-s", type=float, default=30)
    ld.add_argument("--idle-s", type=float, default=180, help="phase A (no dashboard) length")
    ld.add_argument("--settle-s", type=float, default=10)
    ld.add_argument("--ingest-bound-ms", type=float, default=250)
    ex = sub.add_parser("explain", help="EXPLAIN (ANALYZE, BUFFERS) of the money statements and the token read forms")
    ex.add_argument("--out", required=True)
    ex.add_argument("--days", type=int, default=90)
    ex.add_argument("--timeout-s", type=int, default=900)
    ex.add_argument("--token-runs", type=int, default=2)
    ex.add_argument("--work-mem-mb", type=int, default=0, help="SET LOCAL work_mem for the two money statements")
    lg = sub.add_parser("lag", help="event-loop lag during one /user-charts")
    lg.add_argument("--out", required=True)
    lg.add_argument("--days", type=int, default=90)
    lg.add_argument("--lag-bound-ms", type=float, default=200)
    args = parser.parse_args()
    if "PG_URL" not in os.environ:
        sys.exit("PG_URL (a SCRATCH database) is required")
    code = asyncio.run(
        {"baseline": baseline, "compare": compare, "load": load_test, "explain": explain, "lag": lag}[args.mode](args)
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
