-- Copyright 2026 EPAM Systems, Inc. ("EPAM")
--
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--
--     http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.

-- =============================================================
-- CodeMie Analytics — ClickHouse Schema  (EPMCDME-13554)
-- All statements are idempotent (IF NOT EXISTS).
--
-- Two ingest paths:
--   Path A: Claude Code OTLP → OTel Collector → coding_agent_logs
--           (event_name = 'api_request'; cost/token data)
--   Path B: bash hooks → /event-hooks → OTel Collector → coding_agent_logs
--           (event_type = 'agent.*'; session/tool data)
-- =============================================================


-- =============================================================
-- DATABASE
-- =============================================================

CREATE DATABASE IF NOT EXISTS codemie_analytics;


-- =============================================================
-- RAW TABLES  (written by OTel Collector, otelcol-contrib 0.105.0)
-- =============================================================

-- ---------------------------------------------------------------
-- coding_agent_logs — all OTel log records from both paths.
-- Promoted materialized columns avoid re-parsing LogAttributes on every query.
-- Dual key normalisation: Path A uses 'session.id' / 'prompt.id' (dot),
-- Path B uses 'session_id' / 'prompt_id' (underscore) — COALESCE covers both.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_logs
(
    Timestamp              DateTime64(9)                              CODEC(Delta, ZSTD(1)),
    TraceId                String                                     CODEC(ZSTD(1)),
    SpanId                 String                                     CODEC(ZSTD(1)),
    TraceFlags             UInt32                                     CODEC(ZSTD(1)),
    SeverityText           LowCardinality(String)                     CODEC(ZSTD(1)),
    SeverityNumber         Int32                                      CODEC(ZSTD(1)),
    ServiceName            LowCardinality(String)                     CODEC(ZSTD(1)),
    Body                   String                                     CODEC(ZSTD(1)),
    ResourceSchemaUrl      LowCardinality(String)                     CODEC(ZSTD(1)),
    ResourceAttributes     Map(LowCardinality(String), String)        CODEC(ZSTD(1)),
    ScopeSchemaUrl         LowCardinality(String)                     CODEC(ZSTD(1)),
    ScopeName              LowCardinality(String)                     CODEC(ZSTD(1)),
    ScopeVersion           LowCardinality(String)                     CODEC(ZSTD(1)),
    ScopeAttributes        Map(LowCardinality(String), String)        CODEC(ZSTD(1)),
    LogAttributes          Map(LowCardinality(String), String)        CODEC(ZSTD(1)),

    -- Promoted / materialized columns
    session_id             String
                               MATERIALIZED COALESCE(
                                   nullIf(LogAttributes['session.id'], ''),
                                   nullIf(LogAttributes['session_id'], ''),
                                   ''
                               )                                      CODEC(ZSTD(1)),
    prompt_id              String
                               MATERIALIZED COALESCE(
                                   nullIf(LogAttributes['prompt_id'], ''),
                                   nullIf(LogAttributes['prompt.id'], ''),
                                   ''
                               )                                      CODEC(ZSTD(1)),
    user_email             String
                               MATERIALIZED LogAttributes['user.email'] CODEC(ZSTD(1)),
    user_id                String
                               MATERIALIZED LogAttributes['user.id']  CODEC(ZSTD(1)),
    organization_id        LowCardinality(String)
                               MATERIALIZED LogAttributes['organization.id'] CODEC(ZSTD(1)),
    model_name             LowCardinality(String)
                               MATERIALIZED LogAttributes['model']    CODEC(ZSTD(1)),
    event_name             LowCardinality(String)
                               MATERIALIZED LogAttributes['event.name'] CODEC(ZSTD(1)),
    event_type             LowCardinality(String)
                               MATERIALIZED LogAttributes['event_type'] CODEC(ZSTD(1)),
    query_source           LowCardinality(String)
                               MATERIALIZED LogAttributes['query_source'] CODEC(ZSTD(1)),
    developer_name         LowCardinality(String)
                               MATERIALIZED LogAttributes['developer_name'] CODEC(ZSTD(1)),
    codemie_project_name   LowCardinality(String)
                               MATERIALIZED LogAttributes['codemie_project_name'] CODEC(ZSTD(1)),
    agent_name             LowCardinality(String)
                               MATERIALIZED LogAttributes['agent.name']   CODEC(ZSTD(1)),
    skill_name             LowCardinality(String)
                               MATERIALIZED LogAttributes['skill.name']   CODEC(ZSTD(1)),
    -- Free-text prompt body (Path B agent.prompt.submit only) — plain String, NOT
    -- LowCardinality: unlike agent_name/skill_name this is genuinely high-cardinality
    -- and would blow up a LowCardinality dictionary.
    prompt_body            String
                               MATERIALIZED LogAttributes['prompt_body']  CODEC(ZSTD(1)),
    event_sequence         UInt32
                               MATERIALIZED toUInt32OrZero(LogAttributes['event.sequence']) CODEC(ZSTD(1)),
    TimestampDate          Date
                               MATERIALIZED toDate(Timestamp),

    INDEX idx_session_id   session_id  TYPE bloom_filter GRANULARITY 4,
    INDEX idx_user_email   user_email  TYPE bloom_filter GRANULARITY 4,
    INDEX idx_user_id      user_id     TYPE bloom_filter GRANULARITY 4,
    INDEX idx_prompt_id    prompt_id   TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(TimestampDate)
ORDER BY (user_email, TimestampDate, event_name, event_type, session_id, Timestamp)
TTL TimestampDate + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;


-- ---------------------------------------------------------------
-- coding_agent_traces — OTel spans (CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1).
-- Span names: claude_code.tool, claude_code.tool.execution, claude_code.interaction.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_traces
(
    Timestamp              DateTime64(9)                              CODEC(Delta, ZSTD(1)),
    TraceId                String                                     CODEC(ZSTD(1)),
    SpanId                 String                                     CODEC(ZSTD(1)),
    ParentSpanId           String                                     CODEC(ZSTD(1)),
    TraceState             String                                     CODEC(ZSTD(1)),
    SpanName               LowCardinality(String)                     CODEC(ZSTD(1)),
    SpanKind               LowCardinality(String)                     CODEC(ZSTD(1)),
    ServiceName            LowCardinality(String)                     CODEC(ZSTD(1)),
    ResourceAttributes     Map(LowCardinality(String), String)        CODEC(ZSTD(1)),
    ScopeName              String                                     CODEC(ZSTD(1)),
    ScopeVersion           String                                     CODEC(ZSTD(1)),
    SpanAttributes         Map(LowCardinality(String), String)        CODEC(ZSTD(1)),
    Duration               Int64                                      CODEC(ZSTD(1)),
    StatusCode             LowCardinality(String)                     CODEC(ZSTD(1)),
    StatusMessage          String                                     CODEC(ZSTD(1)),
    `Events.Timestamp`     Array(DateTime64(9))                       CODEC(ZSTD(1)),
    `Events.Name`          Array(LowCardinality(String))              CODEC(ZSTD(1)),
    `Events.Attributes`    Array(Map(LowCardinality(String), String)) CODEC(ZSTD(1)),
    `Links.TraceId`        Array(String)                              CODEC(ZSTD(1)),
    `Links.SpanId`         Array(String)                              CODEC(ZSTD(1)),
    `Links.TraceState`     Array(String)                              CODEC(ZSTD(1)),
    `Links.Attributes`     Array(Map(LowCardinality(String), String)) CODEC(ZSTD(1)),

    -- Promoted columns
    session_id             String
                               MATERIALIZED SpanAttributes['session.id'] CODEC(ZSTD(1)),
    -- COALESCE: SDK may set user.email on the span or resource attributes depending on version.
    user_email             String
                               MATERIALIZED COALESCE(
                                   nullIf(SpanAttributes['user.email'],   ''),
                                   nullIf(ResourceAttributes['user.email'], ''),
                                   ''
                               )                                          CODEC(ZSTD(1)),
    tool_name              LowCardinality(String)
                               MATERIALIZED SpanAttributes['tool_name'] CODEC(ZSTD(1)),
    tool_use_id            String
                               MATERIALIZED SpanAttributes['tool_use_id'] CODEC(ZSTD(1)),
    file_path              String
                               MATERIALIZED SpanAttributes['file_path']        CODEC(ZSTD(1)),
    subagent_type          LowCardinality(String)
                               MATERIALIZED SpanAttributes['subagent_type']    CODEC(ZSTD(1)),
    span_skill_name        LowCardinality(String)
                               MATERIALIZED SpanAttributes['skill_name']       CODEC(ZSTD(1)),
    interaction_sequence   UInt32
                               MATERIALIZED toUInt32OrZero(SpanAttributes['interaction.sequence']) CODEC(ZSTD(1)),
    TimestampDate          Date
                               MATERIALIZED toDate(Timestamp),

    INDEX idx_session_id   session_id  TYPE bloom_filter GRANULARITY 4,
    INDEX idx_user_email   user_email  TYPE bloom_filter GRANULARITY 4,
    INDEX idx_tool_name    tool_name   TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(TimestampDate)
ORDER BY (ServiceName, SpanName, TimestampDate, session_id, Timestamp)
TTL TimestampDate + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;


-- ---------------------------------------------------------------
-- coding_agent_metrics_sum — OTel Sum metrics from Path A.
-- All Claude Code SDK metrics are Sum type (verified live 2026-08-01);
-- no gauge table is defined — otelcol-contrib would auto-create one
-- via create_schema:true if a gauge metric is ever emitted.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_metrics_sum
(
    ResourceAttributes     Map(LowCardinality(String), String)        CODEC(ZSTD(1)),
    ResourceSchemaUrl      LowCardinality(String)                     CODEC(ZSTD(1)),
    ScopeAttributes        Map(LowCardinality(String), String)        CODEC(ZSTD(1)),
    ScopeDroppedAttrCount  UInt32                                     CODEC(ZSTD(1)),
    ScopeSchemaUrl         LowCardinality(String)                     CODEC(ZSTD(1)),
    ScopeName              LowCardinality(String)                     CODEC(ZSTD(1)),
    ScopeVersion           LowCardinality(String)                     CODEC(ZSTD(1)),
    ServiceName            LowCardinality(String)                     CODEC(ZSTD(1)),
    MetricName             LowCardinality(String)                     CODEC(ZSTD(1)),
    MetricDescription      String                                     CODEC(ZSTD(1)),
    MetricUnit             LowCardinality(String)                     CODEC(ZSTD(1)),
    Attributes             Map(LowCardinality(String), String)        CODEC(ZSTD(1)),
    StartTimeUnix          DateTime64(9)                              CODEC(Delta, ZSTD(1)),
    TimeUnix               DateTime64(9)                              CODEC(Delta, ZSTD(1)),
    Value                  Float64                                    CODEC(ZSTD(1)),
    Flags                  UInt32                                     CODEC(ZSTD(1)),
    AggregationTemporality Int32                                      CODEC(ZSTD(1)),
    IsMonotonic            Boolean                                    CODEC(ZSTD(1)),
    `Exemplars.FilteredAttributes` Array(Map(LowCardinality(String), String)) CODEC(ZSTD(1)),
    `Exemplars.TimeUnix`   Array(DateTime64(9))                       CODEC(ZSTD(1)),
    `Exemplars.Value`      Array(Float64)                             CODEC(ZSTD(1)),
    `Exemplars.SpanId`     Array(String)                              CODEC(ZSTD(1)),
    `Exemplars.TraceId`    Array(String)                              CODEC(ZSTD(1))
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(toDate(TimeUnix))
ORDER BY (MetricName, Attributes, TimeUnix)
TTL toDate(TimeUnix) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;


-- =============================================================
-- DERIVED TABLES  (populated via Materialized Views below)
-- =============================================================

-- ---------------------------------------------------------------
-- coding_agent_hook_events — Path B hook events extracted from coding_agent_logs.
-- Populated by mv_hook_events. Avoids scanning the full OTel envelope
-- for hook-event-only queries (session timeline, tool analytics).
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_hook_events
(
    Timestamp              DateTime64(9)                              CODEC(Delta, ZSTD(1)),
    session_id             String                                     CODEC(ZSTD(1)),
    prompt_id              String                                     CODEC(ZSTD(1)),
    event_type             LowCardinality(String)                     CODEC(ZSTD(1)),
    developer_name         LowCardinality(String)                     CODEC(ZSTD(1)),
    codemie_project_name   LowCardinality(String)                     CODEC(ZSTD(1)),
    cwd                    String                                     CODEC(ZSTD(1)),
    git_branch             LowCardinality(String)                     CODEC(ZSTD(1)),
    repo_remote            LowCardinality(String)                     CODEC(ZSTD(1)),
    permission_mode        LowCardinality(String)                     CODEC(ZSTD(1)),
    source                 LowCardinality(String)                     CODEC(ZSTD(1)),
    effort                 LowCardinality(String)                     CODEC(ZSTD(1)),
    tool_name              LowCardinality(String)                     CODEC(ZSTD(1)),
    tool_use_id            String                                     CODEC(ZSTD(1)),
    tool_input             String                                     CODEC(ZSTD(1)),
    tool_output            String                                     CODEC(ZSTD(1)),
    error_message          String                                     CODEC(ZSTD(1)),
    error_type             LowCardinality(String)                     CODEC(ZSTD(1)),
    reason                 LowCardinality(String)                     CODEC(ZSTD(1)),
    agent_id               String                                     CODEC(ZSTD(1)),
    agent_type             LowCardinality(String)                     CODEC(ZSTD(1)),
    `trigger`              LowCardinality(String)                     CODEC(ZSTD(1)),
    denial_reason          String                                     CODEC(ZSTD(1)),
    notification_type      LowCardinality(String)                     CODEC(ZSTD(1)),
    -- agent.prompt.submit only; '' on every other event_type. Routed by mv_hook_events
    -- from coding_agent_logs.prompt_body (itself promoted from LogAttributes['prompt_body']).
    prompt_body            String                                     CODEC(ZSTD(1)),
    TimestampDate          Date
                               MATERIALIZED toDate(Timestamp),

    INDEX idx_session_id   session_id           TYPE bloom_filter GRANULARITY 4,
    INDEX idx_prompt_id    prompt_id            TYPE bloom_filter GRANULARITY 4,
    INDEX idx_codemie_project_name codemie_project_name TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(TimestampDate)
ORDER BY (developer_name, TimestampDate, session_id, event_type, Timestamp)
TTL TimestampDate + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;


-- ---------------------------------------------------------------
-- coding_agent_cost_daily — per-day cost rollup (Path A api_request events).
-- SummingMergeTree merges on (day, session_id, user_email, model_name, query_source).
-- session_id is in ORDER BY so proxy sessions (user_email='') remain distinct
-- rows and can be resolved via JOIN with coding_agent_hook_events at read time.
-- TTL 365 days (longer than raw tables).
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_cost_daily
(
    day                    Date                                       CODEC(ZSTD(1)),
    session_id             String                                     CODEC(ZSTD(1)),
    user_email             String                                     CODEC(ZSTD(1)),
    model_name             LowCardinality(String)                     CODEC(ZSTD(1)),
    query_source           LowCardinality(String)                     CODEC(ZSTD(1)),
    cost_usd               Float64                                    CODEC(ZSTD(1)),
    input_tokens           UInt64                                     CODEC(ZSTD(1)),
    output_tokens          UInt64                                     CODEC(ZSTD(1)),
    cache_read_tokens      UInt64                                     CODEC(ZSTD(1)),
    cache_creation_tokens  UInt64                                     CODEC(ZSTD(1)),
    api_call_count         UInt64                                     CODEC(ZSTD(1))
)
ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(day)
ORDER BY (day, session_id, user_email, model_name, query_source)
TTL day + INTERVAL 365 DAY
SETTINGS index_granularity = 8192;


-- ---------------------------------------------------------------
-- coding_agent_session_identity — per-session email identity rollup.
-- Two levels: jwt_email (CodeMie JWT, primary) and developer_name (fallback).
-- SimpleAggregateFunction(max, String): engine merges by max(); since
-- '' < any valid email, max() always preserves a non-empty value across batches.
-- Note: AggregateFunction(anyLastIfState,...) was tried but caused
-- "Operator < not implemented for AggregateFunctionStateData" on CH 26.7.1.
-- Read via v_session_email which applies jwt_email → developer_name coalesce.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_session_identity
(
    session_id     String                              CODEC(ZSTD(1)),
    jwt_email      SimpleAggregateFunction(max, String),
    developer_name SimpleAggregateFunction(max, String)
)
ENGINE = AggregatingMergeTree()
ORDER BY session_id
SETTINGS index_granularity = 8192;


-- ---------------------------------------------------------------
-- coding_agent_lines_daily — lines_of_code.count rollup per session/day.
-- Split into added/removed columns so one SummingMergeTree merge covers both.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_lines_daily
(
    day            Date                    CODEC(ZSTD(1)),
    session_id     String                  CODEC(ZSTD(1)),
    user_email     String                  CODEC(ZSTD(1)),
    model_name     LowCardinality(String)  CODEC(ZSTD(1)),
    lines_added    UInt64                  CODEC(ZSTD(1)),
    lines_removed  UInt64                  CODEC(ZSTD(1))
)
ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(day)
ORDER BY (day, session_id, user_email, model_name)
TTL day + INTERVAL 365 DAY
SETTINGS index_granularity = 8192;


-- ---------------------------------------------------------------
-- coding_agent_active_time_daily — active_time.total rollup per session/day.
-- Split by type ('user' | 'cli'); handler sums both as total non-idle time.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_active_time_daily
(
    day             Date    CODEC(ZSTD(1)),
    session_id      String  CODEC(ZSTD(1)),
    user_email      String  CODEC(ZSTD(1)),
    active_ms_user  UInt64  CODEC(ZSTD(1)),
    active_ms_cli   UInt64  CODEC(ZSTD(1))
)
ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(day)
ORDER BY (day, session_id, user_email)
TTL day + INTERVAL 365 DAY
SETTINGS index_granularity = 8192;


-- ---------------------------------------------------------------
-- coding_agent_session_dims — pre-aggregated per-session dimensions.
-- Populated by mv_session_dims from coding_agent_hook_events.
-- Replaces the full-table aggregation previously done at read time
-- by the plain v_session_dimensions view: each cli-analytics query
-- that touches v_session_dimensions now reads ~89 merged rows
-- instead of re-aggregating all hook_event rows.
-- SimpleAggregateFunction for min/max timestamps; full AggregateFunction
-- for argMinIf (stable earliest-value semantics verified on CH 26.7.1).
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_session_dims
(
    session_id     String                                                     CODEC(ZSTD(1)),
    started_at     SimpleAggregateFunction(min, DateTime64(9)),
    last_event_at  SimpleAggregateFunction(max, DateTime64(9)),
    repository     AggregateFunction(argMinIf, String, DateTime64(9), UInt8),
    branch         AggregateFunction(argMinIf, String, DateTime64(9), UInt8),
    repo_remote    AggregateFunction(argMinIf, String, DateTime64(9), UInt8),
    project_name   AggregateFunction(argMinIf, String, DateTime64(9), UInt8),
    developer_name AggregateFunction(argMinIf, String, DateTime64(9), UInt8),
    first_prompt   AggregateFunction(argMinIf, String, DateTime64(9), UInt8)
)
ENGINE = AggregatingMergeTree()
ORDER BY session_id
SETTINGS index_granularity = 8192;


-- ---------------------------------------------------------------
-- coding_agent_turns_daily — pre-aggregated interaction-span counts per (day, session_id).
-- Enables get_turns_by_session to avoid scanning the raw coding_agent_traces table.
-- Pattern mirrors coding_agent_cost_daily / coding_agent_lines_daily.
-- Populated by mv_turns_daily from coding_agent_traces.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_turns_daily
(
    day        Date,
    session_id String,
    turns      UInt64
)
ENGINE = SummingMergeTree(turns)
PARTITION BY toYYYYMM(day)
ORDER BY (day, session_id)
SETTINGS index_granularity = 8192;


-- ---------------------------------------------------------------
-- coding_agent_file_facts_daily — pre-aggregated tool-span file/tool counts
-- per (day, session_id).  Enables get_file_facts_by_session and
-- get_session_detail_scalars to avoid scanning coding_agent_traces.
-- Also pre-aggregates agent_count and skill_count used by get_session_detail_scalars.
-- Populated by mv_file_facts_daily from coding_agent_traces.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_file_facts_daily
(
    day           Date,
    session_id    String,
    tool_calls    SimpleAggregateFunction(sum, UInt64),
    agent_count   SimpleAggregateFunction(sum, UInt64),
    skill_count   SimpleAggregateFunction(sum, UInt64),
    files_changed AggregateFunction(uniqExact, String),
    files_written AggregateFunction(uniqExact, String),
    files_edited  AggregateFunction(uniqExact, String)
)
ENGINE = AggregatingMergeTree()
PARTITION BY toYYYYMM(day)
ORDER BY (day, session_id)
SETTINGS index_granularity = 8192;


-- =============================================================
-- MATERIALIZED VIEWS  (must be created after target tables)
-- =============================================================

-- ---------------------------------------------------------------
-- mv_hook_events — routes Path B events from coding_agent_logs into
-- coding_agent_hook_events. Filters by materialized event_type column.
-- ---------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS codemie_analytics.mv_hook_events
TO codemie_analytics.coding_agent_hook_events
AS
SELECT
    Timestamp,
    session_id,                                           -- materialized: COALESCE(session.id, session_id)
    prompt_id,                                            -- materialized: COALESCE(prompt_id, prompt.id)
    event_type,                                           -- materialized: LogAttributes['event_type']
    developer_name,                                       -- materialized: LogAttributes['developer_name']
    codemie_project_name,                                 -- materialized: LogAttributes['codemie_project_name']
    LogAttributes['cwd']                                  AS cwd,
    LogAttributes['git_branch']                           AS git_branch,
    LogAttributes['repo_remote']                          AS repo_remote,
    LogAttributes['permission_mode']                      AS permission_mode,
    LogAttributes['source']                               AS source,
    LogAttributes['effort']                               AS effort,
    LogAttributes['tool_name']                            AS tool_name,
    LogAttributes['tool_use_id']                          AS tool_use_id,
    LogAttributes['tool_input']                           AS tool_input,
    LogAttributes['tool_output']                          AS tool_output,
    LogAttributes['error_message']                        AS error_message,
    LogAttributes['error_type']                           AS error_type,
    LogAttributes['reason']                               AS reason,
    LogAttributes['agent_id']                             AS agent_id,
    LogAttributes['agent_type']                           AS agent_type,
    LogAttributes['trigger']                              AS `trigger`,
    LogAttributes['denial_reason']                        AS denial_reason,
    LogAttributes['notification_type']                    AS notification_type,
    LogAttributes['prompt_body']                          AS prompt_body
FROM codemie_analytics.coding_agent_logs
WHERE event_type IN (
    'agent.session.start',
    'agent.session.stop',
    'agent.session.end',
    'agent.session.compact',
    'agent.prompt.submit',
    'agent.tool.start',
    'agent.tool.end',
    'agent.tool.error',
    'agent.tool.denied',
    'agent.turn.error',
    'agent.subagent.start',
    'agent.subagent.stop',
    'agent.notification'
);


-- ---------------------------------------------------------------
-- mv_cost_daily — aggregates Path A api_request records into daily cost rollups.
-- toFloat64OrZero/toUInt64OrZero guard against malformed strings during SDK transitions.
-- ---------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS codemie_analytics.mv_cost_daily
TO codemie_analytics.coding_agent_cost_daily
AS
SELECT
    toDate(Timestamp)                                     AS day,
    session_id,                                           -- materialized: COALESCE(session.id, session_id)
    user_email,                                           -- materialized: LogAttributes['user.email']
    model_name,                                           -- materialized: LogAttributes['model']
    query_source,                                         -- materialized: LogAttributes['query_source']
    toFloat64OrZero(LogAttributes['cost_usd'])            AS cost_usd,
    toUInt64OrZero(LogAttributes['input_tokens'])         AS input_tokens,
    toUInt64OrZero(LogAttributes['output_tokens'])        AS output_tokens,
    toUInt64OrZero(LogAttributes['cache_read_tokens'])    AS cache_read_tokens,
    toUInt64OrZero(LogAttributes['cache_creation_tokens']) AS cache_creation_tokens,
    1                                                     AS api_call_count
FROM codemie_analytics.coding_agent_logs
WHERE event_name = 'api_request';                         -- materialized: LogAttributes['event.name']


-- ---------------------------------------------------------------
-- mv_session_identity — accumulates jwt_email and developer_name per session.
-- Uses materialized columns (no Map re-parsing). max() on SimpleAggregateFunction
-- preserves non-empty values across INSERT batches.
-- ---------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS codemie_analytics.mv_session_identity
TO codemie_analytics.coding_agent_session_identity
AS
SELECT
    session_id,
    max(if(event_type != '' AND user_email != '', user_email, '')) AS jwt_email,
    max(if(developer_name != '', developer_name, ''))               AS developer_name
FROM codemie_analytics.coding_agent_logs
WHERE session_id != ''
GROUP BY session_id;


-- ---------------------------------------------------------------
-- mv_lines_daily — routes claude_code.lines_of_code.count metrics into daily rollup.
-- ---------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS codemie_analytics.mv_lines_daily
TO codemie_analytics.coding_agent_lines_daily
AS
SELECT
    toDate(TimeUnix)                                       AS day,
    Attributes['session.id']                               AS session_id,
    Attributes['user.email']                                AS user_email,
    Attributes['model']                                     AS model_name,
    if(Attributes['type'] = 'added',   toUInt64(Value), 0)  AS lines_added,
    if(Attributes['type'] = 'removed', toUInt64(Value), 0)  AS lines_removed
FROM codemie_analytics.coding_agent_metrics_sum
WHERE MetricName = 'claude_code.lines_of_code.count';


-- ---------------------------------------------------------------
-- mv_active_time_daily — routes claude_code.active_time.total metrics into daily rollup.
-- Value is in seconds (SDK); multiplied by 1000 to store as milliseconds.
-- ---------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS codemie_analytics.mv_active_time_daily
TO codemie_analytics.coding_agent_active_time_daily
AS
SELECT
    toDate(TimeUnix)                                     AS day,
    Attributes['session.id']                             AS session_id,
    Attributes['user.email']                              AS user_email,
    if(Attributes['type'] = 'user', toUInt64(Value * 1000), 0)   AS active_ms_user,
    if(Attributes['type'] = 'cli',  toUInt64(Value * 1000), 0)   AS active_ms_cli
FROM codemie_analytics.coding_agent_metrics_sum
WHERE MetricName = 'claude_code.active_time.total';


-- ---------------------------------------------------------------
-- mv_session_dims — routes hook events into coding_agent_session_dims.
-- Aggregates by session_id: first cwd/branch/project/developer via
-- argMinIfState (stable earliest-value semantics, same as old view).
-- argMinIfState(prompt_body,...) filters sentinel/system prompts.
-- Back-fill on first deploy: INSERT INTO coding_agent_session_dims
--   SELECT <same SELECT body> FROM coding_agent_hook_events WHERE session_id != '' GROUP BY session_id
-- ---------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS codemie_analytics.mv_session_dims
TO codemie_analytics.coding_agent_session_dims
AS
SELECT
    session_id,
    minSimpleState(Timestamp)    AS started_at,
    maxSimpleState(Timestamp)    AS last_event_at,
    argMinIfState(cwd,              Timestamp, cwd != '')              AS repository,
    argMinIfState(git_branch,       Timestamp, git_branch != '')       AS branch,
    argMinIfState(repo_remote,      Timestamp, repo_remote != '')      AS repo_remote,
    argMinIfState(codemie_project_name, Timestamp, codemie_project_name != '') AS project_name,
    argMinIfState(developer_name,   Timestamp, developer_name != '')   AS developer_name,
    argMinIfState(
        prompt_body, Timestamp,
        trimBoth(prompt_body) != ''
        AND lower(trimBoth(prompt_body)) NOT IN ('/clear', '/resume', '/compact', '/exit', '/quit')
        AND NOT startsWith(trimBoth(prompt_body), '<command-name>/clear')
        AND NOT startsWith(trimBoth(prompt_body), '<command-name>/resume')
        AND NOT startsWith(prompt_body, 'This session is being continued from a previous conversation')
        AND NOT startsWith(prompt_body, 'Caveat: The messages below were generated by the user while running local commands')
    )                                                                  AS first_prompt
FROM codemie_analytics.coding_agent_hook_events
WHERE session_id != ''
GROUP BY session_id;


-- ---------------------------------------------------------------
-- mv_turns_daily — routes claude_code.interaction spans into daily turns rollup.
-- ---------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS codemie_analytics.mv_turns_daily
TO codemie_analytics.coding_agent_turns_daily AS
SELECT
    toDate(Timestamp) AS day,
    session_id,
    count()           AS turns
FROM codemie_analytics.coding_agent_traces
WHERE SpanName = 'claude_code.interaction'
  AND session_id != ''
GROUP BY day, session_id;


-- ---------------------------------------------------------------
-- mv_file_facts_daily — routes claude_code.tool spans into daily file/tool fact rollup.
-- ---------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS codemie_analytics.mv_file_facts_daily
TO codemie_analytics.coding_agent_file_facts_daily AS
SELECT
    toDate(Timestamp)                                                              AS day,
    session_id,
    countIf(tool_name != '')                                                       AS tool_calls,
    countIf(subagent_type != '')                                                   AS agent_count,
    countIf(span_skill_name != '')                                                 AS skill_count,
    uniqExactStateIf(file_path, file_path != '')                                   AS files_changed,
    uniqExactStateIf(file_path, file_path != '' AND tool_name = 'Write')           AS files_written,
    uniqExactStateIf(
        file_path,
        file_path != '' AND tool_name IN ('Edit', 'MultiEdit', 'NotebookEdit')
    )                                                                              AS files_edited
FROM codemie_analytics.coding_agent_traces
WHERE SpanName = 'claude_code.tool'
  AND session_id != ''
GROUP BY day, session_id;


-- =============================================================
-- VIEWS
-- =============================================================

-- ---------------------------------------------------------------
-- v_session_email — resolves per-session email via 2-level coalesce:
--   jwt_email (CodeMie JWT, primary) → developer_name (git/OS user, fallback).
-- Usage: LEFT JOIN codemie_analytics.v_session_email e ON x.session_id = e.session_id
-- ---------------------------------------------------------------
CREATE VIEW IF NOT EXISTS codemie_analytics.v_session_email
AS
SELECT
    session_id,
    coalesce(
        nullIf(max(jwt_email), ''),
        nullIf(max(developer_name), '')
    ) AS resolved_email
FROM codemie_analytics.coding_agent_session_identity
GROUP BY session_id;


-- ---------------------------------------------------------------
-- v_session_dimensions — per-session dimension resolution shared by the
-- OTel CLI Analytics endpoints (overview, repositories, users, sessions,
-- efficiency, session detail).
--
-- Implementation (post-2026-08-24 optimisation):
--   Reads from coding_agent_session_dims (AggregatingMergeTree) rather than
--   scanning coding_agent_hook_events raw rows directly. At ~89 sessions the
--   difference is negligible, but at scale (10K+ sessions) scanning 9.7K+
--   aggregated rows is orders of magnitude cheaper than the full raw table.
--
-- Why argMinIf rather than anyLast(): `cwd` is NOT stable within a session.
--   Verified live 2026-08-20: 3 of 29 sessions carry multiple distinct cwd
--   values (one carries 6, as subagents and skills run from subdirectories).
--   anyLast() would attribute a session to a non-deterministic final directory.
--   argMinIf pins every session to the directory it STARTED in — stable and
--   reproducible across re-reads.
--
-- `repository` is the normalised project identifier emitted by the analytics
--   plugin: group/repo when a git remote is present, bare directory name
--   otherwise. The plugin is the authoritative normalisation site; no
--   path-stripping is applied here.
--
-- `repo_remote` covers ~59% of sessions, stored as emitted by the plugin.
--
-- first_prompt filters system-generated and slash-command sentinels so
--   session titles show a genuine user prompt.
--
-- Back-fill (first deploy):
--   INSERT INTO codemie_analytics.coding_agent_session_dims
--   SELECT session_id,
--          minSimpleState(Timestamp), maxSimpleState(Timestamp),
--          argMinIfState(cwd, Timestamp, cwd != ''),
--          argMinIfState(git_branch, Timestamp, git_branch != ''),
--          argMinIfState(repo_remote, Timestamp, repo_remote != ''),
--          argMinIfState(codemie_project_name, Timestamp, codemie_project_name != ''),
--          argMinIfState(developer_name, Timestamp, developer_name != ''),
--          argMinIfState(prompt_body, Timestamp,
--              trimBoth(prompt_body) != '' AND ...)
--   FROM codemie_analytics.coding_agent_hook_events
--   WHERE session_id != ''
--   GROUP BY session_id;
-- ---------------------------------------------------------------
CREATE OR REPLACE VIEW codemie_analytics.v_session_dimensions AS
SELECT
    session_id,
    argMinIfMerge(repository)      AS repository,
    argMinIfMerge(branch)         AS branch,
    argMinIfMerge(repo_remote)    AS repo_remote,
    argMinIfMerge(project_name)   AS project_name,
    argMinIfMerge(developer_name) AS developer_name,
    min(started_at)               AS started_at,
    max(last_event_at)            AS last_event_at,
    argMinIfMerge(first_prompt)   AS first_prompt
FROM codemie_analytics.coding_agent_session_dims
GROUP BY session_id;


-- =============================================================
-- SCHEMA EVOLUTION  (idempotent ALTER TABLE)
-- Bloom filter indexes on session_id for the three daily rollup tables.
-- These are queried by session_id without a leading day filter
-- (e.g. get_sessions_per_model_cost). ADD INDEX + MATERIALIZE INDEX
-- are both idempotent: ADD is a no-op if the index exists;
-- MATERIALIZE rebuilds existing parts (harmless on re-run).
-- =============================================================

ALTER TABLE codemie_analytics.coding_agent_cost_daily
    ADD INDEX IF NOT EXISTS idx_session_id session_id TYPE bloom_filter GRANULARITY 4;
ALTER TABLE codemie_analytics.coding_agent_cost_daily
    MATERIALIZE INDEX idx_session_id;

ALTER TABLE codemie_analytics.coding_agent_lines_daily
    ADD INDEX IF NOT EXISTS idx_session_id session_id TYPE bloom_filter GRANULARITY 4;
ALTER TABLE codemie_analytics.coding_agent_lines_daily
    MATERIALIZE INDEX idx_session_id;

ALTER TABLE codemie_analytics.coding_agent_active_time_daily
    ADD INDEX IF NOT EXISTS idx_session_id session_id TYPE bloom_filter GRANULARITY 4;
ALTER TABLE codemie_analytics.coding_agent_active_time_daily
    MATERIALIZE INDEX idx_session_id;


-- =============================================================
-- OTel Collector auto-created objects (informational — not managed here)
-- otelcol-contrib ClickHouse exporter creates these automatically
-- when create_schema: true is set. Documented for completeness.
--
-- coding_agent_traces_trace_id_ts
--   MergeTree, ORDER BY (TraceId, toUnixTimestamp(Start)).
--   TraceId → (Start, End) time-bounds index for efficient trace queries.
--
-- coding_agent_traces_trace_id_ts_mv
--   SELECT TraceId, min(Timestamp) AS Start, max(Timestamp) AS End
--   FROM coding_agent_traces WHERE TraceId != '' GROUP BY TraceId
-- =============================================================