# CLI Analytics Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the dead `/v1/analytics/coding-agents` endpoint stack, add two pre-aggregated ClickHouse tables for trace-level session facts, and optimize the three hottest cli-analytics queries so they never scan the 15 K-row `coding_agent_traces` table on list requests.

**Architecture:** The old `coding-agents` router used raw Python CTEs hitting `coding_agent_hook_events` at query time; it has zero live callers and is safe to delete. Two new SummingMergeTree / AggregatingMergeTree tables (`coding_agent_turns_daily`, `coding_agent_file_facts_daily`) are populated by materialized views from `coding_agent_traces` and backfilled from existing data. `cli_analytics_repository.py` is updated to read from these tables instead of scanning raw spans.

**Tech Stack:** ClickHouse 26.7.1, Python / FastAPI, SQLModel-free repository layer, `clickhouse-connect` or HTTP API for live verification.

**Spec:** `docs/superpowers/plans/2026-08-24-cli-analytics-optimization.md` (predecessor — Tasks 2–5 already applied)

## Global Constraints

- Live ClickHouse at `localhost:8123`, credentials `user=clickhouse password=clickhouse`.
- All ClickHouse DDL in `deployment/clickhouse/schema.sql` must be from-scratch `CREATE … IF NOT EXISTS` statements — no `ALTER`, no migrations.
- Do **not** touch `src/codemie/service/analytics/handlers/coding_agent_pricing.py` — it is shared with the new cli-analytics handler.
- Do **not** modify or delete `v_session_email`, `v_session_dimensions`, or any existing MV/table used by cli-analytics.
- Verify data correctness before and after every ClickHouse mutation.
- Run `make ruff` after every Python change (load setup-guide if `poetry` is not on PATH).

---

### Task 1: Verify caller-free status of `/v1/analytics/coding-agents`

**Files:**
- Read-only verification (no changes)

**Evidence gathered (pre-confirmed, document here for future reviewers):**

| Location searched | Pattern | Result |
|---|---|---|
| `codemie-ui/src/**/*.{ts,tsx,js,vue}` | `coding-agents` / `v1/analytics/coding` | **0 matches** |
| `codemie/src/**/*.py` | `analytics/coding-agents` (excluding router file itself) | Only a comment in `cli_analytics.py:27` |
| `config/**/*.yaml` | `coding-agents` | Only tag category metadata — not API calls |

Conclusion: the `/v1/analytics/coding-agents` router has **zero live callers**. Deletion is safe.

- [ ] **Step 1: Confirm zero callers (fast sanity)**

  ```bash
  grep -r "v1/analytics/coding-agents" \
    C:/Users/OlehPrusak/codemie-dev/codemie-ui/src \
    --include="*.ts" --include="*.tsx" --include="*.js" --include="*.vue"
  # Expected: no output
  ```

- [ ] **Step 2: Confirm no Python code imports the old handler outside its own file**

  ```bash
  grep -rn "CodingAgentsHandler\|CodingAgentAnalyticsRepository" \
    src/ --include="*.py" | grep -v "__pycache__" | grep -v "coding_agents_handler\|coding_agent_analytics_repo"
  # Expected: no output (only main.py + the router import)
  ```

---

### Task 2: Delete the coding-agents endpoint stack

**Files:**
- Delete: `src/codemie/rest_api/routers/coding_agents_analytics.py`
- Delete: `src/codemie/rest_api/models/coding_agents_analytics.py`
- Delete: `src/codemie/repository/coding_agent_analytics_repository.py`
- Delete: `src/codemie/service/analytics/handlers/coding_agents_handler.py`
- Modify: `src/codemie/rest_api/main.py` (lines ~85–90 and ~850)

**Interfaces:**
- Produces: nothing (pure deletion — removes ~1600 lines of dead code)

- [ ] **Step 1: Delete the four files**

  ```bash
  rm src/codemie/rest_api/routers/coding_agents_analytics.py
  rm src/codemie/rest_api/models/coding_agents_analytics.py
  rm src/codemie/repository/coding_agent_analytics_repository.py
  rm src/codemie/service/analytics/handlers/coding_agents_handler.py
  ```

- [ ] **Step 2: Remove the router import and registration from `main.py`**

  Open `src/codemie/rest_api/main.py`. Find and remove:

  ```python
  # In the import block (~line 87):
      coding_agents_analytics,
  ```

  ```python
  # In the router registration block (~line 850):
  app.include_router(coding_agents_analytics.router)
  ```

