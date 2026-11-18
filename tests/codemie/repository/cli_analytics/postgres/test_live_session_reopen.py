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

"""Live-Postgres proof that a reopen with no work of its own changes nothing about an old session.

A session works on day D1. On a later day D2 an older client reopens it and sends only lifecycle hooks
(`agent.session.start` / `env` / `end`, `agent.git.snapshot`) and, optionally, Claude Code's own startup
records. The session must not be listed or counted for any window on D2, in the sessions, overview, users,
repositories, cost and activity endpoints, and its duration must end at its last D1 activity.

Everything is seeded as raw rows and rolled up by the real refresher (D1 first, then D2, as in production),
so the stored `session_dims` is what ingestion leaves. Each scenario runs with the stored activity facts
derived (what the refresher leaves) and reset to NULL (as before they were derived).
Skipped when `TEST_LOCAL_PG_URL` is not set or unreachable: point it at a disposable PostgreSQL.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, time, timedelta, timezone

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres import maintenance, migrations, session_purge
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.maintenance import (
    MaintenanceReport,
    PartitionMaintainer,
    RetentionPolicy,
)
from codemie.repository.cli_analytics.postgres import reader as reader_module
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.support.live_rollup_lock import live_rollup_lock
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

_UTC = timezone.utc
_FACTS = "derived"

# The lifecycle hooks an older client stamps at a prompt-less reopen.
_REOPEN_HOOKS = ("agent.session.start", "agent.session.env", "agent.git.snapshot", "agent.session.end")
# Claude Code's own startup records (event_kind OTHER), as listed in the ticket comment.
_STARTUP_RECORDS = (
    "hook_registered",
    "plugin_loaded",
    "managed_settings_resolved",
    "mcp_server_connection",
    "hook_execution_start",
    "hook_execution_complete",
)
# The end is exactly the last real activity. The seeded session.end hooks lag it by 20 s, so a tolerance below that
# fails when a lifecycle hook moves the end by even a few seconds.
_END_HOOK_LAG = timedelta(seconds=2)
_EMPTY = {"sessions": [], "overview_sessions": 0, "users": 0, "repositories": 0, "cost_sessions": 0, "activity": 0}

_TABLES = (
    "session_dims",
    "turns_daily",
    "tool_facts_daily",
    "cost_daily",
    "session_usage",
    "lines_daily",
    "active_time_daily",
    "hook_events",
    "usage_requests",
    "log_events",
    "spans",
    "metric_points",
    "rollup_dirty",
)


@pytest.fixture(autouse=True, params=["derived", "underived"])
def _activity_facts(request, monkeypatch):
    monkeypatch.setattr(f"{__name__}._FACTS", request.param)


async def _engine() -> AnalyticsPgEngine:
    try:
        settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL))
        engine = AnalyticsPgEngine(settings)
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
    migrations.run_migrations(settings)  # idempotent: a migrated database is left as it is
    maintainer = PartitionMaintainer(
        engine, RetentionPolicy.from_settings(settings), clock=lambda: datetime.now(_UTC).date()
    )
    async with engine.acquire() as conn:
        await maintainer._ensure_partitions(conn, datetime.now(_UTC).date(), MaintenanceReport())
    return engine


async def _cleanup(engine: AnalyticsPgEngine, session_ids: list[str]) -> None:
    async with engine.acquire() as conn:
        for table in _TABLES:
            if table == "rollup_dirty":
                continue
            await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", session_ids)  # noqa: S608


def _naive(value: datetime) -> datetime:
    """`value` as a naive UTC time; a value that is already naive is UTC (that is what the reader returns)."""
    if value.tzinfo is None:
        return value
    return value.astimezone(_UTC).replace(tzinfo=None)


class _Reopen:
    """One old session: real work on D1 (ten days before D2), then a reopen on D2 (half an hour ago)."""

    def __init__(self) -> None:
        self.suffix = uuid.uuid4().hex[:12]
        self.email = f"reopen-{self.suffix}@example.com"
        self.d2 = (datetime.now(_UTC) - timedelta(minutes=30)).replace(microsecond=0)
        self.d1 = datetime.combine((self.d2 - timedelta(days=10)).date(), time(17, 44), tzinfo=_UTC)
        self.last_d1 = self.d1 + timedelta(minutes=112)  # the last real D1 activity: a model request
        self._spans = 0

    def sid(self, name: str) -> str:
        return f"test-reopen-{self.suffix}-{name}"

    # -- the D1 work, shared by every scenario --------------------------------------------------------------

    def d1_hooks(self) -> list[tuple[str, datetime]]:
        return [
            ("agent.session.start", self.d1),
            ("agent.prompt.submit", self.d1 + timedelta(seconds=10)),
            ("agent.tool.start", self.d1 + timedelta(minutes=46)),
            ("agent.tool.end", self.d1 + timedelta(minutes=46, seconds=5)),
            ("agent.session.end", self.last_d1 + timedelta(seconds=20)),
        ]

    def d1_usage(self) -> list[tuple[datetime, str]]:
        return [(self.d1 + timedelta(seconds=20), "d1-r1"), (self.last_d1, "d1-r2")]

    def reopen_hooks(self) -> list[tuple[str, datetime]]:
        return [(t, self.d2 + timedelta(seconds=i)) for i, t in enumerate(_REOPEN_HOOKS)]

    def startup_records(self) -> list[tuple[str, int, datetime, str | None]]:
        return [(name, 0, self.d2 + timedelta(seconds=44 + i), None) for i, name in enumerate(_STARTUP_RECORDS)]

    def d1_summary(self, final: bool) -> list[tuple[str, datetime, dict]]:
        """The summary hook of the D1 run: final (SessionEnd) or a provisional Stop snapshot (SessionEnd cancelled)."""
        sent = self.last_d1 + timedelta(seconds=20)
        attrs = {
            "is_final": final,
            "started_at": self.d1.isoformat().replace("+00:00", "Z"),
            "ended_at": self.last_d1.isoformat().replace("+00:00", "Z"),
        }
        return [("agent.session.summary", sent, attrs)]

    # -- windows ---------------------------------------------------------------------------------------------

    def window(self, start: datetime, end: datetime) -> LocalAnalyticsFilter:
        return LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=_naive(start), end_dt=_naive(end), users=[self.email])

    def d1_day(self) -> LocalAnalyticsFilter:
        start = datetime.combine(self.d1.date(), time(0), tzinfo=_UTC)
        return self.window(start, start + timedelta(days=1) - timedelta(microseconds=1))

    def d2_windows(self) -> dict[str, LocalAnalyticsFilter]:
        start = datetime.combine(self.d2.date(), time(0), tzinfo=_UTC)
        return {
            "d2-day": self.window(start, start + timedelta(days=1) - timedelta(microseconds=1)),
            "d2-around-the-reopen": self.window(self.d2 - timedelta(minutes=10), self.d2 + timedelta(minutes=10)),
        }

    # -- seeding ---------------------------------------------------------------------------------------------

    async def seed(
        self,
        engine: AnalyticsPgEngine,
        sid: str,
        *,
        hooks: list[tuple[str, datetime]] = (),
        usage: list[tuple[datetime, str]] = (),
        logs: list[tuple] = (),
        spans: list[tuple] = (),
    ) -> None:
        """Insert raw rows for `sid` and run the real rollup over their days. A log or a span may end with a span id,
        which links a prompt record, its model request and its interaction span as the client does."""
        days = set()
        async with engine.acquire() as conn:
            for event_type, ts, *attrs in hooks:
                await conn.execute(
                    "INSERT INTO hook_events (ts, session_id, event_type, user_email, attrs) "
                    "VALUES ($1, $2, $3, $4, $5::jsonb)",
                    ts, sid, event_type, self.email, json.dumps(attrs[0]) if attrs else None,
                )  # fmt: skip
                days.add(ts.date())
            for ts, request_id in usage:
                await conn.execute(
                    "INSERT INTO usage_requests (ts, session_id, request_id, model_raw, model, input_tokens, "
                    "output_tokens, cache_read_tokens, stop_reason, user_email) "
                    "VALUES ($1, $2, $3, 'claude-sonnet-4-5', 'claude-sonnet-4-5', 1000, 10, 0, 'end_turn', $4)",
                    ts, sid, request_id, self.email,
                )  # fmt: skip
                days.add(ts.date())
            for name, kind, ts, command, *link in logs:
                await conn.execute(
                    "INSERT INTO log_events (ts, session_id, event_kind, event_name, command_name, user_email, "
                    "severity, resource_id, span_id) VALUES ($1, $2, $3, $4, $5, $6, 9, 0, $7)",
                    ts, sid, kind, name, command, self.email, link[0].to_bytes(8, "big") if link else None,
                )  # fmt: skip
                days.add(ts.date())
            for kind, ts, *link in spans:
                self._spans += 1
                name = "claude_code.interaction" if kind == 3 else "claude_code.tool"
                await conn.execute(
                    "INSERT INTO spans (ts, duration_ns, trace_id, span_id, span_kind, span_name, session_id, "
                    "user_email, tool_name, status_code, resource_id) "
                    "VALUES ($1, 1, '\\x01'::bytea, $2, $3, $6, $4, $5, 'Bash', 0, 0)",
                    ts, (link[0] if link else self._spans).to_bytes(8, "big"), kind, sid, self.email, name,
                )  # fmt: skip
                days.add(ts.date())
        async with live_rollup_lock(engine):
            refresher = RollupRefresher(engine, batch_size=100)
            async with engine.acquire() as conn:
                for day in days:
                    await refresher.mark_dirty(day, day, conn)
            await refresher.refresh()
            if _FACTS == "underived":
                async with engine.acquire() as conn:
                    await conn.execute(
                        "UPDATE session_dims SET has_activity = NULL, last_activity_day = NULL, resumed = NULL "
                        "WHERE session_id = $1",
                        sid,
                    )

    async def stored(self, engine: AnalyticsPgEngine, sid: str) -> dict:
        async with engine.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT started_at, last_event_at, activity_started_at, ended_at, has_activity, resumed "
                "FROM session_dims WHERE session_id = $1",
                sid,
            )
        return dict(row) if row else {}


async def _endpoints(handler: LocalAnalyticsHandler, f: LocalAnalyticsFilter) -> dict:
    """What the six endpoints report for the window `f` (the filter is on one user, so only the test's sessions)."""
    sessions, _, _ = await handler.get_sessions(f, 0, 1000, "start_time", None)
    overview, _, _ = await handler.get_overview(f)
    users, _ = await handler.get_users(f, None, None)
    repositories, _ = await handler.get_repositories(f, 0, 1000, False)
    cost, _ = await handler.get_cost(f)
    activity = await handler.get_activity(f)
    return {
        "sessions": sorted(s["trace_id"] for s in sessions["sessions"]),
        "overview_sessions": overview["kpis"]["total_sessions"],
        "users": sum(r["session_count"] for r in users["rows"]),
        "repositories": sum(r["session_count"] for r in repositories["rows"]),
        "cost_sessions": cost["kpis"]["total_sessions"],
        "activity": sum(activity["by_hour"]),
    }


def _listed(sid: str, **overrides) -> dict:
    """What the endpoints report for a window in which exactly `sid` is active."""
    return {
        "sessions": [sid],
        "overview_sessions": 1,
        "users": 1,
        "repositories": 1,
        "cost_sessions": 1,
        "activity": 1,
    } | overrides


async def _durations(
    handler: LocalAnalyticsHandler, f: LocalAnalyticsFilter, sid: str
) -> tuple[int | None, int | None]:
    """(duration of the list row, duration of the detail) of the session."""
    sessions, _, _ = await handler.get_sessions(f, 0, 1000, "start_time", None)
    row = next(s for s in sessions["sessions"] if s["trace_id"] == sid)
    detail, _, _ = await handler.get_session_detail(sid, scope=EXCLUDE_SCOPE)
    return row["duration_ms"], detail["duration_ms"]


def _expected_ms(start: datetime, end: datetime) -> tuple[float, float]:
    """The duration window a session from `start` to its last real activity `end` may report."""
    span = (end - start).total_seconds() * 1000
    return span - 1000, span + _END_HOOK_LAG.total_seconds() * 1000


async def _run(scenario):
    engine = await _engine()
    seed = _Reopen()
    sids: list[str] = []
    try:
        await scenario(engine, seed, sids, LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine)))
    finally:
        await _cleanup(engine, sids)
        await engine.close()


