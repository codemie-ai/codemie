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

"""EPMCDME-17339 on a real PostgreSQL: an inline Skill-tool row of the Session Detail Timeline prices the main-thread
requests the plugin attributed to its skill in its own prompt, from its call to the next start of the same skill,
not only those up to the next sibling dispatch. Needs `TEST_LOCAL_PG_URL` (a disposable PostgreSQL); skipped without.

    TEST_LOCAL_PG_URL=postgresql://<user>:<password>@<host>:<port>/<db> poetry run pytest -n0 <this file>
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest

from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from tests.codemie.repository.cli_analytics.postgres.test_live_route_pricing import (
    _SONNET,
    _SONNET_RATES,
    _TODAY,
    _cleanup,
    _detail,
    _dispatch,
    _engine,
    _entry,
    _id,
    _ingest,
    _iso,
    _refresh,
)
from tests.codemie.repository.cli_analytics.support import otlp_builders as b
from tests.codemie.repository.cli_analytics.support.scopes import DETAIL_SCOPE

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("TEST_LOCAL_PG_URL"), reason="TEST_LOCAL_PG_URL is not set: no database"
)

_RESOURCE = {"service.name": "claude-code", "user.email": "test@example.com"}
_CC = "claude-code"
_SEQ = iter(range(1, 10_000))


async def _otlp(engine, signal, request):
    ingestor = PostgresTelemetryIngestor(engine, clock=lambda: _TODAY.date())
    await ingestor.ingest_otlp(signal, request.SerializeToString(), "application/x-protobuf")


async def _span(engine, sid, name, start, seconds, span_id, parent, **attrs):
    """A Claude Code span through the OTLP traces path, as the CLI exports it."""
    sp = b.span(
        name, trace_id=b"t" * 16, span_id=span_id, parent_span_id=parent or b"", start=start,
        end=start + timedelta(seconds=seconds), attrs={"session.id": sid, **attrs},
    )  # fmt: skip
    await _otlp(engine, "traces", b.traces_request(_RESOURCE, [sp]))


async def _request(engine, sid, ts, prompt, rid, scope, usage, *, agent_id="", span=None, cache_split=None):
    """An API request (cost, input, output, cache read, cache write): its api_request log record through the OTLP
    logs path, with Claude Code's `prompt.id` attribute, and the plugin's transcript request with its scope."""
    cost, inp, out, read, write = usage
    attrs = {
        "event.name": "api_request", "session.id": sid, "event.sequence": next(_SEQ), "prompt.id": prompt,
        "request_id": rid, "model": _SONNET, "cost_usd": cost, "input_tokens": inp, "output_tokens": out,
        "cache_read_tokens": read, "cache_creation_tokens": write,
    }  # fmt: skip
    record = b.log_record(ts, attrs, span_id=span or _id(f"int-{sid}"))
    await _otlp(engine, "logs", b.logs_request(_RESOURCE, [record]))
    event = {
        "type": "agent.usage.request", "timestamp": _iso(ts), "session_id": sid, "request_id": rid,
        "message_id": "", "agent_id": agent_id, "model_raw": _SONNET, "model": _SONNET, "input_tokens": inp,
        "output_tokens": out, "cache_read_tokens": read,
        "stop_reason": "end_turn", "platform": _CC,
    }  # fmt: skip
    if cache_split is not None:
        cache_5m, cache_1h = cache_split
        event |= {"cache_creation_5m_tokens": cache_5m, "cache_creation_1h_tokens": cache_1h}
    if scope:
        event |= {"scope_kind": scope[0], "scope_name": scope[1]}
    await _ingest(engine, [event])


async def _tool_call(engine, sid, ts, prompt, tool_use_id, tool, *, skill="", agent_type="", agent_id=""):
    """A Claude Code Skill or Agent call as exported: its `claude_code.tool` span under the interaction span, and the
    plugin's PreToolUse and PostToolUse hook events (the CLI's hook input forwarded as is, with `prompt_id`). An
    Agent's result names its agentId; an inline Skill's names no fork."""
    attrs = {"tool_name": tool, "tool_use_id": tool_use_id}
    attrs |= {"subagent_type": agent_type} if agent_type else {"skill_name": skill}
    await _span(engine, sid, "claude_code.tool", ts, 15, _id(f"call-{sid}-{tool_use_id}"), _id(f"int-{sid}"), **attrs)
    # an Agent's result text is cut before its agentId, which the SubagentStop of the call carries (captured:
    # support/foreground-agent-run.json); an inline Skill's result names no fork
    response = {"status": "completed", "prompt": "a long echoed prompt cut"} if agent_id else {"success": True}
    base = {"session_id": sid, "prompt_id": prompt, "platform": _CC}
    call = {**base, "tool_name": tool, "tool_use_id": tool_use_id}
    forks = []
    if agent_id:
        sub = {**base, "agent_id": agent_id, "agent_type": agent_type}
        forks = [
            {"type": "agent.subagent.start", "timestamp": _iso(ts + timedelta(seconds=1)), "hook_event_name": "SubagentStart", **sub},
            {"type": "agent.subagent.stop", "timestamp": _iso(ts + timedelta(seconds=14)), "hook_event_name": "SubagentStop", "tool_use_id": tool_use_id, **sub},
        ]  # fmt: skip
    await _ingest(
        engine,
        [
            {"type": "agent.tool.start", "timestamp": _iso(ts), "hook_event_name": "PreToolUse", **call},
            *forks,
            {
                "type": "agent.tool.end", "timestamp": _iso(ts + timedelta(seconds=15)),
                "hook_event_name": "PostToolUse", "tool_response": json.dumps(response), **call,
            },
        ],
    )  # fmt: skip


async def _interaction(engine, sid):
    await _span(engine, sid, "claude_code.interaction", _TODAY, 3600, _id(f"int-{sid}"), None)


# QA-B: one prompt. REQ1 (unscoped) precedes the Skill call; REQ2 follows it; a sibling Agent starts after REQ2 and its
# subagent's own request carries the skill scope too; REQ3 is the main thread's again; a second Agent follows and
# REQ4 comes last.
_REQ1 = (0.7, 100, 10, 1000, 100)
_REQ2 = (0.0009, 2, 400, 40000, 1000)
_REQ3 = (0.0009, 2, 500, 30000, 2000)
_REQ4 = (0.000879501, 2, 538, 15431, 1310)
_SUBAGENT1 = (0.5, 9, 9, 9, 9)
_SUBAGENT2 = (0.25, 7, 7, 7, 7)
_QAA = (0.0011, 5, 50, 5000, 500)


async def _agent(engine, sid, ts, prompt, tool_use_id, agent_type, agent_id, scope, usage):
    """An Agent call and the one request of its subagent, which sits under its own llm_request span group."""
    group = _id(f"group-{sid}-{agent_id}")
    await _tool_call(engine, sid, ts, prompt, tool_use_id, "Agent", agent_type=agent_type, agent_id=agent_id)
    # the llm_request span carries the agentId and is a child of the group span its api_request records sit under
    await _span(engine, sid, "claude_code.llm_request", ts + timedelta(seconds=10), 1, _id(f"llm-{sid}-{agent_id}"),
                group, agent_id=agent_id)  # fmt: skip
    await _request(engine, sid, ts + timedelta(seconds=10), prompt, f"req-{agent_id}", scope, usage,
                   agent_id=agent_id, span=group)  # fmt: skip


async def _qa_session(engine, sid):
    at = lambda m, s=0: _TODAY + timedelta(minutes=m, seconds=s)  # noqa: E731
    await _interaction(engine, sid)
    await _request(engine, sid, at(1), "p-b", "req1", None, _REQ1)
    await _tool_call(engine, sid, at(2), "p-b", "tu-skill", "Skill", skill="qab")
    await _request(engine, sid, at(2, 10), "p-b", "req2", ("skill", "qab"), _REQ2, cache_split=(1000, None))
    # the first Agent's subagent request carries the skill scope too; the second one's its own agent scope
    await _agent(engine, sid, at(2, 20), "p-b", "tu-a1", "Explore", "ag17339one", ("skill", "qab"), _SUBAGENT1)
    await _request(engine, sid, at(2, 40), "p-b", "req3", ("skill", "qab"), _REQ3, cache_split=(None, 2000))
    await _agent(engine, sid, at(2, 50), "p-b", "tu-a2", "general-purpose", "ag17339two", ("agent", "gp"), _SUBAGENT2)
    await _request(engine, sid, at(3), "p-b", "req4", ("skill", "qab"), _REQ4)
    # QA-A: a slash command of its own prompt, after the Skill call's
    await _ingest(engine, [_dispatch(sid, at(5), "p-a", "qaa")])
    await _request(engine, sid, at(5, 10), "p-a", "reqa", ("skill", "qaa"), _QAA)
    await _refresh(engine, _TODAY.date())


@pytest.mark.asyncio
async def test_live_an_inline_skill_call_row_prices_its_own_prompts_attributed_requests_and_nothing_else() -> None:
    """Spans, hooks and API-request logs reach the tables through the OTLP and hook-event ingestion, as the CLI sends
    them; the Skill row, read at the Session Detail consumer, holds REQ2-REQ4 and nothing of REQ1, the subagents
    or the slash command, and both Agent rows keep their own requests."""
    sid = f"test-17339-skill-tool-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    try:
        await _qa_session(engine, sid)
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
        async with engine.acquire() as conn:
            stored = await conn.fetch(
                "SELECT tool_name, prompt_id FROM hook_events WHERE session_id = $1 AND tool_name = 'Skill'", sid
            )
            logged = await conn.fetchval(
                "SELECT count(*) FROM log_events WHERE session_id = $1 AND prompt_id = 'p-b'", sid
            )
        detail = await _detail(engine, sid, [_entry(_SONNET, _SONNET_RATES)], {})
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    # the prerequisite: ingestion keeps the Skill hooks' prompt_id and the requests' prompt.id as one prompt_id
    assert [(r["tool_name"], r["prompt_id"]) for r in stored] == [("Skill", "p-b")] * 2
    assert logged == 6  # REQ1-REQ4 and the two subagents' requests

    by = {(r["subagent_type"], r["skill_name"], r["is_slash_command"]): r for r in rows}
    skill = by[("", "qab", 0)]
    tokens = [skill[k] for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")]
    assert tokens == [6, 1438, 85431, 4310]
    assert skill["cost_usd"] == pytest.approx(0.002679501, abs=1e-12)
    assert skill["real_duration_ms"] == 60_000  # call (2:00) to its last attributed request (REQ4 at 3:00)
    for key, cost, inp in [
        (("Explore", "", 0), 0.5, 9),
        (("general-purpose", "", 0), 0.25, 7),
        (("", "qaa", 1), 0.0011, 5),
    ]:
        assert (by[key]["cost_usd"], by[key]["input_tokens"]) == (pytest.approx(cost), inp), key

    # the serialized timeline: the Skill row's tokens, catalog-priced cost and duration
    rows_by_label = {d["label"]: d for d in detail["dispatches"]}
    skill_row = rows_by_label["skill · qab"]
    assert (skill_row["input_tokens"], skill_row["output_tokens"], skill_row["cache_read_tokens"]) == (6, 1438, 85431)
    assert skill_row["cache_creation_tokens"] == 4310
    assert skill["models"][0]["cache_creation_5m_tokens"] == 1000
    assert skill["models"][0]["cache_creation_1h_tokens"] == 2000
    # REQ4 has no split and uses the default 5m rate. Only REQ3 uses the 1h rate.
    price = 6 * 3e-6 + 1438 * 1.5e-5 + 85431 * 3e-7 + 2310 * 3.75e-6 + 2000 * 6e-6
    assert skill_row["cost_usd"] == pytest.approx(price, abs=1e-9)
    assert skill_row["duration_ms"] == 60_000, skill_row
    agents = sorted((d["input_tokens"] for d in detail["dispatches"] if d["kind"] == "agent"))
    assert agents == [7, 9], detail["dispatches"]
    assert detail["input_tokens"] == 100 + 6 + 9 + 7 + 5  # REQ1 is nobody's; no request is counted twice


@pytest.mark.asyncio
async def test_live_legacy_fallback_skips_owned_requests_and_a_same_name_pair_splits() -> None:
    """Legacy: a Skill call whose requests carry no scope still takes its parent-request window, less a request a slash
    command owns. Same name: a slash dispatch and a Skill call of one skill in one prompt never share a request."""
    sid = f"test-17339-legacy-{uuid.uuid4().hex[:8]}"
    at = lambda m, s=0: _TODAY + timedelta(minutes=m, seconds=s)  # noqa: E731
    engine = await _engine()
    try:
        await _interaction(engine, sid)
        u = lambda n: (0.001, n, 1, 1, 1)  # noqa: E731
        await _tool_call(engine, sid, at(1), "p-l", "tu-legacy", "Skill", skill="legacy")
        await _request(engine, sid, at(1, 10), "p-l", "r1", None, u(1))
        await _ingest(engine, [_dispatch(sid, at(1, 20), "p-d", "qaa")])
        await _request(engine, sid, at(1, 30), "p-d", "owned", ("skill", "qaa"), u(100))
        await _request(engine, sid, at(1, 40), "p-l", "r2", None, u(2))
        await _ingest(engine, [_dispatch(sid, at(2), "p-m", "mixed")])
        await _request(engine, sid, at(2, 10), "p-m", "m1", ("skill", "mixed"), u(10))
        await _tool_call(engine, sid, at(2, 20), "p-m", "tu-mixed", "Skill", skill="mixed")
        await _request(engine, sid, at(2, 30), "p-m", "m2", ("skill", "mixed"), u(20))
        # a later call of the same skill whose hooks carry no prompt_id still ends the earlier call's window
        await _tool_call(engine, sid, at(3), "p-t", "tu-twice", "Skill", skill="twice")
        await _request(engine, sid, at(3, 10), "p-t", "t1", ("skill", "twice"), u(7))
        await _tool_call(engine, sid, at(3, 20), "", "tu-nohook", "Skill", skill="twice")
        await _request(engine, sid, at(3, 30), "p-t", "t2", ("skill", "twice"), u(9))
        await _refresh(engine, _TODAY.date())
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    assert [r["input_tokens"] for r in rows if r["skill_name"] == "twice"].count(7) == 1  # t1 only; not t2
    by = {(r["skill_name"], r["is_slash_command"]): r["input_tokens"] for r in rows}
    assert by[("legacy", 0)] == 1 + 2  # r1 and r2 of its window to the next sibling (2:20); not `owned`, not m1
    assert by[("qaa", 1)] == 100
    assert by[("mixed", 1)] == 10
    assert by[("mixed", 0)] == 20
