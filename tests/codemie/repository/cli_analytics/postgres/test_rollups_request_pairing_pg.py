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

"""Pairing of transcript requests with OTel records (tiers 1-3 of `_BUILD_REQUESTS`) against a real Postgres.

Each test runs the statement in one transaction on throwaway copies of `usage_requests` and `log_events`
(plain tables in a schema created inside that transaction and put first on the search path), reads
`_cli_requests`, and rolls everything back: no existing table is read or written and nothing is left behind.
Skipped (not failed) when the local Postgres or the analytics schema is unreachable. Run it with
`poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_rollups_request_pairing_pg.py`;
TEST_LOCAL_PG_URL points it at another server (default: the docker-compose `postgres` service).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.postgres import rollups
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.repository.cli_analytics.vocabulary import EventKind

# pytest.ini forces PG_URL to a dead port for the whole suite; TEST_LOCAL_PG_URL points this file at
# a real local Postgres (default: the docker-compose `postgres` service's credentials).
_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")

_DAY = date(2026, 3, 10)
_NOON = datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)
_MODEL = "claude-opus-4-8"

_INSERT_TRANSCRIPT = """
    INSERT INTO usage_requests (ts, session_id, request_id, model_raw, model, input_tokens, output_tokens,
                                cache_read_tokens, stop_reason, is_api_error, agent_id, scope_kind, scope_name,
                                agent_type)
    VALUES ($1, $2, $3, $4, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
"""
_INSERT_OTEL = f"""
    INSERT INTO log_events (ts, session_id, event_kind, model, input_tokens, output_tokens, cache_read_tokens,
                            cache_creation_tokens, cost_usd, severity, resource_id, request_id, query_source, attrs)
    VALUES ($1, $2, {int(EventKind.API_REQUEST)}, $3, $4, $5, $6, 0, $7, 9, 1, $8, $9, $10::jsonb)
"""
_READ = (
    "SELECT req_kind, input_tokens, output_tokens, cost_usd FROM _cli_requests WHERE session_id = $1 "
    "ORDER BY ts, req_kind, output_tokens"
)
_READ_DAYS = (
    "SELECT day, req_kind, output_tokens, cost_usd FROM _cli_requests WHERE session_id = $1 "
    "ORDER BY day, ts, req_kind, output_tokens"
)


@dataclass
class _Db:
    conn: asyncpg.Connection

    async def transcript(
        self,
        session_id: str,
        seconds: float,
        *,
        input_tokens: int,
        output_tokens: int | None,
        cache_read_tokens: int = 0,
        request_id: str | None = None,
        stop_reason: str | None = None,
        is_api_error: bool = False,
        agent_id: str | None = None,
        scope_kind: str | None = None,
        scope_name: str | None = None,
        agent_type: str | None = None,
    ) -> None:
        await self.conn.execute(
            _INSERT_TRANSCRIPT,
            _NOON + timedelta(seconds=seconds),
            session_id,
            request_id,
            _MODEL,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            stop_reason,
            is_api_error,
            agent_id,
            scope_kind,
            scope_name,
            agent_type,
        )

    async def otel(
        self,
        session_id: str,
        seconds: float,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        request_id: str | None = None,
        cost_usd: float = 0.5,
        query_source: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        await self.conn.execute(
            _INSERT_OTEL,
            _NOON + timedelta(seconds=seconds),
            session_id,
            _MODEL,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cost_usd,
            request_id,
            query_source,
            json.dumps({"agent.name": agent_name} if agent_name else {}),
        )

    async def requests(self, session_id: str) -> list[tuple[str, int | None, int | None, float | None]]:
        """Run the statement for the session's day and return (req_kind, input, output, cost) per request."""
        await self.conn.execute(rollups._LOAD_KEYS, [_DAY], [session_id], [rollups._LOG | rollups._USAGE], [1])
        await self.conn.execute(rollups._BUILD_REQUESTS)
        return [tuple(r) for r in await self.conn.fetch(_READ, session_id)]

    async def requests_by_day(
        self, session_id: str, days: list[date]
    ) -> list[tuple[date, str, int | None, float | None]]:
        """Run the statement for all `days` of the session at once and return (day, req_kind, output, cost)."""
        await self.conn.execute(
            rollups._LOAD_KEYS,
            days,
            [session_id] * len(days),
            [rollups._LOG | rollups._USAGE] * len(days),
            [1] * len(days),
        )
        await self.conn.execute(rollups._BUILD_REQUESTS)
        return [tuple(r) for r in await self.conn.fetch(_READ_DAYS, session_id)]


@asynccontextmanager
async def _db() -> AsyncIterator[_Db]:
    schema = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL)).schema
    try:
        conn = await asyncpg.connect(_LOCAL_PG_URL)
    except (OSError, asyncpg.PostgresError) as exc:  # no reachable database (refused, bad auth): skip
        pytest.skip(f"local Postgres is not reachable: {exc!r}")
    try:
        if await conn.fetchval("SELECT to_regclass($1)", f"{schema}.usage_requests") is None:
            pytest.skip(f"no analytics schema {schema!r} on the local Postgres to copy the tables from")
        scratch = f"pairing_test_{uuid.uuid4().hex}"
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(f'CREATE SCHEMA "{scratch}"')
            for table in ("usage_requests", "log_events"):
                await conn.execute(f'CREATE TABLE "{scratch}".{table} (LIKE "{schema}".{table})')
            await conn.execute(f'SET LOCAL search_path TO "{scratch}"')
            await conn.execute(rollups._CREATE_KEYS)
            await conn.execute(rollups._CREATE_REQUESTS)
            yield _Db(conn)
        finally:
            await tx.rollback()  # the scratch schema, its tables and the temp tables' rows vanish with it
    finally:
        await conn.close()


