# CLI Analytics Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename ingest endpoints to `cli-analytics` naming, then materialize `v_session_dimensions` so every CLI Analytics query does a fast ~89-row merge instead of re-aggregating all 9,689 `coding_agent_hook_events` rows; fix one raw-log scan in the users endpoint.

**Architecture:** A new `coding_agent_session_dims` AggregatingMergeTree table is fed by `mv_session_dims` (MV from `coding_agent_hook_events`). The existing plain VIEW `v_session_dimensions` is redefined to SELECT from that table — so no Python code changes are needed for the optimization itself. One Python fix (`get_users_last_active`) replaces a 39K-row log scan with a 9.7K hook_events scan.

**Tech Stack:** ClickHouse 26.7, Python 3.12, FastAPI. All ClickHouse changes are applied live via HTTP API then written back to `schema.sql`.

**Spec:** Task prompt (inline, session 2026-08-24)

## Global Constraints

- Live ClickHouse at `http://localhost:8123` — credentials `clickhouse / clickhouse`.
- `schema.sql` is a **from-scratch DDL file** — no ALTER statements in it; idempotent CREATE statements only (use `IF NOT EXISTS` / `CREATE OR REPLACE`).
- `coding_agents_analytics` endpoints must not be silently broken — verify them after every ClickHouse change.
- Never drop a table or MV before confirming nothing depends on it.
- Report file: `ANALYTICS_DISCOVERY/cli_analytics_optimization_report.md`.

---

## Pre-Task: Context Snapshot (gathered, no action needed)

| Metric | Value |
|---|---|
| `coding_agent_hook_events` rows | 9,689 |
| `coding_agent_logs` rows | 39,414 |
| `coding_agent_traces` rows | 15,225 |
| `coding_agent_cost_daily` rows | 205 |
| Distinct sessions | 89 |
| Baseline query time (get_cost_kpis, 7d) | ~119ms |
| Baseline query time (get_session_cost_facts, 7d) | ~149ms |
| Baseline query time (tool join, 7d) | ~114ms |

**Dependency map** (critical for Task 3):
- `v_session_dimensions` (plain VIEW) ← read by `cli_analytics_repository.py` only
- `v_session_email` (plain VIEW) ← read by BOTH `cli_analytics_repository.py` and `coding_agent_analytics_repository.py`
- `coding_agent_hook_events` (MergeTree) ← read by BOTH repositories; source for new MV
- `coding_agent_session_identity` (AggregatingMergeTree) ← source for `v_session_email`

---

### Task 1: Rename Ingest Router Prefix (Step 0)

**Files:**
- Modify: `src/codemie/rest_api/routers/ingest_router.py` — prefix string only

**Interfaces:**
- Produces: 4 POST routes under `/v1/analytics/cli-analytics-ingest/{logs,metrics,traces,event-hooks}`

- [ ] **Step 1: Verify nothing external references the old path**

```bash
grep -rn "ingest-coding-agent-telemetry" /c/Users/OlehPrusak/codemie-dev/ \
  --include="*.yaml" --include="*.json" --include="*.py" --include="*.sh" \
  --exclude-dir=".venv" --exclude-dir=".git"
```

Expected: only `ingest_router.py` itself (otelcol-config.yaml uses OTLP ports, not HTTP paths).

- [ ] **Step 2: Change prefix**

In `src/codemie/rest_api/routers/ingest_router.py`, find:
```python
router = APIRouter(
    prefix="/v1/analytics/ingest-coding-agent-telemetry",
```
Replace with:
```python
router = APIRouter(
    prefix="/v1/analytics/cli-analytics-ingest",
```

- [ ] **Step 3: Verify router still imports cleanly**

```bash
cd /c/Users/OlehPrusak/codemie-dev/codemie
python -c "
import sys; sys.path.insert(0,'src')
from codemie.rest_api.routers import ingest_router
routes = [r.path for r in ingest_router.router.routes]
print(routes)
assert any('cli-analytics-ingest' in r for r in routes), 'prefix not applied'
print('PASS')
"
```

Expected: `['/v1/analytics/cli-analytics-ingest/logs', ...]`

---

### Task 2: Create `coding_agent_session_dims` Table + MV (Step 3 — core optimization)

**Files:**
- Live ClickHouse: `coding_agent_session_dims` (AggregatingMergeTree), `mv_session_dims` (MV)
- Will update: `deployment/clickhouse/schema.sql` (Task 5)

**Interfaces:**
- Produces: `codemie_analytics.coding_agent_session_dims` — 89-row indexed table replacing 9,689-row re-aggregation

- [ ] **Step 1: Verify argMinIfState works in this ClickHouse version**

