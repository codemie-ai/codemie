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
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from codemie.repository.cli_analytics.postgres.maintenance import quote_ident
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from tests.codemie.repository.cli_analytics.support import otlp_builders as b
from tests.codemie.repository.cli_analytics.support.live_rollup_lock import live_rollup_lock
from tests.codemie.repository.cli_analytics.support.scopes import DETAIL_SCOPE, EXCLUDE_SCOPE

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


@pytest.mark.asyncio
async def test_a_numeric_zero_tool_end_session_stores_a_zero_row() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.hooks([_tool_end(sid, _TODAY, "Write", "toolu_z", lines_added=0, lines_removed=0)])
        assert await live.lines(sid) == [(0, "", 0, 0)]


@pytest.mark.asyncio
async def test_an_otel_lines_metric_summing_zero_stores_a_zero_row() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.metrics(_lines_metric(sid, _TODAY, 0, 0))
        assert await live.lines(sid) == [(0, _MODEL, 0, 0)]


@pytest.mark.asyncio
async def test_a_session_without_a_line_source_has_no_row() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.hooks([_tool_end(sid, _TODAY, "Bash", "toolu_b")])
        await live.metrics(_active_time_metric(sid, _TODAY))
        assert await live.lines(sid) == []


@pytest.mark.asyncio
async def test_a_control_session_with_lines_keeps_its_sums() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.metrics(_lines_metric(sid, _TODAY, 84, 5))
        assert await live.lines(sid) == [(0, _MODEL, 84, 5)]


@pytest.mark.asyncio
async def test_an_otel_zero_row_is_replaced_when_numeric_hook_lines_arrive_later() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.metrics(_lines_metric(sid, _TODAY, 0, 0))
        assert await live.lines(sid) == [(0, _MODEL, 0, 0)]
        await live.hooks([_tool_end(sid, _TODAY, "Write", "toolu_w", lines_added=3, lines_removed=1)])
        assert await live.lines(sid) == [(0, "", 3, 1)]


_DAY2_START = _TODAY.replace(hour=0, minute=0)
_DAY2_WINDOW = (_DAY2_START, _DAY2_START + timedelta(hours=23, minutes=59, seconds=59))


async def _start_today(live: _Live, *session_ids: str) -> None:
    """Dimension rows that start the sessions inside today's window (the test's hooks carry no dimension event)."""
    async with live.engine.acquire() as conn:
        for sid in session_ids:
            await conn.execute(
                "INSERT INTO session_dims (session_id, started_at) VALUES ($1, $2) "
                "ON CONFLICT (session_id) DO UPDATE SET started_at = EXCLUDED.started_at",
                sid,
                _TODAY,
            )


def _today_filter(*session_ids: str) -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(*_DAY2_WINDOW, scope=EXCLUDE_SCOPE).restricted_to(list(session_ids))


@pytest.mark.asyncio
async def test_a_session_with_a_lines_row_on_any_day_is_known_and_one_without_a_row_stays_absent() -> None:
    known, unknown = _session(), _session()
    async with _live(known, unknown) as live:
        await live.hooks(
            [
                _tool_end(known, _YESTERDAY, "Write", "toolu_d1", lines_added=5, lines_removed=2),
                _tool_end(known, _TODAY, "Bash", "toolu_d2"),
                _tool_end(unknown, _TODAY, "Bash", "toolu_u"),
            ]
        )
        assert await live.lines(known) == [(-1, "", 5, 2)]
        assert await live.lines(unknown) == []
        await _start_today(live, known, unknown)
        reader = PostgresCliAnalyticsReader(live.engine)
        f = _today_filter(known, unknown)

        rows = await reader.get_lines_by_session(f)
        assert [(r["session_id"], r["lines_added"], r["lines_removed"]) for r in rows] == [(known, 5, 2)]
        only_unknown = await reader.get_lines_totals(_today_filter(unknown))
        assert [(r["lines_added"], r["lines_removed"]) for r in only_unknown] == [(None, None)]
        assert await reader.get_lines_daily(_today_filter(unknown)) == []


@pytest.mark.asyncio
async def test_a_zero_sum_session_is_known_with_zero_lines() -> None:
    sid = _session()
    async with _live(sid) as live:
        await live.hooks([_tool_end(sid, _TODAY, "Write", "toolu_z", lines_added=0, lines_removed=0)])
        assert await live.lines(sid) == [(0, "", 0, 0)]
        await _start_today(live, sid)
        rows = await PostgresCliAnalyticsReader(live.engine).get_lines_by_session(_today_filter(sid))
        assert [(r["session_id"], r["lines_added"], r["lines_removed"]) for r in rows] == [(sid, 0, 0)]


@pytest.mark.asyncio
async def test_the_day_class_list_ignores_zero_rows_and_keeps_non_zero_ones() -> None:
    zero, nonzero = _session(), _session()
    async with _live(zero, nonzero) as live:
        await live.hooks(
            [
                _tool_end(zero, _TODAY, "Write", "toolu_z", lines_added=0, lines_removed=0),
                _tool_end(nonzero, _TODAY, "Write", "toolu_n", lines_added=3, lines_removed=0),
            ]
        )
        assert await live.lines(zero) == [(0, "", 0, 0)]
        assert await live.lines(nonzero) == [(0, "", 3, 0)]
        await _start_today(live, zero, nonzero)
        reader = PostgresCliAnalyticsReader(live.engine)

        assert await reader.get_class_lists(_today_filter(zero), by="day") == []
        days = await reader.get_class_lists(_today_filter(nonzero), by="day")
        assert [r["key"] for r in days] == [_TODAY.date().isoformat()]
        daily = await reader.get_lines_daily(_today_filter(zero, nonzero))
        assert [(r["net_lines"], r["nonzero"]) for r in daily] == [(3, True)]
        assert [(r["net_lines"], r["nonzero"]) for r in await reader.get_lines_daily(_today_filter(zero))] == [
            (0, False)
        ]


