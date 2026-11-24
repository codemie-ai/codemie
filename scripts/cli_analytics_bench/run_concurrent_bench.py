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

"""The dashboard under concurrent users, with telemetry ingestion beside it (EPMCDME-17301). SCRATCH database only.

    PG_URL=postgresql://.../cli_analytics_scratch poetry run python \
        scripts/cli_analytics_bench/run_concurrent_bench.py \
        [--days 90] [--users 1,4,8] [--iterations 2] [--cache on|off] [--tabs Overview,Users,...]

What it imitates (one process, one event loop, one pool, as a pod; no HTTP and no browser):
  users   N users open the same tab at the same moment (the QA UI test's lock-step), then the next tab, for every
          tab of AI Attribution. A tab is its requests sent together, as the UI sends them; its time is the slowest.
  ingest  a probe takes a pool connection the way an ingest transaction does (`acquire_ingest`, with the 1 s timeout
          of CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS) every `--probe-ms`, and counts a failed acquire as the 503
          it would be.
  job     the rollup job confirms today's row every `--job-interval-s`, on the same pool, as the app's job does.
  window  `--days 90` is what `time_period=last_year` resolves to (the router clamps it to the 90 days of raw
          retention).

What it is not: the QA Preview run. One laptop, synthetic data, no network, and the client is this process. With few
samples the p95 is the maximum (the result says how many samples there are). The time is reported against the
2,000 ms target, not asserted. The database must be a scratch one: the job writes today's rollup row. `--cache off`
turns the answer caches off, which is what N distinct users with different filters would get; `on` shares an answer
between identical requests of one tab step (the cache is emptied before every step).
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import math
import os
import time
from collections import Counter, defaultdict
from unittest.mock import patch

from run_ranking_bench import (
    AnalyticsPgEngine,
    AnalyticsPgSettings,
    Config,
    LocalAnalyticsHandler,
    PostgresCliAnalyticsReader,
    RatesVersionProvider,
    RollupWatch,
    build_days,
    catalog_rate_fingerprint,
    check_scratch,
    config,
    progress,
    reset_dashboard_cache,
    window,
)
from codemie.service.analytics.dashboard_cache import cached_dashboard

TARGET_S = 2.0
API_BILLED = "codemie"  # the subscription type of "API spend" (the Overview's second request)


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(q / 100 * len(ordered)) - 1))]


def repositories_requests(handler: LocalAnalyticsHandler, fresh, projects) -> dict:
    """The Repositories tab's requests; opening a repository row (its branches) only where the code has that request."""

    async def expand_branches():
        page, _ = await handler.get_repositories(fresh(), 0, 1, False, None, True)
        named = [row["repository"] for row in page.get("rows", []) if row.get("repository")]
        if named:
            await handler.get_branches(fresh(), named[0], 0, 10)

    requests = {"projects?per_page=10": projects}
    if hasattr(handler, "get_branches"):
        requests["branches of one repository (expand)"] = expand_branches
    return requests