- [ ] **Step 3: Lint**

  ```bash
  make ruff
  # Expected: exit 0, no errors referencing deleted files
  ```

- [ ] **Step 4: Smoke-test the app starts**

  ```bash
  python -c "from codemie.rest_api.main import app; print('OK')"
  # Expected: OK
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add -A src/codemie/rest_api/routers/coding_agents_analytics.py \
              src/codemie/rest_api/models/coding_agents_analytics.py \
              src/codemie/repository/coding_agent_analytics_repository.py \
              src/codemie/service/analytics/handlers/coding_agents_handler.py \
              src/codemie/rest_api/main.py
  git commit -m "remove: drop dead /v1/analytics/coding-agents endpoint stack

  Zero live callers confirmed (codemie-ui src/ clean; dist/ is stale build artifact).
  Removes CodingAgentAnalyticsRepository, CodingAgentsHandler, router, and models
  (~1 600 lines). coding_agent_pricing.py retained — shared with cli-analytics handler.

  Generated with AI

  Co-Authored-By: codemie-ai <codemie.ai@gmail.com>"
  ```

---

### Task 3: Audit ClickHouse views and materialized views

**Evidence (pre-confirmed):**

| CH Object | Serves cli-analytics? | Serves deleted coding-agents? | Action |
|---|---|---|---|
| `mv_hook_events` → `coding_agent_hook_events` | ✓ (get_users_last_active) | ✗ (not used) | KEEP |
| `mv_cost_daily` → `coding_agent_cost_daily` | ✓ | ✗ | KEEP |
| `mv_session_identity` → `coding_agent_session_identity` | ✓ (via v_session_email) | ✗ | KEEP |
| `mv_lines_daily` → `coding_agent_lines_daily` | ✓ | ✗ | KEEP |
| `mv_active_time_daily` → `coding_agent_active_time_daily` | ✓ | ✗ | KEEP |
| `mv_session_dims` → `coding_agent_session_dims` | ✓ (via v_session_dimensions) | ✗ | KEEP |
| `v_session_email` | ✓ | ✗ | KEEP |
| `v_session_dimensions` | ✓ | ✗ | KEEP |

**Verdict: nothing to drop.** The old coding-agents endpoints used Python CTEs (`_SESSION_META_CTE` in `coding_agent_analytics_repository.py`) — not dedicated CH views — so deletion leaves the schema completely intact.

**New objects to add (Task 4):** `coding_agent_turns_daily`, `mv_turns_daily`, `coding_agent_file_facts_daily`, `mv_file_facts_daily`.

- [ ] **Step 1: Confirm existing objects via live CH**

  ```bash
  curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" \
    --data "SELECT name FROM system.tables WHERE database='codemie_analytics' ORDER BY name"
  # Expected: all tables listed in schema.sql present; none named coding_agent_turns_daily yet
  ```

---

### Task 4: Add pre-aggregated trace-fact tables and MVs

**Why:** `get_turns_by_session`, `get_file_facts_by_session`, and `get_session_detail_scalars` each scan the 15 K-row `coding_agent_traces` table. These three methods are called on every request to `/overview`, `/users`, `/repositories`, `/sessions`, and `/session-detail`. Pre-aggregating at the day × session_id granularity reduces these scans to reads of ~89 rows (one per session). The pattern mirrors the existing `coding_agent_cost_daily` and `coding_agent_lines_daily` tables.

**Files:**
- Modify: `deployment/clickhouse/schema.sql` (add new DDL)
- Apply DDL to live CH

**New DDL:**

```sql
-- ── coding_agent_turns_daily ───────────────────────────────────────────────
-- Pre-aggregated interaction-span counts per (day, session_id).
-- Replaces the raw coding_agent_traces scan in get_turns_by_session.
CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_turns_daily
(
    day        Date,
    session_id String,
    turns      UInt64
) ENGINE = SummingMergeTree(turns)
PARTITION BY toYYYYMM(day)
ORDER BY (day, session_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS codemie_analytics.mv_turns_daily
TO codemie_analytics.coding_agent_turns_daily AS
SELECT
    toDate(Timestamp)  AS day,
    session_id,
    count()            AS turns
FROM codemie_analytics.coding_agent_traces
WHERE SpanName = 'claude_code.interaction'
  AND session_id != ''
GROUP BY day, session_id;

-- ── coding_agent_file_facts_daily ─────────────────────────────────────────
-- Pre-aggregated tool-span file/tool counts per (day, session_id).
-- Replaces the raw coding_agent_traces scan in get_file_facts_by_session.
-- Also provides agent_count + skill_count used by get_session_detail_scalars.
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
) ENGINE = AggregatingMergeTree()
PARTITION BY toYYYYMM(day)
ORDER BY (day, session_id);

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
```

