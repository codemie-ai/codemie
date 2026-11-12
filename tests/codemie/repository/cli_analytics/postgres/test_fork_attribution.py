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

"""The fork attribution rules without a database: the Python mirror of the fork key, and the bodies of the SQL
fragments the rollups and the reader share."""

from __future__ import annotations

from typing import Any

from codemie.repository.cli_analytics.postgres.agent_call_attribution import (
    AGENT_CALL_DAYS_SQL,
    agent_call_ids_cte,
    agent_call_keys,
)
from codemie.repository.cli_analytics.postgres.fork_attribution import (
    dispatch_fork_candidate,
    fork_of_counted_call,
    fork_of_dispatch,
    named_subagent_ids,
)
from codemie.repository.cli_analytics.postgres.otlp import decode_hook_events

_CLAUDE_CODE = "coalesce({}.attrs->>'platform', 'claude-code') = 'claude-code'"


def _end(session_id: str, tool_name: str, **fields: Any) -> dict[str, Any]:
    return {
        "type": "agent.tool.end",
        "timestamp": "2026-10-21T12:00:00Z",
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_use_id": f"toolu_{len(fields)}",
        **fields,
    }


def test_named_subagent_ids_take_the_subagent_each_skill_call_names():
    """A Skill agent.tool.end names its fork by the CLI's fork_agent_id, else by the complete agentId of its result;
    an Agent end is named by the SQL marking of its call (agent_call_keys), so the mirror ignores it; a cut id,
    another tool and an event without a session name none."""
    batch = decode_hook_events(
        [
            _end("s1", "Skill", fork_agent_id="a1", tool_response='{"commandName":"x'),
            _end("s1", "Skill", tool_response='{"status":"forked","agentId": "a2","result":"ok"}'),
            _end("s2", "Skill", tool_response='{"status":"forked","agentId":"a3'),
            _end("s2", "Agent", tool_response='{"agentId":"a4"}', fork_agent_id="ignored-on-agent"),
            _end("s2", "Read", tool_response='{"agentId":"a6"}'),
            _end("", "Skill", fork_agent_id="a5"),
        ],
        "",
        0,
    )

    assert named_subagent_ids(batch.hooks) == [("s1", "a1"), ("s1", "a2")]


def test_named_subagent_ids_read_a_non_string_fork_agent_id_as_its_json_text():
    """As `attrs->>'fork_agent_id'` does: a number or a boolean is the key as its JSON text; an empty string or
    null falls back to the agentId in tool_response."""
    batch = decode_hook_events(
        [
            _end("s1", "Skill", fork_agent_id=5),
            _end("s2", "Skill", fork_agent_id=True),
            _end("s3", "Skill", fork_agent_id="", tool_response='{"agentId":"a7"}'),
            _end("s4", "Skill", fork_agent_id=None, tool_response='{"agentId":"a8"}'),
        ],
        "",
        0,
    )

    assert named_subagent_ids(batch.hooks) == [("s1", "5"), ("s2", "true"), ("s3", "a7"), ("s4", "a8")]


def test_named_subagent_ids_read_an_object_tool_response_as_its_json_text():
    """As `attrs->>'tool_response'` does: an object result is searched as its JSON text."""
    batch = decode_hook_events(
        [
            _end("s1", "Agent", tool_response={"status": "async_launched", "agentId": "a9"}),
            _end("s2", "Skill", tool_response={"status": "forked", "agentId": "a10"}),
        ],
        "",
        0,
    )

    assert named_subagent_ids(batch.hooks) == [("s2", "a10")]


def _hook_event(event_type: str, session_id: str, tool_use_id: str | None, **fields: Any) -> dict[str, Any]:
    event = {"type": event_type, "timestamp": "2026-10-21T12:00:00Z", "session_id": session_id, **fields}
    return event if tool_use_id is None else {**event, "tool_use_id": tool_use_id}


def test_agent_call_keys_take_the_call_of_every_agent_start_end_error_and_stop_of_the_batch():
    """A call is named by any of its rows, in any batch: an Agent start, end, error and a subagent stop with a
    tool_use_id each queue the call's (session, tool_use_id), once however many rows carry it. The start is there
    even though it alone resolves no call (a stop with no start, end or error is no call): a start landing after an
    already-ingested stop or end is what turns that call from unknown into resolved, so it must requeue it too. A
    Skill or another tool's end, and a row without a session or a tool_use_id name no call."""
    batch = decode_hook_events(
        [
            _hook_event("agent.tool.end", "s1", "tu-b", tool_name="Agent"),
            _hook_event("agent.subagent.stop", "s1", "tu-b", agent_id="a1"),
            _hook_event("agent.tool.error", "s1", "tu-a", tool_name="Agent"),
            _hook_event("agent.subagent.stop", "s2", "tu-c", agent_id="a2"),
            _hook_event("agent.tool.start", "s1", "tu-d", tool_name="Agent"),
            _hook_event("agent.tool.start", "s1", "tu-e", tool_name="Skill"),
            _hook_event("agent.tool.end", "s1", "tu-f", tool_name="Skill"),
            _hook_event("agent.tool.end", "s1", "tu-g", tool_name="Read"),
            _hook_event("agent.tool.end", "s1", None, tool_name="Agent"),
            _hook_event("agent.tool.end", "s1", "", tool_name="Agent"),
            _hook_event("agent.subagent.stop", "s1", None, agent_id="a3"),
            _hook_event("agent.tool.end", "", "tu-h", tool_name="Agent"),
        ],
        "",
        0,
    )

    assert agent_call_keys(batch.hooks) == [("s1", "tu-a"), ("s1", "tu-b"), ("s1", "tu-d"), ("s2", "tu-c")]


