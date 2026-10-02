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

"""CLI Analytics schema extension: per-request usage, session dimensions, usage rollups.

Revision ID: a1c1a0000006
Revises: a1c1a0000001
Create Date: 2026-09-29

Partitions of usage_requests are created by the maintenance job.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1c1a0000006"
down_revision: str | None = "a1c1a0000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(sql_type: str, names: str) -> list[tuple[str, str]]:
    return [(name.strip(), sql_type) for name in names.split(",")]


def _add_columns(table: str, columns: list[tuple[str, str]]) -> str:
    clauses = ",\n".join(f"    ADD COLUMN {name} {sql_type}" for name, sql_type in columns)
    return f"ALTER TABLE {table}\n{clauses}"


def _drop_columns(table: str, columns: list[tuple[str, str]]) -> str:
    clauses = ",\n".join(f"    DROP COLUMN {name}" for name, _ in columns)
    return f"ALTER TABLE {table}\n{clauses}"


# ---------------------------------------------------------------------------
# Tables and key changes
# ---------------------------------------------------------------------------

_SESSION_DIMS_A = [
    *_columns("text", "user_email, identity_source, git_email, codemie_cli_email, claude_account_email, os_user"),
    *_columns("text", "platform, ingest_source, client_version, codemie_cli_version, provider"),
    *_columns("text", "branch_dominant, feature_id, story_id, story_source"),
    *_columns("text", "primary_model, primary_command, delivery_framework"),
    ("ended_at", "timestamptz"),
    *_columns("bigint", "duration_ms, active_ms, compaction_pre_tokens"),
    *_columns(
        "int",
        "turns, api_calls, tool_calls, tool_errors, lines_added, lines_removed, files_changed, "
        "files_written, files_edited, compaction_count",
    ),
    *_columns("jsonb", "commands, skills, agents, tools, models"),
    ("source_cost_usd", "double precision"),
]

_COST_DAILY_KEY_COLUMNS = ["speed", "inference_geo", "scope_kind", "scope_name", "agent_type"]
_COST_DAILY_A = [
    *[(name, "text NOT NULL DEFAULT ''") for name in _COST_DAILY_KEY_COLUMNS],
    *_columns("bigint", "cache_creation_5m_tokens, cache_creation_1h_tokens"),
]

UPGRADE_GROUP_A = [
    """
    CREATE TABLE usage_requests (
        ts                        timestamptz NOT NULL,
        session_id                text NOT NULL,
        request_id                text,
        agent_id                  text,
        scope_kind                text,
        scope_name                text,
        model_raw                 text,
        model                     text,
        speed                     text,
        inference_geo             text,
        service_tier              text,
        input_tokens              bigint,
        cache_creation_5m_tokens  bigint,
        cache_creation_1h_tokens  bigint,
        cache_read_tokens         bigint,
        output_tokens             bigint,
        web_search_requests       int,
        web_fetch_requests        int,
        stop_reason               text,
        is_api_error              bool,
        git_branch                text,
        attrs                     jsonb
    ) PARTITION BY RANGE (ts)
    """,
    "CREATE INDEX usage_requests_session_ts ON usage_requests (session_id, ts)",
    "CREATE INDEX usage_requests_ts_brin ON usage_requests USING brin (ts)",
    _add_columns("session_dims", _SESSION_DIMS_A),
    "CREATE INDEX session_dims_user_started ON session_dims (user_email, started_at)",
    "CREATE INDEX session_dims_story ON session_dims (story_id)",
    _add_columns("cost_daily", _COST_DAILY_A),
    "ALTER TABLE cost_daily DROP CONSTRAINT cost_daily_pkey",
    "ALTER TABLE cost_daily ADD PRIMARY KEY (day, session_id, user_email, model_name, query_source, speed, "
    "inference_geo, scope_kind, scope_name, agent_type)",
    """
    CREATE TABLE session_usage (
        session_id                text NOT NULL,
        day                       date NOT NULL,
        model                     text NOT NULL,
        speed                     text NOT NULL,
        inference_geo             text NOT NULL,
        scope_kind                text NOT NULL,
        scope_name                text NOT NULL,
        agent_id                  text NOT NULL,
        input_tokens              bigint,
        cache_creation_5m_tokens  bigint,
        cache_creation_1h_tokens  bigint,
        cache_read_tokens         bigint,
        output_tokens             bigint,
        api_calls                 int,
        source_cost_usd           double precision,
        PRIMARY KEY (session_id, day, model, speed, inference_geo, scope_kind, scope_name, agent_id)
    )
    """,
    "CREATE INDEX session_usage_day ON session_usage (day)",
    """
    CREATE TABLE subagent_invocations (
        session_id    text NOT NULL,
        agent_id      text NOT NULL,
        agent_type    text,
        description   text,
        tool_use_id   text,
        workflow_run  text,
        worktree      text,
        spawn_depth   int,
        started_at    timestamptz,
        duration_ms   bigint,
        attribution   text,
        PRIMARY KEY (session_id, agent_id)
    )
    """,
]

# ---------------------------------------------------------------------------
# Nullable columns
# ---------------------------------------------------------------------------

_USAGE_REQUESTS_B = [
    *_columns("text", "user_email, message_id, agent_type"),
    ("thinking_tokens", "bigint"),
]
_SESSION_USAGE_B = [
    *_columns("bigint", "cache_creation_tokens, thinking_tokens"),
    *_columns("int", "web_search_requests, web_fetch_requests"),
    *_columns("bigint", "api_duration_ms, ttft_ms_sum"),
    ("attrs", "jsonb"),
]
_SUBAGENT_INVOCATIONS_B = [
    ("model", "text"),
    *_columns("int", "api_calls, tool_calls, tool_errors, tool_results, lines_added, lines_removed, files_changed"),
    *_columns("jsonb", "tools, skills, commands, compactions"),
    ("ended_at", "timestamptz"),
    ("attrs", "jsonb"),
]
_SESSION_DIMS_B = [
    *_columns(
        "text",
        "title, team, environment, space_id, space_source, entrypoint, hostname_hash, api_host, "
        "configured_model, effort, output_style, os, arch, node_version, timezone, permission_mode, "
        "git_head_start, git_head_end, organization_id, account_uuid, account_id, otel_user_id, terminal_type",
    ),
    *_columns("jsonb", "sources, attrs, client_versions, compactions, plugins, mcp_servers, git_commits"),
    *_columns("int", "tool_results, schema_version"),
    *_columns("timestamptz", "activity_started_at, summary_ts"),
]
_LOG_EVENTS_B = [("request_id", "text")]

UPGRADE_GROUP_B = [
    _add_columns("usage_requests", _USAGE_REQUESTS_B),
    _add_columns("session_usage", _SESSION_USAGE_B),
    _add_columns("subagent_invocations", _SUBAGENT_INVOCATIONS_B),
    _add_columns("session_dims", _SESSION_DIMS_B),
    _add_columns("log_events", _LOG_EVENTS_B),
    "CREATE INDEX log_events_session_request ON log_events (session_id, request_id)",
    """
    CREATE TABLE session_usage_hourly (
        session_id                text NOT NULL,
        hour                      timestamptz NOT NULL,
        model                     text NOT NULL,
        speed                     text NOT NULL,
        inference_geo             text NOT NULL,
        service_tier              text NOT NULL,
        scope_kind                text NOT NULL,
        scope_name                text NOT NULL,
        agent_id                  text NOT NULL,
        input_tokens              bigint,
        cache_creation_tokens     bigint,
        cache_creation_5m_tokens  bigint,
        cache_creation_1h_tokens  bigint,
        cache_read_tokens         bigint,
        output_tokens             bigint,
        thinking_tokens           bigint,
        web_search_requests       int,
        web_fetch_requests        int,
        api_calls                 int,
        api_duration_ms           bigint,
        ttft_ms_sum               bigint,
        source_cost_usd           double precision,
        attrs                     jsonb,
        PRIMARY KEY (session_id, hour, model, speed, inference_geo, service_tier, scope_kind, scope_name, agent_id)
    )
    """,
    "CREATE INDEX session_usage_hourly_hour ON session_usage_hourly (hour)",
]

UPGRADE = [*UPGRADE_GROUP_A, *UPGRADE_GROUP_B]

DOWNGRADE = [
    # Nullable columns and session_usage_hourly first.
    "DROP TABLE session_usage_hourly",
    "DROP INDEX log_events_session_request",
    _drop_columns("log_events", _LOG_EVENTS_B),
    _drop_columns("session_dims", _SESSION_DIMS_B),
    _drop_columns("subagent_invocations", _SUBAGENT_INVOCATIONS_B),
    _drop_columns("session_usage", _SESSION_USAGE_B),
    _drop_columns("usage_requests", _USAGE_REQUESTS_B),
    # Then the tables and key changes.
    "DROP TABLE subagent_invocations",
    "DROP TABLE session_usage",
    "ALTER TABLE cost_daily DROP CONSTRAINT cost_daily_pkey",
    _drop_columns("cost_daily", _COST_DAILY_A),
    "ALTER TABLE cost_daily ADD PRIMARY KEY (day, session_id, user_email, model_name, query_source)",
    "DROP INDEX session_dims_story",
    "DROP INDEX session_dims_user_started",
    _drop_columns("session_dims", _SESSION_DIMS_A),
    "DROP TABLE usage_requests",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