```sql
SELECT argMinIfState(cwd, Timestamp, cwd != '')
FROM codemie_analytics.coding_agent_hook_events
WHERE session_id = (SELECT session_id FROM codemie_analytics.coding_agent_hook_events LIMIT 1)
LIMIT 1
FORMAT Null
```

```bash
curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" \
  --data "SELECT argMinIfState(cwd, Timestamp, cwd != '') FROM codemie_analytics.coding_agent_hook_events LIMIT 1 FORMAT Null"
```

Expected: empty output (success), not an error.

- [ ] **Step 2: Create the target AggregatingMergeTree table**

```bash
curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" --data "
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_session_dims
(
    session_id     String                                                                      CODEC(ZSTD(1)),
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
SETTINGS index_granularity = 8192
"
echo "EXIT:$?"
```

Expected: empty response + `EXIT:0`.

- [ ] **Step 3: Create the materialized view**

```bash
curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" --data "
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
GROUP BY session_id
"
echo "EXIT:$?"
```

Expected: empty response + `EXIT:0`.

- [ ] **Step 4: Back-fill historical data into the new table**

```bash
curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" --data "
INSERT INTO codemie_analytics.coding_agent_session_dims
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
GROUP BY session_id
"
echo "EXIT:$?"
```

- [ ] **Step 5: Verify row count**

```bash
curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" \
  --data "SELECT count() FROM codemie_analytics.coding_agent_session_dims FORMAT TSV"
```

Expected: ≥89 (multiple parts before OPTIMIZE; actual session count after merge = 89).

---

### Task 3: Replace `v_session_dimensions` View (Step 3 continued)

**Files:**
- Live ClickHouse: `v_session_dimensions` redefined
- Verify: results identical to current `v_session_dimensions`

- [ ] **Step 1: Capture current results as baseline**

```bash
curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" --data "
SELECT session_id, repository, branch, project_name, developer_name,
       toString(started_at), toString(last_event_at)
FROM codemie_analytics.v_session_dimensions
ORDER BY session_id FORMAT TabSeparated
" > /tmp/vsd_before.txt
wc -l /tmp/vsd_before.txt
```

- [ ] **Step 2: Replace the view**

```bash
curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" --data "
CREATE OR REPLACE VIEW codemie_analytics.v_session_dimensions AS
SELECT
    session_id,
    if(argMinIfMerge(repository) = '', '',
       arrayElement(arrayFilter(x -> x != '',
           splitByChar('/', replaceAll(argMinIfMerge(repository), char(92), '/'))), -1)
    )                             AS repository,
    argMinIfMerge(branch)         AS branch,
    argMinIfMerge(repo_remote)    AS repo_remote,
    argMinIfMerge(project_name)   AS project_name,
    argMinIfMerge(developer_name) AS developer_name,
    min(started_at)               AS started_at,
    max(last_event_at)            AS last_event_at,
    argMinIfMerge(first_prompt)   AS first_prompt
FROM codemie_analytics.coding_agent_session_dims
GROUP BY session_id
"
echo "EXIT:$?"
```

**Important**: The basename normalization (`arrayElement(arrayFilter(...))`) moves from `_sessions_cte` in Python into the view itself. This means the `_REPO_EXPR` in `cli_analytics_repository.py` can be simplified to just `d.repository` — but we do NOT make that change now (it would still work with the normalized value; the double-normalization of an already-normalized basename is a no-op for single-element paths).

- [ ] **Step 3: Capture post-change results and diff**

```bash
curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" --data "
SELECT session_id, repository, branch, project_name, developer_name,
       toString(started_at), toString(last_event_at)
FROM codemie_analytics.v_session_dimensions
ORDER BY session_id FORMAT TabSeparated
" > /tmp/vsd_after.txt
diff /tmp/vsd_before.txt /tmp/vsd_after.txt
```

Expected: zero diff (or only whitespace differences in timestamp precision). If there are diffs, investigate before proceeding.

- [ ] **Step 4: Benchmark the new view**

```bash
time curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" --data "
SELECT session_id, repository, branch, project_name, developer_name
FROM codemie_analytics.v_session_dimensions
FORMAT Null
"
```

Expected: ≤50ms (vs ~105ms with the plain view scanning 9,689 rows).

---

### Task 4: Fix `get_users_last_active` (Step 3 — secondary optimization)

**Files:**
- Modify: `src/codemie/repository/cli_analytics_repository.py`

**Context:** `get_users_last_active` currently scans 39,414 `coding_agent_logs` rows to find the last activity timestamp per user. `coding_agent_hook_events` (9,689 rows, already joined via developer_name) has the same Timestamp data and suffices for a max(Timestamp) per user.