def test_the_agent_call_marking_reads_every_row_of_the_call_by_its_session_and_tool_use_id():
    """One statement over unnest(session, tool_use_id): the call's rows carry the agent ids (an end's field and
    text, a stop's agent_id), each id's starts give the days. The rows are those of the Agent-call rule."""
    sql = AGENT_CALL_DAYS_SQL

    assert "unnest($1::text[], $2::text[]) AS u(s, t) WHERE u.t <> ''" in sql
    assert "k.s = r.session_id AND k.t = r.tool_use_id" in sql
    assert "r.event_type = 'agent.tool.end' AND r.tool_name IN ('Agent')" in sql
    assert "r.event_type = 'agent.subagent.stop'" in sql
    assert _CLAUDE_CODE.format("r") in sql
    assert "f.event_type = 'agent.subagent.start'" in sql
    assert "ids.s = f.session_id AND ids.a = f.agent_id" in sql
    assert "(f.ts AT TIME ZONE 'UTC')::date AS day, f.session_id" in sql
    # each table is read once, by the batch's sessions: no probe of the (session_id, ts) index per call or per id
    assert sql.count("= ANY($1::text[])") == 2 and "AS MATERIALIZED" in sql


def test_fork_of_dispatch_pairs_a_claude_code_fork_with_the_earliest_candidate_of_its_dispatch():
    sql = fork_of_dispatch("f")

    # a start without an agent_id (stored NULL) is no fork: false, not NULL, under its callers' NOT
    assert "coalesce(f.agent_id, '') <> '' AND f.prompt_id <> ''" in sql
    assert "f_d.event_type = 'agent.skill.dispatch'" in sql
    assert _CLAUDE_CODE.format("f_d") in sql
    assert "f_d.prompt_id = f.prompt_id" in sql
    assert "f_d.skill_name <> '' AND octet_length(f_d.skill_name) <= 256" in sql
    assert "f_d.ts <= f.ts" in sql
    # only the one dispatch of its prompt_id pairs
    assert (
        "f_d.ts <= f.ts AND NOT EXISTS (SELECT 1 FROM hook_events f_d_o WHERE f_d_o.session_id = f_d.session_id "
        f"AND f_d_o.event_type = 'agent.skill.dispatch' AND {_CLAUDE_CODE.format('f_d_o')} "
        "AND f_d_o.prompt_id = f_d.prompt_id AND (f_d_o.ts, f_d_o.ingest_seq) <> (f_d.ts, f_d.ingest_seq))"
    ) in sql
    # the named and the earlier candidates come from the same set as the fork itself
    assert "coalesce(f_n.agent_id, '') <> '' AND f_n.prompt_id = f.prompt_id" in sql
    assert "coalesce(f_c.agent_id, '') <> '' AND f_c.prompt_id = f.prompt_id" in sql
    assert "(f_c.ts, f_c.ingest_seq) < (f.ts, f.ingest_seq)" in sql
    assert "f_c.ts >= f_d.ts" in sql


def test_fork_of_counted_call_takes_a_skill_call_counted_by_its_span_or_its_bounded_start():
    sql = fork_of_counted_call("f")

    assert sql.startswith(f"({_CLAUDE_CODE.format('f')} AND EXISTS")
    assert "f_e.event_type = 'agent.tool.end' AND f_e.tool_name = 'Skill'" in sql
    assert _CLAUDE_CODE.format("f_e") in sql
    assert "f_e.tool_use_id <> ''" in sql
    assert (
        "coalesce(nullif(f_e.attrs->>'fork_agent_id', ''), "
        """substring(f_e.attrs->>'tool_response' from '"agentId":\\s*"([A-Za-z0-9_-]+)"')) = f.agent_id"""
    ) in sql
    assert "f_s.span_kind = 1 AND f_s.tool_use_id = f_e.tool_use_id AND f_s.skill_name <> ''" in sql
    assert "f_t.skill_name <> '' AND octet_length(f_t.skill_name) <= 256" in sql
    assert _CLAUDE_CODE.format("f_t") in sql