def _session() -> str:
    return str(uuid.uuid4())


@pytest.mark.asyncio
async def test_a_partial_request_with_the_same_request_id_takes_the_otel_output() -> None:
    session_id = _session()
    async with _db() as db:
        await db.transcript(session_id, 0, input_tokens=100, output_tokens=3, request_id="req_1")
        await db.otel(session_id, 2, input_tokens=100, output_tokens=4766, request_id="req_1")
        assert await db.requests(session_id) == [("matched", 100, 4766, 0.5)]


@pytest.mark.asyncio
async def test_a_desktop_partial_request_without_ids_and_other_totals_is_paired_by_tier_3() -> None:
    # Claude Desktop: no request id, the OTel input and cache totals differ from the transcript's and the
    # OTel record lands 12 s later. Without tier 3 this is a 'transcript' row (output 3) plus a 'service' row.
    session_id = _session()
    async with _db() as db:
        await db.transcript(session_id, 0, input_tokens=7, output_tokens=3, cache_read_tokens=1000)
        await db.otel(session_id, 12, input_tokens=9, output_tokens=4766, cache_read_tokens=2000)
        assert await db.requests(session_id) == [("matched", 7, 4766, 0.5)]


@pytest.mark.asyncio
async def test_a_final_request_with_another_otel_output_is_not_paired_by_tier_3() -> None:
    session_id = _session()
    async with _db() as db:
        await db.transcript(session_id, 0, input_tokens=7, output_tokens=3, stop_reason="end_turn")
        await db.otel(session_id, 12, input_tokens=9, output_tokens=4766)
        assert await db.requests(session_id) == [("transcript", 7, 3, None), ("service", 9, 4766, 0.5)]


@pytest.mark.asyncio
async def test_a_partial_request_with_an_api_error_is_not_paired_by_tier_3() -> None:
    session_id = _session()
    async with _db() as db:
        await db.transcript(session_id, 0, input_tokens=7, output_tokens=3, is_api_error=True)
        await db.otel(session_id, 12, input_tokens=9, output_tokens=4766)
        assert await db.requests(session_id) == [("transcript", 7, 3, None), ("service", 9, 4766, 0.5)]


@pytest.mark.asyncio
@pytest.mark.parametrize("placeholder", [0, None])
async def test_a_partial_request_without_a_positive_output_is_not_paired_by_tier_3(placeholder: int | None) -> None:
    session_id = _session()
    async with _db() as db:
        await db.transcript(session_id, 0, input_tokens=7, output_tokens=placeholder)
        await db.otel(session_id, 12, input_tokens=9, output_tokens=4766)
        assert await db.requests(session_id) == [("transcript", 7, placeholder, None), ("service", 9, 4766, 0.5)]


@pytest.mark.asyncio
async def test_a_partial_request_further_than_30_seconds_from_the_otel_record_is_not_paired() -> None:
    session_id = _session()
    async with _db() as db:
        await db.transcript(session_id, 0, input_tokens=7, output_tokens=3)
        await db.otel(session_id, 45, input_tokens=9, output_tokens=4766)
        assert await db.requests(session_id) == [("transcript", 7, 3, None), ("service", 9, 4766, 0.5)]


