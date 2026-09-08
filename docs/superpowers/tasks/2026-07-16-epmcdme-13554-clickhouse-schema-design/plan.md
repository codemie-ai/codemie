# Claude Code Analytics — ClickHouse Schema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `deployment/clickhouse/schema.sql` in the `codemie` backend repo — the single source of truth for all ClickHouse tables that power the `/v1/analytics/coding-agents/*` read endpoints and both data ingestion paths.

**Architecture:** Two data paths (Path A: native Claude Code OTel via OTLP/HTTP proxy → OTel Collector → ClickHouse; Path B: bash hook events → CodeMie API converts to OTel → same Collector) land in a single `codemie_analytics` database. Raw OTel tables match the otelcol-contrib ClickHouse exporter schema (column names, types, encoding). Derived tables and Materialized Views provide sub-second query performance for the 4 read endpoints without requiring scan-heavy GROUP BY queries at runtime.

**Tech Stack:** ClickHouse DDL, MergeTree / SummingMergeTree, Materialized Views, MATERIALIZED columns, ZSTD codec, bloom_filter indexes, otelcol-contrib 0.105.0 ClickHouse exporter schema conventions.

## Global Constraints

- All tables in database `codemie_analytics` (created if not exists).
- Table names use prefix `coding_agent_` (namespace isolation from future OTel sources).
- OTel Collector exporter is configured with `logs_table_name: coding_agent_logs`, `traces_table_name: coding_agent_traces`, `metrics_table_name: coding_agent_metrics` in the production collector config (separate task).
- All `CREATE TABLE` / `CREATE MATERIALIZED VIEW` / `CREATE DATABASE` statements use `IF NOT EXISTS` for idempotency.
- Raw tables (written by OTel Collector): 90-day TTL. Rollup tables: 365-day TTL.
- ZSTD(1) codec on all string/map columns. Delta codec on all timestamp columns.
- `LowCardinality(String)` for columns with < 1000 distinct values at scale (model names, event types, permission modes). Plain `String` for UUIDs, emails, and other high-cardinality values.
- Promoted MATERIALIZED columns for all fields used in ORDER BY, WHERE, or GROUP BY clauses — these are extracted once on insert rather than on every query.
- Bloom filter indexes on `session_id` and `user_email` for point-lookup queries (session detail endpoint, per-user cost endpoint).
- No changes to `codemie-ai-factory-analytics` POC — this schema is a new greenfield artifact.
- DDL source: `deployment/clickhouse/schema.sql`
- Smoke validation: `deployment/clickhouse/smoke.sh`

---

### Task 1: Database and raw OTel log table

**Files:**
- Create: `deployment/clickhouse/schema.sql`

**Interfaces:**
- Produces: `codemie_analytics.coding_agent_logs` table — consumed by Task 3 (MVs) and by the `/cost`, `/sessions`, `/users` read endpoints.

- [ ] **Step 1: Write the failing smoke query**

In `deployment/clickhouse/smoke.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
CH="${CLICKHOUSE_HOST:-localhost}"
CH_PORT="${CLICKHOUSE_PORT:-9000}"

run() { clickhouse-client --host "$CH" --port "$CH_PORT" --query "$1"; }

echo "=== Expect 0 (database does not exist yet) ==="
run "SELECT count() FROM system.databases WHERE name='codemie_analytics'"
```

Run: `bash deployment/clickhouse/smoke.sh`
Expected: prints `0` (database absent → RED baseline confirmed).

- [ ] **Step 2: Create schema.sql — database and `coding_agent_logs`**