async def _assert_unchanged(handler, engine, seed: _Reopen, sid: str, start: datetime | None = None) -> None:
    """The reopen changed nothing: D2 reports nothing, D1 still lists the session and its duration ends at the last D1
    activity. Both are checked before failing, so one problem does not hide the other."""
    problems = []
    on_d2 = {name: await _endpoints(handler, f) for name, f in seed.d2_windows().items()}
    if on_d2 != {name: _EMPTY for name in on_d2}:
        problems.append(f"reported on D2: {on_d2}")
    on_d1 = await _endpoints(handler, seed.d1_day())
    if on_d1 != _listed(sid):
        problems.append(f"D1 window reports {on_d1}")
    else:
        low, high = _expected_ms(start or seed.d1, seed.last_d1)
        durations = await _durations(handler, seed.d1_day(), sid)
        if not all(low <= d <= high for d in durations):
            problems.append(f"list/detail duration {durations} ms outside {(low, high)}")
    stored = await seed.stored(engine, sid)
    assert not problems, "; ".join(problems) + f"; stored session_dims: {stored}"


@pytest.mark.asyncio
async def test_reopen_with_lifecycle_hooks_only_is_not_reported_on_d2_when_d1_has_dimension_hooks():
    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("with-d1-hooks")
        sids.append(sid)
        await seed.seed(engine, sid, hooks=seed.d1_hooks(), usage=seed.d1_usage())
        await seed.seed(engine, sid, hooks=seed.reopen_hooks())
        await _assert_unchanged(handler, engine, seed, sid)

    await _run(scenario)


