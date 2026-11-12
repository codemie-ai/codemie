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

"""SQL fragments that tie a forked skill or a subagent to the call that started it, shared by the rollups (counts)
and the reader (timeline, scalars), so both twins read one rule. The rule that names the agent an Agent call started
is `agent_call_attribution`."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime

from codemie.repository.cli_analytics.postgres.agent_call_attribution import AGENT_CALL_TOOLS, agent_call_of
from codemie.repository.cli_analytics.postgres.hook_row_sql import call_end_row, is_claude_code, tool_response_agent_id
from codemie.repository.cli_analytics.postgres.otlp import MAX_KEY_BYTES, HookRow
from codemie.repository.cli_analytics.vocabulary import SKILL_DISPATCH_EVENT, SpanKind

_AGENT_ID = re.compile(r'"agentId":\s*"([A-Za-z0-9_-]+)"')


def _json_text(value: object) -> str | None:
    """`value` as `->>` reads it: NULL for null, a string as is, anything else as its JSON text."""
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value)


def _named_agent_id(hook: HookRow) -> str | None:
    """The subagent a Skill agent.tool.end names: its `fork_agent_id`, else the complete `agentId` in its
    `tool_response`; None when it names none."""
    attrs = json.loads(hook.attrs)
    if not isinstance(attrs, dict):
        return None
    agent_id = _json_text(attrs.get("fork_agent_id"))
    response = _json_text(attrs.get("tool_response"))
    if not agent_id and response is not None:
        match = _AGENT_ID.search(response)
        agent_id = match.group(1) if match else None
    return agent_id


def named_subagent_ids(hooks: Sequence[HookRow]) -> list[tuple[str, str]]:
    """(session_id, agent_id) of every subagent a Skill agent.tool.end names, as the SQL reads it: its
    `fork_agent_id`, else the complete `agentId` in the end's `tool_response`. An Agent call is named by the rule
    of `AGENT_CALL_DAYS_SQL` instead, which also reads its stops and rows of other batches (`agent_call_keys`)."""
    forks = set()
    for hook in hooks:
        if hook.event_type != "agent.tool.end" or hook.tool_name != "Skill" or not hook.session_id or not hook.attrs:
            continue
        agent_id = _named_agent_id(hook)
        if agent_id:
            forks.add((hook.session_id, agent_id))
    return sorted(forks)


# A Skill call's agent.tool.end may arrive days after the fork it names (a foreground fork's comes at the end of its
# run), and the fork rules read it: the fork's own day is then queued again by its agent_id, wherever it lies. An
# Agent call's rows are marked by AGENT_CALL_DAYS_SQL.
FORK_DAYS_SQL = """
SELECT DISTINCT (f.ts AT TIME ZONE 'UTC')::date AS day, f.session_id
FROM unnest($1::text[], $2::text[]) AS u(s, a)
JOIN hook_events f ON f.session_id = u.s AND f.event_type = 'agent.subagent.start' AND f.agent_id = u.a"""


def keyed_subagent_starts(hooks: Sequence[HookRow]) -> list[tuple[str, str, datetime]]:
    """(session_id, agent_id, ts) of every agent.subagent.start with an agent_id: only the first start of an
    agent_id counts, so a start that arrives late can undo the count of a later start already counted."""
    return sorted(
        {
            (hook.session_id, hook.agent_id, hook.ts)
            for hook in hooks
            if hook.event_type == "agent.subagent.start" and hook.session_id and hook.agent_id
        }
    )


# The days of the later starts of each (session_id, agent_id, ts): each is queued again to drop its count.
LATER_START_DAYS_SQL = """
SELECT DISTINCT (f.ts AT TIME ZONE 'UTC')::date AS day, f.session_id
FROM unnest($1::text[], $2::text[], $3::timestamptz[]) AS u(s, a, t)
JOIN hook_events f ON f.session_id = u.s AND f.event_type = 'agent.subagent.start' AND f.agent_id = u.a
    AND f.ts > u.t"""


def first_subagent_start(start: str) -> str:
    """SQL: `start` (an agent.subagent.start) is the first start of its agent_id in the session (ties by ingest
    order). SendMessage continues an agent under the same agent_id with a new start, which is no new invocation; a
    start with an empty agent_id has no key and stands alone."""
    prior = f"{start}_p"
    return (
        f"NOT EXISTS (SELECT 1 FROM hook_events {prior} WHERE {prior}.session_id = {start}.session_id "
        f"AND {prior}.event_type = 'agent.subagent.start' AND {start}.agent_id <> '' "
        f"AND {prior}.agent_id = {start}.agent_id "
        f"AND ({prior}.ts, {prior}.ingest_seq) < ({start}.ts, {start}.ingest_seq))"
    )


def skill_end_agent_id(alias: str) -> str:
    """SQL: the fork a Skill call's agent.tool.end (`alias`) names: its `fork_agent_id`, which the CLI reads out
    of tool_response before it cuts it, else the agentId in tool_response. Never the typed agent_id: on a call
    made inside a subagent that is the caller's id."""
    in_response = tool_response_agent_id(f"{alias}.attrs->>'tool_response'")
    return f"coalesce(nullif({alias}.attrs->>'fork_agent_id', ''), {in_response})"