```sql
-- =============================================================
-- CodeMie Analytics — ClickHouse Schema
-- Design spec: docs/superpowers/tasks/2026-07-16-epmcdme-13554-clickhouse-schema-design/
-- Ticket: EPMCDME-13554
-- =============================================================

CREATE DATABASE IF NOT EXISTS codemie_analytics;

-- ---------------------------------------------------------------
-- coding_agent_logs
-- Stores all OTel log records from both data paths:
--   Path A: native Claude Code api_request events (cost/token data).
--   Path B: bash hook events converted to OTel by the CodeMie API.
-- Written by the OTel Collector (otelcol-contrib 0.105.0).
-- Collector config: logs_table_name = 'coding_agent_logs'
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_logs
(
    Timestamp              DateTime64(9)                             CODEC(Delta, ZSTD(1)),
    TraceId                String                                    CODEC(ZSTD(1)),
    SpanId                 String                                    CODEC(ZSTD(1)),
    TraceFlags             UInt32                                    CODEC(ZSTD(1)),
    SeverityText           LowCardinality(String)                    CODEC(ZSTD(1)),
    SeverityNumber         Int32                                     CODEC(ZSTD(1)),
    ServiceName            LowCardinality(String)                    CODEC(ZSTD(1)),
    Body                   String                                    CODEC(ZSTD(1)),
    ResourceSchemaUrl      LowCardinality(String)                    CODEC(ZSTD(1)),
    ResourceAttributes     Map(LowCardinality(String), String)       CODEC(ZSTD(1)),
    ScopeSchemaUrl         LowCardinality(String)                    CODEC(ZSTD(1)),
    ScopeName              LowCardinality(String)                    CODEC(ZSTD(1)),
    ScopeVersion           LowCardinality(String)                    CODEC(ZSTD(1)),
    ScopeAttributes        Map(LowCardinality(String), String)       CODEC(ZSTD(1)),
    LogAttributes          Map(LowCardinality(String), String)       CODEC(ZSTD(1)),

    -- Promoted columns: extracted once on insert, stored for O(1) filter/sort.
    -- Path A: session.id (dot-notation, native OTel SDK)
    -- Path B: session_id (underscore, hook event after API conversion)
    -- COALESCE handles both key formats so ORDER BY / WHERE work uniformly.
    session_id             String
                               MATERIALIZED COALESCE(
                                   nullIf(LogAttributes['session.id'], ''),
                                   nullIf(LogAttributes['session_id'], '')
                               )                                     CODEC(ZSTD(1)),
    user_email             String
                               MATERIALIZED LogAttributes['user.email']  CODEC(ZSTD(1)),
    model_name             LowCardinality(String)
                               MATERIALIZED LogAttributes['model']    CODEC(ZSTD(1)),
    -- Path A api_request events use 'event.name' = 'api_request'.
    event_name             LowCardinality(String)
                               MATERIALIZED LogAttributes['event.name'] CODEC(ZSTD(1)),
    -- Path B hook events set 'event_type' = 'agent.session.start' etc.
    event_type             LowCardinality(String)
                               MATERIALIZED LogAttributes['event_type'] CODEC(ZSTD(1)),
    query_source           LowCardinality(String)
                               MATERIALIZED LogAttributes['query_source'] CODEC(ZSTD(1)),
    developer_name         LowCardinality(String)
                               MATERIALIZED LogAttributes['developer_name'] CODEC(ZSTD(1)),
    TimestampDate          Date
                               MATERIALIZED toDate(Timestamp),

    -- Bloom filter indexes: enables point-lookup queries without full-partition scan.
    INDEX idx_session_id   session_id  TYPE bloom_filter GRANULARITY 4,
    INDEX idx_user_email   user_email  TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(TimestampDate)
ORDER BY (user_email, TimestampDate, event_name, event_type, session_id, Timestamp)
TTL Timestamp + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;
```

- [ ] **Step 3: Apply and verify**

```bash
clickhouse-client --host localhost --port 9000 \
  --multiquery < deployment/clickhouse/schema.sql
clickhouse-client --host localhost --port 9000 \
  --query "SHOW TABLES FROM codemie_analytics"
```

Expected: `coding_agent_logs` appears in output.

- [ ] **Step 4: Commit**

```bash
git add deployment/clickhouse/schema.sql deployment/clickhouse/smoke.sh
git commit -m "EPMCDME-13554: Add codemie_analytics database and coding_agent_logs table"
```

---

### Task 2: Traces table and metrics tables

**Files:**
- Modify: `deployment/clickhouse/schema.sql`

**Interfaces:**
- Consumes: schema.sql from Task 1
- Produces: `coding_agent_traces` (tool/interaction spans, Path A enhanced telemetry beta) and `coding_agent_metrics_gauge` / `coding_agent_metrics_sum` (OTel metrics, Path A).

