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

"""Live-Postgres proof, through the real ingestor, that housekeeping records do not move a session's end (EPMCDME-16847).

Hook events and OTLP logs, metrics and spans go through `PostgresTelemetryIngestor`, the real refresher rolls them up and
the session list and detail readers report the duration. The last hook is at T+8.7 s:
a `retention_sweep` log and periodic metrics at T+607 s must leave the duration at 8.7 s, while a real `api_request`
at T+12 s and a tool span at T+11 s must make it 12 s. Skipped when `TEST_LOCAL_PG_URL` is not set or unreachable:
point it at a disposable PostgreSQL.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.vocabulary import SpanKind
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.postgres.test_live_session_reopen import _cleanup, _engine, _naive
from tests.codemie.repository.cli_analytics.support import otlp_builders as b
from tests.codemie.repository.cli_analytics.support.live_rollup_lock import live_rollup_lock
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_LOCAL_PG_URL"), reason="TEST_LOCAL_PG_URL is not set: no database to run against"
)

_PROTOBUF = "application/x-protobuf"
_RESOURCE = {"service.name": "claude-code"}


class _Run:
    """One session ingested the way the CLI sends it: hooks as NDJSON events, everything else as OTLP."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.sid = f"test-16847-{uuid.uuid4().hex[:12]}"
        self.email = f"real-ingest-{uuid.uuid4().hex[:8]}@example.com"
        self.t0 = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0)
        self.ingestor = PostgresTelemetryIngestor(engine)
        self.handler = LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine))

    def at(self, seconds: float) -> datetime:
        return self.t0 + timedelta(seconds=seconds)

    def _attrs(self, **extra) -> dict:
        return {"session.id": self.sid, "user.email": self.email, **extra}

    async def hooks(self, *, provisional: bool = False) -> None:
        """The session's hooks; the last one, a Stop, is at T+8.7 s."""
        steps = [
            ("agent.session.start", 0, {}),
            ("agent.prompt.submit", 1, {}),
            ("agent.tool.start", 3, {"tool_name": "Bash", "tool_use_id": "tu-1"}),
            ("agent.tool.end", 5, {"tool_name": "Bash", "tool_use_id": "tu-1"}),
            ("agent.session.stop", 8.7, {}),
        ]
        if provisional:
            steps.append(
                (
                    "agent.session.summary",
                    8.7,
                    {"started_at": self.t0.isoformat(), "ended_at": self.at(8.7).isoformat(), "is_final": False},
                )
            )
        events = [
            {
                "type": kind,
                "timestamp": self.at(s).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "session_id": self.sid,
                "platform": "claude-code",
                **fields,
            }
            for kind, s, fields in steps
        ]
        await self.ingestor.ingest_hook_events(events, self.email, 0)

    async def _otlp(self, signal: str, request) -> None:
        await self.ingestor.ingest_otlp(signal, request.SerializeToString(), _PROTOBUF, user_email=self.email)

    async def housekeeping(self) -> None:
        """What a client sends long after the last hook: a retention_sweep at T+607 s and periodic metrics at T+60 s."""
        log = b.log_record(self.at(607), self._attrs(**{"event.name": "retention_sweep"}))
        await self._otlp("logs", b.logs_request(_RESOURCE, [log]))
        points = [
            b.sum_metric("claude_code.active_time.total", [b.number_point(self.at(60), 5.0, self._attrs())]),
            b.sum_metric("claude_code.session.count", [b.number_point(self.at(60), 1, self._attrs())]),
        ]
        await self._otlp("metrics", b.metrics_request(_RESOURCE, points))

    async def real_work(self) -> None:
        """A tool span at T+11 s and an api_request at T+12 s."""
        tool = b.span(
            "claude_code.tool",
            trace_id=uuid.uuid4().bytes,  # the ingest ledger drops a re-sent span id, so each run needs its own
            span_id=uuid.uuid4().bytes[:8],
            start=self.at(11),
            end=self.at(11.5),
            attrs=self._attrs(tool_name="Bash"),
        )
        await self._otlp("traces", b.traces_request(_RESOURCE, [tool]))
        request = b.log_record(
            self.at(12),
            self._attrs(
                **{
                    "event.name": "api_request",
                    "model": "claude-sonnet-4-5",
                    "request_id": "req-16847",
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cost_usd": 0.001,
                    "query_source": "repl_main_thread",
                }
            ),
        )
        await self._otlp("logs", b.logs_request(_RESOURCE, [request]))

    async def rollup(self) -> None:
        await RollupRefresher(self.engine, batch_size=100).refresh()

    async def durations(self) -> tuple[int | None, int | None]:
        """(duration of the list row, duration of the detail)."""
        f = LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE,
            start_dt=_naive(self.t0 - timedelta(hours=1)),
            end_dt=_naive(self.t0 + timedelta(hours=1)),
            users=[self.email],
        )
        sessions, _, _ = await self.handler.get_sessions(f, 0, 100, "start_time", None)
        row = next(s for s in sessions["sessions"] if s["trace_id"] == self.sid)
        detail, _, _ = await self.handler.get_session_detail(self.sid, scope=EXCLUDE_SCOPE)
        return row["duration_ms"], detail["duration_ms"]

    async def stored_rows(self) -> dict:
        async with self.engine.acquire() as conn:
            logs = await conn.fetch(
                "SELECT event_name, event_kind FROM log_events WHERE session_id = $1 ORDER BY ts", self.sid
            )
            metrics = await conn.fetch("SELECT metric_name FROM metric_points WHERE session_id = $1", self.sid)
            spans = await conn.fetch("SELECT span_kind FROM spans WHERE session_id = $1", self.sid)
        return {
            "logs": [(r["event_name"], r["event_kind"]) for r in logs],
            "metrics": sorted(r["metric_name"] for r in metrics),
            "spans": [r["span_kind"] for r in spans],
        }

    async def persisted(self) -> tuple[datetime, int | None]:
        """(ended_at, duration_ms) as stored in session_dims."""
        async with self.engine.acquire() as conn:
            row = await conn.fetchrow("SELECT ended_at, duration_ms FROM session_dims WHERE session_id = $1", self.sid)
        return _naive(row["ended_at"]), row["duration_ms"]

    async def stored_end_ms(self) -> int | None:
        """The stored end of the session (what the last rollup left), in ms after its start."""
        async with self.engine.acquire() as conn:
            return await conn.fetchval(
                "SELECT (extract(epoch FROM greatest(last_event_at, ended_at) - started_at) * 1000)::bigint "
                "FROM session_dims WHERE session_id = $1",
                self.sid,
            )