@pytest.mark.asyncio
async def test_reopen_with_lifecycle_hooks_only_is_not_reported_on_d2_when_d1_has_no_dimension_hook():
    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("no-d1-hooks")
        sids.append(sid)
        # D1 reached the backend as OTel and usage rows only: the reopen's session.start is the first dimension hook.
        await seed.seed(
            engine,
            sid,
            usage=seed.d1_usage(),
            logs=[("api_request", 1, seed.d1 + timedelta(seconds=20), None)],
            spans=[(1, seed.d1 + timedelta(minutes=46))],
        )
        await seed.seed(engine, sid, hooks=seed.reopen_hooks())
        await _assert_unchanged(handler, engine, seed, sid, start=seed.d1 + timedelta(seconds=20))

    await _run(scenario)


@pytest.mark.asyncio
async def test_startup_records_on_d2_do_not_list_or_extend_the_session():
    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("startup-records")
        sids.append(sid)
        await seed.seed(engine, sid, hooks=seed.d1_hooks(), usage=seed.d1_usage())
        await seed.seed(
            engine,
            sid,
            hooks=seed.reopen_hooks(),
            logs=[(name, 0, seed.d2 + timedelta(seconds=44 + i), None) for i, name in enumerate(_STARTUP_RECORDS)],
        )
        await _assert_unchanged(handler, engine, seed, sid)

    await _run(scenario)


@pytest.mark.asyncio
async def test_exit_prompt_record_on_d2_does_not_list_or_extend_the_session():
    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("exit-prompt")
        sids.append(sid)
        await seed.seed(engine, sid, hooks=seed.d1_hooks(), usage=seed.d1_usage())
        # The `/exit` the user types to close the reopened panel: a user_prompt record (kind 2) naming a built-in command.
        await seed.seed(
            engine,
            sid,
            hooks=seed.reopen_hooks(),
            logs=[("user_prompt", 2, seed.d2 + timedelta(minutes=2), "exit")],
        )
        await _assert_unchanged(handler, engine, seed, sid)

    await _run(scenario)


@pytest.mark.asyncio
async def test_reopen_that_does_real_work_is_listed_on_both_days_and_its_duration_spans_both():
    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("real-work")
        sids.append(sid)
        await seed.seed(engine, sid, hooks=seed.d1_hooks(), usage=seed.d1_usage())
        work = seed.d2 + timedelta(seconds=30)
        await seed.seed(
            engine,
            sid,
            hooks=[
                ("agent.session.start", seed.d2),
                ("agent.prompt.submit", work),
                ("agent.session.end", work + timedelta(seconds=40)),
            ],
            usage=[(work + timedelta(seconds=15), "d2-r1")],
        )
        for name, f in seed.d2_windows().items():
            assert await _endpoints(handler, f) == _listed(sid), name
        assert await _endpoints(handler, seed.d1_day()) == _listed(sid)
        low, high = _expected_ms(seed.d1, work + timedelta(seconds=15))
        durations = await _durations(handler, seed.d1_day(), sid)
        assert all(low <= d <= high for d in durations), f"{durations} outside {(low, high)}"

    await _run(scenario)


@pytest.mark.asyncio
async def test_recent_presets_do_not_show_an_old_session_reopened_inside_them():
    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("presets")
        sids.append(sid)
        await seed.seed(engine, sid, hooks=seed.d1_hooks(), usage=seed.d1_usage())
        await seed.seed(
            engine,
            sid,
            hooks=seed.reopen_hooks(),
            logs=[(name, 0, seed.d2 + timedelta(seconds=44 + i), None) for i, name in enumerate(_STARTUP_RECORDS)],
        )
        now = seed.d2 + timedelta(minutes=30)
        presets = {
            "last-7-days": seed.window(now - timedelta(days=7), now),
            "last-24-hours": seed.window(now - timedelta(hours=24), now),
            "last-hour": seed.window(now - timedelta(hours=1), now),
        }
        seen = {name: await _endpoints(handler, f) for name, f in presets.items()}
        assert seen == {name: _EMPTY for name in presets}

    await _run(scenario)


@pytest.mark.asyncio
async def test_a_genuinely_multi_day_session_keeps_its_all_time_tiles_in_each_window():
    """Guard for the accepted semantics: an admitted session's tiles are all-time, so both days show both days' tokens."""

    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("multi-day")
        sids.append(sid)
        await seed.seed(engine, sid, hooks=seed.d1_hooks(), usage=seed.d1_usage())
        work = seed.d2 + timedelta(seconds=30)
        await seed.seed(
            engine,
            sid,
            hooks=[("agent.session.start", seed.d2), ("agent.prompt.submit", work)],
            usage=[(work + timedelta(seconds=15), "d2-r1")],
        )
        tokens = []
        for f in (seed.d1_day(), seed.d2_windows()["d2-day"]):
            cost, _ = await handler.get_cost(f)
            tokens.append(cost["kpis"]["total_tokens"])
        assert tokens[0] == tokens[1] == 3 * (1000 + 10)

    await _run(scenario)


# -- which record moves the end: each reopen record alone, against a D1 with and without a final summary --------------


async def _only_the_end_is_checked(engine, seed, sids, handler, name: str, d1_extra: list, d2: dict) -> None:
    sid = seed.sid(name)
    sids.append(sid)
    await seed.seed(engine, sid, hooks=seed.d1_hooks() + d1_extra, usage=seed.d1_usage())
    await seed.seed(engine, sid, **d2)
    await _assert_unchanged(handler, engine, seed, sid)


@pytest.mark.asyncio
async def test_startup_records_alone_on_d2_do_not_extend_a_session_without_a_summary():
    async def scenario(engine, seed, sids, handler):
        await _only_the_end_is_checked(
            engine, seed, sids, handler, "otel-no-summary", [], {"logs": seed.startup_records()}
        )

    await _run(scenario)


@pytest.mark.asyncio
async def test_startup_records_alone_on_d2_do_not_extend_a_session_with_a_final_summary():
    async def scenario(engine, seed, sids, handler):
        await _only_the_end_is_checked(
            engine, seed, sids, handler, "otel-final", seed.d1_summary(True), {"logs": seed.startup_records()}
        )

    await _run(scenario)