def _named_by_end(fork: str, tool_name: str, call_ids: str | None = None) -> str:
    """SQL: a Claude Code call of `tool_name` in the session of `fork` names its agent_id: for a Skill, its
    agent.tool.end (`skill_end_agent_id`); for an Agent call, the reverse form of the agent-call rule, which also
    reads the stops and the end's `fork_agent_id` and holds on a day with no tool spans. With `call_ids`, the name
    of a CTE of `agent_call_ids_cte` over the same session, the Agent call is a lookup in it instead (the same
    answer, without a scan of the session per start)."""
    if tool_name != "Skill":
        if call_ids is not None:
            return f"EXISTS (SELECT 1 FROM {call_ids} {fork}_k WHERE {fork}_k.agent_id = {fork}.agent_id)"
        return f"({agent_call_of(f'{fork}.session_id', f'{fork}.agent_id', f'{fork}_a')} IS NOT NULL)"
    end = f"{fork}_{tool_name[0].lower()}"
    agent_id = skill_end_agent_id(end)
    return (
        f"EXISTS (SELECT 1 FROM hook_events {end} WHERE {end}.session_id = {fork}.session_id "
        f"AND {end}.event_type = 'agent.tool.end' AND {end}.tool_name = '{tool_name}' AND {is_claude_code(end)} "
        f"AND {agent_id} = {fork}.agent_id)"
    )


def dispatch_fork_candidate(fork: str, call_ids: str | None = None) -> str:
    """SQL: `fork` may be the fork of a slash command: the first start of its agent_id (a SendMessage continuation
    starts again under the new turn's prompt_id), a Claude Code agent.subagent.start no Skill call names; one
    without a skill name also named by no Agent call (an ordinary subagent of the same turn). `call_ids` is the name
    of a session-wide `agent_call_ids_cte` the Agent-call test reads (see _named_by_end); the Timeline passes it, the
    rollups run the per-row form."""
    return (
        f"({fork}.event_type = 'agent.subagent.start' AND {is_claude_code(fork)} AND {first_subagent_start(fork)} "
        f"AND NOT {_named_by_end(fork, 'Skill')} "
        f"AND (coalesce({fork}.skill_name, '') <> '' OR NOT {_named_by_end(fork, 'Agent', call_ids)}))"
    )


def sole_dispatch_of_prompt(dispatch: str) -> str:
    """SQL: `dispatch` is the only Claude Code agent.skill.dispatch of its session and prompt_id. The CLI sends one
    per prompt; with two or more, which fork is whose is a guess, so none of them pairs and their forks count on
    their own."""
    other = f"{dispatch}_o"
    return (
        f"NOT EXISTS (SELECT 1 FROM hook_events {other} WHERE {other}.session_id = {dispatch}.session_id "
        f"AND {other}.event_type = '{SKILL_DISPATCH_EVENT}' AND {is_claude_code(other)} "
        f"AND {other}.prompt_id = {dispatch}.prompt_id "
        f"AND ({other}.ts, {other}.ingest_seq) <> ({dispatch}.ts, {dispatch}.ingest_seq))"
    )


def fork_of_dispatch(fork: str) -> str:
    """SQL: `fork` is the fork of a Claude Code slash command (agent.skill.dispatch) of its prompt_id, the only
    dispatch of that prompt_id. The dispatch pairs with one fork, the earliest candidate at or after it (ties by
    ingest order): one with the dispatch's skill name, else, when none has it, one without a skill name (a
    foreground fork). A start without an agent_id is no fork: the test stays false, not NULL, under a NOT."""
    d, named, earlier = f"{fork}_d", f"{fork}_n", f"{fork}_c"
    return (
        f"({dispatch_fork_candidate(fork)} AND coalesce({fork}.agent_id, '') <> '' AND {fork}.prompt_id <> '' "
        f"AND EXISTS (SELECT 1 FROM hook_events {d} WHERE {d}.session_id = {fork}.session_id "
        f"AND {d}.event_type = '{SKILL_DISPATCH_EVENT}' AND {is_claude_code(d)} "
        f"AND {d}.prompt_id = {fork}.prompt_id AND {d}.skill_name <> '' "
        f"AND octet_length({d}.skill_name) <= {MAX_KEY_BYTES} AND {d}.ts <= {fork}.ts "
        f"AND {sole_dispatch_of_prompt(d)} "
        f"AND ({d}.skill_name = {fork}.skill_name OR (coalesce({fork}.skill_name, '') = '' "
        f"AND NOT EXISTS (SELECT 1 FROM hook_events {named} WHERE {named}.session_id = {fork}.session_id "
        f"AND coalesce({named}.agent_id, '') <> '' AND {named}.prompt_id = {fork}.prompt_id "
        f"AND {named}.skill_name = {d}.skill_name "
        f"AND {named}.ts >= {d}.ts AND {dispatch_fork_candidate(named)}))) "
        f"AND NOT EXISTS (SELECT 1 FROM hook_events {earlier} WHERE {earlier}.session_id = {fork}.session_id "
        f"AND coalesce({earlier}.agent_id, '') <> '' AND {earlier}.prompt_id = {fork}.prompt_id "
        f"AND coalesce({earlier}.skill_name, '') = coalesce({fork}.skill_name, '') AND {earlier}.ts >= {d}.ts "
        f"AND ({earlier}.ts, {earlier}.ingest_seq) < ({fork}.ts, {fork}.ingest_seq) "
        f"AND {dispatch_fork_candidate(earlier)})))"
    )


