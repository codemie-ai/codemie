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

"""Live-Postgres pin of the statement count of page 0 of `/repositories` and `/projects` over a seed the size of the
slow production database (5,763 sessions in 1,237 projects), EPMCDME-17301. The count is pinned, the time is only
bounded by a time budget on the rollup path (the log line of the request carries it). The `/projects` payload must
not grow past what the code before the ranking change produced for the same seed. Skipped without `TEST_LOCAL_PG_URL`."""

from __future__ import annotations

import contextlib
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs.config import config
from codemie.repository.cli_analytics.postgres import user_rollup
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.service.analytics.dashboard_cache import reset_dashboard_cache
from codemie.service.analytics.handlers import cli_analytics_handler as handler_module
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.postgres import test_live_dashboard_aggregates as live
from tests.codemie.repository.cli_analytics.postgres.test_live_ranking_rollup import (
    VERSION,
    _build,
    _forget_days,
    _Spy,
    _Window,
)

pytestmark = pytest.mark.skipif(
    not live._LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against"
)

SESSIONS, PROJECTS, PER_PAGE = 5763, 1237, 50
# Page 0 of /projects before the ranking change (`main` at 9f19b3b3a, this seed, JSON bytes; it carries the pricing marks of
# every row, EPMCDME-17152): the new code must not exceed it.
PROJECTS_PAGE0_BASELINE_BYTES = 123618
# Statements of page 0 (reader statements of the request, from its log line), live path and rollup path. The rollup
# path's are those of its own read: sessions that started before the window and are active in it (rows of other tests
# or of a bigger database) are merged live by another statement set, which would make the count depend on the
# database, so that merge is switched off for the measurement (the Overview and Users rollups pin theirs elsewhere).
LIVE_STATEMENTS = {"repositories": 20, "projects": 30}
ROLLUP_STATEMENTS = {"repositories": 7, "projects": 7}

# Page 0 server time of the rollup path (the path the dashboard takes): the target of the change, 2 s. Measured on this
# seed it takes about 0.02 s, so the budget fails on a regression of two orders of magnitude, not on a slow CI runner.
# The live path's time is only reported: it is the slow path the rollup replaces.
ROLLUP_PAGE0_BUDGET_S = 2.0

_STATEMENTS = re.compile(r"statements=(\d+)")


class _BulkSeed:
    """`SESSIONS` sessions in `PROJECTS` projects of one user, a repository per project, one priced cost row each."""

    def __init__(self) -> None:
        self.suffix = uuid.uuid4().hex[:10]
        self.email = f"perf-{self.suffix}@example.com"
        # Yesterday 01:00 UTC: the window then spans two UTC days (a window inside one day is never served from the rollup).
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        self.t0 = datetime(yesterday.year, yesterday.month, yesterday.day, 1, tzinfo=timezone.utc)

    def sid(self, number: int) -> str:
        return f"test-perf-{self.suffix}-{number:05d}"

    async def insert(self, engine) -> None:
        day = self.t0.date()
        dims, costs = [], []
        for n in range(SESSIONS):
            project = n % PROJECTS
            started = self.t0 + timedelta(seconds=n)
            dims.append(
                (self.sid(n), started, started + timedelta(minutes=1), f"repo-{project:04d}", ("main", "dev")[n % 2],
                 f"project-{project:04d}", self.email, "codemie", "profile", "api_based", f"prompt {n}", day)
            )  # fmt: skip
            costs.append((day, self.sid(n), self.email, live._COST_MODEL, 100 + n % 7, 20, 600, 3))
        async with engine.acquire() as conn:
            await conn.executemany(
                "INSERT INTO session_dims (session_id, started_at, last_event_at, repository, branch, project_name,"
                " jwt_email, subscription_type, project_origin, funding_route, first_prompt, has_activity, resumed,"
                " last_activity_day) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, true, false, $12)",
                dims,
            )
            await conn.executemany(
                "INSERT INTO cost_daily (day, session_id, user_email, model_name, query_source, cost_usd,"
                " input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, api_call_count)"
                " VALUES ($1, $2, $3, $4, 'main', 0, $5, $6, $7, 0, $8)",
                costs,
            )
            # The planner's statistics of a database that other tests filled and emptied are stale: without this the
            # statements of the seed's day can plan as for a table of another size and run into the statement timeout.
            await conn.execute("ANALYZE session_dims")
            await conn.execute("ANALYZE cost_daily")

    async def cleanup(self, engine) -> None:
        ids = [self.sid(n) for n in range(SESSIONS)]
        async with engine.acquire() as conn:
            for table in ("cost_daily", "session_dims"):
                await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", ids)  # noqa: S608


async def _page0(handler, f, name: str):
    """(payload bytes, statements of the request, seconds): the statements are summed over the request's log lines."""
    log = MagicMock()
    reset_dashboard_cache()
    started = time.perf_counter()
    with patch.object(handler_module, "logger", log), patch(live._PRICING, live._lookup):
        if name == "repositories":
            data, _ = await handler.get_repositories(f, 0, PER_PAGE, False, None)
        else:
            data, _ = await handler.get_projects(f, 0, PER_PAGE, None)
    seconds = time.perf_counter() - started
    lines = [str(call.args[0]) for call in log.info.call_args_list if "cli_analytics request" in str(call.args[0])]
    statements = sum(int(_STATEMENTS.search(line).group(1)) for line in lines)
    assert data["rows"]
    return len(json.dumps(data, default=str)), statements, seconds


