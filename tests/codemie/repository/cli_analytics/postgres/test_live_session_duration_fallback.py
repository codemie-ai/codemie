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

"""Live-Postgres behavioral checks for EPMCDME-15584's session_dims.duration_ms fallback.

MR !4532 review (note_2466282, Dmytro_Bondarenko5) found that a non-final `agent.session.summary`
(sent on every Stop since codemie-code v0.15.9, `is_final: false`) was treated exactly like a final
one: `session_dims.duration_ms`/`ended_at` were read from whichever summary was latest, full stop, so
a cancelled `SessionEnd` still truncated the duration to the last Stop instead of the real exit time —
the exact bug this MR was meant to fix, just moved one layer down into `rollups.py`. These tests seed
raw telemetry shaped like the real ingest (hook_events for the summary and a later raw activity event),
run the real `RollupRefresher`, and assert the real `session_dims` row and `get_session_durations`
output. Skipped (not failed) when the local analytics Postgres named by `TEST_LOCAL_PG_URL` is
unreachable.

Also covers two findings from the code review of this same fix (2026-10-19): CR-001 (an
ended_at-before-started_at payload must give a NULL duration_ms, never a negative one) and CR-002 (a
malformed is_final value must default to final rather than raising and failing the whole refresh).
"""

import json
import os
from datetime import date, datetime, timedelta, timezone

import asyncpg
import pytest

from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE
from codemie.configs.config import Config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from tests.codemie.repository.cli_analytics.support.live_rollup_lock import live_rollup_lock

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")
# A fixed, inert day (inside the default 90-day raw retention window but not "today"): isolates these
# tests from any concurrently-refreshed live traffic in a shared dev analytics database.
_DAY = date(2026, 8, 1)


_SUMMARY_HOOK_SQL = """
    INSERT INTO hook_events (ts, session_id, event_type, attrs)
    VALUES ($1, $2, 'agent.session.summary', $3)
"""
_RAW_ACTIVITY_HOOK_SQL = """
    INSERT INTO hook_events (ts, session_id, event_type)
    VALUES ($1, $2, $3)
"""


async def _get_engine() -> AnalyticsPgEngine | None:
    try:
        settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL))
        engine = AnalyticsPgEngine(settings)
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return engine
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
        return None


async def _cleanup(engine: AnalyticsPgEngine, session_id: str) -> None:
    async with engine.acquire() as conn:
        await conn.execute("DELETE FROM hook_events WHERE session_id = $1", session_id)
        await conn.execute("DELETE FROM log_events WHERE session_id = $1", session_id)
        await conn.execute("DELETE FROM cost_daily WHERE session_id = $1", session_id)
        await conn.execute("DELETE FROM session_dims WHERE session_id = $1", session_id)
        await conn.execute("DELETE FROM rollup_dirty WHERE session_id = $1", session_id)


_API_REQUEST_SQL = """
    INSERT INTO log_events
        (ts, session_id, event_kind, cost_usd, input_tokens, output_tokens, cache_read_tokens,
         cache_creation_tokens, severity, resource_id)
    VALUES ($1, $2, 1, $3, $4, $5, $6, $7, 9, 1)
"""
# Two api_request events (EventKind.API_REQUEST = 1): (cost_usd, input, output, cache_read, cache_creation).
_USAGE_ROWS = ((0.10, 1000, 200, 300, 50), (0.15, 2000, 400, 100, 0))


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


async def _seed_summary(
    engine: AnalyticsPgEngine,
    *,
    session_id: str,
    ts: datetime,
    started_at: datetime,
    ended_at: datetime,
    is_final: bool,
) -> None:
    attrs = {
        "started_at": _iso(started_at),
        "ended_at": _iso(ended_at),
        "duration_ms": int((ended_at - started_at).total_seconds() * 1000),
        "is_final": is_final,
    }
    async with engine.acquire() as conn:
        await conn.execute(_SUMMARY_HOOK_SQL, ts, session_id, json.dumps(attrs))