async def _scenario(extra: str, rolled_between: bool):
    """(durations read before the second rollup and the stored end then, or None when nothing was rolled up yet;
    durations after it; stored raw rows). The first read is the reader's own late-work probe over a stale rollup."""
    engine = await _engine()
    run = _Run(engine)
    try:
        async with live_rollup_lock(engine):
            await run.hooks()
            if rolled_between:
                await run.rollup()
            await run.housekeeping()
            if extra == "real_work":
                await run.real_work()
            stale = (await run.durations(), await run.stored_end_ms()) if rolled_between else None
            await run.rollup()
            return stale, await run.durations(), await run.stored_rows()
    finally:
        await _cleanup(engine, [run.sid])
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("rolled_between", [True, False])
async def test_retention_sweep_and_periodic_metrics_do_not_move_the_end(rolled_between):
    stale, durations, stored = await _scenario("housekeeping_only", rolled_between)
    # Positive control: the records are stored (not dropped), as kind OTHER and as non-lines metrics.
    assert stored["logs"] == [("retention_sweep", 0)]
    assert stored["metrics"] == ["claude_code.active_time.total", "claude_code.session.count"]
    assert stale is None or stale == ((8700, 8700), 8700)
    assert durations == (8700, 8700)


@pytest.mark.asyncio
@pytest.mark.parametrize("rolled_between", [True, False])
async def test_a_late_real_api_request_and_tool_span_move_the_end(rolled_between):
    stale, durations, stored = await _scenario("real_work", rolled_between)
    assert ("api_request", 1) in stored["logs"] and ("retention_sweep", 0) in stored["logs"]
    assert stored["spans"] == [1]
    # Before the second rollup the stored end is still the last hook; only the reader's probe sees the late work.
    assert stale is None or stale == ((12000, 12000), 8700)
    assert durations == (12000, 12000)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("manual", "provisional"),
    [(False, False), (False, True), (True, False)],
    ids=["idle-reuses-turn-span", "idle-after-provisional-summary", "idle-reuses-manual-span"],
)
async def test_idle_compact_does_not_move_the_end(manual, provisional):
    engine = await _engine()
    run = _Run(engine)
    trace_id = uuid.uuid4().bytes
    span_id = trace_id[:8]

    def record(seconds, event, **attrs):
        return b.log_record(
            run.at(seconds),
            run._attrs(**{"event.name": event, **attrs}),
            trace_id=trace_id,
            span_id=span_id,
        )

    def request(seconds, source):
        return record(
            seconds,
            "api_request",
            query_source=source,
            request_id=f"{run.sid}-{seconds}",
            model="claude-sonnet-5-5",
            input_tokens=10,
            output_tokens=5,
        )

    try:
        async with live_rollup_lock(engine):
            await run.hooks(provisional=provisional)
            if manual:
                await run._otlp("logs", b.logs_request(_RESOURCE, [request(12, "compact")]))
                await run.rollup()
                assert await run.durations() == (8700, 8700)
                marker = record(13, "compaction", trigger="manual")
                await run._otlp("logs", b.logs_request(_RESOURCE, [marker]))
            else:
                await run._otlp("logs", b.logs_request(_RESOURCE, [request(8.7, "sdk")]))
            await run.rollup()
            expected = 12000 if manual else 8700
            assert await run.stored_end_ms() == expected
            # Desktop emitted this API request and assistant response 54 minutes later on
            # the original span, with no new prompt or compaction marker.
            background = [
                request(3263.724, "compact"),
                record(3263.724, "assistant_response", query_source="compact"),
            ]
            await run._otlp("logs", b.logs_request(_RESOURCE, background))
            llm = b.span(
                "claude_code.llm_request",
                trace_id=trace_id,
                span_id=uuid.uuid4().bytes[:8],
                parent_span_id=span_id,
                start=run.at(3260.724),
                end=run.at(3263.724),
                attrs=run._attrs(query_source_safe="compact", request_id=f"{run.sid}-3263.724"),
            )
            await run._otlp("traces", b.traces_request(_RESOURCE, [llm]))
            assert await run.durations() == (expected, expected)
            await run.rollup()
            assert await run.durations() == (expected, expected)
            assert await run.stored_end_ms() == expected
            assert (await run.stored_rows())["logs"][-2:] == [("api_request", 1), ("assistant_response", 0)]

            if not manual:
                # A pre-fix refresh already persisted the compact request as the end.
                for old_end in (3263.724, 3260.724):
                    await _persist_end(engine, run, old_end)
                    # Exercise the same explicit queueing as the bounded backfill;
                    # re-ingesting identical housekeeping is deduplicated.
                    await RollupRefresher(engine, batch_size=100).mark_dirty(run.t0.date(), run.at(old_end).date())
                    await run.rollup()
                    assert await run.durations() == (expected, expected)
                    assert await run.stored_end_ms() == expected
    finally:
        await _cleanup(engine, [run.sid])
        await engine.close()