@pytest.mark.asyncio
async def test_tier_3_pairs_one_to_one_and_the_session_output_is_the_otel_total() -> None:
    # Transcript partials at 0 s and 10 s, OTel records at 4 s and 14 s: each takes its nearest free record.
    session_id = _session()
    async with _db() as db:
        await db.transcript(session_id, 0, input_tokens=7, output_tokens=3)
        await db.transcript(session_id, 10, input_tokens=8, output_tokens=5)
        await db.otel(session_id, 4, input_tokens=70, output_tokens=4000)
        await db.otel(session_id, 14, input_tokens=80, output_tokens=800)
        rows = await db.requests(session_id)
        assert rows == [("matched", 7, 4000, 0.5), ("matched", 8, 800, 0.5)]
        assert sum(r[2] for r in rows) == 4800


@pytest.mark.asyncio
async def test_tier_3_does_not_take_an_otel_record_already_paired_by_request_id() -> None:
    session_id = _session()
    async with _db() as db:
        await db.transcript(session_id, 0, input_tokens=100, output_tokens=9, request_id="req_1")
        await db.otel(session_id, 1, input_tokens=100, output_tokens=4766, request_id="req_1")
        await db.transcript(session_id, 3, input_tokens=7, output_tokens=3)
        assert await db.requests(session_id) == [("matched", 100, 4766, 0.5), ("transcript", 7, 3, None)]


# Seconds from _NOON to the midnight that ends _DAY.
_MIDNIGHT = 12 * 3600


@pytest.mark.asyncio
async def test_a_partial_request_does_not_take_the_exact_match_of_a_final_request() -> None:
    # Partial P (output 3) at 0 s, its OTel record B_P (4766) at 4.5 s; final F (500) at 1 s, B_F (500) at 2 s,
    # all with the same input and cache totals. F pairs exactly with B_F, P takes B_P in tier 3.
    session_id = _session()
    async with _db() as db:
        await db.transcript(session_id, 0, input_tokens=100, output_tokens=3, cache_read_tokens=50)
        await db.transcript(
            session_id, 1, input_tokens=100, output_tokens=500, cache_read_tokens=50, stop_reason="end_turn"
        )
        await db.otel(session_id, 2, input_tokens=100, output_tokens=500, cache_read_tokens=50)
        await db.otel(session_id, 4.5, input_tokens=100, output_tokens=4766, cache_read_tokens=50)
        rows = await db.requests(session_id)
        assert rows == [("matched", 100, 4766, 0.5), ("matched", 100, 500, 0.5)]
        assert sum(r[2] for r in rows) == 5266


@pytest.mark.asyncio
async def test_a_subagent_partial_request_does_not_take_the_otel_record_of_the_main_thread() -> None:
    # Subagent partial P at 0 s, its OTel record P' (agent:builtin:Explore) at 20 s; the main thread's final A
    # at 4 s with B_A (repl_main_thread) at 5 s, which A's totals do not equal. B_A is nearer to P than P' is.
    session_id = _session()
    async with _db() as db:
        await db.transcript(
            session_id, 0, input_tokens=7, output_tokens=3, agent_id="agent-1", scope_kind="agent", agent_type="Explore"
        )
        await db.transcript(session_id, 4, input_tokens=11, output_tokens=900, stop_reason="end_turn")
        await db.otel(session_id, 5, input_tokens=13, output_tokens=900, query_source="repl_main_thread")
        await db.otel(session_id, 20, input_tokens=9, output_tokens=4766, query_source="agent:builtin:Explore")
        assert await db.requests(session_id) == [
            ("matched", 7, 4766, 0.5),
            ("transcript", 11, 900, None),
            ("service", 13, 900, 0.5),
        ]


@pytest.mark.asyncio
async def test_a_main_thread_partial_request_does_not_take_the_otel_record_of_a_subagent() -> None:
    # Subagent partial P at 0 s with P' at 20 s, main partial A at 4 s with B_A at 5 s: each takes its own
    # class, so the output sum is the two OTel totals (the nearest-first walk would give P the record B_A).
    session_id = _session()
    async with _db() as db:
        await db.transcript(
            session_id, 0, input_tokens=7, output_tokens=3, agent_id="agent-1", scope_kind="agent", agent_type="Explore"
        )
        await db.transcript(session_id, 4, input_tokens=11, output_tokens=5)
        await db.otel(session_id, 5, input_tokens=13, output_tokens=900, query_source="repl_main_thread")
        await db.otel(session_id, 20, input_tokens=9, output_tokens=4766, query_source="agent:builtin:Explore")
        rows = await db.requests(session_id)
        assert rows == [("matched", 7, 4766, 0.5), ("matched", 11, 900, 0.5)]
        assert sum(r[2] for r in rows) == 5666


