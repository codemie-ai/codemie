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

"""SQL predicates over `hook_events` rows that the fork rules (`fork_attribution`) and the agent-call rule
(`agent_call_attribution`) both read: whose event a row is, the agentId a tool result names, and when a tool call
ended. Both import them from here, which keeps the two free of an import cycle."""

from __future__ import annotations


def tool_response_agent_id(expr: str) -> str:
    """SQL: the `agentId` named in a tool_response text (`expr`), else NULL. Only an id followed by its
    closing quote is taken: the CLI cuts tool_response to 300 chars, and a cut id is no key."""
    return f"""substring({expr} from '"agentId":\\s*"([A-Za-z0-9_-]+)"')"""


def is_claude_code(alias: str = "") -> str:
    """SQL: the event of `alias` (unqualified `attrs` without one) is Claude Code's; an event without
    `platform` is Claude Code's. The fork rules rest on Claude Code's events."""
    attrs = f"{alias}.attrs" if alias else "attrs"
    return f"coalesce({attrs}->>'platform', 'claude-code') = 'claude-code'"


def call_end_row(end: str, call: str, tool_name: str) -> str:
    """SQL: the hook row `end` is an agent.tool.end/.error of `tool_name` with the tool_use_id of `call`, in
    `call`'s window. A denied call only ever gets agent.tool.denied."""
    return (
        f"{end}.session_id = {call}.session_id "
        f"AND {end}.event_type IN ('agent.tool.end', 'agent.tool.error') "
        f"AND {end}.tool_name = '{tool_name}' AND {end}.tool_use_id = {call}.tool_use_id "
        f"AND {end}.ts >= {call}.ts - interval '1 hour' AND {end}.ts < {call}.ts + interval '1 day'"
    )


def call_end_ts(call: str, tool_name: str) -> str:
    """SQL: the end of the `tool_name` call of the hook row or TOOL span `call` (it needs session_id, tool_use_id
    and ts): the ts of its latest end or error row (`call_end_row`), NULL when it has none."""
    end = f"{call}_r"
    return f"(SELECT max({end}.ts) FROM hook_events {end} WHERE {call_end_row(end, call, tool_name)})"