async def _persist_end(engine, run: _Run, seconds: float) -> None:
    async with engine.acquire() as conn:
        await conn.execute(
            "UPDATE session_dims SET ended_at = $1, duration_ms = $2 WHERE session_id = $3",
            run.at(seconds),
            round(seconds * 1000),
            run.sid,
        )


def _api_request(run: _Run, seconds: float, source: str, trace_id: bytes, span_id: bytes):
    return b.log_record(
        run.at(seconds),
        run._attrs(
            **{
                "event.name": "api_request",
                "query_source": source,
                "request_id": f"{run.sid}-{seconds}",
                "model": "claude-sonnet-5-5",
                "input_tokens": 10,
                "output_tokens": 5,
            }
        ),
        trace_id=trace_id,
        span_id=span_id,
    )


@pytest.mark.asyncio
async def test_an_interaction_span_evidenced_only_by_an_idle_compact_request_is_not_work():
    """CR-001: the span filter must not take an idle compact request as the model request that makes an interaction
    span a turn. The span is its own (nothing else on it), so the compact log is its only evidence."""
    engine = await _engine()
    run = _Run(engine)
    trace_id, span_id = uuid.uuid4().bytes, uuid.uuid4().bytes[:8]
    try:
        async with live_rollup_lock(engine):
            await run.hooks()
            await run.rollup()
            await run._otlp(
                "logs", b.logs_request(_RESOURCE, [_api_request(run, 3263.724, "compact", trace_id, span_id)])
            )
            interaction = b.span(
                "claude_code.interaction",
                trace_id=trace_id,
                span_id=span_id,
                start=run.at(3263),
                end=run.at(3264),
                attrs=run._attrs(),
            )
            await run._otlp("traces", b.traces_request(_RESOURCE, [interaction]))
            assert (await run.stored_rows())["spans"] == [int(SpanKind.INTERACTION)]
            assert await run.durations() == (8700, 8700)
            await run.rollup()
            assert await run.durations() == (8700, 8700)
            assert await run.stored_end_ms() == 8700
    finally:
        await _cleanup(engine, [run.sid])
        await engine.close()


