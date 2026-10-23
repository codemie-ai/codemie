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


def test_named_subagent_ids_take_the_subagent_each_skill_or_agent_call_names():
    """A Skill agent.tool.end names its fork by the CLI's fork_agent_id, else by the complete agentId of its result;
    an Agent end names its subagent by that agentId; a cut id, another tool and an event without a session name none."""
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

    assert named_subagent_ids(batch.hooks) == [("s1", "a1"), ("s1", "a2"), ("s2", "a4")]


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

    assert named_subagent_ids(batch.hooks) == [("s1", "a9"), ("s2", "a10")]


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
    assert "(coalesce(f.skill_name, '') <> '' OR NOT EXISTS (SELECT 1 FROM hook_events f_a" in sql
    assert "f_a.tool_name = 'Agent'" in sql
    assert _CLAUDE_CODE.format("f_s") in sql and _CLAUDE_CODE.format("f_a") in sql
