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

"""Session binding, lock and guards of the ingest against a disposable analytics Postgres (EPMCDME-16440).

Acceptance criteria 18-24 of the backend-ingest-routing spec: telemetry goes through the real decode and
ingest, the real rollup recompute, `backfill.reclassify` and the idle-session purge. Without
TEST_LOCAL_PG_URL every test is skipped; with it a test is skipped (not failed) when that Postgres is unreachable.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE
from codemie.configs import config
from codemie.configs.config import Config
from codemie.core.models import Application
from codemie.repository.cli_analytics.ports import SessionBinding, StalePolicyError, TelemetryStorageUnavailableError
from codemie.repository.cli_analytics.postgres import backfill, maintenance, session_purge
from codemie.repository.cli_analytics.postgres import ingestor as ingestor_module
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from codemie.repository.cli_analytics.postgres.maintenance import quote_ident
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.rest_api.security.user import User
from codemie.service.analytics.cli_analytics_project_gate import build_evidence_gate
from codemie.service.analytics.cli_analytics_project_gate import build_ingest_gate as build_legacy_gate
from tests.codemie.repository.cli_analytics.support import hook_event_builders as hb
from tests.codemie.repository.cli_analytics.support import otlp_builders as b
from tests.codemie.repository.cli_analytics.support.live_rollup_lock import live_rollup_lock

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")
_SCHEMA_LOCK = 0x6C696E6573  # the same as the other live modules: start() on an empty schema runs one at a time

_A, _B = "alice@example.com", "bob@example.com"
_PERSONAL_A = "alice@example.com-personal"
_SHARED = "team-shared"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_NOW = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
_PERSONAL = SessionBinding(_PERSONAL_A, "personal", "personal", "", "personal", "v1")
_CORPORATE = SessionBinding(_SHARED, "corporate", "default_project", "folder_not_linked", "corporate", "v1")

_SESSION_TABLES = """
    SELECT c.relname FROM pg_class c
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'session_id' AND NOT a.attisdropped
    WHERE c.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = current_schema())
      AND c.relkind IN ('r', 'p') AND NOT c.relispartition