@pytest.mark.asyncio
async def test_exit_prompt_alone_on_d2_does_not_extend_a_session_without_a_summary():
    async def scenario(engine, seed, sids, handler):
        exit_prompt = [("user_prompt", 2, seed.d2 + timedelta(minutes=2), "exit")]
        await _only_the_end_is_checked(engine, seed, sids, handler, "exit-no-summary", [], {"logs": exit_prompt})

    await _run(scenario)


@pytest.mark.asyncio
async def test_lifecycle_hooks_alone_on_d2_do_not_extend_a_session_with_a_final_summary():
    async def scenario(engine, seed, sids, handler):
        await _only_the_end_is_checked(
            engine, seed, sids, handler, "hooks-final", seed.d1_summary(True), {"hooks": seed.reopen_hooks()}
        )

    await _run(scenario)


@pytest.mark.asyncio
async def test_lifecycle_hooks_alone_on_d2_do_not_extend_a_session_with_a_provisional_summary():
    async def scenario(engine, seed, sids, handler):
        await _only_the_end_is_checked(
            engine, seed, sids, handler, "hooks-provisional", seed.d1_summary(False), {"hooks": seed.reopen_hooks()}
        )

    await _run(scenario)


# -- the end of a session on its own day: the last real activity, whatever lifecycle record follows it -------------------


async def _assert_reported_range(handler, seed: _Reopen, sid: str, start: datetime, end: datetime) -> None:
    low, high = _expected_ms(start, end)
    durations = await _durations(handler, seed.d1_day(), sid)
    assert all(low <= d <= high for d in durations), f"list/detail duration {durations} ms outside {(low, high)}"


@pytest.mark.asyncio
async def test_a_session_end_hook_seconds_after_the_last_tool_does_not_extend_the_session():
    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("end-hook-lag")
        sids.append(sid)
        last_tool = seed.d1 + timedelta(minutes=46, seconds=5)
        hooks = [
            ("agent.session.start", seed.d1),
            ("agent.prompt.submit", seed.d1 + timedelta(seconds=10)),
            ("agent.tool.start", seed.d1 + timedelta(minutes=46)),
            ("agent.tool.end", last_tool),
            ("agent.session.end", last_tool + timedelta(seconds=20)),
        ]
        await seed.seed(engine, sid, hooks=hooks)
        await _assert_reported_range(handler, seed, sid, seed.d1, last_tool)

    await _run(scenario)


@pytest.mark.asyncio
async def test_a_subagent_that_stops_after_its_last_record_keeps_the_session_running_until_the_stop():
    """The stop of a subagent closes work that sent no record for a while (a forked skill of 40 s), so it is the end of
    that work, unlike the session's own end hook that follows it."""

    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("subagent-stop")
        sids.append(sid)
        started = seed.d1 + timedelta(minutes=30)
        stopped = started + timedelta(seconds=40)
        hooks = [
            ("agent.session.start", seed.d1),
            ("agent.prompt.submit", seed.d1 + timedelta(seconds=10)),
            ("agent.subagent.start", started),
            ("agent.subagent.stop", stopped),
            ("agent.session.end", stopped + timedelta(seconds=20)),
        ]
        await seed.seed(engine, sid, hooks=hooks)
        await _assert_reported_range(handler, seed, sid, seed.d1, stopped)

    await _run(scenario)


@pytest.mark.asyncio
async def test_a_session_with_lifecycle_hooks_and_usage_only_ends_at_its_last_request():
    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("lifecycle-and-usage")
        sids.append(sid)
        hooks = [("agent.session.start", seed.d1), ("agent.session.end", seed.last_d1 + timedelta(seconds=20))]
        await seed.seed(engine, sid, hooks=hooks, usage=seed.d1_usage())
        await _assert_reported_range(handler, seed, sid, seed.d1, seed.last_d1)

    await _run(scenario)


@pytest.mark.asyncio
async def test_an_api_request_before_the_first_hook_still_starts_the_session():
    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("request-before-hook")
        sids.append(sid)
        first_request = seed.d1 - timedelta(seconds=30)
        await seed.seed(
            engine, sid, hooks=seed.d1_hooks(), usage=seed.d1_usage(), logs=[("api_request", 1, first_request, None)]
        )
        detail, _, _ = await handler.get_session_detail(sid, scope=EXCLUDE_SCOPE)
        assert _at(detail["start_time"]) == first_request
        await _assert_reported_range(handler, seed, sid, first_request, seed.last_d1)

    await _run(scenario)


@pytest.mark.asyncio
async def test_a_stop_summary_with_a_cancelled_session_end_keeps_the_work_after_it():
    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("cancelled-session-end")
        sids.append(sid)
        last_stop = seed.d1 + timedelta(minutes=46, seconds=5)
        summary = ("agent.session.summary", last_stop, seed.d1_summary(False)[0][2] | {"ended_at": _iso(last_stop)})
        hooks = [h for h in seed.d1_hooks() if h[0] != "agent.session.end"] + [summary]
        await seed.seed(engine, sid, hooks=hooks, usage=seed.d1_usage())
        # The last request came after the last Stop snapshot and no SessionEnd followed it.
        await _assert_reported_range(handler, seed, sid, seed.d1, seed.last_d1)

    await _run(scenario)


@pytest.mark.asyncio
async def test_a_session_with_no_stored_end_is_read_through_every_later_row_of_its_work():
    """Both stored end columns can be NULL (a session not rolled up yet, or one with no real-work hook and no summary
    end). The reader then looks for real-work OTel rows after the start without an upper bound, as it did before the
    reopen work: a multi-day session read before its rollup has filled the end shows its full duration."""

    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("no-stored-end")
        sids.append(sid)
        later = seed.d1 + timedelta(days=2)
        await seed.seed(
            engine,
            sid,
            hooks=[("agent.session.start", seed.d1)],
            usage=seed.d1_usage(),
            logs=[
                ("api_request", 1, seed.d1 + timedelta(seconds=20), None),
                ("api_request", 1, later, ""),
                # a startup record after the request is still not work
                ("startup", 0, later + timedelta(hours=3), None),
            ],
        )
        async with engine.acquire() as conn:
            await conn.execute(
                "UPDATE session_dims SET last_event_at = NULL, ended_at = NULL WHERE session_id = $1", sid
            )
        detail, _, _ = await handler.get_session_detail(sid, scope=EXCLUDE_SCOPE)
        low, high = _expected_ms(seed.d1, later)
        assert low <= detail["duration_ms"] <= high, f"{detail['duration_ms']} ms outside {(low, high)}"

    await _run(scenario)


# -- a continued session (a new session id that starts from a copy of an older one) ------------------------------------
# The up-to-date client sends a continuation only its own work. These tests pin what the backend then reports.


