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

"""The agent-call rule: which agent a Claude Code Agent tool call started, as SQL fragments shared by the rollups
(counts), the ingest marking and the reader (Timeline, parent-request fallback), so every twin reads one rule. The
fork rules of Skill calls and slash commands that read it live in `fork_attribution`."""

from __future__ import annotations

from collections.abc import Sequence

from codemie.repository.cli_analytics.postgres.hook_row_sql import call_end_ts, is_claude_code, tool_response_agent_id
from codemie.repository.cli_analytics.postgres.otlp import HookRow

# The tools whose call starts a subagent and is named by the agent-call rule below. `Task` (the older name of the
# Agent tool) is deliberately out: the CLI never stamps it and its rows keep the parent-window estimate.
AGENT_CALL_TOOLS: tuple[str, ...] = ("Agent",)


def agent_call_tool(expr: str) -> str:
    """SQL: the tool name `expr` is one of AGENT_CALL_TOOLS."""
    names = ", ".join(f"'{name}'" for name in AGENT_CALL_TOOLS)
    return f"{expr} IN ({names})"


# The agent-call rule. An Agent call is the Claude Code AGENT_CALL_TOOLS hook rows of one (session, tool_use_id),
# non-empty. Its agent id is the first source that answers: 1. `fork_agent_id` on its agent.tool.end; 2. the
# agent_id of the Claude Code agent.subagent.stop rows of that session and tool_use_id, where several distinct
# ids make the call ambiguous (no id, and source 3 is blocked); 3. the complete agentId in its end's
# tool_response. When several calls resolve to one id, the lowest source number wins, then the earliest call (its
# first row), then the lowest tool_use_id; every other call is unresolved. Both forms below read that one
# resolution (`_agent_call_resolution`), so a call and its agent always name each other. A stop whose call left no
# row naming its tool (an agent.tool.start, .end, .error or .denied of AGENT_CALL_TOOLS) is no call: it names no
# agent, in every form.


def _call_sources(row: str) -> str:
    """SQL select list: what the hook rows `row` of one Agent call say about its agent, by source of the rule (the
    end's field, the number of distinct stop ids and the stop id, the complete text id) and when the call's first
    row naming its tool came, of any event type (NULL when it has none, so it is no Agent call; a denied call's
    own row answers none of the three sources, so it never resolves an agent id regardless)."""
    is_end = f"{row}.event_type = 'agent.tool.end' AND {agent_call_tool(f'{row}.tool_name')}"
    is_stop = f"{row}.event_type = 'agent.subagent.stop' AND {row}.agent_id <> ''"
    text_id = tool_response_agent_id(f"{row}.attrs->>'tool_response'")
    return (
        f"min(nullif({row}.attrs->>'fork_agent_id', '')) FILTER (WHERE {is_end}) AS field_id, "
        f"count(DISTINCT {row}.agent_id) FILTER (WHERE {is_stop}) AS stop_ids, "
        f"min({row}.agent_id) FILTER (WHERE {is_stop}) AS stop_id, "
        f"min({text_id}) FILTER (WHERE {is_end}) AS text_id, "
        f"min({row}.ts) FILTER (WHERE {agent_call_tool(f'{row}.tool_name')}) AS first_ts"
    )


def _call_row_filter(row: str) -> str:
    """SQL: the hook row `row` is a Claude Code row of an Agent call: an Agent tool row or a subagent stop."""
    return (
        f"{is_claude_code(row)} AND ({agent_call_tool(f'{row}.tool_name')} OR {row}.event_type = 'agent.subagent.stop')"
    )


def _call_agent_id(agg: str) -> str:
    """SQL: the agent id the sources `agg` (see _call_sources) resolve to: the field, else the one stop id, else (no
    stop) the text id; NULL when none answers or the stops are ambiguous."""
    return (
        f"CASE WHEN {agg}.field_id IS NOT NULL THEN {agg}.field_id "
        f"WHEN {agg}.stop_ids = 1 THEN {agg}.stop_id WHEN {agg}.stop_ids = 0 THEN {agg}.text_id END"
    )