async def _seed_raw_activity(engine: AnalyticsPgEngine, *, session_id: str, ts: datetime, event_type: str) -> None:
    async with engine.acquire() as conn:
        await conn.execute(_RAW_ACTIVITY_HOOK_SQL, ts, session_id, event_type)


async def conn_exec(engine: AnalyticsPgEngine, sql: str, *args) -> None:
    async with engine.acquire() as conn:
        await conn.execute(sql, *args)


async def _seed_summary_with_attrs(engine: AnalyticsPgEngine, *, session_id: str, ts: datetime, attrs: dict) -> None:
    async with engine.acquire() as conn:
        await conn.execute(_SUMMARY_HOOK_SQL, ts, session_id, json.dumps(attrs))


async def _seed_usage(engine: AnalyticsPgEngine, *, session_id: str, ts: datetime) -> None:
    async with engine.acquire() as conn:
        for offset, (cost, inp, out, cache_read, cache_creation) in enumerate(_USAGE_ROWS):
            await conn.execute(
                _API_REQUEST_SQL, ts + timedelta(seconds=offset), session_id, cost, inp, out, cache_read, cache_creation
            )


def _window(start: datetime, now: datetime) -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=start - timedelta(days=1), end_dt=now + timedelta(days=1))


async def _refresh(engine: AnalyticsPgEngine, day) -> None:
    async with live_rollup_lock(engine):
        refresher = RollupRefresher(engine, batch_size=100)
        async with engine.acquire() as conn:
            await refresher.mark_dirty(day, day, conn)
        await refresher.refresh()


async def _session_dims_row(engine: AnalyticsPgEngine, session_id: str) -> asyncpg.Record | None:
    async with engine.acquire() as conn:
        return await conn.fetchrow("SELECT ended_at, duration_ms FROM session_dims WHERE session_id = $1", session_id)


