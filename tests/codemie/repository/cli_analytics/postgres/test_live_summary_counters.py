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

"""Value-level checks of the summary-only tool counters (tool_calls, tool_errors, tools) against the local
analytics Postgres: the real hook ingest and the real session_dims upsert, only the test's own keys are
recomputed and only its own sessions are deleted afterwards. Skipped (not failed) when the local analytics
Postgres is unreachable; set TEST_LOCAL_PG_URL to point it elsewhere.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from tests.codemie.repository.cli_analytics.postgres.test_live_turns_daily import _READ_KEYS, _Live, _live
from tests.codemie.repository.cli_analytics.support import hook_event_builders as hb

_MARKER = "summary_only"
_TOOLS = {"Read": {"calls": 4, "errors": 1}}
_READ_ROW = "SELECT tool_calls, tool_errors, tools, turns, attrs FROM session_dims WHERE session_id = $1"
_SET_UNMARKED = "UPDATE session_dims SET attrs = attrs - 'tool_counters' WHERE session_id = $1"
# The recompute reads the latest summary of ALL retained hook_events, so a summary can lose to the stored
# row only when the stored summary_ts is strictly newer than every retained one: the test moves it.
_SET_STORED_NEWER = "UPDATE session_dims SET summary_ts = summary_ts + interval '1 hour' WHERE session_id = $1"


def _ts(minute: int) -> str:
    """11:00 plus `minute` minutes (past 59 it rolls into the next hours)."""
    return f"2026-09-29T{11 + minute // 60:02d}:{minute % 60:02d}:00.000Z"


def _summary(sid: str, minute: int, omit: tuple[str, ...] = (), **over: Any) -> dict[str, Any]:
    """A summary dated `minute`, with the three tool counters unless omitted; `over` may set one to None."""
    event = hb.session_summary(session_id=sid, timestamp=_ts(minute), event_id=f"summary:{sid}:{uuid.uuid4()}")
    event.update({"tool_calls": 10, "tool_errors": 2, "tools": _TOOLS, "turns": 7} | over)
    for key in omit:
        event.pop(key)
    return event


def _tool_start(sid: str, minute: int, **over: Any) -> dict[str, Any]:
    event = hb.minimal("agent.tool.start", sid, _ts(minute))
    return event | {"tool_name": "Grep", "tool_use_id": str(uuid.uuid4())} | over


async def _row(live: _Live, sid: str, hooks: list[dict[str, Any]]) -> dict[str, Any]:
    """Ingest, recompute the session's queued keys, return its session_dims row (tools and attrs decoded)."""
    await live.ingest(hooks=hooks)
    async with live.engine.acquire() as conn:
        keys = await conn.fetch(_READ_KEYS, [sid])
        assert keys, "ingest marked no rollup key for the session"
        assert await RollupRefresher(live.engine, batch_size=len(keys)).recompute(conn, keys) == len(keys)
        row = dict(await conn.fetchrow(_READ_ROW, sid))
    for column in ("tools", "attrs"):  # jsonb is text unless the engine registers a codec
        if isinstance(row[column], str):
            row[column] = json.loads(row[column])
    return row


@pytest.mark.asyncio
async def test_a_first_insert_with_a_complete_summary_is_marked() -> None:
    sid = str(uuid.uuid4())
    async with _live(sid) as live:
        row = await _row(live, sid, [_summary(sid, 30)])
        assert (row["tool_calls"], row["tool_errors"], row["tools"]) == (10, 2, _TOOLS)
        assert row["attrs"]["counters_source"] == "summary"
        assert row["attrs"]["tool_counters"] == _MARKER


@pytest.mark.asyncio
async def test_a_fallback_row_is_unmarked_and_a_client_cannot_mark_it() -> None:
    sid = str(uuid.uuid4())
    async with _live(sid) as live:
        row = await _row(live, sid, [_tool_start(sid, 1, tool_counters=_MARKER), _tool_start(sid, 2)])
        assert row["tool_calls"] == 2  # the fallback value: unchanged by the rule
        assert row["attrs"]["counters_source"] == "fallback"
        assert "tool_counters" not in row["attrs"]


@pytest.mark.asyncio
async def test_a_partial_summary_after_the_fallback_does_not_keep_the_fallback_counters() -> None:
    sid = str(uuid.uuid4())
    async with _live(sid) as live:
        await _row(live, sid, [_tool_start(sid, 1), _tool_start(sid, 2)])
        row = await _row(live, sid, [_summary(sid, 30, omit=("tool_calls", "tool_errors", "tools"))])
        assert (row["tool_calls"], row["tool_errors"], row["tools"]) == (None, None, None)
        assert row["turns"] == 7  # unrelated counters are untouched
        assert row["attrs"]["tool_counters"] == _MARKER