def test_dispatch_fork_candidate_is_a_claude_code_start_no_skill_or_agent_call_names():
    sql = dispatch_fork_candidate("f")

    assert sql.startswith(f"(f.event_type = 'agent.subagent.start' AND {_CLAUDE_CODE.format('f')} ")
    # only an agent_id's first start: a SendMessage continuation is no new fork
    assert (
        "NOT EXISTS (SELECT 1 FROM hook_events f_p WHERE f_p.session_id = f.session_id "
        "AND f_p.event_type = 'agent.subagent.start' AND f.agent_id <> '' AND f_p.agent_id = f.agent_id "
        "AND (f_p.ts, f_p.ingest_seq) < (f.ts, f.ingest_seq))"
    ) in sql
    assert "NOT EXISTS (SELECT 1 FROM hook_events f_s WHERE f_s.session_id = f.session_id" in sql
    assert "f_s.tool_name = 'Skill'" in sql
    # an ordinary subagent is named by no Agent call: the reverse form of the agent-call rule, by the agent_id
    assert (
        "(coalesce(f.skill_name, '') <> '' OR NOT ((SELECT f_a_c.tool_use_id FROM (SELECT DISTINCT f_a_p.tool_use_id "
        "FROM hook_events f_a_p WHERE f_a_p.session_id = f.session_id AND "
    ) in sql
    assert (
        "WHERE f_a_r.agent_id = f.agent_id ORDER BY f_a_r.source, f_a_r.first_ts, f_a_c.tool_use_id LIMIT 1) IS NOT NULL)"
        in sql
    )
    # the reverse form reads the field, the stops and the text, not the end row alone
    assert "nullif(f_a_p.attrs->>'fork_agent_id', '') = f.agent_id" in sql
    assert "f_a_p.event_type = 'agent.subagent.stop' AND f_a_p.agent_id = f.agent_id" in sql
    assert "f_a.tool_name" not in sql
    assert (
        _CLAUDE_CODE.format("f_s") in sql
        and _CLAUDE_CODE.format("f_a_p") in sql
        and _CLAUDE_CODE.format("f_a_r_h") in sql
    )


def test_the_session_wide_resolution_reads_the_session_once_and_applies_the_one_call_per_agent_rule_in_a_window():
    """The Timeline resolves every Agent call of the session in one pass (the correlated forward and claim forms
    scanned the whole session once per call, a quadratic cost): the same sources and ranking as the per-call forms,
    the winner of an agent id chosen by a window over (source, first row, tool_use_id)."""
    sql = " ".join(agent_call_ids_cte("$1::text").split())

    assert sql.startswith("call_ids AS MATERIALIZED (")
    # one scan of the session's hook rows, grouped by call: no lateral probe, no second look at hook_events
    assert sql.count("FROM hook_events") == 1 and "LATERAL" not in sql
    assert "ci_h.session_id = $1::text AND ci_h.tool_use_id <> ''" in sql
    assert _CLAUDE_CODE.format("ci_h") in sql
    assert "GROUP BY ci_h.tool_use_id" in sql
    # the sources of the rule: the end's field, the stops' agent_id (ambiguous when several), the complete text id
    assert "min(nullif(ci_h.attrs->>'fork_agent_id', '')) FILTER (WHERE ci_h.event_type = 'agent.tool.end'" in sql
    assert "count(DISTINCT ci_h.agent_id) FILTER (WHERE ci_h.event_type = 'agent.subagent.stop'" in sql
    assert "WHEN g.stop_ids = 1 THEN g.stop_id WHEN g.stop_ids = 0 THEN g.text_id END AS agent_id" in sql
    # a call is an Agent call only when one of its rows is an Agent tool row
    assert "WHERE g.first_ts IS NOT NULL" in sql
    # the claim is what the call resolves to; the agent id only for the winner of that id
    assert (
        "r.agent_id AS claimed_id, CASE WHEN r.agent_id IS NOT NULL AND row_number() OVER "
        "(PARTITION BY r.agent_id ORDER BY r.source, r.first_ts, r.tool_use_id) = 1 THEN r.agent_id END AS agent_id"
    ) in sql


def test_a_fork_candidate_reads_the_named_agents_from_the_session_wide_resolution_when_given_it():
    """Given the session-wide `call_ids`, the "named by an Agent call" test is a lookup in it, not the reverse form
    run per start; without it the correlated reverse form stays (the rollups and agent_count run it per row)."""
    sql = dispatch_fork_candidate("f", "call_ids")

    assert sql.startswith(f"(f.event_type = 'agent.subagent.start' AND {_CLAUDE_CODE.format('f')} ")
    assert (
        "(coalesce(f.skill_name, '') <> '' OR NOT EXISTS (SELECT 1 FROM call_ids f_k WHERE f_k.agent_id = f.agent_id))"
    ) in sql
    assert "f_a_p" not in sql and "f_a_c" not in sql and "f_a_r" not in sql
    # the Skill rule and the first-start rule are untouched
    assert "f_s.tool_name = 'Skill'" in sql and "f_p.event_type = 'agent.subagent.start'" in sql
    assert dispatch_fork_candidate("f") == dispatch_fork_candidate("f", None) and "f_a_p" in dispatch_fork_candidate(
        "f"
    )