def tabs(handler: LocalAnalyticsHandler, days: int) -> dict[str, dict]:
    """The requests of each tab, as the UI sends them (named by endpoint). Each has its own window, ending now."""

    def fresh():
        return window(days)[1]

    def api_spend():
        f = fresh()
        f.subscription_types = [API_BILLED]
        return handler.get_overview(f, include_class_lists=False, include_buckets=False)

    def cost():  # the router answers /cost through the answer cache (identical requests share one computation)
        f = fresh()
        # The UI sends include_class_lists=false and include_buckets=false (they keep /cost on the rollup).
        return cached_dashboard(
            "cost", f, lambda: handler.get_cost(f, include_class_lists=False, include_buckets=False), False, False
        )

    def users(page: int, per_page: int, chart: bool):  # /users and /user-charts likewise
        f = fresh()
        return cached_dashboard(
            "users",
            f,
            lambda: handler.get_users(
                f, page, per_page, include_chart=chart, include_daily_activity=False, include_avg_duration=False
            ),
            page,
            per_page,
            chart,
            False,
            False,
        )

    # What the Repositories and Efficiency tabs send depends on the code under test: the drill-down contract
    # (totals-only /projects, /repositories with the unattributed group last) and the lean Efficiency flags; older
    # code gets the request the UI of that code sent.
    drilldown = "include_repositories" in inspect.signature(handler.get_projects).parameters
    lean_efficiency = "include_class_lists" in inspect.signature(handler.get_efficiency).parameters

    async def projects():
        if drilldown:
            return await handler.get_projects(fresh(), 0, 10, None, include_repositories=False)
        try:  # the Repositories tab leaves the branches out and loads them on expand
            return await handler.get_projects(fresh(), 0, 10, None, False)
        except TypeError:  # code from before `include_branches`: it sent the branches in the page
            return await handler.get_projects(fresh(), 0, 10, None)

    def repositories():
        if drilldown:  # the page of 10 with the repository-less group last, outside the page
            return handler.get_repositories(fresh(), 0, 10, False, None, True)
        return handler.get_repositories(fresh(), 0, 10, False, None)

    lean_tools = "include_class_lists" in inspect.signature(handler.get_tools).parameters

    def tools():  # the Tools tab reads no class list
        if lean_tools:
            return handler.get_tools(fresh(), include_class_lists=False)
        return handler.get_tools(fresh())

    def efficiency():
        if lean_efficiency:
            return handler.get_efficiency(fresh(), include_class_lists=False, include_buckets=False)
        return handler.get_efficiency(fresh())

    # The UI asks /overview for the user count (`include_total_users`); code from before it has the Users card read
    # `/users?per_page=1` instead.
    counts_users = "include_total_users" in inspect.signature(handler.get_overview).parameters
    overview_flags = {"include_total_users": True} if counts_users else {}
    overview_requests = {
        # The UI sends include_class_lists=false and include_buckets=false: they keep /overview on the rollup.
        "overview": lambda: handler.get_overview(
            fresh(), include_class_lists=False, include_buckets=False, **overview_flags
        ),
        "repositories?per_page=10": repositories,
        "overview (api spend)": api_spend,
    }
    if not counts_users:
        overview_requests["users?per_page=1"] = lambda: users(0, 1, False)

    repositories_tab = repositories_requests(handler, fresh, projects)

    return {
        "Overview": overview_requests,
        "Users": {"users?include_chart": lambda: users(0, 10, True)},
        "Repositories": repositories_tab,
        "Tools & Models": {"tools": tools},
        "Activity": {"activity": lambda: handler.get_activity(fresh())},
        "Efficiency": {
            "efficiency": efficiency,
            "sessions sort=ctx_per_call": lambda: handler.get_sessions(fresh(), 0, 10, "ctx_per_call", None),
        },
        "Cost": {
            "cost": cost,
            "sessions sort=cost_usd": lambda: handler.get_sessions(fresh(), 0, 10, "cost_usd", None),
        },
        "Sessions": {"sessions?per_page=20": lambda: handler.get_sessions(fresh(), 0, 20, "start_time", None)},
    }


async def timed(call) -> tuple[float, str | None]:
    started = time.perf_counter()
    try:
        await call()
        return time.perf_counter() - started, None
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - every failure is a result of the run, not a reason to stop it
        return time.perf_counter() - started, type(exc).__name__


class IngestProbe:
    """Takes a pool connection as an ingest transaction does, every `interval_s`, for as long as it runs."""

    def __init__(self, engine, interval_s: float, timeout_s: float, hold_s: float = 0.01) -> None:
        self.engine, self.interval_s, self.timeout_s, self.hold_s = engine, interval_s, timeout_s, hold_s
        self.waits: list[float] = []
        self.refused = 0
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def _one(self) -> None:
        started = time.monotonic()
        try:
            # Code from before the ingest reserve (main) has one acquire for everything, with the timeout.
            acquire = getattr(self.engine, "acquire_ingest", None) or (lambda t: self.engine.acquire(timeout=t))
            async with acquire(self.timeout_s) as conn:
                self.waits.append(time.monotonic() - started)
                await conn.fetchval("SELECT 1")
                await asyncio.sleep(self.hold_s)
        except TimeoutError:
            self.refused += 1  # the ingest route answers 503 and the plugin retries

    async def _run(self) -> None:
        tasks: set[asyncio.Task] = set()
        while not self._stop.is_set():
            task = asyncio.create_task(self._one())
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            try:
                await asyncio.wait_for(self._stop.wait(), self.interval_s)
            except TimeoutError:
                continue
        await asyncio.gather(*tasks, return_exceptions=True)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> dict:
        self._stop.set()
        if self._task:
            await self._task
        waits = self.waits or [0.0]
        return {
            "attempts": len(self.waits) + self.refused,
            "refused_503": self.refused,
            "acquire_wait_p50_s": round(pct(waits, 50), 4),
            "acquire_wait_p99_s": round(pct(waits, 99), 4),
            "acquire_wait_max_s": round(max(waits), 4),
            "timeout_s": self.timeout_s,
        }