**Interfaces:**
- Produces: `coding_agent_turns_daily`, `coding_agent_file_facts_daily` — consumed by Task 5

- [ ] **Step 1: Record baseline query times**

  ```bash
  # Baseline for get_turns_by_session pattern
  time curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" \
    --data "SELECT session_id, count() AS turns
            FROM codemie_analytics.coding_agent_traces
            WHERE SpanName = 'claude_code.interaction'
              AND Timestamp BETWEEN '2026-01-01' AND '2027-01-01'
              AND session_id != ''
            GROUP BY session_id FORMAT JSON" | python -c "import sys,json; d=json.load(sys.stdin); print('rows:', len(d['data']), 'elapsed:', d['statistics']['elapsed'])"

  # Baseline for get_file_facts_by_session pattern
  time curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" \
    --data "SELECT session_id,
                   uniqExactIf(file_path, file_path != '') AS files_changed,
                   countIf(tool_name != '') AS tool_calls
            FROM codemie_analytics.coding_agent_traces
            WHERE SpanName = 'claude_code.tool'
              AND Timestamp BETWEEN '2026-01-01' AND '2027-01-01'
              AND session_id != ''
            GROUP BY session_id FORMAT JSON" | python -c "import sys,json; d=json.load(sys.stdin); print('rows:', len(d['data']), 'elapsed:', d['statistics']['elapsed'])"
  ```

  Save the elapsed times — paste them into the report in Task 6.

- [ ] **Step 2: Apply DDL for `coding_agent_turns_daily` and its MV**

  ```bash
  curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" --data "
  CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_turns_daily
  (
      day        Date,
      session_id String,
      turns      UInt64
  ) ENGINE = SummingMergeTree(turns)
  PARTITION BY toYYYYMM(day)
  ORDER BY (day, session_id)"

  curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" --data "
  CREATE MATERIALIZED VIEW IF NOT EXISTS codemie_analytics.mv_turns_daily
  TO codemie_analytics.coding_agent_turns_daily AS
  SELECT
      toDate(Timestamp) AS day,
      session_id,
      count()           AS turns
  FROM codemie_analytics.coding_agent_traces
  WHERE SpanName = 'claude_code.interaction'
    AND session_id != ''
  GROUP BY day, session_id"
  ```

- [ ] **Step 3: Backfill `coding_agent_turns_daily`**

  ```bash
  curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" --data "
  INSERT INTO codemie_analytics.coding_agent_turns_daily
  SELECT toDate(Timestamp) AS day, session_id, count() AS turns
  FROM codemie_analytics.coding_agent_traces
  WHERE SpanName = 'claude_code.interaction' AND session_id != ''
  GROUP BY day, session_id"

  # Verify backfill
  curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" \
    --data "SELECT count(), sum(turns) FROM codemie_analytics.coding_agent_turns_daily FORMAT JSON" \
    | python -c "import sys,json; d=json.load(sys.stdin); print(d['data'])"
  # Expected: same total sum as the baseline trace scan above
  ```

- [ ] **Step 4: Apply DDL for `coding_agent_file_facts_daily` and its MV**

  ```bash
  curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" --data "
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
  ) ENGINE = AggregatingMergeTree()
  PARTITION BY toYYYYMM(day)
  ORDER BY (day, session_id)"

  curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" --data "
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
  GROUP BY day, session_id"
  ```

- [ ] **Step 5: Backfill `coding_agent_file_facts_daily`**

  ```bash
  curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" --data "
  INSERT INTO codemie_analytics.coding_agent_file_facts_daily
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
      )                                                                             AS files_edited
  FROM codemie_analytics.coding_agent_traces
  WHERE SpanName = 'claude_code.tool' AND session_id != ''
  GROUP BY day, session_id"

  # Verify backfill — total tool_calls should match baseline
  curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" \
    --data "SELECT count(), sum(tool_calls) FROM codemie_analytics.coding_agent_file_facts_daily FORMAT JSON" \
    | python -c "import sys,json; d=json.load(sys.stdin); print(d['data'])"
  ```