- [ ] **Step 1: Write failing query**

```bash
clickhouse-client --host localhost --port 9000 \
  --query "SELECT count() FROM system.tables WHERE database='codemie_analytics' AND name IN ('coding_agent_traces','coding_agent_metrics_gauge','coding_agent_metrics_sum')"
```

Expected: `0` (tables absent → RED).

- [ ] **Step 2: Append traces table to schema.sql**

```sql
-- ---------------------------------------------------------------
-- coding_agent_traces
-- Stores OTel spans when CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1.
-- Span names: claude_code.tool, claude_code.tool.execution,
--             claude_code.interaction
-- Collector config: traces_table_name = 'coding_agent_traces'
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_traces
(
    Timestamp              DateTime64(9)                             CODEC(Delta, ZSTD(1)),
    TraceId                String                                    CODEC(ZSTD(1)),
    SpanId                 String                                    CODEC(ZSTD(1)),
    ParentSpanId           String                                    CODEC(ZSTD(1)),
    TraceState             String                                    CODEC(ZSTD(1)),
    SpanName               LowCardinality(String)                    CODEC(ZSTD(1)),
    SpanKind               LowCardinality(String)                    CODEC(ZSTD(1)),
    ServiceName            LowCardinality(String)                    CODEC(ZSTD(1)),
    ResourceAttributes     Map(LowCardinality(String), String)       CODEC(ZSTD(1)),
    SpanAttributes         Map(LowCardinality(String), String)       CODEC(ZSTD(1)),
    Duration               Int64                                     CODEC(ZSTD(1)),
    StatusCode             LowCardinality(String)                    CODEC(ZSTD(1)),
    StatusMessage          String                                    CODEC(ZSTD(1)),
    `Events.Timestamp`     Array(DateTime64(9))                      CODEC(ZSTD(1)),
    `Events.Name`          Array(LowCardinality(String))             CODEC(ZSTD(1)),
    `Events.Attributes`    Array(Map(LowCardinality(String), String)) CODEC(ZSTD(1)),
    `Links.TraceId`        Array(String)                             CODEC(ZSTD(1)),
    `Links.SpanId`         Array(String)                             CODEC(ZSTD(1)),
    `Links.TraceState`     Array(String)                             CODEC(ZSTD(1)),
    `Links.Attributes`     Array(Map(LowCardinality(String), String)) CODEC(ZSTD(1)),

    -- Promoted columns for tool analytics endpoint
    session_id             String
                               MATERIALIZED SpanAttributes['session.id'] CODEC(ZSTD(1)),
    tool_name              LowCardinality(String)
                               MATERIALIZED SpanAttributes['tool_name'] CODEC(ZSTD(1)),
    tool_use_id            String
                               MATERIALIZED SpanAttributes['tool_use_id'] CODEC(ZSTD(1)),
    TimestampDate          Date
                               MATERIALIZED toDate(Timestamp),

    INDEX idx_session_id   session_id  TYPE bloom_filter GRANULARITY 4,
    INDEX idx_tool_name    tool_name   TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(TimestampDate)
ORDER BY (ServiceName, SpanName, TimestampDate, session_id, Timestamp)
TTL Timestamp + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;
```

- [ ] **Step 3: Append metrics tables to schema.sql**

