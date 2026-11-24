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

"""Page 0 of /repositories and /projects against the 2,000 ms target, and the rollup rebuild after a rates change
(EPMCDME-17301). SCRATCH database only: the rebuild writes the user rollup tables of the window's days.

    PG_URL=postgresql://.../cli_analytics_scratch poetry run python scripts/cli_analytics_bench/run_ranking_bench.py \
        [--days 30] [--runs 5]

Reports, with the handler the API calls (server time, no HTTP):
  live       page 0 (50 rows) of /repositories and /projects with the rollup switched off (the heavy live statements)
  rollup     the same pages served from the ranking rollup rows (built first under the current rates version)
  rebuild    seconds the job needs to rebuild every day of the window after a rates change (all input prices doubled)
Each time is min, median and max over `--runs` after one cold run, with the payload bytes and the statements of the
request, and `within_target` against `--target-ms` (2000). The time is reported, not asserted: the pinned count
of statements is in the live test test_live_ranking_perf.py. The script refuses a database whose name does
not contain "scratch" (BENCH_ALLOW_DB=1 overrides it for a disposable copy).

A request builds its own window (ending now, as the router does), and today's rollup row is confirmed again before each
rollup measurement: the rollup answers only a window that ends within two minutes of now, and only while the job has
confirmed today lately, so a window built once at the start, or a row left to age, would measure the live path twice.
Each result therefore also says `served_from_rollup` ("n/runs": the requests the rollup answered, a positive check) and
`refusals` (the reason of every refusal, `cli_analytics_rollup_refused_total{reason}`). A rollup result with a refusal
is a live-path time: do not read it as the rollup's.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, UTC
from unittest.mock import patch
from urllib.parse import urlparse

from codemie.configs.config import Config, config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres import user_rollup as ur
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.rates_version import RatesVersionProvider
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.service.analytics.dashboard_cache import reset_dashboard_cache
from codemie.service.analytics.handlers import cli_analytics_handler, coding_agent_pricing

try:  # code from before the request stats (main) has neither the module nor the refusal counter
    from codemie.repository.cli_analytics.postgres import request_stats
except ImportError:
    request_stats = None
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from codemie.service.analytics.handlers.coding_agent_pricing import catalog_rate_fingerprint, model_rates_for

PER_PAGE = 50
T0 = time.perf_counter()


def progress(message: str) -> None:
    """One line on stderr with the seconds since the start, flushed at once: the result only comes at the end."""
    print(f"[bench +{time.perf_counter() - T0:7.1f}s] {message}", file=sys.stderr, flush=True)  # noqa: T201


LOOKUP = coding_agent_pricing.lookup_cost_config  # before the patch replaces it
PRICING = "codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config"


def window(days: int) -> tuple[list, LocalAnalyticsFilter]:
    """The whole days of the window up to now and the filter the router builds for a super admin."""
    now = datetime.now(UTC)
    first = (now - timedelta(days=days)).date()
    day_list = [first + timedelta(days=n) for n in range((now.date() - first).days + 1)]
    start = datetime(first.year, first.month, first.day, tzinfo=UTC)
    return day_list, LocalAnalyticsFilter(scope=ur.ROLLUP_SCOPE, start_dt=start, end_dt=now)


def summary(seconds: list[float], target_ms: float) -> dict:
    return {
        "runs": len(seconds),
        "min_s": round(min(seconds), 3),
        "median_s": round(statistics.median(seconds), 3),
        "max_s": round(max(seconds), 3),
        "within_target": max(seconds) * 1000 <= target_ms,
    }


class StatementCounter:
    """Counts the reader's statements (the request's `statements`)."""

    def __init__(self) -> None:
        self.statements = 0

    def install(self) -> None:
        counter = self
        original = PostgresCliAnalyticsReader._records

        async def counted(*args, **kwargs):
            counter.statements += 1
            return await original(*args, **kwargs)

        PostgresCliAnalyticsReader._records = staticmethod(counted)  # type: ignore[method-assign]


class RollupWatch:
    """What the rollup did for the requests of one measurement: the reason of every refusal, and how many requests it
    answered (`mark_rollup_used` is called only for an answer)."""

    def __init__(self) -> None:
        self.refusals: Counter = Counter()
        self.served = 0

    def reset(self) -> None:
        self.refusals.clear()
        self.served = 0

    def install(self) -> None:
        watch = self
        refused = request_stats.rollup_refused if request_stats else None
        used = cli_analytics_handler.mark_rollup_used

        def spy_refused(reason: str) -> None:
            watch.refusals[reason] += 1
            refused(reason)

        def spy_used() -> None:
            watch.served += 1
            used()

        if request_stats is not None:
            request_stats.rollup_refused = spy_refused  # type: ignore[assignment]
        cli_analytics_handler.mark_rollup_used = spy_used  # type: ignore[assignment]


async def page0(handler, days: int, name: str):
    _, f = window(days)  # a window of its own: it ends now, as the router builds it
    reset_dashboard_cache()  # the answer cache would hide the statements
    if name == "repositories":
        data, _ = await handler.get_repositories(f, 0, PER_PAGE, False, None)
    else:
        data, _ = await handler.get_projects(f, 0, PER_PAGE, None)
    return data


async def measure(  # noqa: PLR0913 - the parameters of one measured bench scenario, passed once
    handler,
    days: int,
    name: str,
    runs: int,
    target_ms: float,
    counter: StatementCounter,
    watch: RollupWatch,
    label: str,
) -> dict:
    progress(f"{label} {name}: cold run")
    started = time.perf_counter()
    await page0(handler, days, name)  # cold: plans and caches are another measurement
    progress(f"{label} {name}: cold run {time.perf_counter() - started:.2f}s")
    seconds, size = [], 0
    counter.statements = 0
    watch.reset()
    for n in range(1, runs + 1):
        started = time.perf_counter()
        data = await page0(handler, days, name)
        seconds.append(time.perf_counter() - started)
        size = len(json.dumps(data, default=str))
        progress(
            f"{label} {name}: run {n}/{runs} {seconds[-1]:.2f}s "
            f"(rollup answered {watch.served}, refused {dict(watch.refusals) or 'none'})"
        )
    return {
        **summary(seconds, target_ms),
        "payload_bytes": size,
        "statements": counter.statements // runs,
        "served_from_rollup": f"{watch.served}/{runs}",
        "refusals": dict(watch.refusals),
    }


async def build_days(engine, reader, days, version: str, what: str = "build") -> float:
    """What the job does for the days under `version`'s rates; returns the seconds."""
    reader.set_rates(lambda: version)
    started = time.perf_counter()
    async with engine.acquire() as conn:
        signatures = await ur.day_signatures(conn, days[0], days[-1])
        for n, day in enumerate(days, 1):
            await ur.build_day(reader, conn, day, model_rates_for, version, signatures.get(day, ur._NO_SESSIONS))
            if len(days) > 1 and (n % 5 == 0 or n == len(days)):
                progress(f"{what}: day {n}/{len(days)} ({day}), {time.perf_counter() - started:.0f}s so far")
    return time.perf_counter() - started


