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

"""Live-Postgres behavioral checks for EPMCDME-15506's session activity range.

`session_dims.started_at` / `last_event_at` are client-stamped hook times and lag the session's OTel
events. These write a session whose api_request precedes its first dimension hook and whose tool span
follows its last one into the real local analytics Postgres, then read it back through the real,
unmocked reader and handler: the reported start, end and duration cover every event, while window
membership still follows `started_at`. Skipped (not failed) when the local analytics Postgres is
unreachable.
"""

import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler

# pytest.ini forces PG_URL to a dead port for the whole suite; TEST_LOCAL_PG_URL points this file at
# a real local analytics Postgres (default: the docker-compose `postgres` service's credentials).
_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")

_EMAIL = "range@example.com"
_SESSION = "test-epmcdme-15506-live-range"
_HOOKS_ONLY = "test-epmcdme-15506-live-hooks-only"

_SESSION_DIMS_SQL = """
    INSERT INTO session_dims (session_id, started_at, last_event_at, jwt_email, activity_started_at, ended_at)
    VALUES ($1, $2, $3, $4, $5, $6)
"""
_COST_DAILY_SQL = """
    INSERT INTO cost_daily
        (day, session_id, user_email, model_name, query_source, cost_usd,
         input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, api_call_count)
    VALUES ($1, $2, $3, 'claude-opus-5-5', 'repl_main_thread', 1.0, 100, 10, 0, 0, 1)
"""
_LOG_EVENTS_SQL = """
    INSERT INTO log_events
        (ts, session_id, event_kind, trace_id, span_id,
         cost_usd, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
         severity, resource_id)
    VALUES ($1, $2, 1, ''::bytea, ''::bytea, 1.0, 100, 10, 0, 0, 0, 1)
"""
_SPANS_SQL = """
    INSERT INTO spans
        (ts, duration_ns, trace_id, span_id, parent_span_id, span_kind, span_name,
         session_id, subagent_type, skill_name, tool_name, tool_use_id, attrs, success, status_code, resource_id)
    VALUES ($1, 1000000, ''::bytea, 'span'::bytea, ''::bytea, 1, 'claude_code.tool', $2, '', '', 'Read', 'tu-read', NULL, 'true', 0, 1)
"""


async def _get_engine() -> AnalyticsPgEngine | None:
    try:
        settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL))
        engine = AnalyticsPgEngine(settings)
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return engine
    except (OSError, asyncpg.PostgresError) as exc:  # no reachable database (refused, wrong server, bad auth): skip
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
        return None


async def _cleanup(engine: AnalyticsPgEngine, *session_ids: str) -> None:
    async with engine.acquire() as conn:
        for table in ("session_dims", "cost_daily", "log_events", "spans"):
            await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", list(session_ids))


def _hook_time() -> datetime:
    """The first dimension hook of the seeded sessions: whole seconds, an hour ago (inside today's partitions)."""
    return datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)


async def _seed_session(engine: AnalyticsPgEngine, session_id: str, t: datetime, *, otel: bool) -> None:
    """Dimension hooks at t and t+10 min; with `otel`, an api_request at t-12 s and a tool span at t+11 min."""
    async with engine.acquire() as conn:
        await conn.execute(_SESSION_DIMS_SQL, session_id, t, t + timedelta(minutes=10), _EMAIL, None, None)
        await conn.execute(_COST_DAILY_SQL, t.date(), session_id, _EMAIL)
        if otel:
            await conn.execute(_LOG_EVENTS_SQL, t - timedelta(seconds=12), session_id)
            await conn.execute(_SPANS_SQL, t + timedelta(minutes=11), session_id)


