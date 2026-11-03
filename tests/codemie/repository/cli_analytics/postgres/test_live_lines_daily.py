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

"""Value-level checks of the lines_daily source rule against a disposable analytics Postgres.

A session takes its lines from its agent.tool.end hook events when any of them carries a numeric lines_added, else
from the OTel lines metric. Telemetry goes through the real decode and ingest; only the test's own rollup
keys are recomputed and only its own sessions are deleted afterwards. Without TEST_LOCAL_PG_URL every test
is skipped; with it a test is skipped (not failed) when that Postgres is unreachable.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from codemie.repository.cli_analytics.postgres.maintenance import quote_ident
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from tests.codemie.repository.cli_analytics.support import otlp_builders as b
from tests.codemie.repository.cli_analytics.support.live_rollup_lock import live_rollup_lock

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")
# Any constant shared by the xdist workers: start() on an empty schema must run in one of them at a time.
_SCHEMA_LOCK = 0x6C696E6573

_EMAIL = "lines.live@example.com"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_TODAY = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
_YESTERDAY = _TODAY - timedelta(days=1)
_CLAUDE_LINES, _CURSOR_LINES = "claude_code.lines_of_code.count", "cursor.lines_of_code.count"
_ACTIVE_TIME = "claude_code.active_time.total"
_MODEL = "claude-opus-5-5"

# A waiting (not granted) advisory lock on a bigint key: pg_locks splits the key into classid / objid.
_LOCK_WAITERS = """SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted
    AND classid = (($1::bigint >> 32) & 4294967295)::oid AND objid = ($1::bigint & 4294967295)::oid"""
_READ_KEYS = "SELECT day, session_id, kinds, version, timeouts FROM rollup_dirty WHERE session_id = ANY($1::text[])"
_READ_LINES = """SELECT day, model_name, lines_added, lines_removed FROM lines_daily
    WHERE session_id = $1 ORDER BY day, model_name"""
_SESSION_TABLES = """
    SELECT c.relname FROM pg_class c
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'session_id' AND NOT a.attisdropped
    WHERE c.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = current_schema())
      AND c.relkind IN ('r', 'p') AND NOT c.relispartition
"""


def _session() -> str:
    return str(uuid.uuid4())


def _tool_end(session_id: str, ts: datetime, tool_name: str, tool_use_id: str, **lines: object) -> dict[str, object]:
    return {
        "type": "agent.tool.end",
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_use_id": tool_use_id,
        **lines,
    }


def _lines_metric(session_id: str, ts: datetime, added: int, removed: int, name: str = _CLAUDE_LINES) -> bytes:
    attrs = {"session.id": session_id, "user.email": _EMAIL, "model": _MODEL}
    points = [
        b.number_point(ts, added, {**attrs, "type": "added"}, start=ts),
        b.number_point(ts, removed, {**attrs, "type": "removed"}, start=ts),
    ]
    return b.metrics_request({"service.name": "claude-code"}, [b.sum_metric(name, points)]).SerializeToString()


def _active_time_metric(session_id: str, ts: datetime) -> bytes:
    point = b.number_point(ts, 30, {"session.id": session_id, "user.email": _EMAIL}, start=ts)
    return b.metrics_request({"service.name": "claude-code"}, [b.sum_metric(_ACTIVE_TIME, [point])]).SerializeToString()


@dataclass
class _Live:
    engine: AnalyticsPgEngine
    ingestor: PostgresTelemetryIngestor

    async def hooks(self, events: Sequence[dict[str, object]]) -> None:
        received_ns = (datetime.now(timezone.utc) - _EPOCH) // timedelta(microseconds=1) * 1000
        await self.ingestor.ingest_hook_events(list(events), _EMAIL, received_ns)

    async def metrics(self, body: bytes) -> None:
        await self.ingestor.ingest_otlp("metrics", body, "application/x-protobuf")

    async def refresh(self, session_id: str) -> None:
        async with self.engine.acquire() as conn:
            keys = await conn.fetch(_READ_KEYS, [session_id])
            assert keys, "ingest marked no rollup key for the session"
            assert await RollupRefresher(self.engine, batch_size=len(keys)).recompute(conn, keys) == len(keys)

    async def marked_days(self, session_id: str) -> list[int]:
        """The days (offsets from today) of the session's queued rollup keys."""
        async with self.engine.acquire() as conn:
            rows = await conn.fetch(_READ_KEYS, [session_id])
        return sorted((r["day"] - _TODAY.date()).days for r in rows)

    async def lines(self, session_id: str) -> list[tuple]:
        """lines_daily rows of the session after recomputing its queued keys: (day offset, model, added, removed)."""
        await self.refresh(session_id)
        async with self.engine.acquire() as conn:
            rows = await conn.fetch(_READ_LINES, session_id)
        return [((r["day"] - _TODAY.date()).days, r["model_name"], r["lines_added"], r["lines_removed"]) for r in rows]


