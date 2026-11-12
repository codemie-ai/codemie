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

"""A Claude Desktop Cowork session as the plugin sends it: hook events only, no email in the payload."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from tests.codemie.repository.cli_analytics.support.hook_event_builders import (
    minimal,
    session_summary,
    usage_request,
    without,
)

# Builder envelope fields a Cowork session has no value for: no sender identity, no git, no story, no prompt.
_NOT_SENT = (
    "user_email",
    "developer_name",
    "prompt_id",
    "identity_source",
    "story_id",
    "story_source",
    "git_branch",
    "agent_id",
    "agent_type",
)


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def cowork_session(session_id: str, project: str, start: datetime) -> list[dict[str, Any]]:
    """The 16 events of one Cowork session from `start`: a prompt writing a file, an auto prompt that runs a
    subagent, a prompt editing the file, a request on a LiteLLM route and one plain, the final summary and the end."""
    at = lambda seconds: _iso(start + timedelta(seconds=seconds))  # noqa: E731

    def event(base: dict[str, Any], key: str, seconds: int, **fields: Any) -> dict[str, Any]:
        return {
            **without(base, *_NOT_SENT),
            "session_id": session_id,
            "platform": "claude-code",
            "entrypoint": "local-agent",
            "codemie_project_name": project,
            "cwd": "Cowork",
            "schema_version": 2,
            "timestamp": at(seconds),
            "event_id": f"{base['type']}:{session_id}:{key}",
            **fields,
        }

    def hook(type_: str, key: str, seconds: int, **fields: Any) -> dict[str, Any]:
        return event(minimal(type_, session_id, at(seconds)), key, seconds, **fields)

    def usage(key: str, seconds: int, **fields: Any) -> dict[str, Any]:
        return event(usage_request(), key, seconds, request_id="", message_id=key, thinking_tokens=0, **fields)

    agent = {"agent_id": "a1", "agent_type": "general-purpose", "tool_use_id": "tu2"}
    return [
        hook("agent.session.start", "start", 0, session_title="Cowork fixture"),
        hook("agent.prompt.submit", "u1", 1, prompt_body="create notes.md"),
        hook("agent.tool.start", "tu1", 2, tool_name="Write", tool_use_id="tu1", file_path="notes.md"),
        hook("agent.tool.end", "tu1", 3, tool_name="Write", tool_use_id="tu1", lines_added=3, lines_removed=0),
        usage(
            "msg_a",
            4,
            model_raw="claude-opus-5-5",
            model="bedrock/eu.anthropic.claude-opus-5-5",
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=1000,
            cache_creation_5m_tokens=200,
            cache_creation_1h_tokens=0,
        ),
        hook("agent.prompt.submit", "u2", 10, prompt_body="<task-notification>done</task-notification>"),
        hook("agent.tool.start", "tu2", 11, tool_name="Agent", tool_use_id="tu2", subagent_type="general-purpose"),
        hook("agent.subagent.start", "a1", 12, **agent),
        usage(
            "msg_b",
            13,
            agent_id="a1",
            agent_type="general-purpose",
            model_raw="claude-opus-5-5",
            model="claude-opus-5-5",
            input_tokens=10,
            output_tokens=20,
            cache_read_tokens=300,
            cache_creation_5m_tokens=0,
            cache_creation_1h_tokens=0,
        ),
        hook("agent.subagent.stop", "a1", 14, **agent),
        hook("agent.tool.end", "tu2", 15, tool_name="Agent", tool_use_id="tu2"),
        hook("agent.prompt.submit", "u3", 20, prompt_body="add a line"),
        hook("agent.tool.start", "tu3", 21, tool_name="Edit", tool_use_id="tu3", file_path="notes.md"),
        hook("agent.tool.end", "tu3", 22, tool_name="Edit", tool_use_id="tu3", lines_added=2, lines_removed=1),
        event(
            without(
                session_summary(),
                "primary_command",
                "branch_dominant",
                "branch_counts",
                "client_versions",
                "compaction_pre_tokens",
            ),
            "final",
            30,
            is_final=True,
            started_at=at(0),
            ended_at=at(31),
            duration_ms=31000,
            title="Cowork fixture",
            turns=2,
            api_calls=2,
            tool_calls=3,
            tool_results=3,
            tool_errors=0,
            lines_added=5,
            lines_removed=1,
            files_changed=1,
            files_written=1,
            files_edited=1,
            skills={},
            commands=[],
            compactions=[],
            compaction_count=0,
            primary_model="claude-opus-5-5",
            models=["claude-opus-5-5"],
            agents={"general-purpose": 1},
            tools={
                "Write": {"calls": 1, "errors": 0},
                "Agent": {"calls": 1, "errors": 0},
                "Edit": {"calls": 1, "errors": 0},
            },
        ),
        hook("agent.session.end", at(22), 31),
    ]