"""
_BINDING = """SELECT owner_email, project_name, jwt_email, subscription_type, project_origin, route_note
    FROM session_dims WHERE session_id = $1"""
_LOCK_WAITERS = """SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted
    AND classid = (($1::bigint >> 32) & 4294967295)::oid AND objid = ($1::bigint & 4294967295)::oid"""


def _iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def _hook(sid: str, ts: datetime, project: str | None = None, type_: str = "agent.session.start") -> dict:
    event = hb.minimal(type_, sid, _iso(ts))
    if project is not None:
        event["codemie_project_name"] = project
    return event


def _metric(sid: str, ts: datetime = _NOW) -> bytes:
    point = b.number_point(ts, 30, {"session.id": sid}, start=ts)
    metric = b.sum_metric("claude_code.active_time.total", [point])
    return b.metrics_request({"service.name": "claude-code"}, [metric]).SerializeToString()


def _log(sid: str, seq: int = 1) -> bytes:
    attrs = {"event.name": "api_request", "session.id": sid, "event.sequence": seq, "cost_usd": 0.5}
    return b.logs_request({"service.name": "claude-code"}, [b.log_record(_NOW, attrs)]).SerializeToString()


def _trace(sid: str) -> bytes:
    span = b.span(
        "claude_code.llm_request",
        trace_id=uuid.uuid4().bytes,
        span_id=uuid.uuid4().bytes[:8],
        start=_NOW,
        end=_NOW + timedelta(seconds=1),
        attrs={"session.id": sid},
    )
    return b.traces_request({"service.name": "claude-code"}, [span]).SerializeToString()


@dataclass
class _Live:
    engine: AnalyticsPgEngine
    ingestor: PostgresTelemetryIngestor

    async def hooks(self, events: list[dict], user: str = _A, binding: SessionBinding | None = None) -> dict:
        received_ns = (datetime.now(timezone.utc) - _EPOCH) // timedelta(microseconds=1) * 1000
        result = await self.ingestor.ingest_hook_events(events, user, received_ns, binding=binding)
        return json.loads(result.body)

    async def otlp(self, signal, body: bytes, user: str = _A, binding: SessionBinding | None = None) -> str:
        """The partial_success error message ('' when stored)."""
        result = await self.ingestor.ingest_otlp(
            signal, body, "application/x-protobuf", user_email=user, binding=binding
        )
        return _partial_message(signal, result.body)

    async def binding(self, sid: str) -> tuple | None:
        async with self.engine.acquire() as conn:
            row = await conn.fetchrow(_BINDING, sid)
        return tuple(row.values()) if row else None

    async def count(self, table: str, sid: str) -> int:
        async with self.engine.acquire() as conn:
            return await conn.fetchval(f"SELECT count(*) FROM {table} WHERE session_id = $1", sid)

    async def hook_projects(self, sid: str) -> list[tuple]:
        async with self.engine.acquire() as conn:
            rows = await conn.fetch(
                "SELECT codemie_project_name, subscription_type, project_origin FROM hook_events "
                "WHERE session_id = $1 ORDER BY ts",
                sid,
            )
        return [tuple(r.values()) for r in rows]

    async def recompute(self, sid: str) -> None:
        async with self.engine.acquire() as conn:
            keys = await conn.fetch(
                "SELECT day, session_id, kinds, version, timeouts FROM rollup_dirty WHERE session_id = $1", sid
            )
            if keys:
                assert await RollupRefresher(self.engine, batch_size=len(keys)).recompute(conn, keys) == len(keys)

    async def mark_all_days(self, sid: str) -> None:
        """Queue every day of the session with every rollup family, like a backfill rebuild."""
        async with self.engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO rollup_dirty (day, session_id, kinds) SELECT DISTINCT (ts AT TIME ZONE 'UTC')::date, "
                "session_id, 2147483647 FROM hook_events WHERE session_id = $1 "
                "ON CONFLICT (day, session_id) DO UPDATE SET kinds = 2147483647, version = rollup_dirty.version + 1",
                sid,
            )


def _partial_message(signal: str, body: bytes) -> str:
    response_type, _field = ingestor_module._OTLP_RESPONSES[signal]
    response = response_type()
    response.ParseFromString(body)
    return response.partial_success.error_message


@asynccontextmanager
async def _live(*sessions: str) -> AsyncIterator[_Live]:
    settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL, CLI_ANALYTICS_PG_URL=_LOCAL_PG_URL))
    engine = AnalyticsPgEngine(settings)
    try:
        try:
            lock = await asyncpg.connect(settings.dsn)
        except (OSError, asyncpg.PostgresError) as exc:
            pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
        try:
            await lock.execute("SELECT pg_advisory_lock($1)", _SCHEMA_LOCK)
            await PostgresAnalyticsRuntime(engine, settings).start()
        finally:
            await lock.close()
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


def _sid() -> str:
    return str(uuid.uuid4())


# ── AC18: binding atomicity ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["hooks", "usage", "logs", "metrics", "traces"])
async def test_the_first_accepted_evidence_request_binds_the_session_on_every_route(route) -> None:
    sid = _sid()
    async with _live(sid) as live:
        if route == "hooks":
            assert await live.hooks([_hook(sid, _NOW, "whatever")], binding=_CORPORATE) == {}
        elif route == "usage":  # usage-only: no hook row
            assert await live.hooks([hb.usage_request(session_id=sid, timestamp=_iso(_NOW))], binding=_CORPORATE) == {}
            assert await live.count("hook_events", sid) == 0
        else:
            body = {"logs": _log, "metrics": _metric, "traces": _trace}[route](sid)
            assert await live.otlp(route, body, binding=_CORPORATE) == ""
        assert await live.binding(sid) == (_A, _SHARED, _A, "corporate", "default_project", "folder_not_linked")


@pytest.mark.asyncio
async def test_a_legacy_request_creates_no_binding() -> None:
    sid = _sid()
    async with _live(sid) as live:
        assert await live.otlp("metrics", _metric(sid)) == ""
        assert await live.hooks([_hook(sid, _NOW, _SHARED)]) == {}
        assert await live.binding(sid) is None


@pytest.mark.asyncio
async def test_a_failing_insert_rolls_back_binding_and_rows_together() -> None:
    sid = _sid()
    async with _live(sid) as live:
        failing = patch.object(ingestor_module, "_insert_rows", side_effect=asyncpg.PostgresError("boom"))
        with failing, pytest.raises(TelemetryStorageUnavailableError):
            await live.hooks([_hook(sid, _NOW)], binding=_PERSONAL)
        assert await live.binding(sid) is None
        assert await live.count("hook_events", sid) == 0


@pytest.mark.asyncio
async def test_a_rejected_request_leaves_no_binding_and_no_rows() -> None:
    """A Personal evidence request on a session with legacy shared rows is discarded: no binding."""
    sid = _sid()
    async with _live(sid) as live:
        await live.hooks([_hook(sid, _NOW, _SHARED)])
        answer = await live.hooks([_hook(sid, _NOW + timedelta(seconds=1))], binding=_PERSONAL)
        assert answer == {
            "accepted": 0,
            "dropped": 1,
            "reason": "session_project_conflict",
            "other_sessions_dropped": 0,
        }
        assert await live.binding(sid) is None
        assert await live.count("hook_events", sid) == 1


# ── AC19: older legacy rows on a bound session ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_earlier_legacy_hook_of_another_project_is_discarded_and_empty_or_same_are_stamped() -> None:
    sid = _sid()
    async with _live(sid) as live:
        await live.hooks([_hook(sid, _NOW)], binding=_PERSONAL)
        before = await live.binding(sid)

        answer = await live.hooks([_hook(sid, _NOW - timedelta(hours=1), _SHARED)])
        assert answer["reason"] == "session_project_conflict"
        assert await live.count("hook_events", sid) == 1

        assert await live.hooks([_hook(sid, _NOW - timedelta(hours=2), "")]) == {}
        assert (
            await live.hooks([_hook(sid, _NOW - timedelta(hours=3), _PERSONAL_A.upper(), "agent.prompt.submit")]) == {}
        )
        stamped = await live.hook_projects(sid)
        assert stamped == [(_PERSONAL_A, "personal", "personal")] * 3

        await live.recompute(sid)
        assert await live.binding(sid) == before


# ── AC20: another user ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_another_user_is_discarded_on_every_route_and_the_binding_stays() -> None:
    sid = _sid()
    async with _live(sid) as live:
        await live.hooks([_hook(sid, _NOW)], binding=_CORPORATE)
        before = await live.binding(sid)

        assert (await live.hooks([_hook(sid, _NOW, _SHARED, "agent.prompt.submit")], user=_B))["reason"] == (
            "session_owner_conflict"
        )
        assert (await live.hooks([_hook(sid, _NOW, None, "agent.tool.start")], user=_B, binding=_CORPORATE))[
            "reason"
        ] == "session_owner_conflict"
        assert await live.otlp("logs", _log(sid), user=_B) == "session_owner_conflict"
        assert await live.otlp("metrics", _metric(sid), user=_B, binding=_CORPORATE) == "session_owner_conflict"
        assert await live.otlp("traces", _trace(sid), user=_B) == "session_owner_conflict"

        assert await live.binding(sid) == before
        assert await live.count("hook_events", sid) == 1
        for table in ("log_events", "metric_points", "spans"):
            assert await live.count(table, sid) == 0


# ── AC21: concurrent first writes ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_concurrent_first_evidence_writes_serialize_and_one_owner_wins() -> None:
    sid = _sid()
    async with _live(sid) as live:
        answers = await asyncio.gather(
            live.hooks([_hook(sid, _NOW)], user=_A, binding=_CORPORATE),
            live.hooks([_hook(sid, _NOW + timedelta(seconds=1))], user=_B, binding=_CORPORATE),
        )
        assert sorted(a.get("reason", "") for a in answers) == ["", "session_owner_conflict"]
        owner = (await live.binding(sid))[0]
        assert owner in (_A, _B)
        assert await live.count("hook_events", sid) == 1


@pytest.mark.asyncio
async def test_every_ingest_waits_for_the_session_lock() -> None:
    """The lock covers every session of a batch now, not only lines sessions (a plain metric here)."""
    sid = _sid()
    async with _live(sid) as live:
        key = live.engine.lock_key(f"ingest-session:{sid}")
        async with live.engine.acquire() as holder:
            await holder.execute("SELECT pg_advisory_lock($1)", key)
            try:
                ingest = asyncio.create_task(live.otlp("metrics", _metric(sid)))
                deadline = asyncio.get_running_loop().time() + 30
                while not await holder.fetchval(_LOCK_WAITERS, key):
                    assert not ingest.done(), "the ingest finished while the session lock was held"
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0.05)
            finally:
                await holder.execute("SELECT pg_advisory_unlock($1)", key)
            assert await asyncio.wait_for(ingest, timeout=30) == ""


@pytest.mark.asyncio
async def test_legacy_first_then_evidence_binds_to_the_legacy_project_or_discards_personal() -> None:
    shared_sid, personal_sid = _sid(), _sid()
    async with _live(shared_sid, personal_sid) as live:
        await live.hooks([_hook(shared_sid, _NOW, "legacy-proj")])
        assert await live.hooks([_hook(shared_sid, _NOW + timedelta(seconds=1))], binding=_CORPORATE) == {}
        assert (await live.binding(shared_sid))[:2] == (_A, "legacy-proj")
        assert [p for p, *_ in await live.hook_projects(shared_sid)] == ["legacy-proj", "legacy-proj"]

        await live.hooks([_hook(personal_sid, _NOW, _SHARED)])
        answer = await live.hooks([_hook(personal_sid, _NOW + timedelta(seconds=1))], binding=_PERSONAL)
        assert answer["reason"] == "session_project_conflict"


# ── AC22: hookless binding, then raw expiry ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_hookless_binding_survives_raw_expiry_and_guards_later_writes() -> None:
    sid = _sid()
    async with _live(sid) as live:
        assert await live.otlp("metrics", _metric(sid), binding=_PERSONAL) == ""
        async with live.engine.acquire() as conn:  # the raw rows expire before any recompute
            await conn.execute("DELETE FROM metric_points WHERE session_id = $1", sid)
        bound = await live.binding(sid)
        assert bound[:2] == (_A, _PERSONAL_A)

        # (a) same user and destination: stored
        assert await live.hooks([_hook(sid, _NOW)], binding=_PERSONAL) == {}
        # (b) a different, non-Personal destination: stored under the bound personal project
        assert (
            await live.hooks([_hook(sid, _NOW + timedelta(seconds=1), None, "agent.tool.start")], binding=_CORPORATE)
            == {}
        )
        assert {p for p, *_ in await live.hook_projects(sid)} == {_PERSONAL_A}
        # (c) another user
        assert await live.otlp("metrics", _metric(sid), user=_B) == "session_owner_conflict"
        # (d) a contradictory legacy hook
        assert (await live.hooks([_hook(sid, _NOW, _SHARED, "agent.prompt.submit")]))["reason"] == (
            "session_project_conflict"
        )
        assert await live.binding(sid) == bound


@pytest.mark.asyncio
async def test_a_personal_request_on_a_session_bound_to_a_shared_project_is_discarded() -> None:
    sid = _sid()
    async with _live(sid) as live:
        await live.otlp("traces", _trace(sid), binding=_CORPORATE)
        answer = await live.hooks([_hook(sid, _NOW)], binding=_PERSONAL)
        assert answer["reason"] == "session_project_conflict"
        assert await live.count("hook_events", sid) == 0


@pytest.mark.asyncio
async def test_the_idle_purge_removes_only_a_stale_binding_and_its_id_is_then_new() -> None:
    stale, active = _sid(), _sid()
    async with _live(stale, active) as live:
        await live.otlp("metrics", _metric(stale), binding=_CORPORATE)
        await live.otlp("metrics", _metric(active), binding=_CORPORATE)
        async with live.engine.acquire() as conn:
            await conn.execute("DELETE FROM metric_points WHERE session_id = ANY($1::text[])", [stale, active])
            await conn.execute(
                "UPDATE session_dims SET updated_at = now() - interval '400 days' WHERE session_id = $1", stale
            )
            cutoff = datetime.now(timezone.utc).date() - timedelta(days=200)
            await session_purge.purge_idle_sessions(
                conn,
                "($1::date::timestamp AT TIME ZONE 'UTC')",
                "(($1::date + 201)::timestamp AT TIME ZONE 'UTC')",
                cutoff,
                100,
                maintenance.MaintenanceReport(),
            )
        assert await live.binding(stale) is None
        assert (await live.binding(active))[0] == _A

        assert await live.otlp("metrics", _metric(stale), user=_B, binding=_CORPORATE) == ""
        assert (await live.binding(stale))[0] == _B


# ── AC23: recompute never overrides a binding ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recompute_and_reclassify_keep_the_binding_against_an_older_replayed_row() -> None:
    bound_sid, legacy_sid = _sid(), _sid()
    async with _live(bound_sid, legacy_sid) as live:
        await live.hooks([_hook(bound_sid, _NOW)], binding=_PERSONAL)
        await live.hooks([_hook(legacy_sid, _NOW, "later-proj")])
        await live.recompute(bound_sid)
        await live.recompute(legacy_sid)
        before = await live.binding(bound_sid)
        assert before[:3] == (_A, _PERSONAL_A, _A)

        async with live.engine.acquire() as conn:  # a replayed older row of another project and a larger email
            for sid in (bound_sid, legacy_sid):
                await conn.execute(
                    "INSERT INTO hook_events (ts, session_id, event_type, codemie_project_name, user_email) "
                    "VALUES ($1, $2, 'agent.session.start', 'older-proj', 'zzz@example.com')",
                    _NOW - timedelta(hours=1),
                    sid,
                )
            day = _NOW.date()
            await backfill.reclassify(conn, day - timedelta(days=1), day)
        for sid in (bound_sid, legacy_sid):
            await live.mark_all_days(sid)
            await live.recompute(sid)

        assert await live.binding(bound_sid) == before
        # Control: an unbound session still follows its earliest row and the larger email.
        assert (await live.binding(legacy_sid))[1:3] == ("older-proj", "zzz@example.com")


# ── AC24: mixed batch ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_mixed_legacy_batch_is_discarded_whole_and_counts_its_other_sessions() -> None:
    bound_sid, other_sid = _sid(), _sid()
    async with _live(bound_sid, other_sid) as live:
        await live.hooks([_hook(bound_sid, _NOW)], binding=_PERSONAL)
        counted = ingestor_module.SESSION_DISCARDS["session_project_conflict"]

        answer = await live.hooks(
            [_hook(bound_sid, _NOW, _SHARED, "agent.prompt.submit"), _hook(other_sid, _NOW, _SHARED)]
        )

        assert answer == {
            "accepted": 0,
            "dropped": 2,
            "reason": "session_project_conflict",
            "other_sessions_dropped": 1,
        }
        assert ingestor_module.SESSION_DISCARDS["session_project_conflict"] == counted + 1
        assert await live.count("hook_events", other_sid) == 0
        assert await live.count("hook_events", bound_sid) == 1


# ── Task 6: the destination is re-checked under the lock; the boundary; readers ─────────────────


class _Gate:
    """A gate that accepts, hands over the candidate binding and a destination validator."""

    def __init__(self, binding: SessionBinding, validate) -> None:
        self.binding = binding
        self.validate_destination = validate
        self.checked: list[SessionBinding] = []
        self._validate = validate

        async def record(effective: SessionBinding, bound: bool = False):
            self.checked.append(effective)
            return await self._validate(effective)

        self.validate_destination = record

    async def __call__(self, _explicit) -> None:
        return None


async def _accept(_binding: SessionBinding) -> None:
    return None


@pytest.mark.asyncio
async def test_a_legacy_project_that_replaces_the_candidate_is_checked_before_anything_is_written() -> None:
    sid = _sid()
    async with _live(sid) as live:
        await live.hooks([_hook(sid, _NOW, "legacy-proj")])  # legacy first: the session is unbound under legacy-proj
        rows = await live.count("hook_events", sid)

        async def disabled(effective: SessionBinding) -> str | None:
            return "cli_analytics_disabled" if effective.project_name == "legacy-proj" else None

        gate = _Gate(_CORPORATE, disabled)
        result = await live.ingestor.ingest_otlp("metrics", _metric(sid), "application/x-protobuf", gate, user_email=_A)

        assert _partial_message("metrics", result.body) == "cli_analytics_disabled"
        assert [b.project_name for b in gate.checked] == ["legacy-proj"]  # the effective project, not the candidate
        assert await live.binding(sid) is None and await live.count("hook_events", sid) == rows
        assert await live.count("metric_points", sid) == 0


@pytest.mark.asyncio
async def test_a_bound_personal_session_still_runs_the_destination_checks() -> None:
    sid = _sid()
    async with _live(sid) as live:
        await live.hooks([_hook(sid, _NOW)], binding=_PERSONAL)

        async def unavailable(effective: SessionBinding) -> str | None:
            raise TelemetryStorageUnavailableError("session_project_unavailable")

        gate = _Gate(_CORPORATE, unavailable)  # derived Corporate, bound Personal: stored under the personal project
        with pytest.raises(TelemetryStorageUnavailableError, match="session_project_unavailable"):
            await live.ingestor.ingest_otlp("traces", _trace(sid), "application/x-protobuf", gate, user_email=_A)

        assert [(b.project_name, b.subscription_type) for b in gate.checked] == [(_PERSONAL_A, "personal")]
        assert await live.count("spans", sid) == 0
        assert (await live.binding(sid))[1] == _PERSONAL_A


@pytest.mark.asyncio
async def test_the_candidate_that_the_resolver_already_checked_is_not_checked_again() -> None:
    sid = _sid()
    async with _live(sid) as live:
        gate = _Gate(_CORPORATE, _accept)

        result = await live.ingestor.ingest_otlp("metrics", _metric(sid), "application/x-protobuf", gate, user_email=_A)

        assert _partial_message("metrics", result.body) == "" and gate.checked == []
        assert (await live.binding(sid))[1] == _SHARED  # the binding came from the gate, not a keyword


@pytest.mark.asyncio
@pytest.mark.parametrize("signal", ["logs", "metrics", "traces"])
async def test_an_evidence_request_with_another_session_id_is_discarded_before_the_lock(signal) -> None:
    sid, other = _sid(), _sid()
    async with _live(sid, other) as live:
        body = {"logs": _log, "metrics": _metric, "traces": _trace}[signal](other)

        result = await live.ingestor.ingest_otlp(
            signal, body, "application/x-protobuf", user_email=_A, binding=_CORPORATE, evidence_session_id=sid
        )

        assert _partial_message(signal, result.body) == "evidence_session_conflict"
        assert await live.binding(sid) is None and await live.binding(other) is None


@pytest.mark.asyncio
async def test_a_binding_only_row_appears_in_no_dashboard_list() -> None:
    from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
    from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader

    sid = _sid()
    async with _live(sid) as live:
        window = LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE, start_dt=_NOW - timedelta(days=2), end_dt=_NOW + timedelta(days=1)
        )
        reader = PostgresCliAnalyticsReader(live.engine)
        before = len(await reader.get_session_start_times(window))
        await live.otlp("metrics", _metric(sid), binding=_CORPORATE)
        assert await live.binding(sid) is not None

        assert len(await reader.get_session_start_times(window)) == before
        assert [r for r in await reader.get_repository_sessions(window) if r["session_id"] == sid] == []
        assert [r for r in await reader.get_users(window) if sid in str(r)] == []


# ── AC17: two sequential evidence batches of one session ────────────────────────────────────


@pytest.mark.asyncio
async def test_sequential_evidence_batches_follow_the_first_binding() -> None:
    same, to_personal, to_shared, other = _sid(), _sid(), _sid(), _sid()
    changed = SessionBinding("team-changed", "corporate", "registered_repository", "", "corporate", "v2")
    async with _live(same, to_personal, to_shared, other) as live:
        # same destination: both stored
        for second in (0, 1):
            assert await live.hooks([_hook(same, _NOW + timedelta(seconds=second))], binding=_CORPORATE) == {}
        assert [p for p, *_ in await live.hook_projects(same)] == [_SHARED, _SHARED]

        # Corporate then Personal: second discarded, nothing more stored
        await live.hooks([_hook(other, _NOW)], binding=_CORPORATE)
        answer = await live.hooks([_hook(other, _NOW + timedelta(seconds=1))], binding=_PERSONAL)
        assert answer["reason"] == "session_project_conflict"
        assert await live.count("hook_events", other) == 1

        # Personal then Corporate: second stored under the personal project
        await live.hooks([_hook(to_personal, _NOW)], binding=_PERSONAL)
        assert await live.hooks([_hook(to_personal, _NOW + timedelta(seconds=1))], binding=_CORPORATE) == {}
        assert {p for p, *_ in await live.hook_projects(to_personal)} == {_PERSONAL_A}

        # shared, then a changed registration/default: stored under the bound shared project
        await live.hooks([_hook(to_shared, _NOW)], binding=_CORPORATE)
        assert await live.hooks([_hook(to_shared, _NOW + timedelta(seconds=1))], binding=changed) == {}
        assert {p for p, *_ in await live.hook_projects(to_shared)} == {_SHARED}
        assert (await live.binding(to_shared))[:2] == (_A, _SHARED)


# ── review CR-002..004, CR-006, CR-010: the destination chosen under the lock, with the real resolver ─────────

GATE = "codemie.service.analytics.cli_analytics_project_gate"
_RECEIVED_NS = 1_700_000_000_000_000_000


def _app(name: str, **kw) -> Application:
    return Application(id=name, name=name, cli_analytics_enabled=True, **kw)


@asynccontextmanager
async def _world(*apps: Application):
    """The application lookups the real EvidenceGate makes, answered from `apps` (the database is the real one)."""
    by_name = {a.name.lower(): a for a in apps}
    provision = AsyncMock(return_value=True)

    @asynccontextmanager
    async def session():
        yield object()

    with (
        patch(f"{GATE}.get_async_session", session),
        patch(
            f"{GATE}.application_repository.aget_active_by_name_case_insensitive",
            AsyncMock(side_effect=lambda _s, name: by_name.get(name.lower())),
        ),
        patch(f"{GATE}.user_project_repository.aget_by_user_and_project", new_callable=AsyncMock),
        patch(f"{GATE}.personal_project_service.ensure_personal_project_async", provision),
        patch.object(config, "ENABLE_USER_MANAGEMENT", False),
        patch.object(User, "has_access_to_application", lambda self, name: True),
    ):
        yield provision


def _gate(subscription: str):
    return build_evidence_gate(
        User(id="u-1", email=_A),
        header_project=None,
        project_source=None,
        repo_remote=None,
        subscription_type=subscription,
        client_subscription_type=subscription,
        client_policy_version="v1",
    )


async def _evidence(live: _Live, sid: str, gate, user: str = _A, at: datetime = _NOW, **kw) -> dict:
    result = await live.ingestor.ingest_hook_events(
        [_hook(sid, at)], user, _RECEIVED_NS, gate=gate, evidence_session_id=sid, **kw
    )
    return json.loads(result.body)


@pytest.mark.asyncio
async def test_personal_with_the_real_resolver_is_stored_under_the_owners_personal_project() -> None:
    sid = _sid()
    async with _live(sid) as live, _world(_app(_A, project_type=Application.ProjectType.PERSONAL, created_by="u-1")):
        answer = await _evidence(live, sid, _gate("personal"))

        assert answer == {}
        assert await live.binding(sid) == (_A, _A, _A, "personal", "personal", "")
        assert await live.hook_projects(sid) == [(_A, "personal", "personal")]


@pytest.mark.asyncio
async def test_personal_provisioning_failure_keeps_the_session_unwritten() -> None:
    sid = _sid()
    async with _live(sid) as live, _world() as provision:
        provision.return_value = False
        with pytest.raises(TelemetryStorageUnavailableError, match="personal_destination_unavailable"):
            await _evidence(live, sid, _gate("personal"))

        provision.assert_awaited_once()
        assert await live.binding(sid) is None and await live.count("hook_events", sid) == 0


@pytest.mark.asyncio
async def test_a_stale_policy_leaves_an_existing_binding_and_its_rows_intact() -> None:
    sid = _sid()
    async with _live(sid) as live:
        await live.hooks([_hook(sid, _NOW)], binding=_CORPORATE)
        async with live.engine.acquire() as conn:
            before = await conn.fetchrow("SELECT * FROM session_dims WHERE session_id = $1", sid)
        rows = await live.count("hook_events", sid)

        with pytest.raises(StalePolicyError):
            await _evidence(live, sid, _gate("personal"), stale_policy_version="v2")

        async with live.engine.acquire() as conn:
            after = await conn.fetchrow("SELECT * FROM session_dims WHERE session_id = $1", sid)
        assert dict(after) == dict(before)  # not even updated_at moved
        assert await live.count("hook_events", sid) == rows


@pytest.mark.asyncio
async def test_a_bound_session_is_stored_under_its_binding_whatever_its_new_candidate_would_do() -> None:
    """The bound destination is validated alone: a candidate that cannot be resolved (here a default that no
    longer exists) neither discards nor delays a valid bound session, and the candidate is never resolved."""
    sid = _sid()
    async with _live(sid) as live, _world(_app(_SHARED)):
        await live.hooks([_hook(sid, _NOW)], binding=_CORPORATE)

        with patch(f"{GATE}.resolve_destination", side_effect=AssertionError("the candidate must not be resolved")):
            answer = await _evidence(live, sid, _gate("corporate"), at=_NOW + timedelta(seconds=1))

        assert answer == {}
        assert await live.count("hook_events", sid) == 2
        assert {row[0] for row in await live.hook_projects(sid)} == {_SHARED}


@pytest.mark.asyncio
async def test_a_personal_request_on_a_shared_bound_session_is_discarded_without_provisioning() -> None:
    sid = _sid()
    async with _live(sid) as live, _world(_app(_SHARED)) as provision:
        await live.hooks([_hook(sid, _NOW)], binding=_CORPORATE)

        answer = await _evidence(live, sid, _gate("personal"))

        assert answer["reason"] == "session_project_conflict"
        provision.assert_not_awaited()
        assert await live.count("hook_events", sid) == 1
        assert (await live.binding(sid))[1] == _SHARED


@pytest.mark.asyncio
async def test_a_disabled_bound_project_discards_the_session_under_the_lock() -> None:
    sid = _sid()
    async with _live(sid) as live, _world(Application(id="s", name=_SHARED, cli_analytics_enabled=False)):
        await live.hooks([_hook(sid, _NOW)], binding=_CORPORATE)

        answer = await _evidence(live, sid, _gate("corporate"))

        assert answer["reason"] == "cli_analytics_disabled" and await live.count("hook_events", sid) == 1


@pytest.mark.asyncio
async def test_a_concurrent_first_binding_waits_and_then_resolves_against_the_committed_binding() -> None:
    """Request B reaches the lock while A holds it: B's resolver gets A's committed binding, so what B writes is
    the bound project and never an unchecked candidate of its own."""
    sid = _sid()
    seen: list[SessionBinding | None] = []

    class _Slow:
        def __init__(self, binding: SessionBinding, delay: float) -> None:
            self._binding, self._delay = binding, delay

        async def __call__(self, _explicit) -> None:
            return None

        async def resolve_session(self, bound, _legacy, _remote) -> SessionBinding:
            seen.append(bound)
            await asyncio.sleep(self._delay)  # holds the session lock
            return bound or self._binding

    candidate_b = SessionBinding("candidate-b", "corporate", "default_project", "folder_not_linked", "corporate", "v1")
    async with _live(sid) as live:
        first = asyncio.create_task(_evidence(live, sid, _Slow(_CORPORATE, 0.5)))
        await asyncio.sleep(0.2)
        second = asyncio.create_task(_evidence(live, sid, _Slow(candidate_b, 0.0)))
        answers = await asyncio.gather(first, second)

        assert answers == [{}, {}]
        assert seen[0] is None and seen[1].project_name == _SHARED  # B saw A's binding, not an empty session
        assert {row[0] for row in await live.hook_projects(sid)} == {_SHARED}
        assert (await live.binding(sid))[1] == _SHARED


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["hooks", "metrics"])
async def test_a_missing_sender_is_a_discard_on_a_bound_session_and_for_an_evidence_request(route) -> None:
    sid = _sid()
    async with _live(sid) as live:
        await live.hooks([_hook(sid, _NOW)], binding=_CORPORATE)
        before = await live.binding(sid)

        if route == "hooks":
            legacy = await live.ingestor.ingest_hook_events([_hook(sid, _NOW)], "", _RECEIVED_NS)
            assert json.loads(legacy.body)["reason"] == "session_owner_conflict"
            evidence = await live.ingestor.ingest_hook_events(
                [_hook(sid, _NOW)], "", _RECEIVED_NS, binding=_CORPORATE, evidence_session_id=sid
            )
            assert json.loads(evidence.body)["reason"] == "session_owner_conflict"
        else:
            assert await live.otlp("metrics", _metric(sid), user="") == "session_owner_conflict"

        assert await live.binding(sid) == before and await live.count("hook_events", sid) == 1


@pytest.mark.asyncio
async def test_the_owner_is_the_same_user_whatever_the_case_of_the_email() -> None:
    sid = _sid()
    async with _live(sid) as live:
        await live.hooks([_hook(sid, _NOW)], binding=_CORPORATE)

        answer = await live.hooks([_hook(sid, _NOW + timedelta(seconds=1))], user=_A.upper())

        assert answer == {} and await live.count("hook_events", sid) == 2
        assert (await live.binding(sid))[0] == _A  # the stored owner is unchanged


# ── IR-001: a known legacy owner is not transferred ─────────────────────────────────────────────────────


async def _tables_snapshot(live: _Live, sid: str) -> tuple:
    counts = [await live.count(t, sid) for t in ("hook_events", "log_events", "metric_points", "spans")]
    async with live.engine.acquire() as conn:
        dims = await conn.fetchrow("SELECT * FROM session_dims WHERE session_id = $1", sid)
    return tuple(counts), tuple(dims.values()) if dims else None


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["hooks", "metrics", "logs", "traces"])
async def test_another_user_cannot_adopt_a_legacy_session_of_a_known_owner(route) -> None:
    sid = _sid()
    async with _live(sid) as live:
        await live.hooks([_hook(sid, _NOW, "team")], user=_A)  # legacy raw hook row of Alice
        before = await _tables_snapshot(live, sid)

        if route == "hooks":
            answer = (await live.hooks([_hook(sid, _NOW, None, "agent.tool.start")], user=_B, binding=_CORPORATE))[
                "reason"
            ]
        else:
            body = {"logs": _log, "metrics": _metric, "traces": _trace}[route](sid)
            answer = await live.otlp(route, body, user=_B, binding=_CORPORATE)
        assert answer == "session_owner_conflict"
        assert await _tables_snapshot(live, sid) == before and await live.binding(sid) is None

        # the known owner binds it, whatever the case of her email
        assert await live.hooks([_hook(sid, _NOW, None, "agent.tool.start")], user=_A.upper(), binding=_CORPORATE) == {}
        assert (await live.binding(sid))[:2] == (_A.upper(), "team")


@pytest.mark.asyncio
async def test_a_retained_session_dims_identity_protects_the_session_after_raw_expiry() -> None:
    sid = _sid()
    async with _live(sid) as live:
        async with live.engine.acquire() as conn:  # raw hook rows expired; the dimension row stays
            await conn.execute(
                "INSERT INTO session_dims (session_id, project_name, jwt_email) VALUES ($1, 'team', $2)", sid, _A
            )
        before = await _tables_snapshot(live, sid)

        assert await live.otlp("metrics", _metric(sid), user=_B, binding=_CORPORATE) == "session_owner_conflict"
        assert await _tables_snapshot(live, sid) == before

        assert await live.otlp("metrics", _metric(sid), user=_A, binding=_CORPORATE) == ""
        assert (await live.binding(sid))[:2] == (_A, "team")


@pytest.mark.asyncio
async def test_a_legacy_session_with_no_known_identity_is_still_adopted() -> None:
    sid = _sid()
    async with _live(sid) as live:
        async with live.engine.acquire() as conn:
            await conn.execute("INSERT INTO session_dims (session_id, project_name) VALUES ($1, 'team')", sid)
        await live.hooks([_hook(sid, _NOW, "team")], user="")  # a hook row with no sender email

        assert await live.otlp("metrics", _metric(sid), user=_B, binding=_CORPORATE) == ""
        assert (await live.binding(sid))[:2] == (_B, "team")


# ── IR-002: fresh server-side activity keeps a bound session ────────────────────────────────────────────


async def _purge(live: _Live) -> None:
    async with live.engine.acquire() as conn:
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=200)
        await session_purge.purge_idle_sessions(
            conn,
            "($1::date::timestamp AT TIME ZONE 'UTC')",
            "(($1::date + 201)::timestamp AT TIME ZONE 'UTC')",
            cutoff,
            100,
            maintenance.MaintenanceReport(),
        )


async def _age(live: _Live, sid: str, dims: str, updated: str) -> None:
    """Set the dimension times (`dims` is a SQL interval, or 'future'/'null') and updated_at (an interval ago)."""
    value = {"null": "NULL", "future": "now() + interval '400 days'"}.get(dims, f"now() - interval '{dims}'")
    async with live.engine.acquire() as conn:
        await conn.execute(
            f"UPDATE session_dims SET started_at = {value}, last_event_at = {value}, "
            f"updated_at = now() - interval '{updated}' WHERE session_id = $1",
            sid,
        )


@pytest.mark.asyncio
async def test_a_resumed_bound_session_with_old_dimension_times_is_kept_while_it_is_written_to() -> None:
    sid = _sid()
    async with _live(sid) as live:
        await live.otlp("metrics", _metric(sid), binding=_CORPORATE)
        await _age(live, sid, "400 days", "0 days")  # old dimension hooks, fresh hookless write
        await _purge(live)
        assert (await live.binding(sid))[0] == _A and await live.count("metric_points", sid) == 1

        # it idles: the write is older than the cutoff too
        await _age(live, sid, "400 days", "300 days")
        await _purge(live)
        assert await live.binding(sid) is None


@pytest.mark.asyncio
async def test_a_fresh_write_refreshes_a_stale_bound_session_and_the_owner_check_still_holds() -> None:
    sid = _sid()
    async with _live(sid) as live:
        await live.otlp("metrics", _metric(sid), binding=_CORPORATE)
        await _age(live, sid, "400 days", "300 days")  # would be purged now

        assert await live.otlp("traces", _trace(sid), binding=_CORPORATE) == ""  # same owner resumes
        assert await live.otlp("traces", _trace(sid), user=_B) == "session_owner_conflict"
        await _purge(live)

        assert (await live.binding(sid))[0] == _A and await live.count("spans", sid) == 1


@pytest.mark.asyncio
async def test_a_future_client_time_does_not_keep_a_bound_session_and_legacy_retention_is_unchanged() -> None:
    bound, legacy = _sid(), _sid()
    async with _live(bound, legacy) as live:
        await live.otlp("metrics", _metric(bound), binding=_CORPORATE)
        await _age(live, bound, "future", "300 days")
        async with live.engine.acquire() as conn:  # legacy: an old hook time wins over a fresh updated_at
            await conn.execute(
                "INSERT INTO session_dims (session_id, started_at, last_event_at, updated_at) "
                "VALUES ($1, now() - interval '400 days', now() - interval '400 days', now())",
                legacy,
            )
        await _purge(live)

        assert await live.binding(bound) is None
        assert await live.binding(legacy) is None


# ── EPMCDME-16440: a bound session whose project is confirmed unavailable is a final discard ─────────────────


async def _send(live: _Live, route: str, sid: str, gate, user: str = _A) -> str:
    """The discard reason of one evidence request on `route`, '' when accepted; a retryable failure raises."""
    if route == "hooks":
        return (await _evidence(live, sid, gate, user)).get("reason", "")
    body = {"logs": _log, "metrics": _metric, "traces": _trace}[route](sid)
    result = await live.ingestor.ingest_otlp(
        route, body, "application/x-protobuf", gate, user_email=user, evidence_session_id=sid
    )
    return _partial_message(route, result.body)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["hooks", "logs", "metrics", "traces"])
async def test_a_bound_session_whose_project_is_gone_is_discarded_finally_on_every_route(route) -> None:
    sid = _sid()
    async with _live(sid) as live:
        await live.hooks([_hook(sid, _NOW)], binding=_CORPORATE)
        before = await live.binding(sid)

        async with _world() as provision:  # the bound project was renamed, deleted or became inaccessible
            assert await _send(live, route, sid, _gate("corporate")) == "session_project_unavailable"
            provision.assert_not_awaited()

        assert await live.binding(sid) == before  # history stays, nothing rerouted or migrated
        assert await live.count("hook_events", sid) == 1
        for table in ("log_events", "metric_points", "spans"):
            assert await live.count(table, sid) == 0


@pytest.mark.asyncio
async def test_a_bound_personal_session_whose_personal_project_is_gone_is_discarded_not_a_conflict() -> None:
    sid = _sid()
    async with _live(sid) as live:
        await live.hooks([_hook(sid, _NOW)], binding=_PERSONAL)

        async with _world() as provision:
            assert (await _evidence(live, sid, _gate("personal")))["reason"] == "session_project_unavailable"
            provision.assert_not_awaited()  # never recreated for a bound session

        assert (await live.binding(sid))[1] == _PERSONAL_A and await live.count("hook_events", sid) == 1


@pytest.mark.asyncio
async def test_a_failed_lookup_of_the_bound_project_is_retried_and_a_new_session_without_a_destination_too() -> None:
    sid, fresh = _sid(), _sid()
    async with _live(sid, fresh) as live:
        await live.hooks([_hook(sid, _NOW)], binding=_CORPORATE)
        async with _world(_app(_SHARED)):
            with patch(f"{GATE}.application_repository.aget_active_by_name_case_insensitive", side_effect=OSError):
                with pytest.raises(TelemetryStorageUnavailableError):
                    await _evidence(live, sid, _gate("corporate"))
        async with _world():  # a NEW session: no destination at all stays retryable (AC9)
            with pytest.raises(TelemetryStorageUnavailableError, match="default_project_unavailable"):
                await _evidence(live, fresh, _gate("corporate"))
        assert await live.binding(fresh) is None and await live.count("hook_events", sid) == 1


@pytest.mark.asyncio
async def test_another_users_bound_session_is_an_owner_conflict_before_its_project_is_looked_at() -> None:
    sid = _sid()
    async with _live(sid) as live:
        await live.hooks([_hook(sid, _NOW)], binding=_CORPORATE)
        async with _world():
            assert await _send(live, "hooks", sid, _gate("corporate"), user=_B) == "session_owner_conflict"


@pytest.mark.asyncio
async def test_a_legacy_request_on_a_bound_session_stays_discarded_by_its_own_guards() -> None:
    sid = _sid()
    async with _live(sid) as live:
        await live.hooks([_hook(sid, _NOW)], binding=_CORPORATE)
        gate = build_legacy_gate(User(id="u-1", email=_A), "other")
        async with _world(_app("other")):
            result = await live.ingestor.ingest_hook_events(
                [_hook(sid, _NOW + timedelta(seconds=1), None)], _A, _RECEIVED_NS, gate=gate
            )
        # A legacy request never reaches the bound-project check: its gate needs the header project to exist, and any
        # project but the bound one is a conflict, so the lifecycle discard is the evidence path's.
        assert json.loads(result.body).get("reason") == "session_project_conflict"
        assert await live.count("hook_events", sid) == 1