```sql
-- ---------------------------------------------------------------
-- coding_agent_metrics_gauge / _sum
-- OTel metrics from Path A: claude_code.lines_of_code.count,
-- session/token counters.
-- otelcol-contrib creates one table per metric type using the
-- base name as prefix. Only gauge and sum are expected from
-- Claude Code SDK; histogram/exponential_histogram omitted.
-- Collector config: metrics_table_name = 'coding_agent_metrics'
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_metrics_gauge
(
    ResourceAttributes     Map(LowCardinality(String), String)       CODEC(ZSTD(1)),
    ResourceSchemaUrl      LowCardinality(String)                    CODEC(ZSTD(1)),
    ScopeAttributes        Map(LowCardinality(String), String)       CODEC(ZSTD(1)),
    ScopeSchemaUrl         LowCardinality(String)                    CODEC(ZSTD(1)),
    ScopeName              LowCardinality(String)                    CODEC(ZSTD(1)),
    ScopeVersion           LowCardinality(String)                    CODEC(ZSTD(1)),
    MetricName             LowCardinality(String)                    CODEC(ZSTD(1)),
    MetricDescription      String                                    CODEC(ZSTD(1)),
    MetricUnit             LowCardinality(String)                    CODEC(ZSTD(1)),
    Attributes             Map(LowCardinality(String), String)       CODEC(ZSTD(1)),
    StartTimeUnix          DateTime64(9)                             CODEC(Delta, ZSTD(1)),
    TimeUnix               DateTime64(9)                             CODEC(Delta, ZSTD(1)),
    Value                  Float64                                   CODEC(ZSTD(1)),
    Flags                  UInt32                                    CODEC(ZSTD(1)),
    `Exemplars.FilteredAttributes` Array(Map(LowCardinality(String), String)) CODEC(ZSTD(1)),
    `Exemplars.TimeUnix`   Array(DateTime64(9))                      CODEC(ZSTD(1)),
    `Exemplars.Value`      Array(Float64)                            CODEC(ZSTD(1)),
    `Exemplars.SpanId`     Array(String)                             CODEC(ZSTD(1)),
    `Exemplars.TraceId`    Array(String)                             CODEC(ZSTD(1))
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(toDate(TimeUnix))
ORDER BY (MetricName, Attributes, TimeUnix)
TTL TimeUnix + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_metrics_sum
(
    ResourceAttributes     Map(LowCardinality(String), String)       CODEC(ZSTD(1)),
    ResourceSchemaUrl      LowCardinality(String)                    CODEC(ZSTD(1)),
    ScopeAttributes        Map(LowCardinality(String), String)       CODEC(ZSTD(1)),
    ScopeSchemaUrl         LowCardinality(String)                    CODEC(ZSTD(1)),
    ScopeName              LowCardinality(String)                    CODEC(ZSTD(1)),
    ScopeVersion           LowCardinality(String)                    CODEC(ZSTD(1)),
    MetricName             LowCardinality(String)                    CODEC(ZSTD(1)),
    MetricDescription      String                                    CODEC(ZSTD(1)),
    MetricUnit             LowCardinality(String)                    CODEC(ZSTD(1)),
    Attributes             Map(LowCardinality(String), String)       CODEC(ZSTD(1)),
    StartTimeUnix          DateTime64(9)                             CODEC(Delta, ZSTD(1)),
    TimeUnix               DateTime64(9)                             CODEC(Delta, ZSTD(1)),
    Value                  Float64                                   CODEC(ZSTD(1)),
    Flags                  UInt32                                    CODEC(ZSTD(1)),
    IsMonotonic            UInt8                                     CODEC(ZSTD(1)),
    AggregationTemporality LowCardinality(String)                    CODEC(ZSTD(1)),
    `Exemplars.FilteredAttributes` Array(Map(LowCardinality(String), String)) CODEC(ZSTD(1)),
    `Exemplars.TimeUnix`   Array(DateTime64(9))                      CODEC(ZSTD(1)),
    `Exemplars.Value`      Array(Float64)                            CODEC(ZSTD(1)),
    `Exemplars.SpanId`     Array(String)                             CODEC(ZSTD(1)),
    `Exemplars.TraceId`    Array(String)                             CODEC(ZSTD(1))
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(toDate(TimeUnix))
ORDER BY (MetricName, Attributes, TimeUnix)
TTL TimeUnix + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;
```

- [ ] **Step 4: Apply and verify**

```bash
clickhouse-client --host localhost --port 9000 \
  --multiquery < deployment/clickhouse/schema.sql
clickhouse-client --host localhost --port 9000 \
  --query "SELECT count() FROM system.tables WHERE database='codemie_analytics' AND name IN ('coding_agent_traces','coding_agent_metrics_gauge','coding_agent_metrics_sum')"
```

Expected: `3`.

- [ ] **Step 5: Commit**

```bash
git add deployment/clickhouse/schema.sql
git commit -m "EPMCDME-13554: Add coding_agent_traces and metrics_gauge/sum tables"
```