@pytest.mark.asyncio
async def test_page_zero_statements_are_pinned_and_the_projects_payload_does_not_grow():
    engine, seed = await live._engine(), _BulkSeed()
    window = _Window(seed)
    try:
        await seed.insert(engine)
        reader = PostgresCliAnalyticsReader(engine)
        reader.set_rates(lambda: VERSION)
        handler = LocalAnalyticsHandler(reader)
        report = {}
        with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", False):  # the live statements
            for name in ("repositories", "projects"):
                size, statements, seconds = await _page0(handler, window.filter(), name)
                report["live", name] = (size, statements, round(seconds, 2))
        started = time.perf_counter()
        await _build(engine, reader, window.days, VERSION)
        report["rollup_build_s"] = round(time.perf_counter() - started, 2)
        with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):  # the ranking rows
            spy = _Spy()
            with contextlib.ExitStack() as stack:
                for patched in spy.patches():
                    stack.enter_context(patched)
                stack.enter_context(patch.object(user_rollup, "_sessions_read_live", AsyncMock(return_value=[])))
                for name in ("repositories", "projects"):
                    size, statements, seconds = await _page0(handler, window.filter(), name)
                    report["rollup", name] = (size, statements, round(seconds, 2))
            assert spy.served == [True, True], "page 0 was not served from the ranking rows"
        print(f"\nranking perf report (bytes, statements, seconds): {report}")  # noqa: T201 - the reported time
        for path, pinned in (("live", LIVE_STATEMENTS), ("rollup", ROLLUP_STATEMENTS)):
            for name in ("repositories", "projects"):
                assert report[path, name][1] == pinned[name], (path, name, report)
        for name in ("repositories", "projects"):
            assert report["rollup", name][2] <= ROLLUP_PAGE0_BUDGET_S, (name, ROLLUP_PAGE0_BUDGET_S, report)
        assert report["live", "projects"][0] <= PROJECTS_PAGE0_BASELINE_BYTES, report
        assert report["rollup", "projects"][0] <= PROJECTS_PAGE0_BASELINE_BYTES, report
    finally:
        await seed.cleanup(engine)
        await _forget_days(engine, window.days)
        reset_dashboard_cache()


TOOL_NAMES, SKILL_NAMES = 60, 15


class _ToolsBulkSeed(_BulkSeed):
    """The same sessions with tool calls, skill and subagent dispatches and OTel slash commands, as the Tools & Models
    tab reads them."""

    async def insert(self, engine) -> None:
        await super().insert(engine)
        rows = []
        for n in range(SESSIONS):
            hour = (self.t0 + timedelta(seconds=n)).replace(minute=0, second=0, microsecond=0)
            sid = self.sid(n)
            rows.append((hour, sid, 1, f"tool-{n % TOOL_NAMES:02d}", 1 + n % 5, 1 + n % 4))
            rows.append((hour, sid, 2, f"skill-{n % SKILL_NAMES:02d}", 1, 0))
            rows.append((hour, sid, 4, f"/command-{n % 7}", 1, 0))
        async with engine.acquire() as conn:
            await conn.executemany(
                "INSERT INTO invocations_hourly (hour, session_id, kind, name, calls, success)"
                " VALUES ($1, $2, $3, $4, $5, $6)",
                rows,
            )
            await conn.execute("ANALYZE invocations_hourly")

    async def cleanup(self, engine) -> None:
        ids = [self.sid(n) for n in range(SESSIONS)]
        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM invocations_hourly WHERE session_id = ANY($1::text[])", ids)
        await super().cleanup(engine)


@pytest.mark.asyncio
async def test_the_tools_tab_is_served_from_the_rollup_within_the_page_zero_budget():
    engine, seed = await live._engine(), _ToolsBulkSeed()
    window = _Window(seed)
    try:
        async with engine.acquire() as conn:
            if not await conn.fetchval("SELECT to_regclass('user_day_tools_rollup') IS NOT NULL"):
                pytest.skip("the Tools & Models rollup tables are not in this database")
        await seed.insert(engine)
        reader = PostgresCliAnalyticsReader(engine)
        reader.set_rates(lambda: VERSION)
        handler = LocalAnalyticsHandler(reader)

        async def tools(enabled: bool) -> tuple[dict, float]:
            reset_dashboard_cache()
            started = time.perf_counter()
            with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", enabled), patch(live._PRICING, live._lookup):
                data = await handler.get_tools(window.filter(), include_class_lists=False)
            return data, time.perf_counter() - started

        from_live, live_seconds = await tools(False)
        await _build(engine, reader, window.days, VERSION)
        served: list[bool] = []
        original = user_rollup.serve_tools

        async def spy(*args, **kwargs):
            answer = await original(*args, **kwargs)
            served.append(answer is not None)
            return answer

        with (
            patch.object(user_rollup, "serve_tools", spy),
            # sessions of other tests or of a bigger database that started earlier would be merged live: the time
            # measured is the rollup's own read
            patch.object(user_rollup, "_sessions_read_live", AsyncMock(return_value=[])),
        ):
            from_rollup, rollup_seconds = await tools(True)
        print(  # noqa: T201 - the reported time
            f"\ntools perf report (seconds): live {live_seconds:.2f}, rollup {rollup_seconds:.2f}"
        )

        assert served == [True], "the Tools & Models tab was not served from the rollup"
        assert len(from_rollup["tool_usage"]) == 20 and from_rollup["skills_invoked"] and from_rollup["slash_commands"]
        assert from_rollup == from_live  # the same response at the size of the slow production database
        assert rollup_seconds <= ROLLUP_PAGE0_BUDGET_S, (ROLLUP_PAGE0_BUDGET_S, rollup_seconds, live_seconds)
    finally:
        await seed.cleanup(engine)
        await _forget_days(engine, window.days)
        reset_dashboard_cache()