@pytest.mark.asyncio
async def test_live_a_cancelled_sessionend_with_a_non_final_summary_takes_the_later_of_summary_and_raw_activity():
    """note_2466282's exact repro: a Stop fires a non-final summary, SessionEnd is then cancelled (a
    normal /exit), but the CLI still emits raw activity after that Stop (idle-tail Notification, or
    simply the exit itself landing as a raw hook). duration_ms/ended_at must reach that later point,
    not stay cut at the last non-final summary."""
    engine = await _get_engine()
    session_id = "test-epmcdme-15584-live-provisional-summary"
    now = datetime(_DAY.year, _DAY.month, _DAY.day, 12, tzinfo=timezone.utc)
    start = now - timedelta(minutes=10)
    stop_summary_ts = start + timedelta(seconds=30)
    later_raw_ts = start + timedelta(seconds=90)
    try:
        await _cleanup(engine, session_id)
        await _seed_raw_activity(engine, session_id=session_id, ts=start, event_type="agent.session.start")
        await _seed_summary(
            engine,
            session_id=session_id,
            ts=stop_summary_ts,
            started_at=start,
            ended_at=stop_summary_ts,
            is_final=False,
        )
        await _seed_raw_activity(engine, session_id=session_id, ts=later_raw_ts, event_type="Notification")
        await _refresh(engine, _DAY)

        row = await _session_dims_row(engine, session_id)
        assert row is not None
        assert row["ended_at"] >= later_raw_ts
        assert row["duration_ms"] == pytest.approx(90_000, abs=2_000)

        reader = PostgresCliAnalyticsReader(engine)
        durations = await reader.get_session_durations(
            LocalAnalyticsFilter(
                scope=EXCLUDE_SCOPE, start_dt=start - timedelta(days=1), end_dt=now + timedelta(days=1)
            )
        )
        by_id = {r["session_id"]: r["duration_ms"] for r in durations}
        assert by_id[session_id] == pytest.approx(90_000, abs=2_000)
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_live_a_final_summary_stays_authoritative_even_with_a_later_raw_record():
    """A FINAL summary (the real SessionEnd, is_final: true) must win outright, not get pushed later
    by some unrelated raw record that happens to land after it (a late async metric flush, say) —
    the reviewer's fix must not regress the case code review already approved."""
    engine = await _get_engine()
    session_id = "test-epmcdme-15584-live-final-summary-wins"
    now = datetime(_DAY.year, _DAY.month, _DAY.day, 12, tzinfo=timezone.utc)
    start = now - timedelta(minutes=10)
    final_ts = start + timedelta(seconds=120)
    late_unrelated_raw_ts = start + timedelta(seconds=600)
    try:
        await _cleanup(engine, session_id)
        await _seed_raw_activity(engine, session_id=session_id, ts=start, event_type="agent.session.start")
        await _seed_summary(
            engine, session_id=session_id, ts=final_ts, started_at=start, ended_at=final_ts, is_final=True
        )
        await _seed_raw_activity(engine, session_id=session_id, ts=late_unrelated_raw_ts, event_type="Notification")
        await _refresh(engine, _DAY)

        row = await _session_dims_row(engine, session_id)
        assert row is not None
        assert row["duration_ms"] == pytest.approx(120_000, abs=2_000)
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_live_repeated_normal_exits_with_a_final_summary_give_a_stable_correct_duration():
    """A normal /exit that DOES complete SessionEnd (a final summary each time) must keep giving the
    same, correct duration across repeated refreshes — AC5's "repeated normal exits" case."""
    engine = await _get_engine()
    session_id = "test-epmcdme-15584-live-repeated-normal-exit"
    now = datetime(_DAY.year, _DAY.month, _DAY.day, 12, tzinfo=timezone.utc)
    start = now - timedelta(minutes=10)
    final_ts = start + timedelta(seconds=45)
    try:
        await _cleanup(engine, session_id)
        await _seed_raw_activity(engine, session_id=session_id, ts=start, event_type="agent.session.start")
        await _seed_summary(
            engine, session_id=session_id, ts=final_ts, started_at=start, ended_at=final_ts, is_final=True
        )
        await _refresh(engine, _DAY)
        first = await _session_dims_row(engine, session_id)

        await _refresh(engine, _DAY)  # a second, idempotent refresh: nothing new is dirty
        second = await _session_dims_row(engine, session_id)

        assert first is not None and second is not None
        assert first["duration_ms"] == second["duration_ms"] == pytest.approx(45_000, abs=2_000)
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_live_an_ended_at_before_the_started_at_gives_a_null_duration_not_a_negative_one():
    """CR-001 (code review on this same fix, 2026-10-19): a malformed or clock-skewed client payload
    can carry an ended_at earlier than its own started_at. duration_ms must come out NULL, the same
    "missing, don't guess" convention the sibling subagent duration_ms already uses, never a negative
    number that would corrupt session list/detail durations and the overview KPI sums."""
    engine = await _get_engine()
    session_id = "test-epmcdme-15584-live-ended-before-started"
    now = datetime(_DAY.year, _DAY.month, _DAY.day, 12, tzinfo=timezone.utc)
    start = now - timedelta(minutes=10)
    bad_ended_at = start - timedelta(seconds=30)  # before its own started_at: malformed/skewed
    try:
        await _cleanup(engine, session_id)
        await _seed_raw_activity(engine, session_id=session_id, ts=start, event_type="agent.session.start")
        await _seed_raw_activity(
            engine, session_id=session_id, ts=start + timedelta(seconds=20), event_type="agent.notification"
        )
        await _seed_summary_with_attrs(
            engine,
            session_id=session_id,
            ts=start + timedelta(seconds=5),
            attrs={
                "started_at": _iso(start),
                "ended_at": _iso(bad_ended_at),
                "duration_ms": -30_000,
                "is_final": True,
            },
        )
        await _refresh(engine, _DAY)

        row = await _session_dims_row(engine, session_id)
        assert row is not None
        assert row["duration_ms"] is None

        reader = PostgresCliAnalyticsReader(engine)
        durations = await reader.get_session_durations(
            LocalAnalyticsFilter(
                scope=EXCLUDE_SCOPE, start_dt=start - timedelta(days=1), end_dt=now + timedelta(days=1)
            )
        )
        by_id = {r["session_id"]: r["duration_ms"] for r in durations}
        # duration_ms is NULL, so the reader's coalesce falls back to the hook pair: last_event_at
        # (the agent.notification hook at start + 20 s) minus started_at (the start hook) = 20000, never negative.
        assert by_id[session_id] == 20_000
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_live_a_malformed_is_final_value_does_not_fail_the_refresh():
    """CR-002 (code review on this same fix, 2026-10-19): is_final should only ever be a JSON
    boolean or a "true"/"false" string, but a malformed client payload or a future client sending a
    different representation must not raise and abort the whole session's rollup recompute. (Note:
    Postgres's own ::boolean cast already accepts several extra spellings -- "1"/"0", "yes"/"no",
    "t"/"f", "y"/"n", "on"/"off" -- so a genuinely invalid value has to be something outside that
    whole set, e.g. "unknown".) It defaults to final (true) -- the same safety choice already made
    for an absent is_final -- so a later, unrelated raw record must NOT widen the stored duration
    past the summary's own ended_at."""
    engine = await _get_engine()
    session_id = "test-epmcdme-15584-live-malformed-is-final"
    now = datetime(_DAY.year, _DAY.month, _DAY.day, 12, tzinfo=timezone.utc)
    start = now - timedelta(minutes=10)
    summary_ts = start + timedelta(seconds=45)
    later_unrelated_raw_ts = start + timedelta(seconds=600)
    try:
        await _cleanup(engine, session_id)
        await _seed_raw_activity(engine, session_id=session_id, ts=start, event_type="agent.session.start")
        await _seed_summary_with_attrs(
            engine,
            session_id=session_id,
            ts=summary_ts,
            attrs={
                "started_at": _iso(start),
                "ended_at": _iso(summary_ts),
                "duration_ms": 45_000,
                "is_final": "unknown",  # malformed: not a JSON boolean and not a valid ::boolean literal
            },
        )
        await _seed_raw_activity(engine, session_id=session_id, ts=later_unrelated_raw_ts, event_type="Notification")

        await _refresh(engine, _DAY)  # must not raise

        row = await _session_dims_row(engine, session_id)
        assert row is not None
        assert row["duration_ms"] == pytest.approx(45_000, abs=2_000)  # final-wins, not widened by fb.last_ts
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_live_repeated_cancelled_sessionends_each_session_gets_its_own_duration():
    """AC5 "repeated normal exits": several separate sessions each end with a cancelled SessionEnd
    (a non-final summary at +30 s, a later raw record), and one refresh covers them all. Every
    session must get its own later-of-summary-and-raw duration, with no bleed between sessions."""
    engine = await _get_engine()
    now = datetime(_DAY.year, _DAY.month, _DAY.day, 12, tzinfo=timezone.utc)
    start = now - timedelta(minutes=10)
    # session_id -> seconds after start of the raw record that lands after the Stop summary
    later_raw_offsets = {
        "test-epmcdme-15584-live-repeated-cancelled-a": 90,
        "test-epmcdme-15584-live-repeated-cancelled-b": 150,
        "test-epmcdme-15584-live-repeated-cancelled-c": 210,
    }
    try:
        for session_id, offset in later_raw_offsets.items():
            await _cleanup(engine, session_id)
            stop_ts = start + timedelta(seconds=30)
            await _seed_raw_activity(engine, session_id=session_id, ts=start, event_type="agent.session.start")
            await _seed_summary(
                engine, session_id=session_id, ts=stop_ts, started_at=start, ended_at=stop_ts, is_final=False
            )
            await _seed_raw_activity(
                engine, session_id=session_id, ts=start + timedelta(seconds=offset), event_type="Notification"
            )
        await _refresh(engine, _DAY)

        durations = await PostgresCliAnalyticsReader(engine).get_session_durations(_window(start, now))
        by_id = {r["session_id"]: r["duration_ms"] for r in durations}
        for session_id, offset in later_raw_offsets.items():
            assert by_id[session_id] == pytest.approx(offset * 1000, abs=2_000), session_id
    finally:
        for session_id in later_raw_offsets:
            await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_live_tail_sources_outside_the_reader_probes_still_widen_the_duration():
    """Why this fix is still needed after EPMCDME-15506 (!4568).

    15506's reader helper derives the reported range from
    `greatest(last_event_at, ended_at, max(log_events.ts), max(spans.ts))`. Its two probes read
    log_events and spans only, and `last_event_at` comes from the 13 DIMENSION_HOOK_TYPES alone. So
    when a cancelled SessionEnd leaves the session's last telemetry in a table the probes do not read
    -- a non-dimension hook such as `agent.git.snapshot` (which the CLI sends *at the end of the
    session*), a usage_request, or a metric_point -- the reader has no way to see it. The only route
    by which those reach the reported duration is the stored `ended_at` this fix corrects, because
    rollups' `fb.last_ts` spans all five raw tables.

    Measured on this tree: with this fix every tail below gives the real 118 s; with rollups.py at
    main's version the first three give 30 s (cut at the last Stop) and only the log_events tail is
    recovered by 15506's own probe.
    """
    engine = await _get_engine()
    now = datetime(_DAY.year, _DAY.month, _DAY.day, 12, tzinfo=timezone.utc)
    start = now - timedelta(minutes=10)
    stop_ts = start + timedelta(seconds=30)
    tail_ts = start + timedelta(seconds=118)
    gitsnap_id = "test-epmcdme-15584-live-tail-git-snapshot"
    usage_id = "test-epmcdme-15584-live-tail-usage-request"
    metric_id = "test-epmcdme-15584-live-tail-metric-point"
    session_ids = (gitsnap_id, usage_id, metric_id)
    try:
        for session_id in session_ids:
            await _cleanup(engine, session_id)
            await conn_exec(engine, "DELETE FROM metric_points WHERE session_id = $1", session_id)
            await conn_exec(engine, "DELETE FROM usage_requests WHERE session_id = $1", session_id)
            await _seed_raw_activity(engine, session_id=session_id, ts=start, event_type="agent.session.start")
            await _seed_usage(engine, session_id=session_id, ts=start + timedelta(seconds=10))
            await _seed_summary(
                engine, session_id=session_id, ts=stop_ts, started_at=start, ended_at=stop_ts, is_final=False
            )
        # Each tail lands in a table 15506's reader probes do not read.
        await conn_exec(
            engine,
            "INSERT INTO hook_events (ts, session_id, event_type, attrs) VALUES ($1,$2,'agent.git.snapshot',$3)",
            tail_ts,
            gitsnap_id,
            json.dumps({"git_branch": "main"}),
        )
        await conn_exec(
            engine,
            "INSERT INTO usage_requests (ts, session_id, model, input_tokens, output_tokens)"
            " VALUES ($1,$2,'claude-opus-5-5',10,20)",
            tail_ts,
            usage_id,
        )
        await conn_exec(
            engine,
            "INSERT INTO metric_points (ts, session_id, metric_name, value, temporality, is_monotonic, resource_id)"
            " VALUES ($1,$2,'claude_code.token.usage',42,1,true,1)",
            tail_ts,
            metric_id,
        )
        await _refresh(engine, _DAY)

        by_id = {
            r["session_id"]: r["duration_ms"]
            for r in await PostgresCliAnalyticsReader(engine).get_session_durations(_window(start, now))
        }
        for session_id in session_ids:
            assert by_id[session_id] == pytest.approx(118_000, abs=2_000), session_id
    finally:
        for session_id in session_ids:
            await conn_exec(engine, "DELETE FROM metric_points WHERE session_id = $1", session_id)
            await conn_exec(engine, "DELETE FROM usage_requests WHERE session_id = $1", session_id)
            await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_live_session_cost_facts_carry_the_fallback_duration_for_a_cancelled_sessionend():
    """get_session_cost_facts' duration_ms reaches the summary's later raw record (90 s), not the
    stale last-Stop duration (30 s). EPMCDME-15506 derives this column from the activity range, of
    which the stored ended_at this fix corrects is one input."""
    engine = await _get_engine()
    session_id = "test-epmcdme-15584-live-cost-facts-duration"
    now = datetime(_DAY.year, _DAY.month, _DAY.day, 12, tzinfo=timezone.utc)
    start = now - timedelta(minutes=10)
    stop_ts = start + timedelta(seconds=30)
    try:
        await _cleanup(engine, session_id)
        await _seed_raw_activity(engine, session_id=session_id, ts=start, event_type="agent.session.start")
        await _seed_usage(engine, session_id=session_id, ts=start + timedelta(seconds=10))
        await _seed_summary(
            engine, session_id=session_id, ts=stop_ts, started_at=start, ended_at=stop_ts, is_final=False
        )
        await _seed_raw_activity(
            engine, session_id=session_id, ts=start + timedelta(seconds=90), event_type="Notification"
        )
        await _refresh(engine, _DAY)

        facts = await PostgresCliAnalyticsReader(engine).get_session_cost_facts(_window(start, now))
        by_id = {r["session_id"]: r for r in facts}
        assert by_id[session_id]["duration_ms"] == pytest.approx(90_000, abs=2_000)
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_live_cost_and_tokens_are_identical_with_and_without_the_provisional_summary_fallback():
    """AC4: the fallback only moves ended_at/duration_ms. The same usage rows must give the same cost
    and token counters whether or not a later raw record pushes the provisional summary's end forward."""
    engine = await _get_engine()
    with_fallback_id = "test-epmcdme-15584-live-cost-with-fallback"
    without_fallback_id = "test-epmcdme-15584-live-cost-without-fallback"
    now = datetime(_DAY.year, _DAY.month, _DAY.day, 12, tzinfo=timezone.utc)
    start = now - timedelta(minutes=10)
    stop_ts = start + timedelta(seconds=30)
    try:
        for session_id in (with_fallback_id, without_fallback_id):
            await _cleanup(engine, session_id)
            await _seed_raw_activity(engine, session_id=session_id, ts=start, event_type="agent.session.start")
            await _seed_usage(engine, session_id=session_id, ts=start + timedelta(seconds=10))
            await _seed_summary(
                engine, session_id=session_id, ts=stop_ts, started_at=start, ended_at=stop_ts, is_final=False
            )
        await _seed_raw_activity(
            engine, session_id=with_fallback_id, ts=start + timedelta(seconds=90), event_type="Notification"
        )
        await _refresh(engine, _DAY)

        reader = PostgresCliAnalyticsReader(engine)
        facts = {r["session_id"]: r for r in await reader.get_session_cost_facts(_window(start, now))}
        # The fallback path really ran for one session and not the other.
        assert facts[with_fallback_id]["duration_ms"] == pytest.approx(90_000, abs=2_000)
        assert facts[without_fallback_id]["duration_ms"] == pytest.approx(30_000, abs=2_000)

        async with engine.acquire() as conn:
            dims = {
                r["session_id"]: r
                for r in await conn.fetch(
                    "SELECT session_id, source_cost_usd, api_calls FROM session_dims WHERE session_id = ANY($1)",
                    [with_fallback_id, without_fallback_id],
                )
            }
        for session_id in (with_fallback_id, without_fallback_id):
            row = facts[session_id]
            assert row["cost_usd"] == pytest.approx(0.25)
            assert row["input_tokens"] == 3000
            assert row["output_tokens"] == 600
            assert row["cache_read_tokens"] == 400
            assert row["cache_creation_tokens"] == 50
            assert row["api_call_count"] == 2
            assert dims[session_id]["source_cost_usd"] == pytest.approx(0.25)
        assert dims[with_fallback_id]["api_calls"] == dims[without_fallback_id]["api_calls"]
    finally:
        await _cleanup(engine, with_fallback_id)
        await _cleanup(engine, without_fallback_id)
        await engine.close()
