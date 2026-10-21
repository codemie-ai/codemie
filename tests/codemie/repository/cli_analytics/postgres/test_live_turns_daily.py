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

"""Value-level checks of the turn rule against the local analytics Postgres.

Telemetry shaped like Claude Code's (OTLP/JSON logs and traces, plugin hook events) goes through the
real decode and ingest; only the test's own rollup keys are recomputed, and only its own sessions are
deleted afterwards. Skipped (not failed) when the local analytics Postgres is unreachable.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from codemie.repository.cli_analytics.postgres.maintenance import quote_ident
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings

# pytest.ini forces PG_URL to a dead port for the whole suite; TEST_LOCAL_PG_URL points this file at
# a real local analytics Postgres (default: the docker-compose `postgres` service's credentials).
_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")
# Any constant shared by the xdist workers: start() on an empty schema must run in one of them at a time.
_SCHEMA_LOCK = 0x7475726E73

_JSON = "application/json"
_EMAIL = "turns.live@example.com"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_RESOURCE = {
    "attributes": [
        {"key": "service.name", "value": {"stringValue": "claude-code"}},
        {"key": "service.version", "value": {"stringValue": "2.1.0"}},
    ]
}
_READ_KEYS = "SELECT day, session_id, kinds, version, timeouts FROM rollup_dirty WHERE session_id = ANY($1::text[])"
_READ_TURNS = (
    "SELECT session_id, sum(turns)::int AS turns FROM turns_daily WHERE session_id = ANY($1::text[]) GROUP BY 1"
)
# Every table of the analytics schema keyed by session: raw tables, rollups and rollup_dirty.
_SESSION_TABLES = """
    SELECT c.relname FROM pg_class c
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'session_id' AND NOT a.attisdropped
    WHERE c.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = current_schema())
      AND c.relkind IN ('r', 'p') AND NOT c.relispartition