@pytest.mark.asyncio
@pytest.mark.parametrize("hours_after_the_end", [2, 5], ids=["same-utc-day", "next-utc-day"])
async def test_a_reopen_less_than_a_day_after_the_end_does_not_extend_or_list_the_session(hours_after_the_end):
    """The reader looks for OTel rows up to a day after the stored end: startup records and `/exit` of a quick reopen
    must not be taken for activity there either, and the user's last active time must stay on the real work."""

    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("within-a-day")
        sids.append(sid)
        reopen = seed.last_d1 + timedelta(hours=hours_after_the_end)
        await seed.seed(engine, sid, hooks=seed.d1_hooks(), usage=seed.d1_usage())
        await seed.seed(
            engine,
            sid,
            hooks=[(t, reopen + timedelta(seconds=i)) for i, t in enumerate(_REOPEN_HOOKS)],
            logs=[(n, 0, reopen + timedelta(seconds=44 + i), None) for i, n in enumerate(_STARTUP_RECORDS)]
            + [("user_prompt", 2, reopen + timedelta(minutes=1), "exit")],
        )
        problems = []
        low, high = _expected_ms(seed.d1, seed.last_d1)
        durations = await _durations(handler, seed.d1_day(), sid)
        if not all(low <= d <= high for d in durations):
            problems.append(f"list/detail duration {durations} ms outside {(low, high)}")
        next_day = datetime.combine(reopen.date() + timedelta(days=1), time(0), tzinfo=_UTC)
        if reopen.date() != seed.d1.date():
            windows = {
                "reopen-day": seed.window(next_day - timedelta(days=1), next_day - timedelta(microseconds=1)),
                "around-the-reopen": seed.window(reopen - timedelta(minutes=10), reopen + timedelta(minutes=10)),
            }
            on_reopen_day = {name: await _endpoints(handler, f) for name, f in windows.items()}
            # D1's day is the reopen's day when it is the same UTC day; only a different day must report nothing.
            if on_reopen_day != {name: _EMPTY for name in windows}:
                problems.append(f"reported on the reopen day: {on_reopen_day}")
        last_active = await PostgresCliAnalyticsReader(engine).get_users_last_active(seed.d1_day())
        latest = max((r["last_active"] for r in last_active), default=None)
        if latest is None or _naive(latest) > _naive(seed.last_d1 + _END_HOOK_LAG):
            problems.append(f"users last active {latest} is after the last D1 activity {seed.last_d1}")
        stored = await seed.stored(engine, sid)
        assert not problems, "; ".join(problems) + f"; stored session_dims: {stored}"

    await _run(scenario)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def _tokens_by_session(engine: AnalyticsPgEngine, f: LocalAnalyticsFilter) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in await PostgresCliAnalyticsReader(engine).get_session_model_costs(f):
        out[row["session_id"]] = out.get(row["session_id"], 0) + int(row["input_tokens"]) + int(row["output_tokens"])
    return out


async def _parent_and_continuation(
    engine, seed: _Reopen, sids: list[str], *, own_work: bool
) -> tuple[str, str, datetime]:
    """A parent that worked, and a continuation of it opened 13 minutes after the parent's start."""
    parent, child = seed.sid("parent"), seed.sid("continuation")
    sids += [parent, child]
    opened = seed.d1 + timedelta(minutes=13)
    await seed.seed(engine, parent, hooks=seed.d1_hooks(), usage=seed.d1_usage())
    hooks = [("agent.session.start", opened), ("agent.session.env", opened + timedelta(seconds=1))]
    kwargs: dict = {
        "logs": [(name, 0, opened + timedelta(seconds=2 + i), None) for i, name in enumerate(_STARTUP_RECORDS)]
    }
    if own_work:
        first = opened + timedelta(seconds=10)
        summary = {"is_final": True, "started_at": first.isoformat().replace("+00:00", "Z")}
        hooks += [("agent.prompt.submit", first), ("agent.session.summary", first + timedelta(seconds=30), summary)]
        kwargs["usage"] = [(first + timedelta(seconds=10), "c-r1")]
    else:
        hooks.append(("agent.session.end", opened + timedelta(seconds=9)))
    await seed.seed(engine, child, hooks=hooks, **kwargs)
    return parent, child, opened


@pytest.mark.asyncio
async def test_continuation_with_no_work_of_its_own_keeps_its_own_start_and_reports_nothing():
    async def scenario(engine, seed, sids, handler):
        parent, child, opened = await _parent_and_continuation(engine, seed, sids, own_work=False)
        assert await _endpoints(handler, seed.d1_day()) == _listed(parent)
        assert await _tokens_by_session(engine, seed.d1_day()) == {parent: 2 * (1000 + 10)}
        detail, _, _ = await handler.get_session_detail(child, scope=EXCLUDE_SCOPE)
        assert _at(detail["start_time"]) == opened
        assert not detail.get("turns")
        assert not detail.get("tool_call_count")

    await _run(scenario)


@pytest.mark.asyncio
async def test_continuation_with_own_work_reports_only_its_own_requests():
    async def scenario(engine, seed, sids, handler):
        parent, child, opened = await _parent_and_continuation(engine, seed, sids, own_work=True)
        assert await _tokens_by_session(engine, seed.d1_day()) == {parent: 2 * (1000 + 10), child: 1000 + 10}
        detail, _, _ = await handler.get_session_detail(child, scope=EXCLUDE_SCOPE)
        assert _at(detail["start_time"]) == opened

    await _run(scenario)


@pytest.mark.asyncio
async def test_each_request_is_counted_once_in_the_overview_user_and_repository_totals():
    async def scenario(engine, seed, sids, handler):
        parent, child, _ = await _parent_and_continuation(engine, seed, sids, own_work=True)
        window = seed.d1_day()
        overview, _, _ = await handler.get_overview(window)
        users, _ = await handler.get_users(window, None, None)
        repositories, _ = await handler.get_repositories(window, 0, 1000, False)
        assert overview["kpis"]["total_sessions"] == 2
        assert overview["kpis"]["total_input_tokens"] == 3 * 1000  # three distinct requests, none twice
        assert sum(r["input_tokens"] for r in users["rows"]) == 3 * 1000
        assert sum(r["session_count"] for r in repositories["rows"]) == 2

    await _run(scenario)


# -- guards for the window rule: a session with real activity in or after its start keeps the listing it has --------------


@pytest.mark.asyncio
async def test_a_session_started_inside_a_sub_day_window_with_activity_after_the_window_end_is_listed_only_for_a_window_with_the_activity():
    # EPMCDME-16496 AC4 review: work stamped after the window's end does not admit the window (started 00:00, first
    # prompt 00:20: not in 23:59-00:05, listed for a window that holds the prompt).
    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("sub-day")
        sids.append(sid)
        work = seed.d2 + timedelta(minutes=20)
        await seed.seed(
            engine,
            sid,
            hooks=[("agent.session.start", seed.d2), ("agent.prompt.submit", work)],
            usage=[(work + timedelta(seconds=10), "w-r1")],
        )
        before_the_work = seed.window(seed.d2 - timedelta(minutes=1), seed.d2 + timedelta(minutes=5))
        assert (await _endpoints(handler, before_the_work))["sessions"] == []
        with_the_work = seed.window(seed.d2 - timedelta(minutes=1), seed.d2 + timedelta(minutes=25))
        assert (await _endpoints(handler, with_the_work))["sessions"] == [sid]

    await _run(scenario)


