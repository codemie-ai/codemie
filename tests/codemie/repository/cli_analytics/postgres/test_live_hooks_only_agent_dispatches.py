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

"""Live-Postgres checks for EPMCDME-16573: Session Detail Timeline agent bars for hooks-only sessions
(GitHub Copilot CLI, and any Claude Code session ingested without OTel). Needs `TEST_LOCAL_PG_URL`
(a disposable PostgreSQL); skipped without.

    TEST_LOCAL_PG_URL=postgresql://<user>:<password>@<host>:<port>/<db> poetry run pytest -n0 <this file>
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

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
    _fork,
    _hook,
    _id,
    _ingest,
    _refresh,
    _stop,
)
from tests.codemie.repository.cli_analytics.postgres.test_live_skill_tool_attribution import (
    _agent,
    _interaction,
    _request,
    _span,
    _tool_call,
)
from tests.codemie.repository.cli_analytics.support.scopes import DETAIL_SCOPE

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("TEST_LOCAL_PG_URL"), reason="TEST_LOCAL_PG_URL is not set: no database"
)

_USAGE = (0.5, 9, 9, 9, 9)  # cost, input, output, cache_read, cache_write


async def _plain_subagent(engine, sid, start_ts, stop_ts, agent_id, agent_type, platform, *, duration_ms=None):
    """A hooks-only subagent fork: SubagentStart/Stop with no Agent tool call and no span at all. `duration_ms`
    seeds the stop's own duration_ms (an older CLI sends none, the default)."""
    await _ingest(
        engine,
        [
            _hook("agent.subagent.start", "SubagentStart", sid, start_ts,
                  agent_id=agent_id, agent_type=agent_type, platform=platform),
            _hook("agent.subagent.stop", "SubagentStop", sid, stop_ts,
                  agent_id=agent_id, agent_type=agent_type, platform=platform,
                  **({} if duration_ms is None else {"duration_ms": duration_ms})),
        ],
    )  # fmt: skip