@asynccontextmanager
async def _live(*sessions: str) -> AsyncIterator[_Live]:
    settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL, CLI_ANALYTICS_PG_URL=_LOCAL_PG_URL))
    engine = AnalyticsPgEngine(settings)
    try:
        try:
            lock = await asyncpg.connect(settings.dsn)
        except (OSError, asyncpg.PostgresError) as exc:  # no reachable database: skip, do not fail
            pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
        try:
            await lock.execute("SELECT pg_advisory_lock($1)", _SCHEMA_LOCK)
            await PostgresAnalyticsRuntime(engine, settings).start()
        finally:
            await lock.close()  # closing the session releases its advisory lock
        async with live_rollup_lock(engine):
            try:
                yield _Live(engine, PostgresTelemetryIngestor(engine))
            finally:
                async with engine.acquire() as conn:
                    for table in await conn.fetch(_SESSION_TABLES):
                        await conn.execute(
                            f"DELETE FROM {quote_ident(table['relname'])} WHERE session_id = ANY($1::text[])",
                            list(sessions),
                        )
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_a_session_with_tool_end_lines_takes_them_and_not_the_metric() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.hooks(
            [
                _tool_end(sid, _TODAY, "Write", "toolu_w", lines_added=5, lines_removed=0),
                _tool_end(sid, _TODAY + timedelta(minutes=1), "Edit", "toolu_e", lines_added=1, lines_removed=1),
            ]
        )
        await live.metrics(_lines_metric(sid, _TODAY, 7, 1))
        assert await live.lines(sid) == [(0, "", 6, 1)]


@pytest.mark.asyncio
async def test_a_session_with_metric_lines_only_takes_the_metric() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.metrics(_lines_metric(sid, _TODAY, 7, 1))
        assert await live.lines(sid) == [(0, _MODEL, 7, 1)]


@pytest.mark.asyncio
async def test_a_cursor_session_takes_the_cursor_metric() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.metrics(_lines_metric(sid, _TODAY, 4, 2, name=_CURSOR_LINES))
        assert await live.lines(sid) == [(0, _MODEL, 4, 2)]


@pytest.mark.asyncio
async def test_a_tool_use_id_counts_once_and_rows_without_one_each_count() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.hooks(
            [
                _tool_end(sid, _TODAY, "Write", "toolu_same", lines_added=5, lines_removed=0),
                _tool_end(sid, _TODAY + timedelta(seconds=1), "Write", "toolu_same", lines_added=5, lines_removed=0),
                _tool_end(sid, _TODAY + timedelta(seconds=2), "Edit", "", lines_added=2, lines_removed=1),
                _tool_end(sid, _TODAY + timedelta(seconds=3), "Edit", "", lines_added=2, lines_removed=1),
            ]
        )
        assert await live.lines(sid) == [(0, "", 9, 2)]


@pytest.mark.asyncio
async def test_an_earlier_copy_on_a_previous_day_moves_the_call_off_the_later_day() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.hooks([_tool_end(sid, _TODAY, "Write", "toolu_x", lines_added=5, lines_removed=0)])
        assert await live.lines(sid) == [(0, "", 5, 0)]
        await live.hooks([_tool_end(sid, _YESTERDAY, "Write", "toolu_x", lines_added=5, lines_removed=0)])
        assert await live.lines(sid) == [(-1, "", 5, 0)]


@pytest.mark.asyncio
async def test_a_late_tool_end_with_lines_rebuilds_the_earlier_metric_day() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.metrics(_lines_metric(sid, _YESTERDAY, 7, 1))
        assert await live.lines(sid) == [(-1, _MODEL, 7, 1)]
        await live.hooks([_tool_end(sid, _TODAY, "Write", "toolu_w", lines_added=5, lines_removed=0)])
        assert await live.lines(sid) == [(0, "", 5, 0)]