def _naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_reported_range_covers_events_outside_the_hook_times():
    """AC #1, #3, #4, #5: an api_request 12 s before the first hook moves the reported start, a tool span
    after the last hook moves the end, and every duration consumer (list, Users Avg) reports that range."""
    engine = await _get_engine()
    t = _hook_time()
    f = LocalAnalyticsFilter(start_dt=_naive(t - timedelta(hours=1)), end_dt=_naive(t + timedelta(hours=1)))
    try:
        await _cleanup(engine, _SESSION)
        await _seed_session(engine, _SESSION, t, otel=True)
        reader = PostgresCliAnalyticsReader(engine)

        durations = {r["session_id"]: r["duration_ms"] for r in await reader.get_session_durations(f)}
        assert durations[_SESSION] == 672_000

        facts = next(r for r in await reader.get_session_cost_facts(f) if r["session_id"] == _SESSION)
        assert facts["started_at"] == _naive(t - timedelta(seconds=12))
        assert facts["last_event_at"] == _naive(t + timedelta(minutes=11))
        assert facts["duration_ms"] == 672_000

        assert _naive(t - timedelta(seconds=12)) in [r["started_at"] for r in await reader.get_session_start_times(f)]

        users = await LocalAnalyticsHandler(reader).get_users(
            LocalAnalyticsFilter(start_dt=f.start_dt, end_dt=f.end_dt, users=[_EMAIL]), page=None, per_page=None
        )
        assert users["avg_session_duration_ms"] == 672_000
    finally:
        await _cleanup(engine, _SESSION)
        await engine.close()


@pytest.mark.asyncio
async def test_window_membership_still_follows_started_at():
    """The earlier reported start does not move the session into a window that ends before its first hook."""
    engine = await _get_engine()
    t = _hook_time()
    try:
        await _cleanup(engine, _SESSION)
        await _seed_session(engine, _SESSION, t, otel=True)
        reader = PostgresCliAnalyticsReader(engine)

        inside = LocalAnalyticsFilter(start_dt=_naive(t - timedelta(seconds=6)), end_dt=_naive(t + timedelta(hours=1)))
        before = LocalAnalyticsFilter(start_dt=_naive(t - timedelta(hours=1)), end_dt=_naive(t - timedelta(seconds=1)))
        assert _SESSION in {r["session_id"] for r in await reader.get_session_durations(inside)}
        assert _SESSION not in {r["session_id"] for r in await reader.get_session_durations(before)}
    finally:
        await _cleanup(engine, _SESSION)
        await engine.close()


@pytest.mark.asyncio
async def test_range_without_otel_rows_is_the_hook_times():
    """No OTel row and no activity columns: the range is the hook times, NULLs never hide them."""
    engine = await _get_engine()
    t = _hook_time()
    f = LocalAnalyticsFilter(start_dt=_naive(t - timedelta(hours=1)), end_dt=_naive(t + timedelta(hours=1)))
    try:
        await _cleanup(engine, _HOOKS_ONLY)
        await _seed_session(engine, _HOOKS_ONLY, t, otel=False)
        reader = PostgresCliAnalyticsReader(engine)

        durations = {r["session_id"]: r["duration_ms"] for r in await reader.get_session_durations(f)}
        assert durations[_HOOKS_ONLY] == 600_000
        facts = next(r for r in await reader.get_session_cost_facts(f) if r["session_id"] == _HOOKS_ONLY)
        assert facts["started_at"] == _naive(t)
    finally:
        await _cleanup(engine, _HOOKS_ONLY)
        await engine.close()


@pytest.mark.asyncio
async def test_session_detail_reports_the_activity_range():
    """AC #1, #2: Session Detail starts no later than its own first timeline event and lasts the whole range."""
    engine = await _get_engine()
    t = _hook_time()
    try:
        await _cleanup(engine, _SESSION)
        await _seed_session(engine, _SESSION, t, otel=True)

        detail, _ = await LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine)).get_session_detail(_SESSION)

        assert detail is not None
        start = datetime.fromisoformat(detail["start_time"])
        assert start == t - timedelta(seconds=12)
        assert detail["duration_ms"] == 672_000
        assert all(datetime.fromisoformat(e["timestamp"]) >= start for e in detail["events"])
    finally:
        await _cleanup(engine, _SESSION)
        await engine.close()