@pytest.mark.asyncio
async def test_a_session_started_at_2359_utc_with_next_day_activity_is_listed_for_both_days():
    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("midnight")
        sids.append(sid)
        midnight = datetime.combine(seed.d1.date() + timedelta(days=1), time(0), tzinfo=_UTC)
        work = midnight + timedelta(seconds=30)
        await seed.seed(
            engine,
            sid,
            hooks=[("agent.session.start", midnight - timedelta(minutes=1)), ("agent.prompt.submit", work)],
            usage=[(work + timedelta(seconds=10), "m-r1")],
        )
        day_one = seed.window(midnight - timedelta(days=1), midnight - timedelta(microseconds=1))
        day_two = seed.window(midnight, midnight + timedelta(days=1) - timedelta(microseconds=1))
        assert (await _endpoints(handler, day_one))["sessions"] == [sid]
        assert (await _endpoints(handler, day_two))["sessions"] == [sid]

    await _run(scenario)


# -- same-day reopens and the raw check of the window rule --------------------------------------------------------------


def _same_day(seed: _Reopen, hour: int, minute: int = 0) -> datetime:
    """A time on D1's UTC day, so a window around it is a sub-day window of the day the work was done."""
    return datetime.combine(seed.d1.date(), time(hour, minute), tzinfo=_UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "listed"),
    [("exit", False), ("cost", False), (None, True)],
    ids=["exit-only-reopen", "cost-only-reopen", "typed-prompt-reopen"],
)
async def test_a_same_day_reopen_with_only_exit_is_not_listed_in_a_sub_day_window(command, listed):
    """`/exit` is a user_prompt record naming a built-in command, not a prompt: it must not admit a session whose work
    was done earlier the same UTC day (the rows of that day exist, so only the raw check can tell)."""

    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("same-day-exit")
        sids.append(sid)
        morning, reopen = _same_day(seed, 8), _same_day(seed, 14)
        await seed.seed(
            engine,
            sid,
            hooks=[
                ("agent.session.start", morning),
                ("agent.prompt.submit", morning + timedelta(seconds=10)),
                ("agent.tool.start", morning + timedelta(minutes=5)),
                ("agent.tool.end", morning + timedelta(minutes=5, seconds=5)),
                ("agent.session.end", morning + timedelta(minutes=6)),
            ],
            usage=[(morning + timedelta(seconds=20), "m-r1")],
        )
        await seed.seed(
            engine,
            sid,
            hooks=[(t, reopen + timedelta(seconds=i)) for i, t in enumerate(_REOPEN_HOOKS)],
            logs=[("user_prompt", 2, reopen + timedelta(minutes=1), command)],
        )
        around = seed.window(reopen - timedelta(minutes=10), reopen + timedelta(minutes=10))
        assert (await _endpoints(handler, around))["sessions"] == ([sid] if listed else [])

    await _run(scenario)


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["hour", "24h"])
@pytest.mark.parametrize("real_work", [False, True], ids=["lifecycle-only-reopen", "reopen-with-a-request"])
async def test_a_same_day_reopen_with_no_dimension_hook_before_it_is_listed_only_for_real_work_in_the_window(
    window, real_work
):
    """D1's work reached the backend as usage and OTel rows only, so the reopen's session.start becomes the start of the
    session and lands inside the window; the day rows of that day are no proof of activity at or after the window's
    start, the raw records are."""

    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("same-day-no-hook")
        sids.append(sid)
        morning, reopen = _same_day(seed, 8), _same_day(seed, 14)
        await seed.seed(
            engine,
            sid,
            usage=[(morning, "n-r1"), (morning + timedelta(minutes=5), "n-r2")],
            logs=[("api_request", 1, morning, None)],
            spans=[(1, morning + timedelta(minutes=2))],
        )
        await seed.seed(
            engine,
            sid,
            hooks=[(t, reopen + timedelta(seconds=i)) for i, t in enumerate(_REOPEN_HOOKS)],
            usage=[(reopen + timedelta(minutes=2), "n-r3")] if real_work else (),
        )
        if window == "hour":
            f = seed.window(reopen - timedelta(minutes=10), reopen + timedelta(minutes=10))
        else:
            f = seed.window(_same_day(seed, 13), _same_day(seed, 13) + timedelta(hours=24))
        assert (await _endpoints(handler, f))["sessions"] == ([sid] if real_work else [])
        # The day of the work still lists it.
        whole_day = seed.window(_same_day(seed, 0), _same_day(seed, 0) + timedelta(days=1) - timedelta(microseconds=1))
        assert (await _endpoints(handler, whole_day))["sessions"] == [sid]

    await _run(scenario)


@pytest.mark.asyncio
async def test_a_window_longer_than_a_day_keeps_the_day_rule_for_a_first_day_row():
    """Accepted: only a window of a day or less asks for a raw record to confirm a first-day row. A Last 7 days window
    that starts at 13:00 still lists a session whose only work is earlier the same UTC day and whose start was moved to
    14:00 by an idle reopen (a session with no work in the window at all is out of scope of the day-granular rule)."""

    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("seven-days")
        sids.append(sid)
        morning, reopen = _same_day(seed, 8), _same_day(seed, 14)
        await seed.seed(engine, sid, usage=[(morning, "w-r1")], logs=[("api_request", 1, morning, None)])
        await seed.seed(engine, sid, hooks=[(t, reopen + timedelta(seconds=i)) for i, t in enumerate(_REOPEN_HOOKS)])
        f = seed.window(_same_day(seed, 13), _same_day(seed, 13) + timedelta(days=7))
        assert (await _endpoints(handler, f))["sessions"] == [sid]

    await _run(scenario)


@pytest.mark.asyncio
async def test_users_last_active_stays_on_real_work_when_the_session_was_active_again_after_the_window():
    """The reader takes the stored `last_event_at` when it is inside the window and reads the raw hooks otherwise: both
    must ignore the lifecycle hooks (a session.end seconds after the last tool call)."""

    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("last-active-later")
        sids.append(sid)
        await seed.seed(engine, sid, hooks=seed.d1_hooks(), usage=seed.d1_usage())
        work = seed.d2 + timedelta(seconds=30)
        await seed.seed(
            engine, sid, hooks=[("agent.session.start", seed.d2), ("agent.prompt.submit", work)], usage=[(work, "l-r1")]
        )
        last_tool = seed.d1 + timedelta(minutes=46, seconds=5)  # the last D1 activity hook; session.end follows it
        last_active = await PostgresCliAnalyticsReader(engine).get_users_last_active(seed.d1_day())
        latest = max(_naive(r["last_active"]) for r in last_active)
        assert latest == _naive(last_tool), f"users last active {latest}, the last D1 activity hook is {last_tool}"

    await _run(scenario)