async def _seed_subagent_usage_row(engine, sid, day, agent_id, model, usage):
    """A session_usage totals-only row (attrs.token_source = 'subagent_usage') for an agent with no span at
    all: a direct insert, since the production rollup only ever writes this row with source_cost_usd
    NULL (it is priced at query time from the session's requests, which such an agent has none of) — this
    seeds a real cost so the reader's fallback can be proven against it."""
    cost, inp, out, read, write = usage
    async with engine.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO session_usage
                (session_id, day, model, speed, inference_geo, scope_kind, scope_name, agent_id,
                 input_tokens, cache_read_tokens, cache_creation_tokens, output_tokens, api_calls,
                 source_cost_usd, attrs)
            VALUES ($1, $2, $3, '', '', '', '', $4, $5, $6, $7, $8, 1, $9,
                    jsonb_build_object('token_source', 'subagent_usage'))
            """,
            sid,
            day,
            model,
            agent_id,
            inp,
            read,
            write,
            out,
            cost,
        )


async def _plain_subagent_with_usage(engine, sid, start_ts, stop_ts, agent_id, agent_type, platform, prompt_id, usage):
    """The same hooks-only fork, plus agent-scope usage: an `llm_request` span (never a TOOL span) and its
    API request, both carrying `agent_id` — the `span_agent_usage` path `agent_usage` already joins
    through, exactly as the existing OTel-sourced agent rows do."""
    await _plain_subagent(engine, sid, start_ts, stop_ts, agent_id, agent_type, platform)
    group = _id(f"group-{sid}-{agent_id}")
    await _span(engine, sid, "claude_code.llm_request", start_ts + timedelta(seconds=5), 1,
                _id(f"llm-{sid}-{agent_id}"), group, agent_id=agent_id)  # fmt: skip
    await _request(engine, sid, start_ts + timedelta(seconds=5), prompt_id, f"req-{agent_id}", None, usage,
                   agent_id=agent_id, span=group)  # fmt: skip


async def _seed_hooks_only_session(engine, sid):
    at = lambda m, s=0: _TODAY + timedelta(minutes=m, seconds=s)  # noqa: E731
    # two named agents, sequential, Copilot-shaped (platform != 'claude-code')
    await _plain_subagent(engine, sid, at(1), at(3), "ag-explore", "explore", "copilot-cli")
    await _plain_subagent_with_usage(
        engine, sid, at(4), at(6), "ag-file-auditor", "file-auditor", "copilot-cli", "p-fa", _USAGE
    )
    # a /fleet-shaped parallel run: three overlapping subagents of the same agent_type
    await _plain_subagent(engine, sid, at(7, 0), at(7, 30), "ag-fleet-1", "general-purpose", "copilot-cli")
    await _plain_subagent(engine, sid, at(7, 5), at(7, 35), "ag-fleet-2", "general-purpose", "copilot-cli")
    await _plain_subagent(engine, sid, at(7, 10), at(7, 20), "ag-fleet-3", "general-purpose", "copilot-cli")
    await _refresh(engine, _TODAY.date())


@pytest.mark.asyncio
async def test_live_hooks_only_session_gets_one_agent_row_per_subagent() -> None:
    """A Copilot-shaped, hooks-only session (no TOOL spans at all) with five subagent forks renders one
    Timeline agent bar per fork, each from its own start hook to its own stop hook, overlaps preserved,
    only the one with usage carrying cost/tokens, the synthetic 'session' row unaffected."""
    sid = f"test-16573-hooks-only-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    try:
        await _seed_hooks_only_session(engine, sid)
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
        detail = await _detail(engine, sid, [_entry(_SONNET, _SONNET_RATES)], {})
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    agent_rows = [r for r in rows if r["subagent_type"] != ""]
    assert len(agent_rows) == 5, rows  # one row per fork: none dropped, none doubled
    by_type: dict[str, list[dict]] = {}
    for r in agent_rows:
        by_type.setdefault(r["subagent_type"], []).append(r)
    assert set(by_type) == {"explore", "file-auditor", "general-purpose"}
    assert len(by_type["general-purpose"]) == 3  # the three fleet rows share a type, not a row

    # asyncpg returns `dispatch_start` as a naive timestamp (the column has no time zone); strip tzinfo to compare.
    at = lambda m, s=0: (_TODAY + timedelta(minutes=m, seconds=s)).replace(tzinfo=None)  # noqa: E731
    explore = by_type["explore"][0]
    assert explore["dispatch_start"] == at(1)
    assert explore["subagent_duration_ms"] == 120_000  # 1:00 to 3:00
    assert explore["real_duration_ms"] == 120_000
    assert explore["cost_usd"] is None and explore["input_tokens"] is None  # never the main thread's

    auditor = by_type["file-auditor"][0]
    assert auditor["dispatch_start"] == at(4)
    assert auditor["subagent_duration_ms"] == 120_000  # 4:00 to 6:00
    assert auditor["cost_usd"] == pytest.approx(0.5)
    assert auditor["input_tokens"] == 9

    # CR-003: exact values, not a relative inequality many wrong orderings/durations would also satisfy
    # (seeded: fleet-1 7:00-7:30, fleet-2 7:05-7:35, fleet-3 7:10-7:20).
    fleet = sorted(by_type["general-purpose"], key=lambda r: r["dispatch_start"])
    assert fleet[0]["dispatch_start"] == at(7, 0)
    assert fleet[0]["subagent_duration_ms"] == 30_000
    assert fleet[1]["dispatch_start"] == at(7, 5)
    assert fleet[1]["subagent_duration_ms"] == 30_000
    assert fleet[2]["dispatch_start"] == at(7, 10)
    assert fleet[2]["subagent_duration_ms"] == 10_000
    assert all(r["cost_usd"] is None for r in fleet)  # none of the fleet rows takes the auditor's usage

    kinds = [d["kind"] for d in detail["dispatches"]]
    assert kinds.count("session") == 1
    assert kinds.count("agent") == 5


@pytest.mark.asyncio
async def test_live_hooks_only_agent_duration_prefers_stop_duration_ms_over_wall_time() -> None:
    """CR-001: a hooks-only agent's bar anchors from the stop's own `duration_ms`, exactly as the OTel
    branch's does, instead of the raw SubagentStart-to-stop wall time: with a stop lagging the agent's
    real end, the bar still starts `duration_ms` before the stop and the drawn duration is that
    `duration_ms`, not the wider wall-clock gap."""
    sid = f"test-16573-hooks-only-stopdur-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    at = lambda m, s=0: (_TODAY + timedelta(minutes=m, seconds=s)).replace(tzinfo=None)  # noqa: E731
    try:
        await _plain_subagent(
            engine, sid, at(1), at(5), "ag-lagging-stop", "general-purpose", "copilot-cli", duration_ms=120_000
        )
        await _refresh(engine, _TODAY.date())
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    (row,) = [r for r in rows if r["subagent_type"] == "general-purpose"]
    assert row["subagent_duration_ms"] == 120_000  # the stop's own duration_ms, not the 4-minute wall time
    assert row["real_duration_ms"] == 120_000  # mirrors subagent_duration_ms, as the skill-fallback row does
    assert row["dispatch_start"] == at(5) - timedelta(milliseconds=120_000)  # anchored to end exactly on the stop
    assert row["dispatch_start"] != at(1)  # not the raw SubagentStart


@pytest.mark.asyncio
async def test_live_hooks_only_agent_with_no_span_falls_back_to_session_usage_totals() -> None:
    """CR-002: a hooks-only agent Copilot reports cost for only via its `agent.subagent.usage` hook (no
    span at all) still gets its cost/tokens/models on the Timeline, from the session_usage row the cost
    rollup keeps for exactly that case (attrs.token_source = 'subagent_usage'), never overriding a
    span-priced agent (see `auditor` above, and `explore`'s NULL cost with neither source)."""
    sid = f"test-16573-hooks-only-usage-fallback-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    at = lambda m, s=0: _TODAY + timedelta(minutes=m, seconds=s)  # noqa: E731
    usage = (0.42, 100, 50, 20, 5)  # cost, input, output, cache_read, cache_write
    try:
        await _plain_subagent(engine, sid, at(1), at(3), "ag-copilot-usage", "general-purpose", "copilot-cli")
        await _refresh(engine, _TODAY.date())
        await _seed_subagent_usage_row(engine, sid, _TODAY.date(), "ag-copilot-usage", _SONNET, usage)
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    (row,) = [r for r in rows if r["subagent_type"] == "general-purpose"]
    assert row["cost_usd"] == pytest.approx(0.42)
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 50
    assert row["cache_read_tokens"] == 20
    assert row["cache_creation_tokens"] == 5
    assert row["models"] == [
        {
            "model_name": _SONNET, "input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 20,
            "cache_creation_tokens": 5, "cache_creation_5m_tokens": None, "cache_creation_1h_tokens": None,
        }
    ]  # fmt: skip


@pytest.mark.asyncio
async def test_live_claude_code_agent_with_hooks_gets_no_duplicate_row() -> None:
    """A normal Claude Code session (an OTel Agent tool call span) whose subagent also has
    SubagentStart/Stop hooks for the same agent_id must still get exactly one Timeline row for it: the
    new hooks-only branch's NOT EXISTS guard against `t`/`launched` must suppress its own row here."""
    sid = f"test-16573-no-duplicate-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    try:
        await _interaction(engine, sid)
        await _agent(
            engine, sid, _TODAY + timedelta(minutes=1), "p-a", "tu-a1", "general-purpose", "ag-16573",
            None, (0.3, 4, 4, 4, 4),
        )  # fmt: skip
        await _refresh(engine, _TODAY.date())
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    agent_rows = [r for r in rows if r["subagent_type"] == "general-purpose"]
    assert len(agent_rows) == 1, rows  # the hook-only branch must not add a second row for ag-16573
    assert agent_rows[0]["cost_usd"] == pytest.approx(0.3)
    # CR-003: the OTel branch's own start/duration, not only its row count and cost. SubagentStart lands 1s
    # after the call (_tool_call's fork timing), SubagentStop 14s after it, no stop duration_ms here, so the
    # bar keeps the timestamp-only fallback and real_duration_ms runs to the end of the interaction span.
    assert agent_rows[0]["dispatch_start"] == (_TODAY + timedelta(minutes=1, seconds=1)).replace(tzinfo=None)
    assert agent_rows[0]["subagent_duration_ms"] == 13_000
    assert agent_rows[0]["real_duration_ms"] == 3_540_000


@pytest.mark.asyncio
async def test_live_slash_command_foreground_fork_gets_no_duplicate_row() -> None:
    """CR-001: a slash-command dispatch whose foreground fork (a SubagentStart with no skill_name, but a
    typed agent_type, same shape as GitHub Copilot CLI's) already gets its own Timeline row from the
    dispatch_fork branch. The new hooks-only plain-agent branch matches that same SubagentStart too
    (agent_type set, skill_name empty) and, without the dispatch_fork guard the skill-fallback branch
    carries, would add a second row for the same fork."""
    sid = f"test-16573-slash-fg-dup-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    at = lambda m, s=0: _TODAY + timedelta(minutes=m, seconds=s)  # noqa: E731
    fork_agent = "ag-slash-fg"
    try:
        await _ingest(
            engine,
            [
                _dispatch(sid, at(1), "p-slash", "myskill"),
                _fork(sid, at(1, 2), fork_agent, "p-slash"),
                _stop(sid, at(1, 20), fork_agent),
            ],
        )
        await _refresh(engine, _TODAY.date())
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    assert len(rows) == 1, rows  # one row for the fork, not one from dispatch_fork plus one from hooks-only
    assert rows[0]["skill_name"] == "myskill" and rows[0]["is_slash_command"] == 1
    assert not any(r["subagent_type"] == "general-purpose" for r in rows)  # hooks-only must not also claim it


@pytest.mark.asyncio
async def test_live_skill_tool_fork_with_agent_type_gets_no_duplicate_row() -> None:
    """A Claude Code Skill tool call's fork (named by the Skill call's agent.tool.end via `fork_agent_id`)
    already gets its own Timeline row from the skill-fallback branch above, even when its own
    SubagentStart carries a typed agent_type and no skill_name (the same hook shape GitHub Copilot CLI's
    plain agent forks use). The new hooks-only plain-agent branch matches that same SubagentStart too and,
    without a guard against `skill_fork`, would add a second row for the same fork: one row, the
    initiator's, never also an agent."""
    sid = f"test-16573-skill-fork-dup-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    at = lambda m, s=0: _TODAY + timedelta(minutes=m, seconds=s)  # noqa: E731
    fork_agent = "ag-sk"
    try:
        await _interaction(engine, sid)
        await _span(
            engine, sid, "claude_code.tool", at(1), 15, _id(f"call-{sid}-tu-skill"), _id(f"int-{sid}"),
            tool_name="Skill", tool_use_id="tu-skill", skill_name="my-skill",
        )  # fmt: skip
        await _ingest(
            engine,
            [
                _hook("agent.tool.start", "PreToolUse", sid, at(1),
                      prompt_id="p-sk", tool_name="Skill", tool_use_id="tu-skill"),
                _hook("agent.tool.end", "PostToolUse", sid, at(1, 15),
                      prompt_id="p-sk", tool_name="Skill", tool_use_id="tu-skill",
                      fork_agent_id=fork_agent, tool_response='{"success": true}'),
            ],
        )  # fmt: skip
        await _plain_subagent(engine, sid, at(1, 2), at(1, 20), fork_agent, "general-purpose", "claude-code")
        await _refresh(engine, _TODAY.date())
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    assert len(rows) == 1, rows  # one row for the fork, not one from skill_fork plus one from hooks-only
    assert rows[0]["skill_name"] == "my-skill"
    assert not any(r["subagent_type"] == "general-purpose" for r in rows)  # hooks-only must not also claim it


@pytest.mark.asyncio
async def test_live_hooks_only_skill_fork_with_no_span_gets_no_agent_row() -> None:
    """A Claude Code Skill call recorded only by hooks (no span at all) still has a `skill_fork` row naming
    its fork's agentId (the CTE lists every Skill call's fork by agent_id alone, span or not), so the guard
    above must not require a span to find it. Without this, the fork's own SubagentStart (agent_type set,
    no skill_name, the same hook shape GitHub Copilot CLI's plain agent forks use) would wrongly render as
    a standalone agent bar instead of staying invisible, as it is on main: a fork is never an agent."""
    sid = f"test-16573-skill-fork-nospan-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    at = lambda m, s=0: _TODAY + timedelta(minutes=m, seconds=s)  # noqa: E731
    fork_agent = "ag-s"
    try:
        await _ingest(
            engine,
            [
                _hook("agent.tool.start", "PreToolUse", sid, at(1),
                      prompt_id="p-s", tool_name="Skill", tool_use_id="tu-s"),
                _hook("agent.tool.end", "PostToolUse", sid, at(1, 15),
                      prompt_id="p-s", tool_name="Skill", tool_use_id="tu-s",
                      fork_agent_id=fork_agent, tool_response='{"success": true}'),
            ],
        )  # fmt: skip
        await _plain_subagent(engine, sid, at(1, 2), at(1, 20), fork_agent, "general-purpose", "claude-code")
        await _refresh(engine, _TODAY.date())
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    assert rows == []  # the fork is never an agent row; with no span, no skill row exists for it either


@pytest.mark.asyncio
async def test_live_unresolved_agent_call_gets_no_duplicate_row() -> None:
    """A Claude Code Agent tool call whose agentId never resolves (no SubagentStop names its tool_use_id,
    no fork_agent_id, no parseable result text) still gets its own Timeline row from the OTel branch above,
    keyed by tool_use_id rather than agentId. A session can separately have a genuine hooks-only subagent
    run of the same subagent_type with no tie to that call at all; the guard above must still skip it,
    since the unresolved call's own span row already stands for a run of that type and this branch cannot
    tell whether its hook-named agentId belongs to that same unresolved call."""
    sid = f"test-16573-unresolved-call-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    at = lambda m, s=0: _TODAY + timedelta(minutes=m, seconds=s)  # noqa: E731
    try:
        await _interaction(engine, sid)
        await _tool_call(engine, sid, at(1), "p-b", "tu-b", "Agent", agent_type="general-purpose")
        await _plain_subagent(engine, sid, at(1, 2), at(1, 20), "ag-b", "general-purpose", "claude-code")
        await _refresh(engine, _TODAY.date())
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    agent_rows = [r for r in rows if r["subagent_type"] == "general-purpose"]
    assert len(agent_rows) == 1, rows  # the unresolved call's own span row only, not a second one from hooks


@pytest.mark.asyncio
async def test_live_unresolved_agent_call_does_not_hide_an_unrelated_hooks_only_agent_of_another_type() -> None:
    """AI-review finding on the guard above: an unresolved Agent call's own `la.agent_id IS NULL` must be
    scoped to its own subagent_type, never to 'any unresolved call anywhere in the session'. A session
    with an unresolved `general-purpose` Agent call and a completely unrelated, genuinely hooks-only
    `explore` fork must still show the `explore` bar — it is not a candidate for that unresolved call at
    all, so it must never be hidden by it."""
    sid = f"test-16573-unresolved-call-other-type-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    at = lambda m, s=0: _TODAY + timedelta(minutes=m, seconds=s)  # noqa: E731
    try:
        await _interaction(engine, sid)
        await _tool_call(engine, sid, at(1), "p-b", "tu-b", "Agent", agent_type="general-purpose")
        await _plain_subagent(engine, sid, at(2), at(2, 20), "ag-explore", "explore", "claude-code")
        await _refresh(engine, _TODAY.date())
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    assert any(r["subagent_type"] == "explore" for r in rows), rows  # never hidden by an unrelated type


# ── EPMCDME-16573 reopened QA: transcript-only requests that carry their own agent_id ─────────────────────────────
# (Copilot CLI with per-agent identity, Cowork's Agent calls): `agent.usage.request` events with no OTel api_request
# record. They reach session_usage under the agent's id, but no Timeline source read them.


async def _agent_request(engine, sid, ts, rid, agent_id, platform, usage, model=_SONNET):
    """A transcript-only request of one agent: only the plugin's agent.usage.request, with `agent_id` (an empty one is
    the main thread's)."""
    _, inp, out, read, write = usage
    await _ingest(
        engine,
        [{
            "type": "agent.usage.request", "timestamp": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "session_id": sid, "request_id": rid, "message_id": "", "agent_id": agent_id, "model_raw": model,
            "model": model, "input_tokens": inp, "output_tokens": out, "cache_read_tokens": read,
            "cache_creation_5m_tokens": write, "cache_creation_1h_tokens": 0, "stop_reason": "end_turn",
            "platform": platform,
        }],
    )  # fmt: skip


def _price(usage):
    _, inp, out, read, write = usage
    r = _SONNET_RATES
    return inp * r[0] + out * r[1] + read * r[2] + write * r[3]


_A1 = (0.0, 1000, 100, 5000, 300)
_A2 = (0.0, 2000, 40, 700, 0)
_MAIN = (0.0, 50, 5, 0, 0)


def _by_agent(rows):
    return {r["subagent_type"]: r for r in rows if r["subagent_type"] != ""}


@pytest.mark.asyncio
async def test_live_copilot_agent_requests_with_agent_id_price_each_parallel_agent_separately() -> None:
    """Copilot CLI shape: hooks only, two overlapping agents whose requests carry their own agent_id, a third agent
    with no request, and a main-thread request: each agent's bar holds its own input/output/cache and cost, the agent
    without usage stays unknown, the main thread's request is nobody's, and the session total is unchanged."""
    sid = f"test-16573-copilot-agent-id-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    at = lambda m, s=0: _TODAY + timedelta(minutes=m, seconds=s)  # noqa: E731
    try:
        await _plain_subagent(engine, sid, at(1), at(3), "ag-one", "reviewer", "copilot-cli")
        await _plain_subagent(engine, sid, at(1, 10), at(3, 20), "ag-two", "explorer", "copilot-cli")
        await _plain_subagent(engine, sid, at(4), at(5), "ag-idle", "idle-agent", "copilot-cli")
        await _agent_request(engine, sid, at(1, 30), "r1", "ag-one", "copilot-cli", _A1)
        await _agent_request(engine, sid, at(1, 40), "r2", "ag-two", "copilot-cli", _A2)
        await _agent_request(engine, sid, at(0, 30), "r0", "", "copilot-cli", _MAIN)
        await _refresh(engine, _TODAY.date())
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
        detail = await _detail(engine, sid, [_entry(_SONNET, _SONNET_RATES)], {})
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    agents = _by_agent(rows)
    assert len(agents) == 3, rows
    for name, usage in (("reviewer", _A1), ("explorer", _A2)):
        row = agents[name]
        assert (row["input_tokens"], row["output_tokens"], row["cache_read_tokens"], row["cache_creation_tokens"]) == (
            usage[1], usage[2], usage[3], usage[4],
        ), row  # fmt: skip
    assert agents["idle-agent"]["input_tokens"] is None and agents["idle-agent"]["cost_usd"] is None  # unknown, not 0
    # timing and overlap are untouched
    assert agents["reviewer"]["subagent_duration_ms"] == 120_000
    assert agents["explorer"]["subagent_duration_ms"] == 130_000

    bars = {d["label"]: d for d in detail["dispatches"] if d["kind"] == "agent"}
    assert len(bars) == 3  # one bar per agent
    assert bars["agent · reviewer"]["cost_usd"] == pytest.approx(_price(_A1))
    assert (
        bars["agent · reviewer"]["input_tokens"] == _A1[1] and bars["agent · reviewer"]["cache_read_tokens"] == _A1[3]
    )
    assert bars["agent · explorer"]["cost_usd"] == pytest.approx(_price(_A2))
    assert (
        bars["agent · explorer"]["output_tokens"] == _A2[2]
        and bars["agent · explorer"]["cache_creation_tokens"] is None
    )
    assert bars["agent · idle-agent"]["cost_usd"] is None and bars["agent · idle-agent"]["input_tokens"] is None
    assert detail["cost_usd"] == pytest.approx(_price(_A1) + _price(_A2) + _price(_MAIN))  # session total unchanged


@pytest.mark.asyncio
async def test_live_agent_requests_on_an_unpriced_model_keep_their_tokens() -> None:
    """A model the catalog has no price for: the agent's bar keeps its tokens, its cost stays unknown (not a made-up 0)."""
    sid = f"test-16573-unpriced-agent-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    at = lambda m, s=0: _TODAY + timedelta(minutes=m, seconds=s)  # noqa: E731
    try:
        await _plain_subagent(engine, sid, at(1), at(3), "ag-one", "reviewer", "copilot-cli")
        await _agent_request(engine, sid, at(1, 30), "r1", "ag-one", "copilot-cli", _A1, model="no-such-model-9")
        await _refresh(engine, _TODAY.date())
        detail = await _detail(engine, sid, [_entry(_SONNET, _SONNET_RATES)], {})
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    (bar,) = [d for d in detail["dispatches"] if d["kind"] == "agent"]
    assert (bar["input_tokens"], bar["output_tokens"], bar["cache_read_tokens"]) == (1000, 100, 5000)
    assert bar["cost_usd"] is None


async def _cowork_agent_call(engine, sid, ts, tool_use_id, agent_id, agent_type, *, with_span):
    """A Claude Code (Cowork) Agent call as hooks report it: PreToolUse, SubagentStart/Stop (the stop names the call)
    and PostToolUse with `fork_agent_id`; or, with its OTel tool span, the helper the OTel tests use."""
    if with_span:
        await _tool_call(engine, sid, ts, "p-cw", tool_use_id, "Agent", agent_type=agent_type, agent_id=agent_id)
        return
    base = {"prompt_id": "p-cw"}
    call = {**base, "tool_name": "Agent", "tool_use_id": tool_use_id}
    sub = {**base, "agent_id": agent_id, "agent_type": agent_type}
    await _ingest(
        engine,
        [
            _hook("agent.tool.start", "PreToolUse", sid, ts, **call),
            _hook("agent.subagent.start", "SubagentStart", sid, ts + timedelta(seconds=1), **sub),
            _hook("agent.subagent.stop", "SubagentStop", sid, ts + timedelta(seconds=14), tool_use_id=tool_use_id, **sub),
            _hook("agent.tool.end", "PostToolUse", sid, ts + timedelta(seconds=15), fork_agent_id=agent_id,
                  tool_response="{}", **call),
        ],
    )  # fmt: skip


@pytest.mark.asyncio
@pytest.mark.parametrize("with_span", [False, True], ids=["hooks-only", "agent-tool-span"])
async def test_live_cowork_agent_call_takes_its_own_agent_tagged_requests(with_span) -> None:
    """Cowork shape: an Agent tool call (hook rows, with or without its OTel tool span) whose subagent's requests
    arrive as agent.usage.request with its agent_id and no OTel api_request: the Agent bar prices them, once."""
    sid = f"test-16573-cowork-{with_span}-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    at = lambda m, s=0: _TODAY + timedelta(minutes=m, seconds=s)  # noqa: E731
    try:
        if with_span:
            await _interaction(engine, sid)
        await _cowork_agent_call(engine, sid, at(1), "tu-cw", "ag-cw", "Explore", with_span=with_span)
        await _agent_request(engine, sid, at(1, 5), "rc1", "ag-cw", "claude-code", _A1)
        await _agent_request(engine, sid, at(0, 30), "rc0", "", "claude-code", _MAIN)
        await _refresh(engine, _TODAY.date())
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
        detail = await _detail(engine, sid, [_entry(_SONNET, _SONNET_RATES)], {})
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    (row,) = [r for r in rows if r["subagent_type"] == "Explore"]  # one row, never two
    assert (row["input_tokens"], row["output_tokens"], row["cache_read_tokens"], row["cache_creation_tokens"]) == (
        1000, 100, 5000, 300,
    )  # fmt: skip
    (d,) = [d for d in detail["dispatches"] if d["kind"] == "agent"]
    assert d["cost_usd"] == pytest.approx(_price(_A1))
    assert detail["cost_usd"] == pytest.approx(_price(_A1) + _price(_MAIN))


@pytest.mark.asyncio
async def test_live_otel_and_agent_tagged_transcript_requests_count_the_agent_once() -> None:
    """OTel + hooks: the subagent's api_request (span group) and its agent.usage.request carry the same request id and
    agent_id; the bar holds that one request, not both, and still exactly one row."""
    sid = f"test-16573-otel-plus-hooks-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    try:
        await _interaction(engine, sid)
        await _agent(engine, sid, _TODAY + timedelta(minutes=1), "p-a", "tu-a1", "general-purpose", "ag-both",
                     None, (0.3, 4, 4, 4, 4))  # fmt: skip
        await _refresh(engine, _TODAY.date())
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    (row,) = [r for r in rows if r["subagent_type"] == "general-purpose"]
    assert (row["input_tokens"], row["output_tokens"], row["cost_usd"]) == (4, 4, pytest.approx(0.3))


# ── EPMCDME-16573: a skill another client's tool hooks report (Copilot CLI: tool "skill" with skill_name) ──────────


def _skill_call(sid, ts, tool_use_id, skill, *, seconds=10, agent_id="", ended=True, platform="copilot-cli"):
    """The tool hooks of one lowercase `skill` call, as EPMCDME-16571 verified them; `agent_id` set when a subagent
    made the call."""
    tool_name = "Skill" if platform == "claude-code" else "skill"
    call = {"tool_name": tool_name, "tool_use_id": tool_use_id, "platform": platform, "skill_name": skill}
    if agent_id:
        call["agent_id"] = agent_id
    events = [_hook("agent.tool.start", "PreToolUse", sid, ts, **{**call, "prompt_id": "p-sk"})]
    if ended:
        events.append(_hook("agent.tool.end", "PostToolUse", sid, ts + timedelta(seconds=seconds), **call))
    return [{**e, "platform": platform} for e in events]


@pytest.mark.asyncio
async def test_live_copilot_skill_tool_hooks_get_one_bar_per_invocation() -> None:
    """Copilot's skill calls reach the Timeline through the Session Detail projection: a main-thread call, the same
    skill called again, a call made inside a subagent (that subagent keeps its own bar and usage), and a call that
    never ended: one bar each, timed from the call, no usage of their own, the session total untouched."""
    sid = f"test-16573-copilot-skill-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    at = lambda m, s=0: _TODAY + timedelta(minutes=m, seconds=s)  # noqa: E731
    try:
        await _interaction(engine, sid)
        await _ingest(
            engine,
            [
                *_skill_call(sid, at(1), "tc-1", "brainstorm", seconds=20),
                *_skill_call(sid, at(2), "tc-2", "brainstorm", seconds=5),
                *_skill_call(sid, at(4, 20), "tc-3", "review", seconds=8, agent_id="ag-one"),
                *_skill_call(sid, at(6), "tc-4", "unfinished", ended=False),
            ],
        )
        await _plain_subagent(engine, sid, at(4), at(5), "ag-one", "reviewer", "copilot-cli")
        await _agent_request(engine, sid, at(4, 30), "r1", "ag-one", "copilot-cli", _A1)
        await _agent_request(engine, sid, at(0, 30), "r0", "", "copilot-cli", _MAIN)
        await _refresh(engine, _TODAY.date())
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
        detail = await _detail(engine, sid, [_entry(_SONNET, _SONNET_RATES)], {})
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    skills = sorted((r for r in rows if r["skill_name"]), key=lambda r: r["dispatch_start"])
    assert [r["skill_name"] for r in skills] == ["brainstorm", "brainstorm", "review", "unfinished"], rows
    assert [r["real_duration_ms"] for r in skills] == [20_000, 5_000, 8_000, None]  # to each call's own end
    assert all(r["is_slash_command"] == 0 and r["cost_usd"] is None and r["input_tokens"] is None for r in skills)

    bars = detail["dispatches"]
    assert [d["kind"] for d in bars].count("skill") == 4 and [d["kind"] for d in bars].count("agent") == 1
    agent = next(d for d in bars if d["kind"] == "agent")
    assert agent["cost_usd"] == pytest.approx(_price(_A1)) and agent["input_tokens"] == _A1[1]  # not the skill's
    assert detail["cost_usd"] == pytest.approx(_price(_A1) + _price(_MAIN))
    assert detail["skill_count"] == 4  # the counting rule is the same set, untouched


@pytest.mark.asyncio
async def test_live_skill_tool_hooks_leave_claude_code_and_slash_rows_alone() -> None:
    """Preserve span and slash rows and render reported hook-only skills with execution and fork deduplication."""
    sid = f"test-16573-skill-no-dup-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    at = lambda m, s=0: _TODAY + timedelta(minutes=m, seconds=s)  # noqa: E731
    try:
        await _interaction(engine, sid)
        await _tool_call(engine, sid, at(1), "p-cc", "tu-cc", "Skill", skill="ccskill")
        await _ingest(engine, [_dispatch(sid, at(3), "p-sl", "slashskill")])
        await _ingest(engine, _skill_call(sid, at(5), "tc-cp", "cpskill", platform="copilot-cli"))
        await _ingest(engine, _skill_call(sid, at(6), "tc-cc", "hookskill", platform="claude-code"))
        await _ingest(engine, _skill_call(sid, at(7), "tc-denied", "denied", ended=False, platform="claude-code"))
        fork_call = _skill_call(sid, at(8), "tc-fork", "forkskill", platform="claude-code")
        fork_call[-1]["fork_agent_id"] = "cc-fork"
        await _ingest(
            engine,
            [
                *fork_call,
                _hook("agent.subagent.start", "SubagentStart", sid, at(8, 1),
                      platform="claude-code", agent_id="cc-fork", agent_type="general-purpose", skill_name="forkskill"),
                _hook("agent.subagent.start", "SubagentStart", sid, at(9),
                      platform="copilot-cli", agent_id="cp-fork", agent_type="task", skill_name="taggedskill"),
            ],
        )  # fmt: skip
        await _refresh(engine, _TODAY.date())
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    names = sorted(r["skill_name"] for r in rows if r["skill_name"])
    assert names == ["ccskill", "cpskill", "forkskill", "hookskill", "slashskill", "taggedskill"], rows
