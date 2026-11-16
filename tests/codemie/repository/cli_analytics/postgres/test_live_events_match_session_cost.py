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

"""Session Detail on a real PostgreSQL: the requests it lists add up to the session cost, the figure the UI's
"Token & Cost Growth" line ends on.

A session with OTel api_request records and plugin transcript requests is priced by the cost rollup's pairing (a
transcript request and the OTel record of the same request are one request, taking the transcript's 5m / 1h cache
split; a request with no partner is its own). The listed requests follow that pairing, not a per-day switch.

Needs `TEST_LOCAL_PG_URL` (a disposable PostgreSQL; the tests migrate it), else every test is skipped:

    TEST_LOCAL_PG_URL=postgresql://<user>:<password>@<host>:<port>/<db> poetry run pytest -n0 <this file>
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest

from tests.codemie.repository.cli_analytics.postgres.test_live_route_pricing import (
    _DAY,
    _SESSION_START,
    _SONNET,
    _SONNET_RATES,
    _API_REQUEST,
    _at,
    _cleanup,
    _detail,
    _engine,
    _entry,
    _growth_cost,
    _refresh,
    _requests,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_LOCAL_PG_URL", ""), reason="TEST_LOCAL_PG_URL is not set: no database to run against"
)

_USAGE = """INSERT INTO usage_requests
    (ts, session_id, request_id, model_raw, model, input_tokens, output_tokens, cache_read_tokens,
     cache_creation_5m_tokens, cache_creation_1h_tokens, stop_reason)
    VALUES ($1, $2, $3, $4, $5, 1000, 10, 0, $6, $7, 'end_turn')"""
# A streaming snapshot: no stop_reason, its output a placeholder below the final one
_PARTIAL_USAGE = """INSERT INTO usage_requests
    (ts, session_id, request_id, model_raw, model, input_tokens, output_tokens, cache_read_tokens,
     cache_creation_5m_tokens, cache_creation_1h_tokens, stop_reason)
    VALUES ($1, $2, $3, $4, $5, 1000, 5, 0, 0, 0, NULL)"""
_MIDNIGHT = 12 * 3600  # _at() counts from noon UTC

# 1 000 input at 3e-06 + 10 output at 1.5e-05, then the cache write: 3.75e-06 per 5m token, 6e-06 per 1h token
_BASE = 0.00315
_CACHE_1H_TOKENS = 20_000
_CACHE_1H_COST = _CACHE_1H_TOKENS * _SONNET_RATES[4]
_CACHE_5M_COST_OF_THE_SAME_TOKENS = _CACHE_1H_TOKENS * _SONNET_RATES[3]


async def _pair(conn, sid: str, rid: str, seconds: int, cache_1h: int) -> None:
    """One request: its transcript line (the 5m / 1h split) and its OTel record, which carries the total only."""
    await conn.execute(_USAGE, _at(seconds), sid, rid, _SONNET, _SONNET, 0, cache_1h)
    await conn.execute(_API_REQUEST, _at(seconds), sid, rid, _SONNET, 1000, 10, 0, cache_1h, "{}")


async def _session_detail(build, *, days: int = 1) -> tuple[dict, int]:
    """The session's detail and its priced request count, the `api_call_count` the sessions list sums from
    cost_daily, with the rollup refreshed over the first `days` UTC days of the session."""
    sid = f"t-events-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            await conn.execute(_SESSION_START, _at(0), sid)
            await build(conn, sid)
        await _refresh(engine, _DAY, _DAY + timedelta(days=days - 1))
        detail = await _detail(engine, sid, [_entry(_SONNET, _SONNET_RATES)], {})
        async with engine.acquire() as conn:
            calls = await conn.fetchval("SELECT sum(api_call_count) FROM cost_daily WHERE session_id = $1", sid)
        return detail, calls
    finally:
        await _cleanup(engine, [sid])
        await engine.close()


@pytest.mark.asyncio
async def test_a_paired_request_is_priced_at_the_cache_split_of_its_transcript_line() -> None:
    async def build(conn, sid: str) -> None:
        await _pair(conn, sid, "r1", 10, _CACHE_1H_TOKENS)

    detail, _ = await _session_detail(build)

    assert detail["cost_usd"] == pytest.approx(_BASE + _CACHE_1H_COST, abs=1e-8)
    assert [e["cost_usd"] for e in _requests(detail)] == pytest.approx([_BASE + _CACHE_1H_COST], abs=1e-8)
    assert _growth_cost(detail) == pytest.approx(detail["cost_usd"], abs=1e-8)


@pytest.mark.asyncio
async def test_a_request_without_an_otel_record_is_listed_next_to_a_paired_one_of_its_day() -> None:
    async def build(conn, sid: str) -> None:
        await _pair(conn, sid, "r1", 10, 0)
        # the second request reached the plugin only: its OTel record never arrived
        await conn.execute(_USAGE, _at(20), sid, "r2", _SONNET, _SONNET, 0, 0)

    detail, _ = await _session_detail(build)

    assert len(_requests(detail)) == 2
    assert detail["cost_usd"] == pytest.approx(2 * _BASE, abs=1e-8)
    assert _growth_cost(detail) == pytest.approx(detail["cost_usd"], abs=1e-8)


@pytest.mark.asyncio
async def test_every_priced_request_is_listed_once_with_transcript_only_and_side_requests() -> None:
    async def build(conn, sid: str) -> None:
        await _pair(conn, sid, "r1", 10, 0)
        await _pair(conn, sid, "r2", 20, _CACHE_1H_TOKENS)
        # two requests that reached the plugin only: no OTel record
        await conn.execute(_USAGE, _at(30), sid, "r3", _SONNET, _SONNET, 0, 0)
        await conn.execute(_USAGE, _at(40), sid, "r4", _SONNET, _SONNET, 0, 0)
        # an OTel-only side request (generate_session_title): no transcript line
        await conn.execute(_API_REQUEST, _at(50), sid, "side1", _SONNET, 1000, 10, 0, 0, "{}")

    detail, calls = await _session_detail(build)

    events = _requests(detail)
    assert len(events) == 5  # r1..r4 and the side request, each once
    assert len(events) == calls  # the api_call_count the sessions list shows
    assert sum(e["cost_usd"] for e in events) == pytest.approx(detail["cost_usd"], abs=1e-8)
    assert detail["cost_usd"] == pytest.approx(5 * _BASE + _CACHE_1H_COST, abs=1e-8)
    assert _growth_cost(detail) == pytest.approx(detail["cost_usd"], abs=1e-8)


def _assert_listed_once(detail: dict, calls: int, requests: int) -> None:
    """Each priced request is listed once, and the listed requests add up to the session cost."""
    events = _requests(detail)
    assert len(events) == calls == requests
    assert sum(e["cost_usd"] for e in events) == pytest.approx(detail["cost_usd"], abs=1e-8)
    assert detail["cost_usd"] == pytest.approx(requests * _BASE, abs=1e-8)
    assert _growth_cost(detail) == pytest.approx(detail["cost_usd"], abs=1e-8)


@pytest.mark.asyncio
async def test_a_request_paired_by_tier_2_is_listed_once() -> None:
    async def build(conn, sid: str) -> None:
        # the transcript line has its id, the OTel record has none: same raw model and totals, 3 s apart
        await conn.execute(_USAGE, _at(10), sid, "r1", _SONNET, _SONNET, 0, 0)
        await conn.execute(_API_REQUEST, _at(13), sid, "", _SONNET, 1000, 10, 0, 0, "{}")

    detail, calls = await _session_detail(build)

    _assert_listed_once(detail, calls, 1)


@pytest.mark.asyncio
async def test_a_partial_snapshot_paired_by_tier_3_is_listed_once() -> None:
    async def build(conn, sid: str) -> None:
        # a streaming snapshot (no stop_reason, output 5) and the OTel record of the final response 15 s later:
        # outside tier 2's 5 s and with another output, inside tier 3's 30 s
        await conn.execute(_PARTIAL_USAGE, _at(10), sid, "r1", _SONNET, _SONNET)
        await conn.execute(_API_REQUEST, _at(25), sid, "", _SONNET, 1000, 10, 0, 0, "{}")

    detail, calls = await _session_detail(build)

    _assert_listed_once(detail, calls, 1)


@pytest.mark.asyncio
async def test_pairs_across_utc_midnight_are_listed_once() -> None:
    async def build(conn, sid: str) -> None:
        # tier 1: one request id, 59 min before and after midnight, each within the rollup's 1 h reach
        await conn.execute(_USAGE, _at(_MIDNIGHT - 59 * 60), sid, "m1", _SONNET, _SONNET, 0, 0)
        await conn.execute(_API_REQUEST, _at(_MIDNIGHT + 59 * 60), sid, "m1", _SONNET, 1000, 10, 0, 0, "{}")
        # tier 2: no OTel id, 2 s before and after midnight, inside the day widened by 5 s
        await conn.execute(_USAGE, _at(_MIDNIGHT - 2), sid, "m2", _SONNET, _SONNET, 0, 0)
        await conn.execute(_API_REQUEST, _at(_MIDNIGHT + 2), sid, "", _SONNET, 1000, 10, 0, 0, "{}")

    detail, calls = await _session_detail(build, days=2)

    _assert_listed_once(detail, calls, 2)