@pytest.mark.asyncio
async def test_a_persisted_idle_compaction_end_is_kept_when_the_raw_history_is_partial():
    """CR-002: the end is repaired only when the raw history is complete. Here the first hooks are gone (a purged
    head), so the stored end, its duration and the reported duration stay as they were."""
    engine = await _engine()
    run = _Run(engine)
    trace_id, span_id = uuid.uuid4().bytes, uuid.uuid4().bytes[:8]
    try:
        async with live_rollup_lock(engine):
            await run.hooks()
            requests = [_api_request(run, 8.7, "sdk", trace_id, span_id)]
            requests.append(_api_request(run, 3263.724, "compact", trace_id, span_id))
            await run._otlp("logs", b.logs_request(_RESOURCE, requests))
            await run.rollup()
            assert await run.stored_end_ms() == 8700
            await _persist_end(engine, run, 3263.724)  # what a pre-fix refresh stored
            async with engine.acquire() as conn:
                await conn.execute("DELETE FROM hook_events WHERE session_id = $1 AND ts < $2", run.sid, run.at(8))
            await run.housekeeping()
            await run.rollup()
            assert await run.persisted() == (_naive(run.at(3263.724)), 3263724)
            assert await run.durations() == (3263724, 3263724)
    finally:
        await _cleanup(engine, [run.sid])
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [True, False], ids=["trace-context", "no-trace-context"])
async def test_manual_compact_request_and_llm_span_remain_activity(context):
    engine = await _engine()
    run = _Run(engine)
    trace_id = uuid.uuid4().bytes if context else b""
    parent_id = uuid.uuid4().bytes[:8] if context else b""
    try:
        async with live_rollup_lock(engine):
            await run.hooks()
            await run.rollup()
            request = _api_request(run, 12, "compact", trace_id, parent_id)
            llm = b.span(
                "claude_code.llm_request",
                trace_id=trace_id,
                span_id=uuid.uuid4().bytes[:8],
                parent_span_id=parent_id,
                start=run.at(11),
                end=run.at(12),
                attrs=run._attrs(query_source_safe="compact"),
            )
            await run._otlp("traces", b.traces_request(_RESOURCE, [llm]))
            assert await run.durations() == (8700, 8700)
            marker = b.log_record(
                run.at(13),
                run._attrs(**{"event.name": "compaction", "trigger": "manual"}),
                trace_id=trace_id,
                span_id=parent_id,
            )
            await run._otlp("logs", b.logs_request(_RESOURCE, [marker]))
            assert await run.durations() == (11000, 11000)
            await run._otlp("logs", b.logs_request(_RESOURCE, [request]))
            assert await run.durations() == (12000, 12000)
            await run.rollup()
            assert await run.durations() == (12000, 12000)
            assert await run.stored_end_ms() == 12000
    finally:
        await _cleanup(engine, [run.sid])
        await engine.close()