# -- the start of a continuation whose summary carries the parent's first line ------------------------------------------


@pytest.mark.asyncio
async def test_a_continuation_summary_that_carries_the_parents_first_line_as_its_start_is_reported_as_that_start():
    """Accepted: a client up to 0.16.6 copies the parent's history into the continuation, and the summary's start is the
    parent's first line. The earlier start wins on purpose (the merge never moves a start later), so the backend
    reports it; the corrected start comes from the new client and is pinned by the test with own work above."""

    async def scenario(engine, seed, sids, handler):
        parent, child = seed.sid("parent"), seed.sid("continuation")
        sids += [parent, child]
        opened = seed.d1 + timedelta(minutes=13)
        first = opened + timedelta(seconds=10)
        await seed.seed(engine, parent, hooks=seed.d1_hooks(), usage=seed.d1_usage())
        summary = {"is_final": True, "started_at": _iso(seed.d1), "ended_at": _iso(first + timedelta(seconds=10))}
        await seed.seed(
            engine,
            child,
            hooks=[
                ("agent.session.start", opened),
                ("agent.prompt.submit", first),
                ("agent.session.summary", first + timedelta(seconds=30), summary),
            ],
            usage=[(first + timedelta(seconds=10), "c-r1")],
        )
        detail, _, _ = await handler.get_session_detail(child, scope=EXCLUDE_SCOPE)
        assert _at(detail["start_time"]) == seed.d1
        # Its own requests are still its own: the copied history is not counted twice within the session.
        assert (await _tokens_by_session(engine, seed.d1_day()))[child] == 1000 + 10

    await _run(scenario)


# -- review round 3: the closing hook of a hook-only client, local commands and their interaction span ------------------


@pytest.mark.asyncio
async def test_a_hook_only_session_ends_at_its_closing_stop_hook():
    """A client with no usage request and no summary (GitHub Copilot Chat) closes each turn with `agent.session.stop`:
    the model's answer after the last prompt or tool hook is work, so the duration and Users "last active" reach it."""

    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("hook-only-stop")
        sids.append(sid)
        begin = seed.d2
        stop = begin + timedelta(seconds=180)
        await seed.seed(
            engine,
            sid,
            hooks=[
                ("agent.session.start", begin),
                ("agent.prompt.submit", begin + timedelta(seconds=5)),
                ("agent.session.stop", stop),
            ],
        )
        f = seed.window(begin - timedelta(minutes=10), begin + timedelta(minutes=10))
        low, high = _expected_ms(begin, stop)
        durations = await _durations(handler, f, sid)
        assert all(low <= d <= high for d in durations), f"{durations} ms outside {(low, high)}"
        last_active = await PostgresCliAnalyticsReader(engine).get_users_last_active(f)
        assert abs((max(_naive(r["last_active"]) for r in last_active) - _naive(stop)).total_seconds()) < 2

    await _run(scenario)


_CMD_SPAN = 7  # the span id that links a command's prompt record, its model request and its interaction span


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["cost-command", "exit-with-interaction-span", "cost-with-interaction-span"])
async def test_a_reopen_with_only_a_local_command_leaves_the_session_unchanged(variant):
    """A local command (`/cost`, `/status`, `/exit`) sends a user_prompt record and an interaction span but no model
    request: neither lists the session, and the end stays at the last real activity (the rule of `_REAL_TURN`)."""

    async def scenario(engine, seed, sids, handler):
        sid = seed.sid(variant)
        sids.append(sid)
        await seed.seed(engine, sid, hooks=seed.d1_hooks(), usage=seed.d1_usage())
        typed = seed.d2 + timedelta(minutes=2)
        command = "exit" if variant.startswith("exit") else "cost"
        linked = variant.endswith("interaction-span")
        await seed.seed(
            engine,
            sid,
            hooks=seed.reopen_hooks(),
            logs=[("user_prompt", 2, typed, command, *([_CMD_SPAN] if linked else []))],
            spans=[(3, typed, _CMD_SPAN)] if linked else [],
        )
        await _assert_unchanged(handler, engine, seed, sid)

    await _run(scenario)


@pytest.mark.asyncio
async def test_a_command_that_sends_a_model_request_is_real_work():
    """A custom command that expands into a prompt has a model request on the same span: it lists the session and
    moves its end, whatever the command is named."""

    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("command-with-request")
        sids.append(sid)
        await seed.seed(engine, sid, hooks=seed.d1_hooks(), usage=seed.d1_usage())
        typed = seed.d2 + timedelta(minutes=2)
        await seed.seed(
            engine,
            sid,
            hooks=seed.reopen_hooks(),
            logs=[
                ("user_prompt", 2, typed, "review", _CMD_SPAN),
                ("api_request", 1, typed + timedelta(seconds=5), None, _CMD_SPAN),
            ],
            spans=[(3, typed, _CMD_SPAN)],
        )
        for name, f in seed.d2_windows().items():
            assert (await _endpoints(handler, f))["sessions"] == [sid], name

    await _run(scenario)


# -- review round 4: a turn that ends in an error, and the interaction span of a command whatever arrives first ----------