- [ ] **Step 6: Correctness spot-check — turns per session must match**

  ```bash
  # Old: raw trace scan
  curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" \
    --data "SELECT session_id, count() AS turns
            FROM codemie_analytics.coding_agent_traces
            WHERE SpanName = 'claude_code.interaction' AND session_id != ''
            GROUP BY session_id ORDER BY session_id FORMAT CSV" > /tmp/turns_old.csv

  # New: pre-aggregated
  curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" \
    --data "SELECT session_id, sum(turns) AS turns
            FROM codemie_analytics.coding_agent_turns_daily
            WHERE session_id != ''
            GROUP BY session_id ORDER BY session_id FORMAT CSV" > /tmp/turns_new.csv

  diff /tmp/turns_old.csv /tmp/turns_new.csv
  # Expected: empty diff (files identical)
  ```

- [ ] **Step 7: Record post-optimization query times**

  ```bash
  time curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" \
    --data "SELECT session_id, sum(turns) AS turns
            FROM codemie_analytics.coding_agent_turns_daily
            WHERE day BETWEEN '2026-01-01' AND '2026-12-31'
            GROUP BY session_id FORMAT JSON" | python -c "import sys,json; d=json.load(sys.stdin); print('rows:', len(d['data']), 'elapsed:', d['statistics']['elapsed'])"
  ```

  Save the elapsed time for the report.

---

### Task 5: Optimize `cli_analytics_repository.py`

**Files:**
- Modify: `src/codemie/repository/cli_analytics_repository.py`

**Interfaces:**
- Consumes: `coding_agent_turns_daily`, `coding_agent_file_facts_daily` (from Task 4)
- Produces: updated `get_turns_by_session`, `get_file_facts_by_session`, `get_session_detail_scalars`, simplified `_sessions_cte`

#### 5a — Simplify `_REPO_EXPR` (lines 46–49)

`v_session_dimensions.repository` already applies the basename-normalization transformation. `_REPO_EXPR` re-applies it, which is a no-op for clean data but adds needless CPU overhead and query size.

- [ ] **Step 1: Replace `_REPO_EXPR` constant and its usage**

  Old (`cli_analytics_repository.py:46–49`):
  ```python
  _REPO_EXPR = (
      "if(d.repository = '', '', "
      "arrayElement(arrayFilter(x -> x != '', splitByChar('/', replaceAll(d.repository, char(92), '/'))), -1))"
  )
  ```

  Delete the `_REPO_EXPR` constant entirely (lines 46–49).

  Old usage in `_sessions_cte` (line 143):
  ```python
                  {_REPO_EXPR}                                                  AS repository,
  ```

  New:
  ```python
                  d.repository                                                  AS repository,
  ```

#### 5b — Update `get_turns_by_session` (lines 336–350)

Old:
```python
async def get_turns_by_session(self, f: LocalAnalyticsFilter) -> list[dict]:
    """D3: one row per claude_code.interaction span = one user interaction."""
    sql = f"""
    WITH {self._sessions_cte(f)}
    SELECT
        session_id  AS session_id,
        count()     AS turns
    FROM codemie_analytics.coding_agent_traces
    WHERE {_INTERACTION_SPAN}
          AND Timestamp BETWEEN {{start_dt:DateTime64(3)}} AND {{end_dt:DateTime64(3)}}
          AND session_id != ''
          {self._session_scope(f)}
    GROUP BY session_id
    """
    return await self._q(sql, f.params())
```

New:
```python
async def get_turns_by_session(self, f: LocalAnalyticsFilter) -> list[dict]:
    """D3: interaction turns per session from the pre-aggregated daily rollup.

    Uses coding_agent_turns_daily (SummingMergeTree, ORDER BY (day, session_id))
    instead of scanning the raw coding_agent_traces table.  Pattern mirrors
    get_cost_kpis / get_lines_by_session which already read daily rollups.
    """
    sql = f"""
    WITH {self._sessions_cte(f)}
    SELECT
        session_id          AS session_id,
        sum(turns)          AS turns
    FROM codemie_analytics.coding_agent_turns_daily
    WHERE day BETWEEN toDate({{start_dt:DateTime64(3)}}) AND toDate({{end_dt:DateTime64(3)}})
          {self._session_scope(f)}
    GROUP BY session_id
    """
    return await self._q(sql, f.params())
```