"""


def _ns(ts: datetime) -> str:
    return str((ts - _EPOCH) // timedelta(microseconds=1) * 1000)


def _kv(values: dict[str, str | int | float]) -> list[dict[str, object]]:
    def value(v: str | int | float) -> dict[str, object]:
        if isinstance(v, str):
            return {"stringValue": v}
        if isinstance(v, int):
            return {"intValue": str(v)}
        return {"doubleValue": v}

    return [{"key": k, "value": value(v)} for k, v in values.items()]


def _session() -> str:
    return str(uuid.uuid4())


@dataclass
class _Interaction:
    """One claude_code.interaction span and the ids its log records and hook events carry."""

    session_id: str
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc) - timedelta(minutes=30))
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    prompt_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def span(self) -> dict[str, object]:
        return {
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "name": "claude_code.interaction",
            "kind": 1,
            "startTimeUnixNano": _ns(self.ts),
            "endTimeUnixNano": _ns(self.ts + timedelta(seconds=20)),
            "attributes": _kv(
                {
                    "session.id": self.session_id,
                    "span.type": "interaction",
                    "user_prompt": "<REDACTED>",
                    "user_prompt_length": 12,
                    "interaction.sequence": 1,
                }
            ),
        }

    def log(
        self, event: str, seconds: int, observed_seconds: int | None = None, **attrs: str | int | float
    ) -> dict[str, object]:
        ts = self.ts + timedelta(seconds=seconds)
        observed = self.ts + timedelta(seconds=seconds if observed_seconds is None else observed_seconds)
        return {
            "timeUnixNano": _ns(ts),
            "observedTimeUnixNano": _ns(observed),
            "severityNumber": 9,
            "body": {"stringValue": f"claude_code.{event}"},
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "attributes": _kv(
                {
                    "event.name": event,
                    "event.timestamp": ts.isoformat().replace("+00:00", "Z"),
                    "session.id": self.session_id,
                    "user.email": _EMAIL,
                    "prompt.id": self.prompt_id,
                    **attrs,
                }
            ),
        }

    def user_prompt(self, command_name: str | None = None, observed_seconds: int | None = None) -> dict[str, object]:
        extra: dict[str, str | int] = {"prompt_length": 12, "prompt": "<REDACTED>"}
        if command_name is not None:
            extra["command_name"] = command_name
        return self.log("user_prompt", 0, observed_seconds, **extra)

    def api_request(self, seconds: int = 3) -> dict[str, object]:
        return self.log(
            "api_request",
            seconds,
            model="claude-sonnet-4-5",
            cost_usd=0.0123,
            duration_ms=2400,
            input_tokens=1200,
            output_tokens=340,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            query_source="repl_main_thread",
        )

    def api_error(self) -> dict[str, object]:
        return self.log(
            "api_error", 3, model="claude-sonnet-4-5", error="Request was aborted.", status_code="undefined"
        )

    def prompt_submit(self, prompt_body: str) -> dict[str, object]:
        return _prompt_submit(self.session_id, self.ts, self.prompt_id, prompt_body)


def _prompt_submit(session_id: str, ts: datetime, prompt_id: str, prompt_body: str) -> dict[str, object]:
    return {
        "type": "agent.prompt.submit",
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "session_id": session_id,
        "prompt_id": prompt_id,
        "prompt_body": prompt_body,
        "cwd": "/home/dev/project",
        "permission_mode": "default",
    }


def _traces(spans: list[dict[str, object]]) -> bytes:
    scope = {"scope": {"name": "com.anthropic.claude_code.tracing", "version": "2.1.0"}, "spans": spans}
    return json.dumps({"resourceSpans": [{"resource": _RESOURCE, "scopeSpans": [scope]}]}).encode()


def _logs(records: list[dict[str, object]]) -> bytes:
    scope = {"scope": {"name": "com.anthropic.claude_code.events", "version": "2.1.0"}, "logRecords": records}
    return json.dumps({"resourceLogs": [{"resource": _RESOURCE, "scopeLogs": [scope]}]}).encode()


@dataclass
class _Live:
    engine: AnalyticsPgEngine
    ingestor: PostgresTelemetryIngestor

    async def ingest(
        self,
        spans: Sequence[dict[str, object]] = (),
        logs: Sequence[dict[str, object]] = (),
        hooks: Sequence[dict[str, object]] = (),
    ) -> None:
        if spans:
            await self.ingestor.ingest_otlp("traces", _traces(list(spans)), _JSON)
        if logs:
            await self.ingestor.ingest_otlp("logs", _logs(list(logs)), _JSON)
        if hooks:
            received_ns = (datetime.now(timezone.utc) - _EPOCH) // timedelta(microseconds=1) * 1000
            await self.ingestor.ingest_hook_events(list(hooks), _EMAIL, received_ns)

    async def queued_days(self, *sessions: str) -> set[date]:
        """The days of the sessions' keys waiting in rollup_dirty."""
        async with self.engine.acquire() as conn:
            return {k["day"] for k in await conn.fetch(_READ_KEYS, list(sessions))}

    async def turns(self, *sessions: str) -> dict[str, int]:
        """Recompute the sessions' queued rollup keys and read their turns_daily total."""
        async with self.engine.acquire() as conn:
            keys = await conn.fetch(_READ_KEYS, list(sessions))
            assert keys, "ingest marked no rollup key for the sessions"
            assert await RollupRefresher(self.engine, batch_size=len(keys)).recompute(conn, keys) == len(keys)
            rows = await conn.fetch(_READ_TURNS, list(sessions))
        return {s: 0 for s in sessions} | {r["session_id"]: r["turns"] for r in rows}


async def _assert_ingest_target(engine: AnalyticsPgEngine) -> None:
    """start() logs and swallows its own errors: prove today's ingest_dedup row has a partition."""
    async with engine.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(
                "INSERT INTO ingest_dedup (day, h) VALUES ((now() AT TIME ZONE 'UTC')::date, 0) ON CONFLICT DO NOTHING"
            )
        except asyncpg.PostgresError as exc:
            pytest.fail(f"the analytics schema is not ready for ingest after start(): {exc}")
        finally:
            await tx.rollback()