@pytest.mark.asyncio
async def test_lines_by_user_has_no_row_for_unknown_lines_and_zero_for_a_zero_sum_session() -> None:
    zero, unknown = _session(), _session()
    async with _live(zero, unknown) as live:
        await live.hooks(
            [
                _tool_end(zero, _TODAY, "Write", "toolu_z", lines_added=0, lines_removed=0),
                _tool_end(unknown, _TODAY, "Bash", "toolu_u"),
            ]
        )
        assert await live.lines(zero) == [(0, "", 0, 0)]
        assert await live.lines(unknown) == []
        await _start_today(live, zero, unknown)
        reader = PostgresCliAnalyticsReader(live.engine)

        assert await reader.get_lines_by_user(_today_filter(unknown)) == []
        rows = await reader.get_lines_by_user(_today_filter(zero))
        assert [r["net_lines"] for r in rows] == [0]


@pytest.mark.asyncio
async def test_session_detail_scalars_report_null_lines_for_an_unknown_session_and_zero_for_a_zero_row() -> None:
    zero, unknown = _session(), _session()
    async with _live(zero, unknown) as live:
        await live.hooks(
            [
                _tool_end(zero, _TODAY, "Write", "toolu_z", lines_added=0, lines_removed=0),
                _tool_end(unknown, _TODAY, "Bash", "toolu_u"),
            ]
        )
        assert await live.lines(zero) == [(0, "", 0, 0)]
        assert await live.lines(unknown) == []
        reader = PostgresCliAnalyticsReader(live.engine)

        unknown_rows = await reader.get_session_detail_scalars(unknown, scope=DETAIL_SCOPE)
        assert [(r["lines_added"], r["lines_removed"]) for r in unknown_rows] == [(None, None)]
        zero_rows = await reader.get_session_detail_scalars(zero, scope=DETAIL_SCOPE)
        assert [(r["lines_added"], r["lines_removed"]) for r in zero_rows] == [(0, 0)]


@pytest.mark.asyncio
async def test_a_zero_sum_lines_row_is_not_a_developer_source_but_a_non_zero_one_is() -> None:
    zero, nonzero = _session(), _session()
    async with _live(zero, nonzero) as live:
        await live.hooks(
            [
                _tool_end(zero, _TODAY, "Write", "toolu_z", lines_added=0, lines_removed=0),
                _tool_end(nonzero, _TODAY, "Write", "toolu_n", lines_added=3, lines_removed=0),
            ]
        )
        assert await live.lines(zero) == [(0, "", 0, 0)]
        assert await live.lines(nonzero) == [(0, "", 3, 0)]
        await _start_today(live, zero, nonzero)
        async with live.engine.acquire() as conn:
            await conn.execute(
                "UPDATE session_dims SET last_event_at = $1, jwt_email = NULL WHERE session_id = ANY($2)",
                _TODAY,
                [zero, nonzero],
            )
        reader = PostgresCliAnalyticsReader(live.engine)

        # No session email (jwt_email cleared) and no cost_daily row: only a lines_daily row that reports lines can
        # name the developer.
        assert [r["developer_name"] for r in await reader.get_users_last_active(_today_filter(zero))] == ["unknown"]
        assert [r["developer_name"] for r in await reader.get_users_last_active(_today_filter(nonzero))] == [_EMAIL]


@pytest.mark.asyncio
async def test_lines_by_user_names_the_same_developer_as_last_active_for_a_zero_sum_session() -> None:
    zero, nonzero = _session(), _session()
    async with _live(zero, nonzero) as live:
        await live.hooks(
            [
                _tool_end(zero, _TODAY, "Write", "toolu_z", lines_added=0, lines_removed=0),
                _tool_end(nonzero, _TODAY, "Write", "toolu_n", lines_added=3, lines_removed=0),
            ]
        )
        assert await live.lines(zero) == [(0, "", 0, 0)]
        assert await live.lines(nonzero) == [(0, "", 3, 0)]
        await _start_today(live, zero, nonzero)
        async with live.engine.acquire() as conn:
            await conn.execute(
                "UPDATE session_dims SET last_event_at = $1, jwt_email = NULL WHERE session_id = ANY($2)",
                _TODAY,
                [zero, nonzero],
            )
        reader = PostgresCliAnalyticsReader(live.engine)

        for session, developer, net in ((zero, "unknown", 0), (nonzero, _EMAIL, 3)):
            f = _today_filter(session)
            by_user = [(r["developer_name"], r["net_lines"]) for r in await reader.get_lines_by_user(f)]
            last_active = [r["developer_name"] for r in await reader.get_users_last_active(f)]
            assert by_user == [(developer, net)]
            assert last_active == [developer]