#### 5c — Update `get_file_facts_by_session` (lines 352–376)

Old:
```python
async def get_file_facts_by_session(self, f: LocalAnalyticsFilter) -> list[dict]:
    """Per-session distinct file counts from claude_code.tool spans. ..."""
    sql = f"""
    WITH {self._sessions_cte(f)}
    SELECT
        session_id                                                                    AS session_id,
        uniqExactIf(file_path, file_path != '')                                       AS files_changed,
        uniqExactIf(file_path, file_path != '' AND tool_name = 'Write')               AS files_written,
        uniqExactIf(
            file_path,
            file_path != '' AND tool_name IN ('Edit', 'MultiEdit', 'NotebookEdit')
        )                                                                             AS files_edited,
        countIf(tool_name != '')                                                      AS tool_calls
    FROM codemie_analytics.coding_agent_traces
    WHERE {_TOOL_SPAN}
          AND Timestamp BETWEEN {{start_dt:DateTime64(3)}} AND {{end_dt:DateTime64(3)}}
          AND session_id != ''
          {self._session_scope(f)}
    GROUP BY session_id
    """
    return await self._q(sql, f.params())
```

New:
```python
async def get_file_facts_by_session(self, f: LocalAnalyticsFilter) -> list[dict]:
    """Per-session distinct file counts from the pre-aggregated daily rollup.

    Uses coding_agent_file_facts_daily (AggregatingMergeTree) instead of scanning
    coding_agent_traces.  uniqExactMerge correctly accumulates state across days and
    across AggregatingMergeTree parts.
    """
    sql = f"""
    WITH {self._sessions_cte(f)}
    SELECT
        session_id,
        uniqExactMerge(files_changed)  AS files_changed,
        uniqExactMerge(files_written)  AS files_written,
        uniqExactMerge(files_edited)   AS files_edited,
        sum(tool_calls)                AS tool_calls
    FROM codemie_analytics.coding_agent_file_facts_daily
    WHERE day BETWEEN toDate({{start_dt:DateTime64(3)}}) AND toDate({{end_dt:DateTime64(3)}})
          {self._session_scope(f)}
    GROUP BY session_id
    """
    return await self._q(sql, f.params())
```

#### 5d — Optimize `get_session_detail_scalars` (lines 607–629)

Old (7 subqueries, 5 scan raw `coding_agent_traces`):
```python
async def get_session_detail_scalars(self, session_id: str) -> list[dict]:
    sql = """
    SELECT
        (SELECT sum(lines_added) FROM codemie_analytics.coding_agent_lines_daily
         WHERE session_id = {session_id:String})                        AS lines_added,
        (SELECT sum(lines_removed) FROM codemie_analytics.coding_agent_lines_daily
         WHERE session_id = {session_id:String})                        AS lines_removed,
        (SELECT sum(active_ms_user) + sum(active_ms_cli)
         FROM codemie_analytics.coding_agent_active_time_daily
         WHERE session_id = {session_id:String})                        AS active_ms,
        (SELECT count() FROM codemie_analytics.coding_agent_traces
         WHERE session_id = {session_id:String} AND SpanName = 'claude_code.interaction') AS turns,
        (SELECT count() FROM codemie_analytics.coding_agent_traces
         WHERE session_id = {session_id:String} AND SpanName = 'claude_code.tool'
           AND tool_name != '')                                         AS tool_call_count,
        (SELECT count() FROM codemie_analytics.coding_agent_traces
         WHERE session_id = {session_id:String} AND SpanName = 'claude_code.tool'
           AND subagent_type != '')                                     AS agent_count,
        (SELECT count() FROM codemie_analytics.coding_agent_traces
         WHERE session_id = {session_id:String} AND SpanName = 'claude_code.tool'
           AND span_skill_name != '')                                   AS skill_count
    """
    return await self._q(sql, {"session_id": session_id})
```