@pytest.mark.asyncio
@pytest.mark.parametrize("side_source", ["compact", "generate_session_title", "prompt_suggestion"])
async def test_a_main_thread_partial_request_does_not_take_a_non_conversation_otel_record(side_source: str) -> None:
    # Main partial P at 0 s; a side-call record (not the conversation) at 2 s is nearer than the real
    # repl_main_thread record at 10 s. P takes the real one; the side call stays its own service row.
    session_id = _session()
    async with _db() as db:
        await db.transcript(session_id, 0, input_tokens=7, output_tokens=3)
        await db.otel(session_id, 2, input_tokens=13, output_tokens=900, query_source=side_source)
        await db.otel(session_id, 10, input_tokens=9, output_tokens=4766, query_source="repl_main_thread")
        rows = await db.requests(session_id)
        assert rows == [("matched", 7, 4766, 0.5), ("service", 13, 900, 0.5)]
        assert sum(r[2] for r in rows) == 4766 + 900


@pytest.mark.asyncio
@pytest.mark.parametrize("main_source", ["sdk", None])
async def test_a_main_thread_partial_request_pairs_with_an_sdk_or_an_unlabelled_otel_record(
    main_source: str | None,
) -> None:
    session_id = _session()
    async with _db() as db:
        await db.transcript(session_id, 0, input_tokens=7, output_tokens=3)
        await db.otel(session_id, 5, input_tokens=9, output_tokens=4766, query_source=main_source)
        assert await db.requests(session_id) == [("matched", 7, 4766, 0.5)]


@pytest.mark.asyncio
async def test_without_a_query_source_the_agent_name_picks_the_otel_record() -> None:
    # Claude Desktop sends no query_source: the class filter lets both records through and the agent name
    # (transcript agent_type = OTel agent.name) decides, though the other record is nearer.
    session_id = _session()
    async with _db() as db:
        await db.transcript(
            session_id, 0, input_tokens=7, output_tokens=3, agent_id="agent-1", scope_kind="agent", agent_type="Explore"
        )
        await db.otel(session_id, 5, input_tokens=13, output_tokens=900)
        await db.otel(session_id, 20, input_tokens=9, output_tokens=4766, agent_name="Explore")
        assert await db.requests(session_id) == [("matched", 7, 4766, 0.5), ("service", 13, 900, 0.5)]


@pytest.mark.asyncio
async def test_tier_3_prefers_the_otel_record_with_equal_input_and_cache_totals_over_a_nearer_one() -> None:
    session_id = _session()
    async with _db() as db:
        await db.transcript(session_id, 0, input_tokens=7, output_tokens=3, cache_read_tokens=1000)
        await db.otel(session_id, 2, input_tokens=9, output_tokens=800, cache_read_tokens=2000)
        await db.otel(session_id, 10, input_tokens=7, output_tokens=4766, cache_read_tokens=1000)
        assert await db.requests(session_id) == [("matched", 7, 4766, 0.5), ("service", 9, 800, 0.5)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transcript_s", "otel_s", "expected"),
    [
        # partial at 23:59:58 of D, OTel at 00:00:20 of D+1
        (
            _MIDNIGHT - 2,
            _MIDNIGHT + 20,
            [(_DAY, "transcript", 3, None), (_DAY + timedelta(days=1), "service", 4766, 0.5)],
        ),
        # OTel at 23:59:58 of D, partial at 00:00:20 of D+1
        (
            _MIDNIGHT + 20,
            _MIDNIGHT - 2,
            [(_DAY, "otel_only", 4766, 0.5), (_DAY + timedelta(days=1), "transcript", 3, None)],
        ),
        # a pair 12 s apart inside one day still pairs, and is written on that day only
        (_MIDNIGHT - 40, _MIDNIGHT - 28, [(_DAY, "matched", 4766, 0.5)]),
    ],
)
async def test_tier_3_pairs_only_within_one_utc_day(transcript_s: int, otel_s: int, expected: list) -> None:
    # Both days are recomputed; a pair across midnight is found by neither, so the OTel cost is counted once.
    session_id = _session()
    async with _db() as db:
        await db.transcript(session_id, transcript_s, input_tokens=7, output_tokens=3)
        await db.otel(session_id, otel_s, input_tokens=9, output_tokens=4766)
        assert await db.requests_by_day(session_id, [_DAY, _DAY + timedelta(days=1)]) == expected
