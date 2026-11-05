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

"""Live-Postgres checks for EPMCDME-16833: a session whose analytics hooks were enabled mid-session serves the
whole-session totals of its latest summary, with unknown (None) kept apart from a reported zero, in the Session Detail,
the session list and the turn aggregate alike.

Raw rows are inserted and rolled up by the real refresher; the expected counts are written down here from the inserted
summary and events, not read back from the reader. Tool successes stay what the observed tool events say: a summary
carries no outcome. Skipped when `TEST_LOCAL_PG_URL` is unset or unreachable.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.vocabulary import SpanKind
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.postgres.test_live_session_detail_consistency import (
    _START,
    _SPAN,
    _USAGE,
    _MODEL,
    _at,
    _cleanup,
    _detail,
    _engine,
    _growth_cost,
    _lookup,
    _price,
    _PRICING,
    _refresh,
    _LOCAL_PG_URL,
)
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

_SUMMARY = (
    "INSERT INTO hook_events (ts, session_id, event_type, attrs) VALUES ($1, $2, 'agent.session.summary', $3::jsonb)"
)
_TOOLS = {"Read": {"calls": 2, "errors": 0}, "Bash": {"calls": 1, "errors": 0}}


_ORDINARY = (10, 5, 100, None, None)  # (input, output, cache read, cache write 5m, cache write 1h) of one request

# SYNTHETIC high-volume usage in the order of magnitude of a long session (5 requests: 318 input, 113,300 output,
# 42.2M cache read, 1.1M cache write tokens). It is not the original session and carries no claimed real cost.
_HIGH = [
    (100, 30_000, 10_000_000, None, 300_000),
    (50, 25_300, 9_000_000, 100_000, 100_000),
    (68, 20_000, 8_500_000, None, None),
    (60, 18_000, 8_000_000, 200_000, None),
    (40, 20_000, 6_700_000, 150_000, 250_000),
]


async def _seed(
    engine,
    sid: str,
    *,
    summary: dict | None,
    interactions: int,
    tools: int,
    requests: int = 5,
    usage: list[tuple] | None = None,
) -> None:
    """The mid-session enable: every request has usage (transcript), but the spans (turns, tools) are post-enable."""
    usage = usage or [_ORDINARY] * requests
    async with engine.acquire() as conn:
        await conn.execute(
            "INSERT INTO hook_events (ts, session_id, event_type) VALUES ($1, $2, 'agent.session.start')", _at(0), sid
        )
        for n, (inp, out, read, w5m, w1h) in enumerate(usage):
            await conn.execute(_USAGE, _at(n), sid, f"r{n}", _MODEL, inp, out, read, "end_turn", w5m, w1h)
        for n in range(interactions):
            await conn.execute(
                _SPAN,
                _at(30 + n),
                b"turn%d" % n,
                b"main",
                int(SpanKind.INTERACTION),
                "claude_code.interaction",
                sid,
                "",
                f"i{n}",
                None,
            )
        for n in range(tools):
            tool, execution = b"tool%d" % n, b"exec%d" % n
            await conn.execute(
                _SPAN, _at(40 + n), tool, b"main", int(SpanKind.TOOL), "claude_code.tool", sid, "Read", f"tu-{n}", None
            )
            await conn.execute(
                _SPAN,
                _at(41 + n),
                execution,
                tool,
                int(SpanKind.TOOL_EXECUTION),
                "tool.execution",
                sid,
                "",
                f"tu-{n}",
                "true",
            )
        if summary is not None:
            await conn.execute(_SUMMARY, _at(60), sid, json.dumps(summary))


async def _list_row(engine, sid: str) -> dict:
    window = LocalAnalyticsFilter(
        scope=EXCLUDE_SCOPE, start_dt=_START - timedelta(days=1), end_dt=_START + timedelta(days=1)
    ).restricted_to([sid])
    with patch(_PRICING, _lookup):
        data, _, _ = await LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine)).get_sessions(
            window, 0, 10, "start_time", None
        )
    assert any(s["trace_id"] == sid for s in data["sessions"]), (sid, data)
    return next(s for s in data["sessions"] if s["trace_id"] == sid)


async def _aggregates(engine, sid: str) -> dict:
    """The KPI, user and repository views of the same session: tool calls stay observed, turns are whole-session."""
    window = LocalAnalyticsFilter(
        scope=EXCLUDE_SCOPE, start_dt=_START - timedelta(days=1), end_dt=_START + timedelta(days=1)
    ).restricted_to([sid])
    handler = LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine))
    with patch(_PRICING, _lookup):
        overview, _, _ = await handler.get_overview(window)
        users, _ = await handler.get_users(window, None, None)
        repos, _ = await handler.get_repositories(window, 0, 10, False)
    return {"overview": overview, "users": users, "repos": repos}


async def _served(sid: str, *, then: dict | None = None, legacy: bool = False, **seed) -> tuple[dict, dict, int]:
    engine = await _engine()
    try:
        await _seed(engine, sid, **seed)
        await _refresh(engine)
        if then is not None:  # a later summary replaces the stored one
            async with engine.acquire() as conn:
                await conn.execute(_SUMMARY, _at(70), sid, json.dumps(then))
            await _refresh(engine)
        if legacy:  # a row stored before summary_state / summary_reported existed
            async with engine.acquire() as conn:
                await conn.execute(
                    "UPDATE session_dims SET attrs = attrs - 'summary_state' - 'summary_reported' WHERE session_id = $1",
                    sid,
                )
        detail = await _detail(engine, sid)
        row = await _list_row(engine, sid)
        window = LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE, start_dt=_START - timedelta(days=1), end_dt=_START + timedelta(days=1)
        ).restricted_to([sid])
        turns = sum(r["turns"] or 0 for r in await PostgresCliAnalyticsReader(engine).get_turns_by_session(window))
        _served.aggregates = await _aggregates(engine, sid)
        return detail, row, turns
    finally:
        await _cleanup(engine, [sid])
        await engine.close()


def _summary(**over) -> dict:
    return {
        "turns": 2,
        "api_calls": 5,
        "tool_calls": 3,
        "tool_errors": 0,
        "tools": _TOOLS,
        "agents": {"Explore": 2},
        "skills": {"brainstorming": 1},
        "is_final": True,
    } | over


def _view(view: dict) -> tuple:
    """What the session list and the detail both serve for the five counters and the summary meaning."""
    return (
        view["turns"],
        view["tool_call_count"],
        view["tool_calls_total"],
        view["summary_state"],
        view["summary_metrics"],
    )


_ALL = ["agents", "skills", "tool_calls", "turns"]


def _assert_usage_totals(detail: dict, row: dict, usage: list[tuple]) -> None:
    """Totals and cost written down from the inserted usage rows: the incomplete telemetry must not touch them."""
    expected = {
        "input_tokens": sum(u[0] for u in usage),
        "output_tokens": sum(u[1] for u in usage),
        "cache_read_tokens": sum(u[2] for u in usage),
        "cache_creation_tokens": sum((u[3] or 0) + (u[4] or 0) for u in usage),
    }
    for view in (detail, row):
        assert {k: view[k] for k in expected} == expected
    cost = sum(_price(u[0], u[1], u[2], u[3] or 0, u[4] or 0) for u in usage)
    assert detail["cost_usd"] == pytest.approx(cost, abs=1e-9)
    assert row["cost_usd"] == pytest.approx(cost, abs=1e-9)
    assert _growth_cost(detail) == pytest.approx(cost, abs=1e-9)  # the events add up to the session total
    requests = [e for e in detail["events"] if e["event_type"] == "api_request"]
    assert len(requests) == row["api_call_count"] == len(usage)
    assert sum(e["output_tokens"] for e in requests) == expected["output_tokens"]
    if usage is _HIGH:
        assert expected["cache_read_tokens"] > 40_000_000 and detail["cost_usd"] > 10  # a large session is not clipped


@pytest.mark.asyncio
async def test_example_complete_zero_is_a_reported_zero() -> None:
    sid = f"t16833-zero-{uuid.uuid4().hex[:8]}"
    summary = _summary(turns=0, tool_calls=0, agents={}, skills={})
    detail, row, _ = await _served(sid, summary=summary, interactions=0, tools=0)
    assert _view(detail) == _view(row) == (0, 0, 0, "final", _ALL)
    assert (detail["agent_count"], detail["skill_count"]) == (0, 0)


@pytest.mark.asyncio
async def test_example_no_summary_and_no_activity_is_unknown_not_zero() -> None:
    sid = f"t16833-none-{uuid.uuid4().hex[:8]}"
    detail, row, turns = await _served(sid, summary=None, interactions=0, tools=0)
    assert _view(detail) == _view(row) == (None, 0, None, "none", [])
    assert (detail["agent_count"], detail["skill_count"], turns) == (None, None, 0)


@pytest.mark.asyncio
async def test_example_hooks_only_observed_is_a_partial_lower_bound() -> None:
    sid = f"t16833-obs-{uuid.uuid4().hex[:8]}"
    detail, row, turns = await _served(sid, summary=None, interactions=1, tools=1)
    assert _view(detail) == _view(row) == (1, 1, None, "none", [])
    assert (detail["tool_calls_success"], detail["tool_success_rate"], turns) == (1, 100.0, 1)
    assert (detail["agent_count"], detail["skill_count"]) == (None, None)


@pytest.mark.parametrize("usage", [None, _HIGH], ids=["ordinary-usage", "synthetic-high-usage"])
@pytest.mark.asyncio
async def test_example_summary_total_with_incomplete_events(usage) -> None:
    sid = f"t16833-mid-{uuid.uuid4().hex[:8]}"
    detail, row, turns = await _served(sid, summary=_summary(), interactions=1, tools=1, usage=usage)
    _assert_usage_totals(detail, row, usage or [_ORDINARY] * 5)
    assert _view(detail) == _view(row) == (2, 1, 3, "final", _ALL)
    assert turns == 2
    assert (detail["agent_count"], detail["skill_count"]) == (2, 1)
    # successes stay the observed ones: 1 of the 1 observed call
    assert (detail["tool_calls_success"], detail["tool_success_rate"]) == (1, 100.0)
    assert len([e for e in detail["events"] if e["event_type"] == "api_request"]) == 5
    agg = _served.aggregates  # observed definitions for the aggregates; turns are whole-session
    assert (agg["overview"]["kpis"]["total_tool_calls"], agg["overview"]["kpis"]["total_turns"]) == (1, 2)
    assert [u["tool_calls"] for u in agg["users"]["rows"]] == [1]
    assert [r["tool_calls"] for r in agg["repos"]["rows"]] == [1]
    # whole-session turns reach the users and repositories views as well, the same value the session shows
    assert [u["turns"] for u in agg["users"]["rows"]] == [2]
    assert [r["turns"] for r in agg["repos"]["rows"]] == [2]


@pytest.mark.asyncio
async def test_example_provisional_summary_with_later_events_never_lowers_the_observed() -> None:
    sid = f"t16833-prov-{uuid.uuid4().hex[:8]}"
    summary = _summary(turns=1, tool_calls=1, agents={}, skills={}, is_final=False)
    detail, row, turns = await _served(sid, summary=summary, interactions=2, tools=2)
    assert _view(detail) == _view(row) == (2, 2, 2, "provisional", _ALL)
    assert turns == 2


@pytest.mark.asyncio
async def test_a_later_final_summary_replaces_a_provisional_one() -> None:
    sid = f"t16833-final-{uuid.uuid4().hex[:8]}"
    first = _summary(turns=1, tool_calls=1, is_final=False)
    detail, row, _ = await _served(sid, summary=first, then=_summary(turns=4, tool_calls=6), interactions=0, tools=0)
    assert _view(detail) == _view(row) == (4, 0, 6, "final", _ALL)


@pytest.mark.asyncio
async def test_example_omitted_and_malformed_summary_fields_are_unknown() -> None:
    sid = f"t16833-bad-{uuid.uuid4().hex[:8]}"
    summary = _summary(turns="many", tool_calls=-1, agents=5, skills=["x"])
    detail, row, _ = await _served(sid, summary=summary, interactions=1, tools=1)
    assert _view(detail) == _view(row) == (1, 1, None, "final", [])
    assert (detail["agent_count"], detail["skill_count"]) == (None, None)
    omitted = f"t16833-omit-{uuid.uuid4().hex[:8]}"
    partial = {k: v for k, v in _summary().items() if k not in ("turns", "tool_calls", "agents", "skills")}
    detail, row, _ = await _served(omitted, summary=partial, interactions=0, tools=0)
    assert _view(detail) == _view(row) == (None, 0, None, "final", [])


@pytest.mark.asyncio
async def test_a_row_stored_before_summary_state_existed_is_provisional_with_its_non_null_columns() -> None:
    sid = f"t16833-legacy-{uuid.uuid4().hex[:8]}"
    detail, row, _ = await _served(sid, summary=_summary(), legacy=True, interactions=1, tools=1)
    assert _view(detail) == _view(row) == (2, 1, 3, "provisional", _ALL)


@pytest.mark.parametrize("key", ["agents", "skills"])
@pytest.mark.parametrize(
    ("value", "served"),
    [
        ({}, 0),  # an empty map is a reported zero
        ({"Explore": 2, "Plan": 1}, 3),
        ({"Explore": "bad"}, None),  # one bad entry makes the map unknown, never a known zero
        ({"Explore": -1}, None),
        ({"Explore": 2, "Plan": "bad"}, None),  # mixed: no silent undercount, no completeness claim
        ({"Explore": "2"}, None),  # a count is a JSON number
    ],
)
@pytest.mark.asyncio
async def test_a_summary_map_is_reported_only_when_every_entry_is_a_count(key: str, value: dict, served) -> None:
    sid = f"t16833-map-{uuid.uuid4().hex[:8]}"
    detail, _, _ = await _served(sid, summary=_summary(**{key: value}), interactions=0, tools=0)
    assert detail[f"{key[:-1]}_count"] == served
    other = "agent_count" if key == "skills" else "skill_count"
    assert detail[other] is not None  # the sibling map is untouched
    assert detail["summary_metrics"] == (_ALL if served is not None else [m for m in _ALL if m != key])
    assert detail["summary_state"] == "final"


_FORGED = {"summary_state": "final", "summary_reported": ["turns", "tool_calls", "agents", "skills"]}


@pytest.mark.asyncio
async def test_client_attrs_named_like_the_summary_meaning_are_not_stored() -> None:
    """A client key in an env or summary event must neither be stored nor change the served state."""
    env, prov = f"t16833-forge-env-{uuid.uuid4().hex[:8]}", f"t16833-forge-prov-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    try:
        await _seed(engine, env, summary=None, interactions=1, tools=1)
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO hook_events (ts, session_id, event_type, attrs) VALUES ($1, $2, 'agent.session.env', $3::jsonb)",
                _at(1),
                env,
                json.dumps(_FORGED),
            )
        await _seed(engine, prov, summary=_summary(is_final=False) | _FORGED, interactions=1, tools=1)
        await _refresh(engine)
        async with engine.acquire() as conn:
            stored = await conn.fetchval("SELECT attrs FROM session_dims WHERE session_id = $1", env)
        stored = json.loads(stored) if stored else {}
        assert not {"summary_state", "summary_reported"} & set(stored)
        detail = await _detail(engine, env)
        assert (detail["summary_state"], detail["summary_metrics"]) == ("none", [])
        assert (await _detail(engine, prov))["summary_state"] == "provisional"  # the server's state, not the client's
    finally:
        await _cleanup(engine, [env, prov])
        await engine.close()


@pytest.mark.asyncio
async def test_efficiency_session_depth_uses_whole_session_turns() -> None:
    """Summary turns above the observed ones move the session to the deeper bucket; unknown turns stay in '1'."""
    deep, unknown = f"t16833-deep-{uuid.uuid4().hex[:8]}", f"t16833-unk-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    try:
        await _seed(engine, deep, summary=_summary(turns=12), interactions=1, tools=0)
        await _seed(engine, unknown, summary=None, interactions=0, tools=0)
        await _refresh(engine)
        window = LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE, start_dt=_START - timedelta(days=1), end_dt=_START + timedelta(days=1)
        ).restricted_to([deep, unknown])
        with patch(_PRICING, _lookup):
            data, _ = await LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine)).get_efficiency(window)
        buckets = {b["bucket"]: b["count"] for b in data["session_depth"]}
        assert buckets["11-25"] == 1 and buckets["1"] == 1
        assert sum(buckets.values()) == 2
    finally:
        await _cleanup(engine, [deep, unknown])
        await engine.close()
