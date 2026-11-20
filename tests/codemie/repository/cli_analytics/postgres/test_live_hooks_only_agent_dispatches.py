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