---

### Task 3: Derived tables and Materialized Views

**Files:**
- Modify: `deployment/clickhouse/schema.sql`

**Interfaces:**
- Consumes: `coding_agent_logs` (Task 1)
- Produces:
  - `coding_agent_hook_events` — fast timeline queries for the `/sessions/:id` endpoint
  - `coding_agent_cost_daily` — pre-aggregated cost for the `/cost` and `/users` endpoints
  - `mv_hook_events` MV routing Path B events from `coding_agent_logs` → `coding_agent_hook_events`
  - `mv_cost_daily` MV routing `api_request` events from `coding_agent_logs` → `coding_agent_cost_daily`

- [ ] **Step 1: Write failing query**

```bash
clickhouse-client --host localhost --port 9000 \
  --query "SELECT count() FROM system.tables WHERE database='codemie_analytics' AND name IN ('coding_agent_hook_events','coding_agent_cost_daily')"
```

Expected: `0` (tables absent → RED).

- [ ] **Step 2: Append `coding_agent_hook_events` to schema.sql**

```sql
-- ---------------------------------------------------------------
-- coding_agent_hook_events
-- Extracted Path B hook events for efficient session timeline
-- queries. Populated via mv_hook_events from coding_agent_logs.
-- Keeps only Path B fields; avoids scanning full OTel log rows.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_hook_events
(
    Timestamp              DateTime64(9)                             CODEC(Delta, ZSTD(1)),
    session_id             String                                    CODEC(ZSTD(1)),
    event_type             LowCardinality(String)                    CODEC(ZSTD(1)),
    developer_name         LowCardinality(String)                    CODEC(ZSTD(1)),
    cwd                    String                                    CODEC(ZSTD(1)),
    git_branch             LowCardinality(String)                    CODEC(ZSTD(1)),
    permission_mode        LowCardinality(String)                    CODEC(ZSTD(1)),
    turn_number            UInt32                                    CODEC(ZSTD(1)),
    tool_name              LowCardinality(String)                    CODEC(ZSTD(1)),
    tool_use_id            String                                    CODEC(ZSTD(1)),
    tool_input             String                                    CODEC(ZSTD(1)),
    tool_output            String                                    CODEC(ZSTD(1)),
    error_message          String                                    CODEC(ZSTD(1)),
    TimestampDate          Date                  MATERIALIZED toDate(Timestamp),

    INDEX idx_session_id   session_id  TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(TimestampDate)
ORDER BY (developer_name, TimestampDate, session_id, event_type, Timestamp)
TTL Timestamp + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;
```

- [ ] **Step 3: Append `coding_agent_cost_daily` to schema.sql**

```sql
-- ---------------------------------------------------------------
-- coding_agent_cost_daily
-- Daily cost rollup for /cost and /users endpoints.
-- SummingMergeTree automatically merges rows with the same
-- ORDER BY key, summing numeric columns. Queried with
-- GROUP BY day, user_email, model_name, query_source +
-- sumIf guards for correct aggregation over unmerged parts.
-- TTL is 365 days: rollups survive longer than raw 90-day data.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_cost_daily
(
    day                    Date                                      CODEC(ZSTD(1)),
    user_email             String                                    CODEC(ZSTD(1)),
    model_name             LowCardinality(String)                    CODEC(ZSTD(1)),
    query_source           LowCardinality(String)                    CODEC(ZSTD(1)),
    cost_usd               Float64                                   CODEC(ZSTD(1)),
    input_tokens           UInt64                                    CODEC(ZSTD(1)),
    output_tokens          UInt64                                    CODEC(ZSTD(1)),
    cache_read_tokens      UInt64                                    CODEC(ZSTD(1)),
    cache_creation_tokens  UInt64                                    CODEC(ZSTD(1)),
    api_call_count         UInt64                                    CODEC(ZSTD(1))
)
ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(day)
ORDER BY (day, user_email, model_name, query_source)
TTL day + INTERVAL 365 DAY
SETTINGS index_granularity = 8192;
```

- [ ] **Step 4: Append Materialized Views to schema.sql**