@pytest.mark.asyncio
async def test_session_detail_without_dims_starts_at_its_earliest_event():
    """A priced session whose dimension row is missing starts at its earliest OTel row, not at 1970-01-01."""
    engine = await _get_engine()
    t = _hook_time()
    try:
        await _cleanup(engine, _HOOKS_ONLY)
        async with engine.acquire() as conn:
            await conn.execute(_COST_DAILY_SQL, t.date(), _HOOKS_ONLY, _EMAIL)
            await conn.execute(_LOG_EVENTS_SQL, t, _HOOKS_ONLY)
            await conn.execute(_LOG_EVENTS_SQL, t + timedelta(seconds=30), _HOOKS_ONLY)

        detail, _ = await LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine)).get_session_detail(_HOOKS_ONLY)

        assert detail is not None
        assert datetime.fromisoformat(detail["start_time"]) == t
        assert detail["duration_ms"] == 30_000
    finally:
        await _cleanup(engine, _HOOKS_ONLY)
        await engine.close()


@pytest.mark.asyncio
async def test_list_and_detail_agree_on_a_row_more_than_a_day_out():
    """AC #2: one definition of the range. An OTel row two days before the first hook is outside the probe
    bound for every path, so the list, the durations and Session Detail report the same start and duration."""
    engine = await _get_engine()
    t = _hook_time()
    f = LocalAnalyticsFilter(start_dt=_naive(t - timedelta(hours=1)), end_dt=_naive(t + timedelta(hours=1)))
    try:
        await _cleanup(engine, _SESSION)
        await _seed_session(engine, _SESSION, t, otel=True)
        async with engine.acquire() as conn:
            await conn.execute(_LOG_EVENTS_SQL, t - timedelta(days=2), _SESSION)
        reader = PostgresCliAnalyticsReader(engine)
        handler = LocalAnalyticsHandler(reader)

        data, _, _ = await handler.get_sessions(f, page=0, per_page=50, sort_by="start_time", search=None)
        listed = next(s for s in data["sessions"] if s["trace_id"] == _SESSION)
        detail, _ = await handler.get_session_detail(_SESSION)

        assert detail is not None
        assert datetime.fromisoformat(listed["start_time"]) == t - timedelta(seconds=12)
        assert detail["start_time"] == listed["start_time"]
        assert detail["duration_ms"] == listed["duration_ms"] == 672_000
    finally:
        await _cleanup(engine, _SESSION)
        await engine.close()


@pytest.mark.asyncio
async def test_boundary_rows_at_exactly_cand_start_and_cand_end_do_not_widen_the_range():
    """reader.py:150-157 probes are strict (`<` / `>`) on purpose: a log_event at exactly cand_start
    and a span at exactly cand_end are "not earlier"/"not later" and must not shift the reported range."""
    engine = await _get_engine()
    t = _hook_time()
    f = LocalAnalyticsFilter(start_dt=_naive(t - timedelta(hours=1)), end_dt=_naive(t + timedelta(hours=1)))
    try:
        await _cleanup(engine, _SESSION)
        await _seed_session(engine, _SESSION, t, otel=False)
        async with engine.acquire() as conn:
            await conn.execute(_LOG_EVENTS_SQL, t, _SESSION)
            await conn.execute(_SPANS_SQL, t + timedelta(minutes=10), _SESSION)
        reader = PostgresCliAnalyticsReader(engine)

        facts = next(r for r in await reader.get_session_cost_facts(f) if r["session_id"] == _SESSION)
        assert facts["started_at"] == _naive(t)
        assert facts["last_event_at"] == _naive(t + timedelta(minutes=10))
    finally:
        await _cleanup(engine, _SESSION)
        await engine.close()