async def job_loop(  # noqa: PLR0913 - the loop's settings of one bench run, passed once
    engine, reader, today, version: str, interval_s: float, stop: asyncio.Event, log: dict
) -> None:
    """The rollup job's work for today's row, on the same pool, every `interval_s`."""
    while not stop.is_set():
        started = time.perf_counter()
        try:
            await build_days(engine, reader, today, version, "job")
            log["runs"] += 1
            log["seconds"].append(round(time.perf_counter() - started, 2))
        except Exception as exc:  # noqa: BLE001
            log["failures"][type(exc).__name__] += 1
        try:
            await asyncio.wait_for(stop.wait(), interval_s)
        except TimeoutError:
            continue


async def run_step(n: int, calls: dict, timeout_s: float) -> tuple[list[float], dict[str, list[float]], Counter]:
    """N users send the tab's requests at once; returns each user's tab time (the slowest of its requests)."""

    async def user() -> list[tuple[str, float, str | None]]:
        results = await asyncio.gather(*(timed(call) for call in calls.values()))
        return [(name, seconds, error) for name, (seconds, error) in zip(calls, results, strict=True)]

    tasks = [asyncio.create_task(user()) for _ in range(n)]
    done, pending = await asyncio.wait(tasks, timeout=timeout_s)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    tab_times, per_call, errors = [], defaultdict(list), Counter()
    for task in done:
        rows = task.result()
        tab_times.append(max(seconds for _, seconds, _ in rows))
        for name, seconds, error in rows:
            per_call[name].append(seconds)
            if error:
                errors[f"{name}: {error}"] += 1
    if pending:
        errors[f"step timeout {timeout_s:.0f}s"] += len(pending)
        tab_times.extend([timeout_s] * len(pending))
    return tab_times, per_call, errors


async def run_level(  # noqa: PLR0913 - the settings of one bench level, passed once
    n: int, args, handler, engine, reader, watch: RollupWatch, version: str
) -> dict:
    progress(f"=== {n} concurrent user(s), {args.iterations} iteration(s), cache {args.cache}")
    watch.reset()
    probe = IngestProbe(engine, args.probe_ms / 1000, config.CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS / 1000)
    stop = asyncio.Event()
    job = {"runs": 0, "seconds": [], "failures": Counter()}
    job_task = (
        asyncio.create_task(job_loop(engine, reader, window(0)[0], version, args.job_interval_s, stop, job))
        if args.job_interval_s > 0
        else None
    )
    probe.start()
    samples: dict[str, list[float]] = defaultdict(list)
    calls_seen: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    errors: dict[str, Counter] = defaultdict(Counter)
    plan = tabs(handler, args.days)
    for iteration in range(1, args.iterations + 1):
        for tab, calls in plan.items():
            if args.tabs and tab not in args.tabs:
                continue
            reset_dashboard_cache()  # every step starts as a page opened after the answer cache's TTL
            started = time.perf_counter()
            tab_times, per_call, step_errors = await run_step(n, calls, args.step_timeout_s)
            samples[tab].extend(tab_times)
            for name, seconds in per_call.items():
                calls_seen[tab][name].extend(seconds)
            errors[tab].update(step_errors)
            progress(
                f"  iter {iteration}/{args.iterations} {tab:<15} {n} user(s): p50 {pct(tab_times, 50):6.2f}s  "
                f"max {max(tab_times):6.2f}s  errors {sum(step_errors.values())}  "
                f"(step {time.perf_counter() - started:5.1f}s; ingest refused so far {probe.refused}, "
                f"rollup answered {watch.served}, refused {dict(watch.refusals) or 'none'})"
            )
    stop.set()
    if job_task:
        await job_task
    ingest = await probe.stop()
    result = {}
    for tab, values in samples.items():
        result[tab] = {
            "samples": len(values),
            "p50_s": round(pct(values, 50), 3),
            "p95_s": round(pct(values, 95), 3),
            "max_s": round(max(values), 3),
            "within_target": pct(values, 95) <= TARGET_S,
            "errors": dict(errors[tab]),
            "requests_p95_s": {name: round(pct(v, 95), 3) for name, v in calls_seen[tab].items()},
        }
    return {
        "tabs": result,
        "ingest_probe": ingest,
        "rollup": {"answered": watch.served, "refusals": dict(watch.refusals)},
        "job": {"runs": job["runs"], "seconds": job["seconds"], "failures": dict(job["failures"])},
    }


def _errors(tab: dict) -> str:
    return f"x{sum(tab['errors'].values())}" if tab["errors"] else ""