@asynccontextmanager
async def _live(*sessions: str) -> AsyncIterator[_Live]:
    url = _LOCAL_PG_URL
    # Both URLs set: nothing from .env, .env.local or the environment can point the settings elsewhere.
    settings = AnalyticsPgSettings.from_config(Config(PG_URL=url, CLI_ANALYTICS_PG_URL=url))
    engine = AnalyticsPgEngine(settings)
    try:
        lock = None
        try:
            lock = await asyncpg.connect(settings.dsn)
            await lock.fetchval("SELECT 1")
        except (OSError, asyncpg.PostgresError) as exc:  # no reachable database (refused, wrong server, bad auth): skip
            if lock is not None:
                await lock.close()
            pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
        try:
            await lock.execute("SELECT pg_advisory_lock($1)", _SCHEMA_LOCK)
            await PostgresAnalyticsRuntime(engine, settings).start()
        finally:
            await lock.close()  # closing the session releases its advisory lock
        await _assert_ingest_target(engine)
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
async def test_a_typed_prompt_with_a_request_is_one_turn() -> None:
    turn = _Interaction(_session())
    async with _live(turn.session_id) as live:
        await live.ingest(spans=[turn.span()], logs=[turn.user_prompt(), turn.api_request()])
        assert await live.turns(turn.session_id) == {turn.session_id: 1}


@pytest.mark.asyncio
async def test_an_interrupted_typed_prompt_is_still_a_turn() -> None:
    turn = _Interaction(_session())
    async with _live(turn.session_id) as live:
        await live.ingest(spans=[turn.span()], logs=[turn.user_prompt()])
        assert await live.turns(turn.session_id) == {turn.session_id: 1}


@pytest.mark.asyncio
async def test_local_commands_without_a_request_are_not_turns() -> None:
    session_id = _session()
    exit_, context = _Interaction(session_id), _Interaction(session_id)
    async with _live(session_id) as live:
        await live.ingest(
            spans=[exit_.span(), context.span()],
            logs=[exit_.user_prompt(command_name="exit"), context.user_prompt(command_name="context")],
        )
        assert await live.turns(session_id) == {session_id: 0}


@pytest.mark.asyncio
async def test_commands_that_reach_the_model_are_turns() -> None:
    session_id = _session()
    compact, skill, failed = _Interaction(session_id), _Interaction(session_id), _Interaction(session_id)
    async with _live(session_id) as live:
        await live.ingest(
            spans=[compact.span(), skill.span(), failed.span()],
            logs=[
                compact.user_prompt(command_name="compact"),
                compact.api_request(),
                skill.user_prompt(command_name="probe-skill"),
                skill.api_request(),
                failed.user_prompt(command_name="compact"),
                failed.api_error(),
            ],
        )
        assert await live.turns(session_id) == {session_id: 3}


@pytest.mark.asyncio
async def test_an_auto_prompt_is_not_a_turn_even_after_leading_whitespace() -> None:
    plain, newline = _Interaction(_session()), _Interaction(_session())
    notification = "<task-notification><task-id>b7x2</task-id><status>completed</status></task-notification>"
    async with _live(plain.session_id, newline.session_id) as live:
        await live.ingest(
            spans=[plain.span(), newline.span()],
            logs=[plain.user_prompt(), plain.api_request(), newline.user_prompt(), newline.api_request()],
            hooks=[plain.prompt_submit(notification), newline.prompt_submit("\n" + notification)],
        )
        assert await live.turns(plain.session_id, newline.session_id) == {plain.session_id: 0, newline.session_id: 0}


@pytest.mark.asyncio
async def test_a_duplicated_user_prompt_counts_its_interaction_once() -> None:
    turn = _Interaction(_session())
    async with _live(turn.session_id) as live:
        # The same record observed again on an export retry: a distinct wire record, so both rows are stored.
        await live.ingest(
            spans=[turn.span()],
            logs=[turn.user_prompt(), turn.user_prompt(observed_seconds=30), turn.api_request()],
        )
        async with live.engine.acquire() as conn:
            stored = await conn.fetchval(
                "SELECT count(*) FROM log_events WHERE session_id = $1 AND event_name = 'user_prompt'", turn.session_id
            )
        assert stored == 2
        assert await live.turns(turn.session_id) == {turn.session_id: 1}