New (7 subqueries but the 5 traces-based ones now hit pre-aggregated tables — ~89-row reads instead of 15 K scans):
```python
async def get_session_detail_scalars(self, session_id: str) -> list[dict]:
    """Per-session scalar facts for the session detail view.

    lines_added/removed and active_ms already read from pre-aggregated daily tables
    (coding_agent_lines_daily, coding_agent_active_time_daily).  turns, tool_call_count,
    agent_count, and skill_count previously triggered raw scans of coding_agent_traces;
    they now read from coding_agent_turns_daily and coding_agent_file_facts_daily.
    """
    sql = """
    SELECT
        (SELECT sum(lines_added)
         FROM codemie_analytics.coding_agent_lines_daily
         WHERE session_id = {session_id:String})                          AS lines_added,
        (SELECT sum(lines_removed)
         FROM codemie_analytics.coding_agent_lines_daily
         WHERE session_id = {session_id:String})                          AS lines_removed,
        (SELECT sum(active_ms_user) + sum(active_ms_cli)
         FROM codemie_analytics.coding_agent_active_time_daily
         WHERE session_id = {session_id:String})                          AS active_ms,
        (SELECT sum(turns)
         FROM codemie_analytics.coding_agent_turns_daily
         WHERE session_id = {session_id:String})                          AS turns,
        (SELECT sum(tool_calls)
         FROM codemie_analytics.coding_agent_file_facts_daily
         WHERE session_id = {session_id:String})                          AS tool_call_count,
        (SELECT sum(agent_count)
         FROM codemie_analytics.coding_agent_file_facts_daily
         WHERE session_id = {session_id:String})                          AS agent_count,
        (SELECT sum(skill_count)
         FROM codemie_analytics.coding_agent_file_facts_daily
         WHERE session_id = {session_id:String})                          AS skill_count
    """
    return await self._q(sql, {"session_id": session_id})
```

- [ ] **Step 1: Apply 5a (delete `_REPO_EXPR`, update `_sessions_cte`)**

  Edit `src/codemie/repository/cli_analytics_repository.py`:
  - Delete lines 46–49 (the `_REPO_EXPR` constant)
  - In `_sessions_cte` (around line 143), change `{_REPO_EXPR}` → `d.repository`

- [ ] **Step 2: Apply 5b (update `get_turns_by_session`)**

  Replace the method body at lines 336–350 with the new version above.

- [ ] **Step 3: Apply 5c (update `get_file_facts_by_session`)**

  Replace the method body at lines 352–376 with the new version above.

- [ ] **Step 4: Apply 5d (update `get_session_detail_scalars`)**

  Replace the method body at lines 607–629 with the new version above.

- [ ] **Step 5: Lint**

  ```bash
  make ruff
  # Expected: exit 0
  ```

- [ ] **Step 6: Smoke-test repository imports**

  ```bash
  python -c "from codemie.repository.cli_analytics_repository import LocalAnalyticsRepository; print('OK')"
  # Expected: OK
  ```

- [ ] **Step 7: Commit**

  ```bash
  git add src/codemie/repository/cli_analytics_repository.py
  git commit -m "perf: replace raw coding_agent_traces scans with pre-aggregated daily rollups

  - Remove redundant _REPO_EXPR basename normalization (v_session_dimensions already
    normalizes); simplify _sessions_cte to use d.repository directly.
  - get_turns_by_session: coding_agent_traces (15 K rows) → coding_agent_turns_daily
  - get_file_facts_by_session: coding_agent_traces → coding_agent_file_facts_daily
  - get_session_detail_scalars: 5 raw trace subqueries → pre-aggregated table reads;
    all 7 scalars now come from daily rollup tables (~89 rows each, not 15 K).

  Generated with AI

  Co-Authored-By: codemie-ai <codemie.ai@gmail.com>"
  ```

---

### Task 6: Update `schema.sql` with final from-scratch DDL

**Files:**
- Modify: `deployment/clickhouse/schema.sql`

The schema file must be the authoritative from-scratch DDL — no ALTER, no migrations. Add the two new tables and their MVs after the existing `coding_agent_active_time_daily` block, before the views section.