def _call_source_rank(agg: str) -> str:
    """SQL: the rank of the source that answered, 1 field, 2 stop, 3 text, the lowest winning a shared agent id."""
    return f"CASE WHEN {agg}.field_id IS NOT NULL THEN 1 WHEN {agg}.stop_ids = 1 THEN 2 ELSE 3 END"


def _agent_call_resolution(session: str, tool_use_id: str, alias: str) -> str:
    """SQL derived table (no alias of its own): one row (agent_id, source, first_ts) for the Agent call
    (`session`, `tool_use_id`) before the one-call-per-agent rule, none when no Claude Code Agent row has that
    key. agent_id is NULL when no source answers or the stops are ambiguous; `alias` prefixes inner aliases."""
    row, agg = f"{alias}_h", f"{alias}_g"
    return (
        f"(SELECT {_call_agent_id(agg)} AS agent_id, {_call_source_rank(agg)} AS source, {agg}.first_ts "
        f"FROM (SELECT {_call_sources(row)} "
        f"FROM hook_events {row} WHERE {row}.session_id = {session} AND {row}.tool_use_id = {tool_use_id} "
        f"AND {tool_use_id} <> '' AND {_call_row_filter(row)}) {agg} "
        f"WHERE {agg}.first_ts IS NOT NULL)"
    )


def agent_call_ids_cte(session: str, name: str = "call_ids") -> str:
    """SQL, a CTE definition `name AS MATERIALIZED (...)`: every Agent call of `session` resolved in ONE pass over its
    hook rows, with columns (tool_use_id, claimed_id, agent_id): `claimed_id` is the agent id the call resolves to by
    its own sources (agent_call_claim), `agent_id` the same id only when the call wins it under the one-call-per-agent
    rule (agent_call_agent_id), the winner being the lowest source, then the earliest first row, then the lowest
    tool_use_id (agent_call_of); NULL otherwise. The per-call forms above stay the rule's statement and the unit of
    the rollups, which run per row; read inside a session-scoped statement they scanned the whole session once per
    call, a cost quadratic in the session that took 37 s for 200 calls among 11 000 hook rows."""
    row, agg = "ci_h", "g"
    return (
        f"{name} AS MATERIALIZED ("
        f"SELECT r.tool_use_id, r.agent_id AS claimed_id, "
        f"CASE WHEN r.agent_id IS NOT NULL AND row_number() OVER "
        f"(PARTITION BY r.agent_id ORDER BY r.source, r.first_ts, r.tool_use_id) = 1 THEN r.agent_id END AS agent_id "
        f"FROM (SELECT {agg}.tool_use_id, {_call_agent_id(agg)} AS agent_id, {_call_source_rank(agg)} AS source, "
        f"{agg}.first_ts "
        f"FROM (SELECT {row}.tool_use_id, {_call_sources(row)} "
        f"FROM hook_events {row} WHERE {row}.session_id = {session} AND {row}.tool_use_id <> '' "
        f"AND {_call_row_filter(row)} GROUP BY {row}.tool_use_id) {agg} "
        f"WHERE {agg}.first_ts IS NOT NULL) r)"
    )


def carries_agent_id(row: str, agent_id: str) -> str:
    """SQL: the hook row `row` is a Claude Code row of an Agent call (non-empty tool_use_id) that carries
    `agent_id` in one of the rule's sources: its end's `fork_agent_id` or tool_response agentId, or its stop's
    agent_id. The prefilter of the reverse form; carrying an id is not resolving to it."""
    text_id = tool_response_agent_id(f"{row}.attrs->>'tool_response'")
    return (
        f"(coalesce({row}.tool_use_id, '') <> '' AND {is_claude_code(row)} "
        f"AND (({row}.event_type = 'agent.tool.end' AND {agent_call_tool(f'{row}.tool_name')} "
        f"AND (nullif({row}.attrs->>'fork_agent_id', '') = {agent_id} OR {text_id} = {agent_id})) "
        f"OR ({row}.event_type = 'agent.subagent.stop' AND {row}.agent_id = {agent_id})))"
    )