@pytest.mark.asyncio
async def test_a_partial_summary_after_a_full_one_keeps_only_what_it_omits_independently() -> None:
    sid = str(uuid.uuid4())
    async with _live(sid) as live:
        await _row(live, sid, [_summary(sid, 30)])
        row = await _row(live, sid, [_summary(sid, 31, omit=("tool_calls", "tools"), tool_errors=0, turns=9)])
        # tool_calls and tools were omitted: kept; tool_errors is a present zero: replaces 2
        assert (row["tool_calls"], row["tool_errors"], row["tools"]) == (10, 0, _TOOLS)
        assert row["turns"] == 9


@pytest.mark.asyncio
async def test_an_explicit_null_or_malformed_field_is_absent_and_zero_is_a_value() -> None:
    sid = str(uuid.uuid4())
    async with _live(sid) as live:
        await _row(live, sid, [_summary(sid, 30)])
        row = await _row(live, sid, [_summary(sid, 31, tool_calls=None, tool_errors="many", tools=[1])])
        assert (row["tool_calls"], row["tool_errors"], row["tools"]) == (10, 2, _TOOLS)
        row = await _row(live, sid, [_summary(sid, 32, tool_calls=0, tool_errors=0, tools={})])
        assert (row["tool_calls"], row["tool_errors"], row["tools"]) == (0, 0, {})


@pytest.mark.asyncio
async def test_an_equal_timestamp_summary_wins_over_the_stored_one() -> None:
    sid = str(uuid.uuid4())
    async with _live(sid) as live:
        await _row(live, sid, [_summary(sid, 30)])
        # same ts, later ingest_seq: the retained latest, equal to the stored summary_ts, so it wins (>=)
        row = await _row(live, sid, [_summary(sid, 30, tool_calls=11)])
        assert row["tool_calls"] == 11


@pytest.mark.asyncio
async def test_recomputing_an_unmarked_row_with_its_complete_summary_restores_the_marker() -> None:
    sid = str(uuid.uuid4())
    async with _live(sid) as live:
        await _row(live, sid, [_summary(sid, 30)])
        async with live.engine.acquire() as conn:
            await conn.execute(_SET_UNMARKED, sid)  # the state a row stored before the marker is in
        # an older event re-selects the retained 11:30 summary: equal to the stored one, so it wins again
        row = await _row(live, sid, [_summary(sid, 29, tool_calls=99)])
        assert (row["tool_calls"], row["tool_errors"], row["tools"]) == (10, 2, _TOOLS)
        assert row["attrs"]["tool_counters"] == _MARKER


@pytest.mark.asyncio
async def test_a_stored_summary_newer_than_every_retained_one_keeps_fields_and_marker_state() -> None:
    sid = str(uuid.uuid4())
    async with _live(sid) as live:
        await _row(live, sid, [_summary(sid, 30)])
        async with live.engine.acquire() as conn:
            await conn.execute(_SET_STORED_NEWER, sid)  # stored 12:30; the retained summaries stay at 11:30
            await conn.execute(_SET_UNMARKED, sid)
        row = await _row(live, sid, [_summary(sid, 29, tool_calls=99)])  # the retained latest (11:30) now loses
        assert (row["tool_calls"], row["tool_errors"], row["tools"]) == (10, 2, _TOOLS)
        assert "tool_counters" not in row["attrs"]  # a losing summary cannot mark a row


@pytest.mark.asyncio
async def test_a_winning_partial_summary_nulls_the_fields_of_an_unmarked_legacy_row() -> None:
    sid = str(uuid.uuid4())
    async with _live(sid) as live:
        await _row(live, sid, [_summary(sid, 30)])
        async with live.engine.acquire() as conn:
            await conn.execute(_SET_STORED_NEWER, sid)  # stored 12:30
            await conn.execute(_SET_UNMARKED, sid)
        # 12:35 is newer than the stored 12:30: it wins; the row is unmarked, so the omitted fields are NULL
        row = await _row(live, sid, [_summary(sid, 95, omit=("tool_calls", "tool_errors", "tools"))])
        assert (row["tool_calls"], row["tool_errors"], row["tools"]) == (None, None, None)
        assert row["attrs"]["tool_counters"] == _MARKER
