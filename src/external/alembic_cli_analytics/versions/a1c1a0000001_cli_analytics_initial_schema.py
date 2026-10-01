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

"""CLI Analytics initial schema (PostgreSQL storage adapter).

Revision ID: a1c1a0000001
Revises:
Create Date: 2026-09-23

Typed raw tables partitioned by week, a per-record idempotency ledger partitioned by day,
a work queue of (day, session) keys, and rollups with the grain of the ClickHouse
materialized views, partitioned by month. Partitions (including each table's DEFAULT
partition) are created by the maintenance job, not here: see
codemie.repository.cli_analytics.postgres.maintenance.

Canonical kinds (codemie.repository.cli_analytics.vocabulary):
  span_kind  0 other, 1 tool, 2 tool execution, 3 interaction, 4 LLM request
  event_kind 0 other, 1 api_request, 2 user_prompt, 3 skill_activated, 4 tool_result,
             5 tool_decision, 6 api_error
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1c1a0000001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HOOK_TEXT_COLUMNS = """
    developer_name        text,
    codemie_project_name  text,
    cwd                   text,
    git_branch            text,
    repo_remote           text,
    permission_mode       text,
    source                text,
    effort                text,
    tool_name             text,
    tool_use_id           text,
    tool_input            text,
    tool_output           text,
    error_message         text,
    error_type            text,
    reason                text,
    agent_id              text,
    agent_type            text,
    trigger               text,
    denial_reason         text,
    notification_type     text,
    prompt_body           text,
    skill_name            text,"""

UPGRADE = [
    # ── reference data ───────────────────────────────────────────────────────
    """
    CREATE TABLE otel_resources (
        resource_id  bigint PRIMARY KEY,           -- hash of the canonical resource attributes
        service_name text,
        attrs        jsonb NOT NULL,
        first_seen   timestamptz NOT NULL DEFAULT now(),
        last_seen    timestamptz NOT NULL DEFAULT now()  -- refreshed at most daily; purged once unused for the raw retention
    )""",
    """
    CREATE TABLE session_attributes (              -- session-constant OTel attributes, stored once
        session_id   text PRIMARY KEY,
        attrs        jsonb NOT NULL,
        updated_at   timestamptz NOT NULL DEFAULT now()
    )""",
    # ── raw signals (weekly partitions) ──────────────────────────────────────
    """
    CREATE TABLE log_events (
        ts                    timestamptz NOT NULL,
        session_id            text NOT NULL,       -- '' when the record has none, as in ClickHouse
        event_kind            smallint NOT NULL,
        event_name            text,
        prompt_id             text,
        user_email            text,
        model                 text,
        query_source          text,
        skill_name            text,
        command_name          text,
        trace_id              bytea,
        span_id               bytea,
        cost_usd              double precision,
        input_tokens          bigint,
        output_tokens         bigint,
        cache_read_tokens     bigint,
        cache_creation_tokens bigint,
        severity              smallint NOT NULL,
        resource_id           bigint NOT NULL,
        attrs                 jsonb                -- attributes without a typed column
    ) PARTITION BY RANGE (ts)""",
    # Orders hook events with equal timestamps by arrival: the earliest-value rules of the
    # session dimensions break ties that way, like ClickHouse's argMin.
    "CREATE SEQUENCE hook_events_ingest_seq",
    f"""
    CREATE TABLE hook_events (
        ts                    timestamptz NOT NULL,
        ingest_seq            bigint NOT NULL DEFAULT nextval('hook_events_ingest_seq'),
        session_id            text NOT NULL,
        event_type            text NOT NULL,
        prompt_id             text,
        user_email            text,                -- authenticated sender of /event-hooks
        {_HOOK_TEXT_COLUMNS}
        attrs                 jsonb
    ) PARTITION BY RANGE (ts)""",
    """
    CREATE TABLE spans (
        ts              timestamptz NOT NULL,      -- span start
        duration_ns     bigint NOT NULL,
        trace_id        bytea NOT NULL,
        span_id         bytea NOT NULL,
        parent_span_id  bytea,
        span_kind       smallint NOT NULL,
        span_name       text NOT NULL,
        session_id      text NOT NULL,
        user_email      text,
        tool_name       text,
        tool_use_id     text,
        file_path       text,
        subagent_type   text,
        skill_name      text,
        success         text,                      -- 'true' / 'false' as emitted
        status_code     smallint NOT NULL,
        resource_id     bigint NOT NULL,
        attrs           jsonb
    ) PARTITION BY RANGE (ts)""",
    """
    CREATE TABLE metric_points (
        ts            timestamptz NOT NULL,
        start_ts      timestamptz,
        metric_name   text NOT NULL,
        value         double precision NOT NULL,
        session_id    text NOT NULL,
        user_email    text,
        model         text,
        type          text,
        temporality   smallint NOT NULL,
        is_monotonic  boolean NOT NULL,
        resource_id   bigint NOT NULL,
        attrs         jsonb
    ) PARTITION BY RANGE (ts)""",
    # ── idempotency ledger (daily partitions) and rollup work queue ──────────
    """
    CREATE TABLE ingest_dedup (
        day  date   NOT NULL,                      -- UTC day of the record
        h    bigint NOT NULL,                      -- hash of the record's identity
        first_seen timestamptz NOT NULL DEFAULT now(), -- first delivery: a late record's window counts from it
        PRIMARY KEY (day, h)
    ) PARTITION BY RANGE (day)""",
    # Re-marked in place on every ingest of the key: a re-mark changes only kinds and version,
    # which no index covers, so it stays a HOT update; free space on each page for them.
    """
    CREATE TABLE rollup_dirty (
        day        date        NOT NULL,
        session_id text        NOT NULL,
        kinds      integer     NOT NULL,           -- bitmask: 1 log facts, 2 dimensions, 4 span facts, 8 metrics
        version    bigint      NOT NULL DEFAULT 1, -- bumped by each re-mark; see rollups.py
        marked_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
        timeouts   smallint    NOT NULL DEFAULT 0,     -- recomputes of the key timed out in a row
        alone      boolean     NOT NULL DEFAULT false, -- recompute it on its own; see rollups.py
        PRIMARY KEY (day, session_id)
    ) WITH (fillfactor = 70)""",
    # ── rollups (monthly partitions): the grain of the ClickHouse materialized views ──
    """
    CREATE TABLE cost_daily (
        day                   date   NOT NULL,
        session_id            text   NOT NULL,
        user_email            text   NOT NULL,
        model_name            text   NOT NULL,
        query_source          text   NOT NULL,
        cost_usd              double precision NOT NULL,
        input_tokens          bigint NOT NULL,
        output_tokens         bigint NOT NULL,
        cache_read_tokens     bigint NOT NULL,
        cache_creation_tokens bigint NOT NULL,
        api_call_count        bigint NOT NULL,
        PRIMARY KEY (day, session_id, user_email, model_name, query_source)
    ) PARTITION BY RANGE (day)""",
    """
    CREATE TABLE lines_daily (
        day           date   NOT NULL,
        session_id    text   NOT NULL,
        user_email    text   NOT NULL,
        model_name    text   NOT NULL,
        lines_added   bigint NOT NULL,
        lines_removed bigint NOT NULL,
        PRIMARY KEY (day, session_id, user_email, model_name)
    ) PARTITION BY RANGE (day)""",
    """
    CREATE TABLE active_time_daily (
        day            date   NOT NULL,
        session_id     text   NOT NULL,
        user_email     text   NOT NULL,
        active_ms_user bigint NOT NULL,
        active_ms_cli  bigint NOT NULL,
        PRIMARY KEY (day, session_id, user_email)
    ) PARTITION BY RANGE (day)""",
    """
    CREATE TABLE turns_daily (
        day        date   NOT NULL,
        session_id text   NOT NULL,
        turns      bigint NOT NULL,
        PRIMARY KEY (day, session_id)
    ) PARTITION BY RANGE (day)""",
    """
    CREATE TABLE tool_facts_daily (
        day         date   NOT NULL,
        session_id  text   NOT NULL,
        tool_calls  bigint NOT NULL,
        agent_count bigint NOT NULL,
        skill_count bigint NOT NULL,
        PRIMARY KEY (day, session_id)
    ) PARTITION BY RANGE (day)""",
    # Exact distinct file counts across days (ClickHouse keeps uniqExact states instead).
    """
    CREATE TABLE session_files_daily (
        day        date    NOT NULL,
        session_id text    NOT NULL,
        file_path  text    NOT NULL,
        is_written boolean NOT NULL,
        is_edited  boolean NOT NULL,
        PRIMARY KEY (day, session_id, file_path)
    ) PARTITION BY RANGE (day)""",
    # Calls per (hour, session, kind, name) — kind 1 tool (with successes), 2 skill, 3 agent,
    # 4 slash command. Serves the full hours inside a window; the partial hours at its
    # edges are read from raw spans, so window results stay exact.
    """
    CREATE TABLE invocations_hourly (
        hour       timestamptz NOT NULL,
        session_id text        NOT NULL,
        kind       smallint    NOT NULL,
        name       text        NOT NULL,
        calls      integer     NOT NULL,
        success    integer     NOT NULL,
        PRIMARY KEY (hour, session_id, kind, name)
    ) PARTITION BY RANGE (hour)""",
    # One row per session: dimensions (earliest non-empty value of each) and identity.
    """
    CREATE TABLE session_dims (
        session_id     text PRIMARY KEY,
        started_at     timestamptz,                -- NULL: the session has no dimension hook event
        last_event_at  timestamptz,
        repository     text,
        branch         text,
        repo_remote    text,
        project_name   text,
        developer_name text,
        first_prompt   text,
        jwt_email      text,                       -- max() in byte order, as ClickHouse compares
        dev_name_max   text,
        updated_at     timestamptz NOT NULL DEFAULT now()
    ) WITH (fillfactor = 90)""",
    """
    CREATE TABLE session_skills (                  -- distinct activated skills per session
        session_id text NOT NULL,
        skill_name text NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (session_id, skill_name)
    )""",
    # ── indexes (declared on the parents, created on every partition) ────────
    "CREATE INDEX log_events_session_ts ON log_events (session_id, ts)",
    "CREATE INDEX log_events_ts_brin ON log_events USING brin (ts)",
    "CREATE INDEX log_events_skill_activated ON log_events (session_id) INCLUDE (skill_name) WHERE event_kind = 3",
    "CREATE INDEX log_events_commands ON log_events (ts) INCLUDE (session_id, command_name)"
    " WHERE event_kind = 2 AND command_name IS NOT NULL",
    "CREATE INDEX hook_events_session_ts ON hook_events (session_id, ts)",
    "CREATE INDEX hook_events_ts_brin ON hook_events USING brin (ts)",
    "CREATE INDEX spans_session_ts ON spans (session_id, ts)",
    "CREATE INDEX spans_exec_use_id ON spans (tool_use_id) INCLUDE (success, ts) WHERE span_kind = 2",
    "CREATE INDEX spans_tool_cover ON spans (ts)"
    " INCLUDE (session_id, tool_name, tool_use_id, skill_name, subagent_type) WHERE span_kind = 1",
    "CREATE INDEX metric_points_session_ts ON metric_points (session_id, ts)",
    "CREATE INDEX cost_daily_session ON cost_daily (session_id)",
    "CREATE INDEX lines_daily_session ON lines_daily (session_id)",
    "CREATE INDEX active_time_daily_session ON active_time_daily (session_id)",
    "CREATE INDEX turns_daily_session ON turns_daily (session_id)",
    "CREATE INDEX tool_facts_daily_session ON tool_facts_daily (session_id)",
    "CREATE INDEX invocations_hourly_session ON invocations_hourly (session_id)",
    "CREATE INDEX rollup_dirty_marked ON rollup_dirty (marked_at)",  # claims take the oldest due keys
    "CREATE INDEX rollup_dirty_alone ON rollup_dirty (marked_at) WHERE alone",
]

DOWNGRADE_TABLES = [
    "session_skills",
    "session_dims",
    "invocations_hourly",
    "session_files_daily",
    "tool_facts_daily",
    "turns_daily",
    "active_time_daily",
    "lines_daily",
    "cost_daily",
    "rollup_dirty",
    "ingest_dedup",
    "metric_points",
    "spans",
    "hook_events",
    "log_events",
    "session_attributes",
    "otel_resources",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for table in DOWNGRADE_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP SEQUENCE IF EXISTS hook_events_ingest_seq")