def _ended_call(call: str, tool_name: str) -> str:
    """SQL: `call` has an end or error row (`call_end_row`)."""
    end = f"{call}_r"
    return f"EXISTS (SELECT 1 FROM hook_events {end} WHERE {call_end_row(end, call, tool_name)})"


def executed_tool_span(alias: str, tool_name: str) -> str:
    """SQL: the TOOL span `alias` of a `tool_name` call has evidence its call ran. A denied call has none: no
    TOOL_EXECUTION span (a call that ran has one, a background agent's closing at its launch) and no
    agent.tool.end/.error. Without a tool_use_id a denial cannot be proven, so the span still counts. Only a Claude
    Code span is gated: that client's execution evidence is known, another client's span counts as it is."""
    return (
        f"(NOT starts_with({alias}.span_name, 'claude_code.') "
        f"OR coalesce({alias}.tool_use_id, '') = '' "
        f"OR EXISTS (SELECT 1 FROM spans x WHERE x.span_kind = {int(SpanKind.TOOL_EXECUTION)} "
        f"AND x.session_id = {alias}.session_id AND x.tool_use_id = {alias}.tool_use_id "
        f"AND x.ts >= {alias}.ts - interval '1 hour' AND x.ts < {alias}.ts + interval '1 day') "
        f"OR {_ended_call(alias, tool_name)})"
    )


def executed_agent_call_span(alias: str) -> str:
    """SQL: the TOOL span `alias` of an agent call (any of AGENT_CALL_TOOLS) has evidence its call ran: the
    executed_tool_span of each tool name, parenthesised when there are several to sit under an AND."""
    gates = " OR ".join(executed_tool_span(alias, name) for name in AGENT_CALL_TOOLS)
    return f"({gates})" if len(AGENT_CALL_TOOLS) > 1 else gates


def executed_tool_start(alias: str, tool_name: str) -> str:
    """SQL: the agent.tool.start `alias` of a `tool_name` call has evidence its call ran, as executed_tool_span
    reads it from the hook events alone. Only a Claude Code start is gated: another client's tool ends are
    unverified, so its start counts as it is; so does a start without a tool_use_id."""
    return (
        f"(NOT {is_claude_code(alias)} OR coalesce({alias}.tool_use_id, '') = '' "
        f"OR {_ended_call(alias, tool_name)})"
    )


def fork_of_counted_call(fork: str) -> str:
    """SQL: `fork` (a Claude Code agent.subagent.start) was started by a Skill call whose initiator is counted —
    its TOOL span with a skill name, or its Claude Code agent.tool.start with one. Only such a fork is a skill
    through its call; one named by an uncounted call stays what it is on its own (an agent without a skill name)."""
    end, span, start = f"{fork}_e", f"{fork}_s", f"{fork}_t"
    return (
        f"({is_claude_code(fork)} AND EXISTS (SELECT 1 FROM hook_events {end} "
        f"WHERE {end}.session_id = {fork}.session_id AND {end}.event_type = 'agent.tool.end' "
        f"AND {end}.tool_name = 'Skill' AND {is_claude_code(end)} "
        f"AND {skill_end_agent_id(end)} = {fork}.agent_id AND {end}.tool_use_id <> '' "
        f"AND (EXISTS (SELECT 1 FROM spans {span} WHERE {span}.session_id = {end}.session_id "
        f"AND {span}.span_kind = {int(SpanKind.TOOL)} AND {span}.tool_use_id = {end}.tool_use_id "
        f"AND {span}.skill_name <> '' AND {executed_tool_span(span, 'Skill')}) "
        f"OR EXISTS (SELECT 1 FROM hook_events {start} WHERE {start}.session_id = {end}.session_id "
        f"AND {start}.event_type = 'agent.tool.start' AND {start}.tool_use_id = {end}.tool_use_id "
        f"AND {start}.skill_name <> '' AND octet_length({start}.skill_name) <= {MAX_KEY_BYTES} "
        f"AND {is_claude_code(start)} AND {executed_tool_start(start, 'Skill')}))))"
    )
