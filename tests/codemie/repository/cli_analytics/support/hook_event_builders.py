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

"""Builders for the six sdlc-analytics hook events (spec section 4), as flat JSON-shaped dicts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from opentelemetry.proto.logs.v1.logs_pb2 import LogRecord

from tests.codemie.repository.cli_analytics.support import otlp_builders as b

SESSION = "3f6c0a52"
TS = "2026-09-29T10:59:41.512Z"


def _common(kind: str, discriminator: str, timestamp: str = TS) -> dict[str, Any]:
    """Envelope plus the common optional fields."""
    return {
        "timestamp": timestamp,
        "session_id": SESSION,
        "prompt_id": "prompt-1",
        "schema_version": 2,
        "event_id": f"{kind}:{SESSION}:{discriminator}",
        "platform": "linux",
        "entrypoint": "cli",
        "client_version": "2.1.0",
        "codemie_cli_version": "0.14.1",
        "identity_source": "git",
        "story_id": "EPMCDME-15302",
        "story_source": "branch",
        "developer_name": "Jane Doe",
        "codemie_project_name": "codemie",
        "cwd": "/work/codemie",
        "agent_id": "",
        "agent_type": "",
    }


def _usage_fields() -> dict[str, Any]:
    return {
        "model_raw": "claude-opus-5-5-20260101",
        "model": "claude-opus-5-5",
        "speed": "standard",
        "inference_geo": "not_available",
        "service_tier": "standard",
        "scope_kind": "main",
        "scope_name": "",
        "input_tokens": 12,
        "cache_creation_5m_tokens": 0,
        "cache_creation_1h_tokens": 2500,
        "cache_read_tokens": 48000,
        "output_tokens": 295,
        "thinking_tokens": 289,
        "web_search_requests": 0,
        "web_fetch_requests": 0,
    }


def _compaction() -> dict[str, Any]:
    return {
        "start": "2026-09-29T10:20:00.000Z",
        "end": "2026-09-29T10:20:30.000Z",
        "duration_ms": 30000,
        "trigger": "auto",
        "pre_tokens": 150000,
        "post_tokens": 20000,
        "dropped_tokens": 130000,
    }


def usage_request(**over: Any) -> dict[str, Any]:
    event = {
        "type": "agent.usage.request",
        **_common("usage", "req_011CTx9aB"),
        "request_id": "req_011CTx9aB",
        "message_id": "msg_01H7Yq",
        **_usage_fields(),
        "stop_reason": "tool_use",
        "is_api_error": False,
        "git_branch": "feature/EPMCDME-15302",
    }
    event.update(over)
    return event


def subagent_usage(**over: Any) -> dict[str, Any]:
    event = {
        "type": "agent.subagent.usage",
        **_common("subagent", "toolu_01Abc", "2026-09-29T10:45:00.000Z"),
        "agent_id": "agent-7",
        "agent_type": "Explore",
        "description": "Explore the ingest code",
        "tool_use_id": "toolu_01Abc",
        "workflow_run": "wf-1",
        "worktree": "",
        "started_at": "2026-09-29T10:40:00.000Z",
        "ended_at": "2026-09-29T10:45:00.000Z",
        "model": "claude-opus-5-5",
        "spawn_depth": 1,
        "duration_ms": 300000,
        "api_calls": 4,
        "tool_calls": 9,
        "tool_results": 9,
        "tool_errors": 1,
        "lines_added": 10,
        "lines_removed": 2,
        "files_changed": 3,
        "tools": {"Read": {"calls": 6, "errors": 0}, "Bash": {"calls": 3, "errors": 1}},
        "skills": {"brainstorming": 1},
        "commands": ["review"],
        "compactions": [_compaction()],
        "usage": [{**_usage_fields(), "api_calls": 4}],
    }
    event.update(over)
    return event


def session_summary(**over: Any) -> dict[str, Any]:
    event = {
        "type": "agent.session.summary",
        **_common("summary", "final", "2026-09-29T11:30:00.000Z"),
        "title": "Add PostgreSQL ingest",
        "started_at": "2026-09-29T10:00:00.000Z",
        "ended_at": "2026-09-29T11:30:00.000Z",
        "primary_model": "claude-opus-5-5",
        "primary_command": "sdlc-standard",
        "branch_dominant": "feature/EPMCDME-15302",
        "git_branch": "feature/EPMCDME-15302",
        "is_final": True,
        "duration_ms": 5400000,
        "turns": 20,
        "api_calls": 55,
        "tool_calls": 80,
        "tool_results": 80,
        "tool_errors": 3,
        "lines_added": 400,
        "lines_removed": 50,
        "files_changed": 12,
        "files_written": 5,
        "files_edited": 7,
        "compaction_count": 1,
        "compaction_pre_tokens": 150000,
        "models": ["claude-opus-5-5"],
        "commands": ["sdlc-standard"],
        "client_versions": ["2.1.0"],
        "skills": {"brainstorming": 1},
        "agents": {"Explore": 2},
        "tools": {"Read": {"calls": 40, "errors": 0}, "Bash": {"calls": 40, "errors": 3}},
        "branch_counts": {"feature/EPMCDME-15302": 55},
        "compactions": [_compaction()],
    }
    event.update(over)
    return event


def session_env(**over: Any) -> dict[str, Any]:
    event = {
        "type": "agent.session.env",
        **_common("env", "start", "2026-09-29T10:00:00.000Z"),
        "provider": "anthropic",
        "api_host": "api.anthropic.com",
        "configured_model": "claude-opus-5-5",
        "effort": "high",
        "output_style": "default",
        "permission_mode": "default",
        "os": "linux",
        "arch": "x64",
        "node_version": "22.1.0",
        "timezone": "UTC",
        "hostname_hash": "9f86d081",
        "plugins": {"sdlc-analytics": "1.4.0"},
        "mcp_servers": ["codegraph"],
    }
    event.update(over)
    return event


def skill_dispatch(**over: Any) -> dict[str, Any]:
    event = {
        "type": "agent.skill.dispatch",
        **_common("skill", "brainstorming-1", "2026-09-29T10:05:00.000Z"),
        "skill_name": "brainstorming",
        "command_name": "brainstorm",
        "command_source": "user",
    }
    event.update(over)
    return event


def git_snapshot(**over: Any) -> dict[str, Any]:
    event = {
        "type": "agent.git.snapshot",
        **_common("git", "abc1234", "2026-09-29T11:00:00.000Z"),
        "git_branch": "feature/EPMCDME-15302",
        "repo_remote": "git@example.com:epam/codemie.git",
        "git_email": "jane.doe@example.com",
        "git_head_start": "0123456789abcdef0123456789abcdef01234567",
        "git_head_end": "abc1234abc1234abc1234abc1234abc1234abc12",
        "git_commits": [
            {
                "sha": "abc1234abc1234abc1234abc1234abc1234abc12",
                "time": "2026-09-29T10:50:00Z",
                "files": 3,
                "added": 40,
                "removed": 5,
            },
        ],
    }
    event.update(over)
    return event


def minimal(type_: str, session_id: str, timestamp: str) -> dict[str, Any]:
    """Only the envelope: the smallest event the server must accept."""
    return {"type": type_, "timestamp": timestamp, "session_id": session_id}


def without(event: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Copy of the event with the given keys left out (minimal and old-format variants)."""
    return {k: v for k, v in event.items() if k not in keys}


def as_otlp_log(event: dict[str, Any], record_ts: datetime | None) -> LogRecord:
    """OTLP log record: `type` becomes the `event_type` attribute, every other key an attribute."""
    attrs = {("event_type" if k == "type" else k): v for k, v in event.items()}
    return b.log_record(record_ts, attrs)