@pytest.mark.asyncio
async def test_an_ordinary_llm_request_span_needs_no_api_log_to_extend_the_end():
    engine = await _engine()
    run = _Run(engine)
    try:
        async with live_rollup_lock(engine):
            await run.hooks()
            await run.rollup()
            llm = b.span(
                "claude_code.llm_request",
                trace_id=uuid.uuid4().bytes,
                span_id=uuid.uuid4().bytes[:8],
                start=run.at(12),
                end=run.at(13),
                attrs=run._attrs(query_source_safe="sdk"),
            )
            await run._otlp("traces", b.traces_request(_RESOURCE, [llm]))
            assert await run.durations() == (12000, 12000)
            await run.rollup()
            assert await run.durations() == (12000, 12000)
    finally:
        await _cleanup(engine, [run.sid])
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("old_span_end", [False, True], ids=["log-end", "llm-span-end"])
async def test_repair_compact_on_its_own_span_more_than_an_hour_after_work(old_span_end):
    engine = await _engine()
    run = _Run(engine)
    trace_id, span_id = uuid.uuid4().bytes, uuid.uuid4().bytes[:8]
    try:
        async with live_rollup_lock(engine):
            await run.hooks()
            await run.rollup()
            await run._otlp("logs", b.logs_request(_RESOURCE, [_api_request(run, 7200, "compact", trace_id, span_id)]))
            llm = b.span(
                "claude_code.llm_request",
                trace_id=trace_id,
                span_id=uuid.uuid4().bytes[:8],
                parent_span_id=span_id,
                start=run.at(7197),
                end=run.at(7200),
                attrs=run._attrs(query_source_safe="compact"),
            )
            await run._otlp("traces", b.traces_request(_RESOURCE, [llm]))
            await _persist_end(engine, run, 7197 if old_span_end else 7200)
            await run.rollup()
            assert await run.durations() == (8700, 8700)
            assert await run.stored_end_ms() == 8700
    finally:
        await _cleanup(engine, [run.sid])
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["compact", "sdk"])
async def test_command_turn_requires_a_real_model_request(source):
    engine = await _engine()
    run = _Run(engine)
    trace_id, span_id = uuid.uuid4().bytes, uuid.uuid4().bytes[:8]
    try:
        async with live_rollup_lock(engine):
            await run.hooks()
            await run.rollup()
            prompt = b.log_record(
                run.at(20),
                run._attrs(**{"event.name": "user_prompt", "command_name": "cost"}),
                trace_id=trace_id,
                span_id=span_id,
            )
            interaction = b.span(
                "claude_code.interaction",
                trace_id=trace_id,
                span_id=span_id,
                start=run.at(20),
                end=run.at(22),
                attrs=run._attrs(),
            )
            await run._otlp(
                "logs", b.logs_request(_RESOURCE, [prompt, _api_request(run, 21, source, trace_id, span_id)])
            )
            await run._otlp("traces", b.traces_request(_RESOURCE, [interaction]))
            await run.rollup()
            async with engine.acquire() as conn:
                turns = await conn.fetchval(
                    "SELECT coalesce(sum(turns), 0) FROM turns_daily WHERE session_id = $1",
                    run.sid,
                )
            assert turns == (1 if source == "sdk" else 0)
    finally:
        await _cleanup(engine, [run.sid])
        await engine.close()