- [ ] **Step 1: Add `coding_agent_turns_daily` DDL block**

  Insert after the `mv_active_time_daily` block:

  ```sql
  -- ── coding_agent_turns_daily ───────────────────────────────────────────────
  -- Pre-aggregated interaction-span counts; fed by mv_turns_daily.
  -- Enables get_turns_by_session to avoid scanning coding_agent_traces.
  CREATE TABLE IF NOT EXISTS codemie_analytics.coding_agent_turns_daily
  (
      day        Date,
      session_id String,
      turns      UInt64
  ) ENGINE = SummingMergeTree(turns)
  PARTITION BY toYYYYMM(day)
  ORDER BY (day, session_id);

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
  ```

- [ ] **Step 2: Add `coding_agent_file_facts_daily` DDL block**

  Insert immediately after the block from Step 1:

  ```sql
  -- ── coding_agent_file_facts_daily ─────────────────────────────────────────
  -- Pre-aggregated tool-span file/tool counts; fed by mv_file_facts_daily.
  -- Enables get_file_facts_by_session and get_session_detail_scalars to avoid
  -- scanning coding_agent_traces.
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
  ) ENGINE = AggregatingMergeTree()
  PARTITION BY toYYYYMM(day)
  ORDER BY (day, session_id);

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
  ```

- [ ] **Step 3: Commit schema change**

  ```bash
  git add deployment/clickhouse/schema.sql
  git commit -m "schema: add coding_agent_turns_daily and coding_agent_file_facts_daily

  Two new pre-aggregated tables replacing raw coding_agent_traces scans in
  get_turns_by_session, get_file_facts_by_session, get_session_detail_scalars.
  Both tables are from-scratch CREATE IF NOT EXISTS — no ALTER statements.

  Generated with AI

  Co-Authored-By: codemie-ai <codemie.ai@gmail.com>"
  ```

---

### Task 7: Write the optimization report

**Files:**
- Create: `C:\Users\OlehPrusak\codemie-dev\ANALYTICS_DISCOVERY\cli_analytics_optimization_report.md`

- [ ] **Step 1: Collect final verification metrics from live CH**

  ```bash
  # Final row counts
  curl -s "http://localhost:8123/?user=clickhouse&password=clickhouse" \
    --data "SELECT 'turns_daily' AS t, count() AS rows, sum(turns) AS total_turns
            FROM codemie_analytics.coding_agent_turns_daily
            UNION ALL
            SELECT 'file_facts_daily', count(), sum(tool_calls)
            FROM codemie_analytics.coding_agent_file_facts_daily FORMAT JSON" \
    | python -c "import sys,json; [print(r) for r in json.load(sys.stdin)['data']]"
  ```