@pytest.mark.asyncio
async def test_a_command_becomes_a_turn_when_its_request_arrives_late() -> None:
    turn = _Interaction(_session())
    async with _live(turn.session_id) as live:
        await live.ingest(spans=[turn.span()], logs=[turn.user_prompt(command_name="compact")])
        assert await live.turns(turn.session_id) == {turn.session_id: 0}
        await live.ingest(logs=[turn.api_request()])
        assert await live.turns(turn.session_id) == {turn.session_id: 1}


@pytest.mark.asyncio
async def test_a_hooks_only_session_counts_every_prompt_but_auto_prompts() -> None:
    session_id = _session()
    ts = datetime.now(timezone.utc) - timedelta(minutes=30)
    async with _live(session_id) as live:
        await live.ingest(
            hooks=[
                _prompt_submit(session_id, ts, str(uuid.uuid4()), "Refactor the payment service"),
                _prompt_submit(session_id, ts + timedelta(seconds=40), str(uuid.uuid4()), ""),
                _prompt_submit(
                    session_id,
                    ts + timedelta(seconds=80),
                    str(uuid.uuid4()),
                    "<task-notification><status>completed</status></task-notification>",
                ),
            ]
        )
        assert await live.turns(session_id) == {session_id: 2}


@pytest.mark.asyncio
async def test_a_model_switch_without_a_request_is_not_a_turn() -> None:
    turn = _Interaction(_session())
    async with _live(turn.session_id) as live:
        await live.ingest(spans=[turn.span()], logs=[turn.user_prompt(command_name="model")])
        assert await live.turns(turn.session_id) == {turn.session_id: 0}


def _last_midnight() -> datetime:
    """The most recent past UTC midnight; the day before it is inside the raw retention, so its partitions exist."""
    return datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_a_command_whose_request_lands_after_midnight_is_a_turn() -> None:
    """A request arriving after midnight re-counts the day before it, where its command's span is."""
    midnight = _last_midnight()
    turn = _Interaction(_session(), ts=midnight - timedelta(seconds=10))
    async with _live(turn.session_id) as live:
        await live.ingest(spans=[turn.span()], logs=[turn.user_prompt(command_name="compact")])
        assert await live.turns(turn.session_id) == {turn.session_id: 0}
        assert await live.queued_days(turn.session_id) == set()
        await live.ingest(logs=[turn.api_request(20)])
        assert midnight.date() - timedelta(days=1) in await live.queued_days(turn.session_id)
        assert await live.turns(turn.session_id) == {turn.session_id: 1}


@pytest.mark.asyncio
async def test_an_auto_prompt_hook_after_midnight_is_not_a_turn() -> None:
    """An auto-prompt hook arriving after midnight re-counts the day before it, where its interaction's span is."""
    midnight = _last_midnight()
    turn = _Interaction(_session(), ts=midnight - timedelta(seconds=10))
    notification = "<task-notification><task-id>b7x2</task-id><status>completed</status></task-notification>"
    async with _live(turn.session_id) as live:
        await live.ingest(spans=[turn.span()], logs=[turn.user_prompt(), turn.api_request()])
        assert await live.turns(turn.session_id) == {turn.session_id: 1}
        assert await live.queued_days(turn.session_id) == set()
        await live.ingest(
            hooks=[_prompt_submit(turn.session_id, midnight + timedelta(seconds=10), turn.prompt_id, notification)]
        )
        assert midnight.date() - timedelta(days=1) in await live.queued_days(turn.session_id)
        assert await live.turns(turn.session_id) == {turn.session_id: 0}


@pytest.mark.asyncio
async def test_a_hooks_only_prompt_with_an_empty_body_after_midnight_is_a_turn() -> None:
    session_id = _session()
    ts = _last_midnight() + timedelta(seconds=10)
    async with _live(session_id) as live:
        await live.ingest(hooks=[_prompt_submit(session_id, ts, str(uuid.uuid4()), "")])
        async with live.engine.acquire() as conn:
            body_is_null = await conn.fetchval(
                "SELECT prompt_body IS NULL FROM hook_events WHERE session_id = $1", session_id
            )
        assert body_is_null is True
        assert await live.turns(session_id) == {session_id: 1}