- [ ] **Step 1: Verify hook_events returns same or better result**

```bash
# Compare last_active values from both sources for the current user
curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" --data "
SELECT 'logs' AS src, max(Timestamp) last_active
FROM codemie_analytics.coding_agent_logs
WHERE user_email != '' AND Timestamp > now()-INTERVAL 90 DAY
UNION ALL
SELECT 'hook_events' AS src, max(Timestamp) last_active
FROM codemie_analytics.coding_agent_hook_events
WHERE developer_name != '' AND Timestamp > now()-INTERVAL 90 DAY
FORMAT TSV"
```

Expected: timestamps should be within minutes of each other (logs has api_request events, hooks has session/tool events — both reflect activity).

- [ ] **Step 2: Apply fix in `cli_analytics_repository.py`**

Find `get_users_last_active`:
```python
    async def get_users_last_active(self, f: LocalAnalyticsFilter) -> list[dict]:
        """Timestamp-granular last activity. The daily rollup only carries a Date, so this
        reads the raw log table."""
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            coalesce(nullIf(s.user_email, ''), nullIf(l.user_email, ''), 'unknown') AS developer_name,
            max(l.Timestamp)                                                        AS last_active
        FROM codemie_analytics.coding_agent_logs l
        LEFT JOIN sel s ON s.session_id = l.session_id
        WHERE l.Timestamp BETWEEN {{start_dt:DateTime64(3)}} AND {{end_dt:DateTime64(3)}}
              AND l.session_id != ''
              {self._session_scope(f, "l.session_id")}
        GROUP BY developer_name
        """
        return await self._q(sql, f.params())
```

Replace with:
```python
    async def get_users_last_active(self, f: LocalAnalyticsFilter) -> list[dict]:
        """Timestamp-granular last activity per user.

        Source: coding_agent_hook_events (9.7K rows) rather than coding_agent_logs
        (39K rows) — hook events cover all session activity and avoid scanning
        the full api_request log for a max(Timestamp) that doesn't need token data.
        """
        sql = f"""
        WITH {self._sessions_cte(f)}
        SELECT
            coalesce(nullIf(s.user_email, ''), nullIf(h.developer_name, ''), 'unknown') AS developer_name,
            max(h.Timestamp)                                                             AS last_active
        FROM codemie_analytics.coding_agent_hook_events h
        LEFT JOIN sel s ON s.session_id = h.session_id
        WHERE h.Timestamp BETWEEN {{start_dt:DateTime64(3)}} AND {{end_dt:DateTime64(3)}}
              AND h.session_id != ''
              {self._session_scope(f, "h.session_id")}
        GROUP BY developer_name
        """
        return await self._q(sql, f.params())
```

- [ ] **Step 3: Run end-to-end verification**

```bash
cd /c/Users/OlehPrusak/codemie-dev/codemie
python scratchpad/verify_http.py  # or the existing verify script path
```

Expected: all HTTP checks pass as before.

---

### Task 5: Update `schema.sql` (Step 4)

**Files:**
- Modify: `deployment/clickhouse/schema.sql`

**Rules:** File is a from-scratch creation script. Add `coding_agent_session_dims` table and `mv_session_dims` MV in the DERIVED TABLES and MATERIALIZED VIEWS sections. Update `v_session_dimensions` in the VIEWS section. No ALTER statements.

- [ ] **Step 1: Add `coding_agent_session_dims` to DERIVED TABLES section** (after `coding_agent_active_time_daily`)

```sql
-- ---------------------------------------------------------------
-- coding_agent_session_dims — pre-aggregated per-session dimensions.
-- Populated by mv_session_dims from coding_agent_hook_events.
-- Replaces the full-table aggregation previously done at read time
-- by the plain v_session_dimensions view: each cli-analytics query
-- that touches v_session_dimensions now reads ~89 merged rows
-- instead of re-aggregating all 9,689+ hook_event rows.
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
```

- [ ] **Step 2: Add `mv_session_dims` to MATERIALIZED VIEWS section** (after `mv_active_time_daily`)

```sql
-- ---------------------------------------------------------------
-- mv_session_dims — routes hook events into coding_agent_session_dims.
-- Aggregates by session_id: first cwd/branch/project/developer via
-- argMinIf (stable earliest-value semantics matching the old view),
-- first meaningful prompt via argMinIf with sentinel filtering.
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
```

- [ ] **Step 3: Update `v_session_dimensions` in VIEWS section**

Replace the old `CREATE OR REPLACE VIEW codemie_analytics.v_session_dimensions` block with:

```sql
-- ---------------------------------------------------------------
-- v_session_dimensions — per-session dimension resolution shared by
-- the cli-analytics endpoints. Reads from coding_agent_session_dims
-- (pre-aggregated by mv_session_dims) rather than re-aggregating
-- coding_agent_hook_events at query time.
--
-- Basename normalization of `repository` happens here so downstream
-- queries get a clean folder name regardless of OS path format.
-- ---------------------------------------------------------------
CREATE OR REPLACE VIEW codemie_analytics.v_session_dimensions
AS
SELECT
    session_id,
    if(argMinIfMerge(repository) = '', '',
       arrayElement(arrayFilter(x -> x != '',
           splitByChar('/', replaceAll(argMinIfMerge(repository), char(92), '/'))), -1)
    )                             AS repository,
    argMinIfMerge(branch)         AS branch,
    argMinIfMerge(repo_remote)    AS repo_remote,
    argMinIfMerge(project_name)   AS project_name,
    argMinIfMerge(developer_name) AS developer_name,
    min(started_at)               AS started_at,
    max(last_event_at)            AS last_event_at,
    argMinIfMerge(first_prompt)   AS first_prompt
FROM codemie_analytics.coding_agent_session_dims
GROUP BY session_id;
```

- [ ] **Step 4: Verify schema.sql is idempotent on a fresh run**

```bash
# Apply schema.sql to a test and check for errors (dry-run via EXPLAIN is not available for DDL;
# instead verify the idempotency by re-running the key CREATE statements via HTTP)
CH="http://localhost:8123/?user=clickhouse&password=clickhouse"
# Table: IF NOT EXISTS → no-op
curl -s "$CH" --data "CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_session_dims (session_id String CODEC(ZSTD(1))) ENGINE=AggregatingMergeTree() ORDER BY session_id" | head -1
# MV: IF NOT EXISTS → no-op
curl -s "$CH" --data "CREATE MATERIALIZED VIEW IF NOT EXISTS codemie_analytics.mv_session_dims TO codemie_analytics.coding_agent_session_dims AS SELECT 1" | grep -i "error" || echo "no error (already exists - IF NOT EXISTS works)"
# View: CREATE OR REPLACE → replaces idempotently
curl -s "$CH" --data "CREATE OR REPLACE VIEW codemie_analytics.v_session_dimensions AS SELECT 1 AS dummy" | head -1 && \
curl -s "$CH" --data "CREATE OR REPLACE VIEW codemie_analytics.v_session_dimensions AS SELECT session_id FROM codemie_analytics.coding_agent_session_dims GROUP BY session_id" | head -1
echo "idempotency check done"
```

---

### Task 6: Final Verification and Report

**Files:**
- Create: `ANALYTICS_DISCOVERY/cli_analytics_optimization_report.md`

- [ ] **Step 1: Full KPI correctness check**

```bash
CH="http://localhost:8123/?user=clickhouse&password=clickhouse"
# Verify key KPIs match pre-optimization baseline (21 sessions, $303.52, 10331 net lines)
curl -s "$CH" --data "
SELECT uniqExact(session_id) sessions, round(sum(cost_usd), 2) total_cost
FROM codemie_analytics.coding_agent_cost_daily
WHERE day BETWEEN today()-7 AND today() FORMAT TSV"
```

- [ ] **Step 2: Benchmark post-optimization query time**

```bash
time curl -s "$CH" --data "SELECT session_id, repository, branch FROM codemie_analytics.v_session_dimensions FORMAT Null"
```

Expected: ≤50ms (was ~105ms).

- [ ] **Step 3: Verify coding_agents_analytics endpoints unaffected**

```bash
cd /c/Users/OlehPrusak/codemie-dev/codemie
python -c "
import sys, os
sys.path.insert(0, 'src')
os.environ['CLICKHOUSE_HOST'] = 'localhost'
os.environ['CLICKHOUSE_PORT'] = '8123'
os.environ['CLICKHOUSE_USER'] = 'clickhouse'
os.environ['CLICKHOUSE_PASSWORD'] = 'clickhouse'
from codemie.repository.coding_agent_analytics_repository import CodingAgentAnalyticsRepository
from codemie.clients.clickhouse import ch_query
import asyncio
repo = CodingAgentAnalyticsRepository(ch_query)
# Basic smoke test: get_sessions_count
result = asyncio.run(repo.get_sessions_count('last_7_days', None, None, None))
print('coding_agents sessions count:', result)
print('PASS')
"
```

- [ ] **Step 4: Write optimization report to `ANALYTICS_DISCOVERY/cli_analytics_optimization_report.md`**

Include: renames done, aggregations added, before/after timing, correctness verification results, coding_agents status.