def report(levels: dict[int, dict]) -> None:
    progress("=== summary: tab time p95 in seconds (target 2.0); n = samples per cell")
    names = list(next(iter(levels.values()))["tabs"])
    progress(f"  {'tab':<15} " + "".join(f"{f'{n} user(s)':>14}" for n in levels))
    for name in names:
        cells = "".join(
            f"{levels[n]['tabs'][name]['p95_s']:>9.2f}{'' if levels[n]['tabs'][name]['within_target'] else ' !':<2}"
            f"{_errors(levels[n]['tabs'][name]):>3}"
            for n in levels
        )
        progress(f"  {name:<15} {cells}")
    for n, level in levels.items():
        probe = level["ingest_probe"]
        progress(
            f"  {n} user(s): ingest attempts {probe['attempts']}, refused (503) {probe['refused_503']}, acquire wait "
            f"p99 {probe['acquire_wait_p99_s']}s max {probe['acquire_wait_max_s']}s; rollup answered "
            f"{level['rollup']['answered']}, refused {level['rollup']['refusals'] or 'none'}"
        )


def lanes(settings) -> dict | None:
    """The heavy, light and jobs slots the run used (None for code from before the lanes)."""
    try:
        from codemie.repository.cli_analytics.postgres.settings import lane_sizes

        sizes = lane_sizes(
            settings.pool_size, settings.ingest_reserve, settings.heavy_slots, settings.light_slots, settings.jobs_slots
        )
        return dict(zip(("heavy", "light", "jobs"), sizes, strict=True))
    except (ImportError, AttributeError):
        return None


async def run(args) -> dict:
    url = os.environ["PG_URL"]
    check_scratch(url)
    kwargs = {"PG_URL": url}
    if args.pool_size:
        kwargs["CLI_ANALYTICS_PG_POOL_SIZE"] = args.pool_size
    engine = AnalyticsPgEngine(AnalyticsPgSettings.from_config(Config(**kwargs)))
    reader = PostgresCliAnalyticsReader(engine)
    version = RatesVersionProvider(catalog_rate_fingerprint).current()
    reader.set_rates(lambda: version)
    handler = LocalAnalyticsHandler(reader)
    watch = RollupWatch()
    watch.install()
    out: dict = {
        "days": args.days,
        "users": args.users,
        "iterations": args.iterations,
        "cache": args.cache,
        "pool_size": engine.settings.pool_size,
        "ingest_reserve": getattr(engine.settings, "ingest_reserve", None),
        "lanes": lanes(engine.settings),
        "target_s": TARGET_S,
    }
    async with engine.acquire() as conn:
        out["sessions"] = await conn.fetchval("SELECT count(*) FROM session_dims")
    progress(
        f"{out['sessions']} sessions, pool {out['pool_size']} (ingest reserve {out['ingest_reserve']}), "
        f"lanes {out['lanes']}"
    )
    await build_days(engine, reader, window(0)[0], version, "confirm today")
    ttl = config.CLI_ANALYTICS_DASHBOARD_CACHE_TTL_S if args.cache == "on" else 0
    levels: dict[int, dict] = {}
    with (
        patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True),
        patch.object(config, "CLI_ANALYTICS_DASHBOARD_CACHE_TTL_S", ttl),
    ):
        for n in args.users:
            levels[n] = await run_level(n, args, handler, engine, reader, watch, version)
    await engine.close()
    report(levels)
    out["levels"] = {str(n): level for n, level in levels.items()}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--days", type=int, default=90, help="the window: 90 is `last_year` (clamped to the raw retention)"
    )
    parser.add_argument("--users", type=lambda s: [int(x) for x in s.split(",")], default=[1, 4, 8])
    parser.add_argument("--iterations", type=int, default=2, help="rounds over all tabs per user count")
    parser.add_argument("--cache", choices=["on", "off"], default="on")
    parser.add_argument(
        "--tabs", type=lambda s: set(s.split(",")), default=None, help="only these tabs, comma separated"
    )
    parser.add_argument("--probe-ms", type=float, default=100.0, help="the ingest probe's interval")
    parser.add_argument(
        "--job-interval-s", type=float, default=30.0, help="the rollup job's run of today's row; 0 = off"
    )
    parser.add_argument(
        "--step-timeout-s", type=float, default=180.0, help="a tab step still running after this is counted failed"
    )
    parser.add_argument(
        "--pool-size", type=int, default=None, help="CLI_ANALYTICS_PG_POOL_SIZE (the app's default is 8)"
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), indent=2))  # noqa: T201 - the result is the output


if __name__ == "__main__":
    main()