def carried_agent_ids(row: str) -> str:
    """SQL array: the agent ids the hook row `row` carries in the sources of the agent-call rule: an agent.tool.end of
    AGENT_CALL_TOOLS gives its `fork_agent_id` and the complete agentId of its tool_response, an agent.subagent.stop
    its agent_id; NULL for any other row, NULL elements for a source that is empty or cut."""
    text_id = tool_response_agent_id(f"{row}.attrs->>'tool_response'")
    return (
        f"CASE WHEN {row}.event_type = 'agent.tool.end' AND {agent_call_tool(f'{row}.tool_name')} "
        f"THEN ARRAY[nullif({row}.attrs->>'fork_agent_id', ''), {text_id}] "
        f"WHEN {row}.event_type = 'agent.subagent.stop' THEN ARRAY[nullif({row}.agent_id, '')] END"
    )


# Ingest marking of the Agent calls. Whichever row of a call arrives (start, end, error or stop, in any batch), the
# day of every start whose agent_id ANY row of that call carries is queued again: the call may become known now (its
# first Agent-tool row, start, end or error, just arrived, where before it had none and so named no agent at all),
# or may name that agent now (a stop arrived after the end, or the reverse), or no longer (a stop beats a text id),
# and any of those changes the agent's counts and its Timeline row. A start is included for exactly this: on its own
# it still resolves no call (a stop with no start, end or error row is no call, see _call_sources), but a late start
# arriving after an already-ingested stop or end, in an earlier or later batch, is what turns such a call from
# unknown into resolved, and only marking its own tool_use_id dirty makes that requeue happen. Keyed on the call,
# (session_id, tool_use_id), $1 and $2, not on the batch's own ids, so the rows of earlier batches are read too. Not
# narrowed to the id the rule resolves to: a superset of days costs one recompute each, a missed day costs a wrong
# count.
# The shape is plan-proof: hook_events has an index on (session_id, ts) only, so a probe per call or per id scans
# the whole session each time (7.6 s for 1500 calls once the planner picked it). Each table is read once, by the
# batch's sessions (`= ANY`), and joined to the calls and the ids through MATERIALIZED sets with no index to probe.
AGENT_CALL_DAYS_SQL = f"""
WITH k AS MATERIALIZED (
    SELECT DISTINCT u.s, u.t FROM unnest($1::text[], $2::text[]) AS u(s, t) WHERE u.t <> ''
), ids AS MATERIALIZED (
    SELECT DISTINCT r.session_id AS s, c.a
    FROM hook_events r
    CROSS JOIN LATERAL unnest({carried_agent_ids('r')}) AS c(a)
    WHERE r.session_id = ANY($1::text[]) AND r.tool_use_id = ANY($2::text[]) AND {is_claude_code('r')}
        AND c.a IS NOT NULL AND EXISTS (SELECT 1 FROM k WHERE k.s = r.session_id AND k.t = r.tool_use_id)
)
SELECT DISTINCT (f.ts AT TIME ZONE 'UTC')::date AS day, f.session_id
FROM hook_events f
WHERE f.session_id = ANY($1::text[]) AND f.event_type = 'agent.subagent.start'
    AND EXISTS (SELECT 1 FROM ids WHERE ids.s = f.session_id AND ids.a = f.agent_id)"""


def agent_call_keys(hooks: Sequence[HookRow]) -> list[tuple[str, str]]:
    """(session_id, tool_use_id) of every Agent call a hook row of the batch belongs to: an agent.tool.start,
    agent.tool.end or agent.tool.error of AGENT_CALL_TOOLS, or an agent.subagent.stop, with a session and a
    tool_use_id. A start is included so a start arriving after the call's stop or end (out of order, in an earlier
    or later batch) requeues the call too: on its own a start still resolves no call, but it is what turns an
    already-ingested stop or end from unknown into resolved. The client is left to the SQL, which reads Claude Code
    rows only."""
    return sorted(
        {
            (hook.session_id, hook.tool_use_id)
            for hook in hooks
            if hook.session_id
            and hook.tool_use_id
            and (
                (
                    hook.event_type in ("agent.tool.start", "agent.tool.end", "agent.tool.error")
                    and hook.tool_name in AGENT_CALL_TOOLS
                )
                or hook.event_type == "agent.subagent.stop"
            )
        }
    )