@pytest.mark.asyncio
async def test_a_second_tool_end_lines_batch_marks_only_its_own_day() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.metrics(_lines_metric(sid, _YESTERDAY, 7, 1))
        await live.hooks([_tool_end(sid, _TODAY, "Write", "toolu_w", lines_added=5, lines_removed=0)])
        assert await live.lines(sid) == [(0, "", 5, 0)]
        # The session already takes its lines from its hook events: yesterday's metric day is not looked up again.
        await live.hooks([_tool_end(sid, _TODAY + timedelta(minutes=1), "Edit", "toolu_e", lines_added=1)])
        assert await live.marked_days(sid) == [0]


@pytest.mark.asyncio
async def test_a_switch_does_not_mark_a_metric_day_older_than_raw_retention() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.metrics(_lines_metric(sid, _YESTERDAY, 7, 1))
        await live.refresh(sid)
        # Raw retention ends today for this ingestor's clock: yesterday is past it, today is kept.
        retention = timedelta(days=live.engine.settings.raw_retention_days)
        aged = _Live(live.engine, PostgresTelemetryIngestor(live.engine, clock=lambda: _TODAY.date() + retention))
        await aged.hooks([_tool_end(sid, _TODAY, "Write", "toolu_w", lines_added=5, lines_removed=0)])
        assert await live.marked_days(sid) == [0]


@pytest.mark.asyncio
async def test_a_tool_end_without_lines_keeps_the_session_on_the_metric() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.hooks([_tool_end(sid, _TODAY, "Bash", "toolu_b")])
        await live.metrics(_lines_metric(sid, _TODAY, 7, 1))
        assert await live.lines(sid) == [(0, _MODEL, 7, 1)]


@pytest.mark.asyncio
async def test_a_string_or_negative_line_count_counts_zero() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.hooks(
            [
                _tool_end(sid, _TODAY, "Write", "toolu_s", lines_added="5", lines_removed=0),
                _tool_end(sid, _TODAY + timedelta(seconds=1), "Edit", "toolu_n", lines_added=-3, lines_removed=-1),
                _tool_end(sid, _TODAY + timedelta(seconds=2), "Edit", "toolu_ok", lines_added=2, lines_removed=1),
            ]
        )
        await live.metrics(_lines_metric(sid, _TODAY, 7, 1))
        assert await live.lines(sid) == [(0, "", 2, 1)]


@pytest.mark.asyncio
async def test_a_null_lines_added_keeps_the_session_on_the_metric() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.hooks([_tool_end(sid, _TODAY, "Write", "toolu_w", lines_added=None, lines_removed=0)])
        await live.metrics(_lines_metric(sid, _TODAY, 7, 1))
        assert await live.lines(sid) == [(0, _MODEL, 7, 1)]


@pytest.mark.asyncio
async def test_an_earlier_copy_without_lines_does_not_hide_a_later_one_with_lines() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.hooks(
            [
                _tool_end(sid, _TODAY, "Write", "toolu_w"),
                _tool_end(sid, _TODAY + timedelta(seconds=1), "Write", "toolu_w", lines_added=5, lines_removed=0),
            ]
        )
        assert await live.lines(sid) == [(0, "", 5, 0)]


@pytest.mark.asyncio
async def test_a_lines_metric_ingest_waits_for_the_session_lock_and_another_session_does_not() -> None:
    # Every ingest takes the lock of each of its sessions (EPMCDME-16440 spec 6c), so only another session's
    # metric finishes while this session's lock is held.
    sid, other = _session(), _session()
    async with _live(sid, other) as live:
        key = live.engine.lock_key(f"ingest-session:{sid}")
        async with live.engine.acquire() as holder:
            await holder.execute("SELECT pg_advisory_lock($1)", key)
            try:
                await asyncio.wait_for(live.metrics(_active_time_metric(other, _TODAY)), timeout=30)
                ingest = asyncio.create_task(live.metrics(_lines_metric(sid, _TODAY, 7, 1)))
                deadline = asyncio.get_running_loop().time() + 30
                while not await holder.fetchval(_LOCK_WAITERS, key):
                    assert not ingest.done(), "the lines metric ingest finished while the session lock was held"
                    assert asyncio.get_running_loop().time() < deadline, "the ingest never waited on the session lock"
                    await asyncio.sleep(0.05)  # polling interval of the deadline loop
                assert not ingest.done()
            finally:
                await holder.execute("SELECT pg_advisory_unlock($1)", key)
            await asyncio.wait_for(ingest, timeout=30)
        assert await live.lines(sid) == [(0, _MODEL, 7, 1)]