```sql
-- ---------------------------------------------------------------
-- mv_hook_events
-- Routes Path B hook events from coding_agent_logs into the
-- coding_agent_hook_events table on insert.
-- Filter: LogAttributes['event_type'] identifies Path B events.
-- ---------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS codemie_analytics.mv_hook_events
TO codemie_analytics.coding_agent_hook_events
AS
SELECT
    Timestamp,
    -- Use the promoted column (COALESCE of both key formats)
    LogAttributes['session_id']                          AS session_id,
    LogAttributes['event_type']                          AS event_type,
    LogAttributes['developer_name']                      AS developer_name,
    LogAttributes['cwd']                                 AS cwd,
    LogAttributes['git_branch']                          AS git_branch,
    LogAttributes['permission_mode']                     AS permission_mode,
    toUInt32OrZero(LogAttributes['turn_number'])         AS turn_number,
    LogAttributes['tool_name']                           AS tool_name,
    LogAttributes['tool_use_id']                         AS tool_use_id,
    LogAttributes['tool_input']                          AS tool_input,
    LogAttributes['tool_output']                         AS tool_output,
    LogAttributes['error_message']                       AS error_message
FROM codemie_analytics.coding_agent_logs
WHERE LogAttributes['event_type'] IN (
    'agent.session.start',
    'agent.session.stop',
    'agent.prompt.submit',
    'agent.tool.start',
    'agent.tool.end',
    'agent.tool.error'
);

-- ---------------------------------------------------------------
-- mv_cost_daily
-- Aggregates api_request log records into per-day cost rollups.
-- toFloat64OrZero guards against malformed cost_usd strings.
-- ---------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS codemie_analytics.mv_cost_daily
TO codemie_analytics.coding_agent_cost_daily
AS
SELECT
    toDate(Timestamp)                                    AS day,
    LogAttributes['user.email']                          AS user_email,
    LogAttributes['model']                               AS model_name,
    LogAttributes['query_source']                        AS query_source,
    toFloat64OrZero(LogAttributes['cost_usd'])           AS cost_usd,
    toUInt64OrZero(LogAttributes['input_tokens'])        AS input_tokens,
    toUInt64OrZero(LogAttributes['output_tokens'])       AS output_tokens,
    toUInt64OrZero(LogAttributes['cache_read_tokens'])   AS cache_read_tokens,
    toUInt64OrZero(LogAttributes['cache_creation_tokens']) AS cache_creation_tokens,
    1                                                    AS api_call_count
FROM codemie_analytics.coding_agent_logs
WHERE LogAttributes['event.name'] = 'api_request';
```

- [ ] **Step 5: Apply and verify**

```bash
clickhouse-client --host localhost --port 9000 \
  --multiquery < deployment/clickhouse/schema.sql
clickhouse-client --host localhost --port 9000 \
  --query "SELECT count() FROM system.tables WHERE database='codemie_analytics' AND name IN ('coding_agent_hook_events','coding_agent_cost_daily')"
```

Expected: `2`.

```bash
clickhouse-client --host localhost --port 9000 \
  --query "SELECT count() FROM system.tables WHERE database='codemie_analytics' AND engine='MaterializedView'"
```

Expected: `2` (`mv_hook_events`, `mv_cost_daily`).

- [ ] **Step 6: Commit**

```bash
git add deployment/clickhouse/schema.sql
git commit -m "EPMCDME-13554: Add hook_events, cost_daily tables and materialized views"
```

---

### Task 4: Smoke test script

**Files:**
- Modify: `deployment/clickhouse/smoke.sh`

**Interfaces:**
- Consumes: complete `schema.sql` from Tasks 1–3
- Produces: a self-contained validation script that asserts all 7 objects exist and the MV routing works end-to-end.

- [ ] **Step 1: Run current smoke.sh — expect partial GREEN**

After Tasks 1–3 the database exists but the smoke test only checks for `codemie_analytics`. We extend it to assert all tables.

```bash
bash deployment/clickhouse/smoke.sh
```

Expected: prints `0` at the "expect 0" line (before schema apply) — current invocation doesn't re-drop, so this should now print `1` (database exists) — not 0. That confirms the previous apply succeeded.

- [ ] **Step 2: Rewrite smoke.sh with full assertions**