def dearer(model: str):
    """The catalog lookup with every input price doubled: what a rates change does to a built day."""
    cost_config = LOOKUP(model)
    return None if cost_config is None else cost_config.model_copy(update={"input": cost_config.input * 2})


def check_scratch(url: str) -> None:
    name = urlparse(url).path.rsplit("/", 1)[-1]
    if "scratch" not in name and os.environ.get("BENCH_ALLOW_DB") != "1":
        raise SystemExit(f"refusing database {name!r}: not a scratch database (BENCH_ALLOW_DB=1 overrides)")


async def run(args) -> dict:
    url = os.environ["PG_URL"]
    check_scratch(url)
    engine = AnalyticsPgEngine(AnalyticsPgSettings.from_config(Config(PG_URL=url)))
    counter = StatementCounter()
    counter.install()
    watch = RollupWatch()
    watch.install()
    reader = PostgresCliAnalyticsReader(engine)
    reader.set_rates(RatesVersionProvider(catalog_rate_fingerprint).current)
    handler = LocalAnalyticsHandler(reader)
    days, _ = window(args.days)
    out: dict = {"days": args.days, "runs": args.runs, "target_ms": args.target_ms}
    async with engine.acquire() as conn:
        out["sessions"] = await conn.fetchval("SELECT count(*) FROM session_dims")
        out["projects"] = await conn.fetchval("SELECT count(DISTINCT project_name) FROM session_dims")
    progress(f"phase 1/4: live path (rollup off), {args.days} days, {out['sessions']} sessions")
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", False):
        out["live"] = {}
        for n in NAMES:
            out["live"][n] = await measure(handler, args.days, n, args.runs, args.target_ms, counter, watch, "live")
    current = RatesVersionProvider(catalog_rate_fingerprint).current()
    progress(f"phase 2/4: first rollup build of {len(days)} days")
    out["rollup_first_build_s"] = round(await build_days(engine, reader, days, current, "first build"), 2)
    progress("phase 3/4: rollup path")
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
        out["rollup"] = {}
        for n in NAMES:
            # The job confirms today's row every few seconds; here nothing does, so it is confirmed before each page.
            await build_days(engine, reader, days[-1:], current, "confirm today")
            out["rollup"][n] = await measure(handler, args.days, n, args.runs, args.target_ms, counter, watch, "rollup")
    for n, result in out["rollup"].items():
        if result["refusals"] or result["served_from_rollup"] != f"{args.runs}/{args.runs}":
            print(  # noqa: T201 - a warning for the person reading the result
                f"WARNING: rollup {n} was not answered by the rollup ({result['served_from_rollup']}, refusals "
                f"{result['refusals']}): its time is the live path's",
                file=sys.stderr,
            )
    progress(f"phase 4/4: rebuild of {len(days)} days after a rates change, then back to the current rates")
    with patch(PRICING, dearer):
        out["rebuild_after_rates_change_s"] = round(
            await build_days(engine, reader, days, current + "-rates-change", "rates-change rebuild"), 2
        )
    await build_days(engine, reader, days, current, "restore rates")  # leave the rows on the current rates
    progress("done")
    await engine.close()
    return out


NAMES = ("repositories", "projects")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--runs", type=int, default=5, help="timed runs per page, after one cold run")
    parser.add_argument("--target-ms", type=float, default=2000.0, help="the page 0 server time target")
    print(json.dumps(asyncio.run(run(parser.parse_args())), indent=2))  # noqa: T201 - the result is the output


if __name__ == "__main__":
    main()