@pytest.mark.asyncio
async def test_a_hook_only_session_ends_at_the_turn_error_that_closes_its_last_turn():
    """A hook-only client (GitHub Copilot Chat) can end a turn with `agent.turn.error` and no stop hook: the failed
    turn is work, so the duration and Users "last active" reach it."""

    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("hook-only-turn-error")
        sids.append(sid)
        begin = seed.d2
        error = begin + timedelta(seconds=120)
        await seed.seed(
            engine,
            sid,
            hooks=[
                ("agent.session.start", begin),
                ("agent.prompt.submit", begin + timedelta(seconds=5)),
                ("agent.turn.error", error),
            ],
        )
        f = seed.window(begin - timedelta(minutes=10), begin + timedelta(minutes=10))
        low, high = _expected_ms(begin, error)
        durations = await _durations(handler, f, sid)
        assert all(low <= d <= high for d in durations), f"{durations} ms outside {(low, high)}"
        last_active = await PostgresCliAnalyticsReader(engine).get_users_last_active(f)
        assert abs((max(_naive(r["last_active"]) for r in last_active) - _naive(error)).total_seconds()) < 2

    await _run(scenario)


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["span-then-prompt", "prompt-then-span", "prompt-without-span-id"])
async def test_the_interaction_span_of_a_local_command_is_not_real_work_whichever_record_is_stored_first(order):
    """The rollup sees the records in the order they are exported: an interaction span stored before the prompt record
    that names its command must not move the end (the stored end never goes back), and a command record that carries no
    span id gives the span no evidence either. A span is real work only on evidence of a typed prompt or a model
    request on it. With no span id on the record the turn rule of the rollup (`turns_daily`) cannot link the command to
    its span either (R21), so only the end is pinned there, not the listing of the reopen's whole day."""

    async def scenario(engine, seed, sids, handler):
        sid = seed.sid(order)
        sids.append(sid)
        await seed.seed(engine, sid, hooks=seed.d1_hooks(), usage=seed.d1_usage())
        typed = seed.d2 + timedelta(minutes=2)
        span = [(3, typed, _CMD_SPAN)]
        linked = [("user_prompt", 2, typed, "cost", _CMD_SPAN)]
        if order == "span-then-prompt":
            await seed.seed(engine, sid, hooks=seed.reopen_hooks(), spans=span)
            await seed.seed(engine, sid, logs=linked)
        elif order == "prompt-then-span":
            await seed.seed(engine, sid, hooks=seed.reopen_hooks(), logs=linked)
            await seed.seed(engine, sid, spans=span)
        else:
            await seed.seed(
                engine, sid, hooks=seed.reopen_hooks(), logs=[("user_prompt", 2, typed, "exit")], spans=span
            )
            await _assert_reported_range(handler, seed, sid, seed.d1, seed.last_d1)
            stored = await seed.stored(engine, sid)
            assert stored["ended_at"] <= seed.last_d1 + _END_HOOK_LAG, stored
            return
        await _assert_unchanged(handler, engine, seed, sid)

    await _run(scenario)


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["span-then-records", "records-then-span"])
async def test_a_typed_prompt_and_its_interaction_span_are_real_work_whichever_record_is_stored_first(order):
    """The same two records of a prompt typed at the reopen, with a model request on its span: the session is listed
    on D2 and its end reaches the request, in either storage order."""

    async def scenario(engine, seed, sids, handler):
        sid = seed.sid(order)
        sids.append(sid)
        await seed.seed(engine, sid, hooks=seed.d1_hooks(), usage=seed.d1_usage())
        typed = seed.d2 + timedelta(minutes=2)
        answered = typed + timedelta(seconds=30)
        span = [(3, typed, _CMD_SPAN)]
        logs = [
            ("user_prompt", 2, typed, None, _CMD_SPAN),
            ("api_request", 1, answered, None, _CMD_SPAN),
        ]
        if order == "span-then-records":
            await seed.seed(engine, sid, hooks=seed.reopen_hooks(), spans=span)
            await seed.seed(engine, sid, logs=logs)
        else:
            await seed.seed(engine, sid, hooks=seed.reopen_hooks(), logs=logs)
            await seed.seed(engine, sid, spans=span)
        for name, f in seed.d2_windows().items():
            assert (await _endpoints(handler, f))["sessions"] == [sid], name
        low, high = _expected_ms(seed.d1, answered)
        durations = await _durations(handler, seed.d2_windows()["d2-day"], sid)
        assert all(low <= d <= high for d in durations), f"{durations} ms outside {(low, high)}"

    await _run(scenario)


@pytest.mark.asyncio
@pytest.mark.parametrize("retention_days", [5, 90], ids=["window-past-the-raw-retention", "window-inside-it"])
async def test_a_sub_day_window_past_the_raw_retention_lists_a_session_by_its_day_rows(monkeypatch, retention_days):
    """Raw rows are purged after the raw retention, so a window of a day or less that starts mid-day and lies wholly
    past it cannot ask for a raw record: the day rows decide, as they did before the raw check. Inside the retention
    the raw rows exist and the check stays (the purge is simulated by deleting them)."""

    async def scenario(engine, seed, sids, handler):
        sid = seed.sid("raw-purged")
        sids.append(sid)
        morning = _same_day(seed, 8)
        await seed.seed(
            engine,
            sid,
            hooks=[("agent.session.start", morning), ("agent.prompt.submit", morning + timedelta(seconds=10))],
            usage=[(morning + timedelta(seconds=20), "p-r1")],
        )
        async with engine.acquire() as conn:
            for table in ("hook_events", "usage_requests", "log_events", "spans", "metric_points"):
                await conn.execute(f"DELETE FROM {table} WHERE session_id = $1", sid)  # noqa: S608 - fixed names
        monkeypatch.setattr(reader_module, "_RAW_RETENTION_DAYS", retention_days)
        f = seed.window(_same_day(seed, 7), _same_day(seed, 9))
        listed = (await _endpoints(handler, f))["sessions"]
        assert listed == ([sid] if retention_days == 5 else [])

    await _run(scenario)


async def _purge_idle_sessions(engine: AnalyticsPgEngine, retention_days: int) -> None:
    """The idle-session purge of the maintenance job, for a session retention of `retention_days`."""
    cutoff = datetime.now(_UTC).date() - timedelta(days=retention_days)
    async with engine.acquire() as conn:
        await session_purge.purge_idle_sessions(
            conn,
            "($1::date::timestamp AT TIME ZONE 'UTC')",
            f"(($1::date + {retention_days + 1})::timestamp AT TIME ZONE 'UTC')",
            cutoff,
            100,
            maintenance.MaintenanceReport(),
        )


@pytest.mark.asyncio
async def test_the_retention_clock_of_a_legacy_session_follows_its_last_real_work_not_a_later_lifecycle_hook():
    """A legacy session (no owner) is idle from its last real work: a `session.end` hook sent long after it by a client
    that was reopened and did nothing no longer keeps the session past the retention (intended, R9). A session whose
    last real work is inside the retention is kept whatever it sent before."""

    async def scenario(engine, seed, sids, handler):
        old, recent = seed.sid("idle-since-the-work"), seed.sid("working-inside-retention")
        sids.extend([old, recent])
        retention = 200
        worked = seed.d2 - timedelta(days=260)
        for sid, work in ((old, worked), (recent, seed.d2 - timedelta(days=150))):
            await seed.seed(
                engine,
                sid,
                hooks=[
                    ("agent.session.start", work),
                    ("agent.prompt.submit", work + timedelta(seconds=10)),
                    ("agent.tool.start", work + timedelta(minutes=5)),
                    ("agent.tool.end", work + timedelta(minutes=5, seconds=5)),
                ],
            )
        # the idle reopen of the old session: only a lifecycle hook, inside the retention
        await seed.seed(engine, old, hooks=[("agent.session.end", seed.d2 - timedelta(days=150))])
        before = await seed.stored(engine, old)
        assert before["last_event_at"] < seed.d2 - timedelta(days=retention), before
        assert before["ended_at"] < seed.d2 - timedelta(days=retention), before

        await _purge_idle_sessions(engine, retention)

        assert not await seed.stored(engine, old), "the session idle since its last real work is purged"
        assert await seed.stored(engine, recent), "a session with real work inside the retention is kept"

    await _run(scenario)