def agent_call_of(session: str, agent_id: str, alias: str = "acr") -> str:
    """SQL scalar (reverse form): the tool_use_id of the Agent call of `session` that started the agent
    `agent_id`, NULL when no call names it. Prefilters the session's rows carrying the id, resolves each of their
    calls and keeps the winner among those resolving to it. `alias` prefixes inner aliases."""
    row, call, res = f"{alias}_p", f"{alias}_c", f"{alias}_r"
    return (
        f"(SELECT {call}.tool_use_id FROM (SELECT DISTINCT {row}.tool_use_id FROM hook_events {row} "
        f"WHERE {row}.session_id = {session} AND {carries_agent_id(row, agent_id)}) {call} "
        f"CROSS JOIN LATERAL {_agent_call_resolution(session, f'{call}.tool_use_id', res)} {res} "
        f"WHERE {res}.agent_id = {agent_id} "
        f"ORDER BY {res}.source, {res}.first_ts, {call}.tool_use_id LIMIT 1)"
    )


def agent_call_agent_id(session: str, tool_use_id: str, alias: str = "acf") -> str:
    """SQL scalar (forward form): the agent id the Agent call (`session`, `tool_use_id`) started, NULL when the
    call is unknown, unresolved, ambiguous, or lost its id to another call. `alias` prefixes inner aliases."""
    res = f"{alias}_r"
    return (
        f"(SELECT {res}.agent_id FROM {_agent_call_resolution(session, tool_use_id, res)} {res} "
        f"WHERE {agent_call_of(session, f'{res}.agent_id', f'{alias}_w')} = {tool_use_id})"
    )


def agent_call_claim(session: str, tool_use_id: str, alias: str = "acl") -> str:
    """SQL scalar: the agent id the Agent call (`session`, `tool_use_id`) resolves to by its own sources, before the
    one-call-per-agent rule; NULL when no source answers or the stops are ambiguous. A call with a claim but no
    `agent_call_agent_id` lost its agent to another call."""
    res = f"{alias}_r"
    return f"(SELECT {res}.agent_id FROM {_agent_call_resolution(session, tool_use_id, res)} {res})"


def agent_call_scope(alias: str) -> str:
    """SQL (never NULL): the TOOL span `alias` is a Claude Code call of one of AGENT_CALL_TOOLS with a tool_use_id and
    a subagent type: the calls whose parent-request fallback is bounded by their end and which, with no end and no
    agent id of their own, take none. The older Task tool, a span without a tool_use_id and another client's span
    are outside it and keep the open window."""
    return (
        f"coalesce(starts_with({alias}.span_name, 'claude_code.') AND {agent_call_tool(f'{alias}.tool_name')} "
        f"AND coalesce({alias}.tool_use_id, '') <> '' AND coalesce({alias}.subagent_type, '') <> '', false)"
    )


def agent_call_span_end(alias: str) -> str:
    """SQL: the end of the call of the TOOL span `alias` (see call_end_ts), NULL when it has none; read it under
    agent_call_scope, whose tool names it covers."""
    branches = " ".join(f"WHEN '{name}' THEN {call_end_ts(alias, name)}" for name in AGENT_CALL_TOOLS)
    return f"CASE {alias}.tool_name {branches} END"


def takes_parent(in_scope: str, call_end: str, agent_id: str, claimed_id: str, skill_agent_id: str) -> str:
    """SQL (never NULL): a dispatch row shows a slice of its parent's API requests. A row that has its own agent
    (`agent_id` of the agent-call rule, `skill_agent_id` of a Skill's fork) never does. Within the Claude Code Agent
    scope (`in_scope`, see agent_call_scope) a call takes none when it never ended (`call_end` NULL: nothing bounds
    its slice) or when its agent id went to another call (`claimed_id`, the id it resolved to, is set but
    `agent_id` is not). Rows outside the scope (Task, no tool_use_id, other clients, Skill) keep today's estimate."""
    return (
        f"({agent_id} IS NULL AND {skill_agent_id} IS NULL "
        f"AND NOT ({in_scope} AND ({call_end} IS NULL OR {claimed_id} IS NOT NULL)))"
    )