```bash
#!/usr/bin/env bash
# Validates that all codemie_analytics schema objects exist and the
# MV end-to-end pipeline is functional.
# Usage: CLICKHOUSE_HOST=localhost bash deployment/clickhouse/smoke.sh
set -euo pipefail

CH="${CLICKHOUSE_HOST:-localhost}"
PORT="${CLICKHOUSE_PORT:-9000}"

ch() { clickhouse-client --host "$CH" --port "$PORT" --query "$1"; }

echo "--- Apply schema (idempotent) ---"
clickhouse-client --host "$CH" --port "$PORT" --multiquery < "$(dirname "$0")/schema.sql"

echo "--- Verify database ---"
DB_COUNT=$(ch "SELECT count() FROM system.databases WHERE name='codemie_analytics'")
[ "$DB_COUNT" = "1" ] || { echo "FAIL: codemie_analytics database missing"; exit 1; }

echo "--- Verify raw tables ---"
TABLES=$(ch "SELECT name FROM system.tables WHERE database='codemie_analytics' AND engine='MergeTree' ORDER BY name" | tr '\n' ',')
for T in coding_agent_cost_daily coding_agent_hook_events coding_agent_logs coding_agent_metrics_gauge coding_agent_metrics_sum coding_agent_traces; do
  echo "$TABLES" | grep -q "$T" || { echo "FAIL: table $T missing"; exit 1; }
done

echo "--- Verify materialized views ---"
MV_COUNT=$(ch "SELECT count() FROM system.tables WHERE database='codemie_analytics' AND engine='MaterializedView'")
[ "$MV_COUNT" = "2" ] || { echo "FAIL: expected 2 MVs, got $MV_COUNT"; exit 1; }

echo "--- End-to-end MV routing: api_request → cost_daily ---"
ch "INSERT INTO codemie_analytics.coding_agent_logs (Timestamp, ServiceName, LogAttributes) VALUES (now64(), 'claude-code', map('event.name','api_request','user.email','test@example.com','model','claude-sonnet-4-6','cost_usd','0.001','input_tokens','100','output_tokens','50','cache_read_tokens','0','cache_creation_tokens','0','query_source','repl_main_thread','session.id','smoke-session-001'))"
sleep 1  # allow MV to process
COST_ROW=$(ch "SELECT count() FROM codemie_analytics.coding_agent_cost_daily WHERE user_email='test@example.com'")
[ "$COST_ROW" = "1" ] || { echo "FAIL: MV mv_cost_daily did not route api_request row; got $COST_ROW rows"; exit 1; }

echo "--- End-to-end MV routing: hook event → hook_events ---"
ch "INSERT INTO codemie_analytics.coding_agent_logs (Timestamp, ServiceName, LogAttributes) VALUES (now64(), 'codemie-api', map('event_type','agent.session.start','session_id','smoke-session-001','developer_name','test@example.com','cwd','/tmp','git_branch','main','permission_mode','default'))"
sleep 1
HOOK_ROW=$(ch "SELECT count() FROM codemie_analytics.coding_agent_hook_events WHERE session_id='smoke-session-001'")
[ "$HOOK_ROW" = "1" ] || { echo "FAIL: MV mv_hook_events did not route hook event row; got $HOOK_ROW rows"; exit 1; }

echo "--- Cleanup smoke data ---"
ch "ALTER TABLE codemie_analytics.coding_agent_logs DELETE WHERE session_id='smoke-session-001' OR LogAttributes['session.id']='smoke-session-001'"
ch "ALTER TABLE codemie_analytics.coding_agent_cost_daily DELETE WHERE user_email='test@example.com'"
ch "ALTER TABLE codemie_analytics.coding_agent_hook_events DELETE WHERE session_id='smoke-session-001'"

echo "=== ALL CHECKS PASSED ==="
```

- [ ] **Step 3: Run smoke test**

```bash
bash deployment/clickhouse/smoke.sh
```

Expected: `=== ALL CHECKS PASSED ===`

- [ ] **Step 4: Commit**

```bash
git add deployment/clickhouse/smoke.sh
git commit -m "EPMCDME-13554: Add smoke test for schema validation"
```