- [ ] **Step 2: Write the report** (content template — fill in measured timings)

  ```markdown
  # CLI Analytics Optimization Report
  _Date: 2026-08-25_
  _Branch: feature/EPMCDME-13580-code-agent-analytics_

  ## What Was Removed

  ### Dead endpoint stack: `/v1/analytics/coding-agents`

  **Files deleted (4):**
  | File | Lines |
  |---|---|
  | `src/codemie/rest_api/routers/coding_agents_analytics.py` | 219 |
  | `src/codemie/rest_api/models/coding_agents_analytics.py` | ~180 |
  | `src/codemie/repository/coding_agent_analytics_repository.py` | 1097 |
  | `src/codemie/service/analytics/handlers/coding_agents_handler.py` | ~580 |

  **Total removed:** ~2076 lines of dead code.

  **Caller verification:** `codemie-ui/src/` — 0 matches for `v1/analytics/coding-agents`.
  `dist/` reference is a stale build artifact. Route was safe to delete.

  **ClickHouse objects dropped:** none. The old coding-agents code used Python CTEs
  (`_SESSION_META_CTE` hitting `coding_agent_hook_events` raw) — no dedicated CH views.
  All existing CH objects (MVs, views, tables) exclusively serve cli-analytics and were kept.

  ## What Was Added

  ### `coding_agent_turns_daily` + `mv_turns_daily`
  - Engine: SummingMergeTree(turns), partitioned by month, ORDER BY (day, session_id)
  - Feed: `claude_code.interaction` spans from `coding_agent_traces`
  - Backfill: NN rows, NNN total turns (matches raw count exactly — diff was empty)

  ### `coding_agent_file_facts_daily` + `mv_file_facts_daily`
  - Engine: AggregatingMergeTree, partitioned by month, ORDER BY (day, session_id)
  - Feed: `claude_code.tool` spans from `coding_agent_traces`
  - Backfill: NN rows, NNN total tool_calls (matches raw count)
  - Carries: tool_calls, agent_count, skill_count, files_changed/written/edited states

  ## Query Optimizations

  | Method | Before | After | Data source change |
  |---|---|---|---|
  | `get_turns_by_session` | Scan coding_agent_traces (15 225 rows) | Read coding_agent_turns_daily (~89 rows) | traces → turns_daily |
  | `get_file_facts_by_session` | Scan coding_agent_traces (15 225 rows) | Read coding_agent_file_facts_daily (~89 rows) | traces → file_facts_daily |
  | `get_session_detail_scalars` | 5 raw trace subqueries per call | 5 pre-aggregated subqueries, 0 raw trace scans | traces → daily rollups |
  | `_sessions_cte._REPO_EXPR` | Redundant basename-of-basename expression | Removed; use `d.repository` directly | n/a |

  **Endpoints that benefit** (these call the above methods via asyncio.gather):
  - `GET /v1/analytics/cli-analytics/overview` — calls turns + file_facts + tool_success + durations
  - `GET /v1/analytics/cli-analytics/users` — same set of 8 parallel queries
  - `GET /v1/analytics/cli-analytics/repositories` — calls turns + file_facts + tool_success
  - `GET /v1/analytics/cli-analytics/sessions` — calls turns + tool_success
  - `GET /v1/analytics/cli-analytics/sessions/{trace_id}` — calls session_detail_scalars

  ## Performance: Before vs After

  | Query pattern | Before (elapsed s) | After (elapsed s) | Speedup |
  |---|---|---|---|
  | get_turns_by_session (all data) | _FILL_IN_ | _FILL_IN_ | _FILL_IN_ |
  | get_file_facts_by_session (all data) | _FILL_IN_ | _FILL_IN_ | _FILL_IN_ |

  _(Fill in from the timing measurements in Task 4 Steps 1 and 7.)_

  ## Schema.sql Update

  Added two DDL blocks (from-scratch CREATE IF NOT EXISTS, no ALTER):
  - `coding_agent_turns_daily` with `mv_turns_daily`
  - `coding_agent_file_facts_daily` with `mv_file_facts_daily`

  ## Data Correctness

  ```
  diff /tmp/turns_old.csv /tmp/turns_new.csv
  → (empty — exact match)
  ```

  File-facts correctness verified: total tool_calls from raw traces == sum(tool_calls)
  from coding_agent_file_facts_daily.

  ## What Was NOT Changed

  - `coding_agent_pricing.py` — shared utility, retained
  - All existing CH MVs (mv_hook_events, mv_cost_daily, mv_session_identity,
    mv_lines_daily, mv_active_time_daily, mv_session_dims) — all serve cli-analytics, kept
  - `v_session_email`, `v_session_dimensions` — kept unchanged
  - `get_tool_success_by_session`, `get_tool_usage` — these do a tool_use_id JOIN between
    two span types; cannot be pre-aggregated without a dedicated joining MV. At current
    volume (15 K rows) this is acceptable. Flagged for future optimization at scale.
  - `get_invocations` skill/agent branches — still scan coding_agent_traces; pre-aggregation
    of per-name counts is future work once name cardinality stabilizes.
  ```

  Save the completed report (with timings filled in) to
  `C:\Users\OlehPrusak\codemie-dev\ANALYTICS_DISCOVERY\cli_analytics_optimization_report.md`.

---

## Self-Review Checklist

- [x] Task 1 covers: verify zero callers before any deletion
- [x] Task 2 covers: delete all 4 coding-agents files + main.py cleanup
- [x] Task 3 covers: ClickHouse view audit — all existing objects kept, reasoning documented
- [x] Task 4 covers: new tables DDL, backfill, correctness diff, timing
- [x] Task 5 covers: 4 Python changes (_REPO_EXPR, get_turns, get_file_facts, get_session_detail_scalars)
- [x] Task 6 covers: schema.sql from-scratch DDL update
- [x] Task 7 covers: report with actual measured timings
- [x] `coding_agent_pricing.py` explicitly called out as NOT deleted
- [x] `get_tool_success_by_session` explicitly noted as not pre-aggregatable (tool_use_id JOIN)
- [x] No ALTER statements in schema.sql changes
- [x] All CH credentials use `user=clickhouse&password=clickhouse`
