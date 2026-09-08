# EPMCDME-13561 — Coding Agents Read Endpoints Design

**Date:** 2026-07-20
**Ticket:** [EPMCDME-13561](https://jiraeu.epam.com/browse/EPMCDME-13561)
**Parent initiative:** EPMCDME-13580
**Status:** Approved for implementation
**Schema source of truth:** `deployment/clickhouse/schema.sql` (EPMCDME-13554)
**POC reference:** `../codemie-ai-factory-analytics/deployment/api-v2/`

---

## 1. Scope

### In scope
- 6 read-only `GET` endpoints under `/v1/analytics/coding-agents/`
- ClickHouse HTTP client (singleton, async, parameterized queries)
- Pydantic response models matching the POC `shared/types.ts` data contracts
- Service handler including model pricing table for `cache_read_cost_usd` computation
- Repository layer with all ClickHouse queries
- FastAPI router registration

### Out of scope
- OTel Collector config, OTLP proxy, ingest endpoints — separate tickets
- Bash hooks, `analytics-sender`, `sdlc-doctor` setup — separate tickets
- Deployed ClickHouse — development targets **local Docker ClickHouse only** (`localhost:8123`)
- Session detail `dispatches` (requires `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA` traces — v2)
- File/line metrics (requires traces — v2)
- Active time window function (requires traces — v2)
- Agent/skill/command invocation rollups (requires traces — v2)

---

## 2. Architecture

### File map

```
src/codemie/
  clients/
    clickhouse.py                              ← NEW: singleton client, async wrapper
  configs/
    config.py                                  ← MOD: +5 CLICKHOUSE_* env vars
  repository/
    coding_agent_analytics_repository.py      ← NEW: all ClickHouse queries
  rest_api/
    models/
      coding_agents_analytics.py              ← NEW: Pydantic response models
    routers/
      coding_agents_analytics.py              ← NEW: APIRouter, 6 endpoints
    main.py                                    ← MOD: +include_router
  service/analytics/handlers/
    coding_agents_handler.py                  ← NEW: assembly + derivation logic
    coding_agent_pricing.py                   ← NEW: model pricing table

pyproject.toml                                 ← MOD: +clickhouse-connect
```

---

## 3. ClickHouse Client

**Library:** `clickhouse-connect` (official ClickHouse Python driver, HTTP port 8123).

```python
# src/codemie/clients/clickhouse.py

_client: clickhouse_connect.driver.Client | None = None

def get_client() -> clickhouse_connect.driver.Client:
    global _client
    if _client is None:
        _client = clickhouse_connect.get_client(
            host=settings.CLICKHOUSE_HOST,
            port=settings.CLICKHOUSE_PORT,
            username=settings.CLICKHOUSE_USER,
            password=settings.CLICKHOUSE_PASSWORD,
            database="codemie_analytics",
            settings={"max_execution_time": settings.CLICKHOUSE_QUERY_TIMEOUT_SECONDS},
        )
    return _client

async def ch_query(sql: str, params: dict) -> list[dict]:
    result = await asyncio.to_thread(get_client().query, sql, parameters=params)
    return result.named_results()
```

**Security rules enforced here:**
- Singleton instantiated once at first call — one HTTP connection reused across all requests
- `max_execution_time` kills runaway queries (default 30 s, configurable)
- All callers must use `{name:Type}` ClickHouse parameterized syntax — no f-string SQL building anywhere in the codebase
- String filter params (`user`, `team`, `model`) are clamped to 256 chars in the `Depends` class before reaching any query

### Config additions (`config.py`)

```python
CLICKHOUSE_HOST: str = "localhost"
CLICKHOUSE_PORT: int = 8123
CLICKHOUSE_USER: str = "default"
CLICKHOUSE_PASSWORD: str = ""
CLICKHOUSE_QUERY_TIMEOUT_SECONDS: int = 30
```

---

## 4. Common Query Parameters

`CodingAgentsQueryParams` — a single Pydantic `Depends` class shared by all endpoints.

| Param | Type | Default | Validation |
|---|---|---|---|
| `from_date` | `date` | today − 30 days | must be ≤ `to_date` |
| `to_date` | `date` | today | must be ≥ `from_date` |
| `user` | `str \| None` | — | max 256 chars |
| `team` | `str \| None` | — | max 256 chars |
| `model` | `str \| None` | — | max 256 chars; `/cost` endpoint only |

**Auth:** Router-level `dependencies=[Depends(authenticate)]` — identical to existing `analytics.py`.
**Error handling:** `@handle_analytics_errors(...)` decorator imported from `analytics.py`.

---

## 5. Pydantic Response Models (`coding_agents_analytics.py`)

These models match the POC `shared/types.ts` contracts exactly (field names in snake_case per project convention; the UI migration will adapt).

```python
class TokenUsage(BaseModel):
    input: int
    output: int
    cache_read: int
    cache_creation: int
    total: int                  # = input + output + cache_read + cache_creation

class ToolStats(BaseModel):
    tool_name: str
    total_calls: int
    success_count: int
    failure_count: int
    success_rate: float         # [0, 100], one decimal

class NamedInvocationStats(BaseModel):
    name: str
    total_calls: int
    success_count: int
    failure_count: int

class ModelCost(BaseModel):
    model_name: str
    tokens: TokenUsage
    cost_usd: float
    unpriced: bool              # True when cost_usd == 0

class CostSeriesPoint(BaseModel):
    t: int                      # event.sequence ordinal (1-based)
    cost: float                 # cumulative cost USD up to this point
    tokens: int                 # cumulative total tokens up to this point

class DispatchEvent(BaseModel):
    kind: str                   # 'agent' | 'skill' | 'command' | 'tool'
    name: str
    start: int                  # epoch ms
    duration_ms: int
    # B1 (added): per-dispatch detail for the POC Gantt drill-down. Defaulted so v1 emitters
    # (which produce no dispatches) stay valid; populated in v2 from traces + subagent logs.
    tokens: TokenUsage = <zeroed default>
    cost_usd: float = 0.0
    tools: list[ToolStats] = []

class SessionSummary(BaseModel):
    # Identity
    session_id: str
    user: str                   # user_email from Path A (Anthropic account email)
    developer_name: str         # from Path B (git/OS identity; may differ from user)
    agent_name: str             # always 'claude-code' in v1
    provider: str               # always 'anthropic' in v1
    team_name: str

    # Location
    project: str                # = cwd (full path; matches POC SessionSummary.project)
    branch: str                 # git_branch
    cwd: str
    permission_mode: str

    # Session identity
    title: str                  # first_prompt: strip leading XML tag, first 10 words, max 120 chars
    first_prompt: str           # raw first prompt body

    # Timing (epoch ms — POC uses ms arithmetic)
    start_time: int             # epoch ms; coalesce(cost log min_ts, hook event start_ts)
    duration_ms: int            # cost log max_ts − min_ts
    active_ms: int | None       # null in v1 (requires traces window function — v2)

    # Activity
    turns: int
    file_ops: int               # 0 in v1 (requires traces — v2)
    lines_added: int            # 0 in v1 (requires traces — v2)
    lines_removed: int          # 0 in v1 (requires traces — v2)
    lines_modified: int         # 0 in v1 (requires traces — v2)
    net_lines: int              # 0 in v1 (requires traces — v2)
    files_changed: int          # 0 in v1 (requires traces — v2)
    files_written: int          # 0 in v1 (requires traces — v2)
    files_edited: int           # 0 in v1 (requires traces — v2)

    # Tools
    tool_calls_total: int
    tool_calls_success: int     # = tool_calls_total − tool_calls_failure
    tool_calls_failure: int
    tools: list[ToolStats]      # per-session breakdown from hook events (second query)

    # Models and tokens
    models: list[str]
    languages: list[str]        # [] in v1 (requires traces — v2)
    tokens: TokenUsage

    # Cost
    cost_usd: float
    cache_read_cost_usd: float  # computed: sum over per_model_cost of cache_read_tokens × pricing rate
    per_model_cost: list[ModelCost]
    priced: bool                # = cost_usd > 0

    # Dispatch rollups — all [] in v1 (require traces — v2)
    agent_invocations: list[NamedInvocationStats]
    skill_invocations: list[NamedInvocationStats]
    command_invocations: list[NamedInvocationStats]


class SessionDetail(SessionSummary):
    cost_series: list[CostSeriesPoint]  # from coding_agent_logs ORDER BY event_sequence
    dispatches: list[DispatchEvent]     # [] in v1 (requires traces — v2)
    tool_events: list[dict]             # raw hook events ORDER BY Timestamp


# ── Aggregate tool row: used by GET /tools (per-developer, per-team, per-tool)
# Different from ToolStats (which is per-session, no developer/team columns).
class AggregateToolRow(BaseModel):
    tool_name: str
    developer_name: str
    team_name: str
    total_calls: int
    success_count: int
    failure_count: int
    success_rate: float         # [0, 100], one decimal


class ReportTotals(BaseModel):
    sessions: int
    duration_ms: int
    turns: int
    files: int                  # sum(file_ops); 0 in v1
    net_lines: int              # sum(net_lines); 0 in v1
    tool_calls_total: int
    tool_success_rate: float    # [0, 100], one decimal
    total_cost_usd: float
    cache_read_cost_usd: float
    priced_sessions: int


class SessionsMeta(BaseModel):
    generated_at: str           # ISO datetime
    range_label: str            # f"{from_date}..{to_date}"
    scope: str                  # 'all' | 'user'
    user: str | None
    agents: list[str]           # distinct agent_name from returned sessions
    projects: list[str]         # distinct project (cwd) from returned sessions
    totals: ReportTotals
    unpriced_models: list[str]  # models where unpriced and tokens.total > 0


class SessionsResponse(BaseModel):
    meta: SessionsMeta
    sessions: list[SessionSummary]
    total_count: int            # total matching sessions (from COUNT query, not page size)


# ── GET /v1/analytics/coding-agents/
class AnalyticsRootResponse(BaseModel):
    distinct_users: int
    distinct_teams: int
    total_cost_usd: float
    date_range_min: str | None  # ISO date string e.g. "2026-06-01"; None when no data
    date_range_max: str | None
    users: list[str]
    teams: list[str]
    models: list[str]
    agents: list[str]           # always ["claude-code"] in v1
    projects: list[str]         # always [] here (expensive scan; returned in /sessions meta)


# ── GET /v1/analytics/coding-agents/cost
class CostRow(BaseModel):
    day: str                    # ISO date string e.g. "2026-07-20"
    user_email: str
    team_name: str
    model_name: str
    query_source: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    api_call_count: int

class CostResponse(BaseModel):
    data: list[CostRow]
    total_cost_usd: float
    total_api_calls: int


# ── GET /v1/analytics/coding-agents/tools
class ToolsResponse(BaseModel):
    tools: list[AggregateToolRow]
    total_tool_calls: int


# ── GET /v1/analytics/coding-agents/users
class UserRow(BaseModel):
    user_email: str
    team_name: str
    total_cost_usd: float
    total_api_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int           # input + output + cache_read + cache_creation
    total_sessions: int
    turns: int                  # 0 in v1 — identity gap
    tool_calls_total: int       # 0 in v1 — identity gap
    net_lines: int              # 0 in v1 — requires traces
    last_active_ms: int | None  # epoch ms; None if no api_request events in window

class UsersResponse(BaseModel):
    users: list[UserRow]
```

---

## 6. Model Pricing Table (`coding_agent_pricing.py`)

Ported from `deployment/api-v2/src/report/pricing.ts`. Used to compute `cache_read_cost_usd` on session summaries.

```python
# Rates: USD per 1,000,000 tokens
PRICING_TABLE: dict[str, dict] = {
    "claude-opus-4":     {"input": 5.0,  "output": 25.0, "cache_read": 0.5,  "cache_write": 6.25},
    "claude-sonnet-4":   {"input": 3.0,  "output": 15.0, "cache_read": 0.3,  "cache_write": 3.75},
    "claude-haiku-4":    {"input": 1.0,  "output": 5.0,  "cache_read": 0.1,  "cache_write": 1.25},
    "claude-3-opus":     {"input": 15.0, "output": 75.0, "cache_read": 1.5,  "cache_write": 18.75},
    "claude-3-7-sonnet": {"input": 3.0,  "output": 15.0, "cache_read": 0.3,  "cache_write": 3.75},
    "claude-3-5-sonnet": {"input": 3.0,  "output": 15.0, "cache_read": 0.3,  "cache_write": 3.75},
    "claude-3-5-haiku":  {"input": 0.8,  "output": 4.0,  "cache_read": 0.08, "cache_write": 1.0},
    "claude-3-haiku":    {"input": 0.25, "output": 1.25, "cache_read": 0.03, "cache_write": 0.3},
    "claude-3-sonnet":   {"input": 3.0,  "output": 15.0, "cache_read": 0.3,  "cache_write": 3.75},
}

def normalize_model(model: str) -> str:
    """Strip AWS Bedrock prefix; normalize to bare claude-* id."""

def lookup_price(model: str) -> dict | None:
    """Exact match, then longest family-prefix match, then None (unpriced)."""

def cache_read_cost(model: str, cache_read_tokens: int) -> float:
    """Returns USD cost for cache_read_tokens at this model's cache_read rate."""
```

---

## 7. Endpoints

### 7.1 `GET /v1/analytics/coding-agents/` — meta / filter options

Powers filter dropdowns and summary stats in the UI header.

**Source tables:** `coding_agent_cost_daily` (two queries, no date filter — returns all-time values)

**Query 1 — scalars:**
```sql
SELECT
    count(DISTINCT user_email)  AS distinct_users,
    count(DISTINCT team_name)   AS distinct_teams,
    sum(cost_usd)               AS total_cost_usd,
    min(day)                    AS date_range_min,
    max(day)                    AS date_range_max
FROM codemie_analytics.coding_agent_cost_daily
WHERE user_email != ''
```

**Query 2 — filter lists:**
```sql
SELECT
    groupUniqArray(user_email)  AS users,
    groupUniqArray(team_name)   AS teams,
    groupUniqArray(model_name)  AS models
FROM codemie_analytics.coding_agent_cost_daily
WHERE user_email != '' AND team_name != '' AND model_name != ''
```

**Response shape (`AnalyticsRootResponse`):**
```json
{
  "distinct_users": 12,
  "distinct_teams": 3,
  "total_cost_usd": 847.32,
  "date_range_min": "2026-06-01",
  "date_range_max": "2026-07-20",
  "users": ["dev1@example.com", "dev2@example.com"],
  "teams": ["backend", "frontend"],
  "models": ["claude-sonnet-4-6", "claude-opus-4-8"],
  "agents": ["claude-code"],
  "projects": []
}
```

`projects` is empty at this endpoint — it requires scanning all cwds from `coding_agent_hook_events` (expensive). Projects are returned in the `/sessions` response meta (derived from returned sessions in Python, zero extra queries).

---

### 7.2 `GET /v1/analytics/coding-agents/cost` — daily cost rollup

**Source:** `coding_agent_cost_daily` (SummingMergeTree — always wrap aggregates in `sum()`)

**Query:**
```sql
SELECT
    day,
    user_email,
    team_name,
    model_name,
    query_source,
    sum(cost_usd)               AS cost_usd,
    sum(input_tokens)           AS input_tokens,
    sum(output_tokens)          AS output_tokens,
    sum(cache_read_tokens)      AS cache_read_tokens,
    sum(cache_creation_tokens)  AS cache_creation_tokens,
    sum(api_call_count)         AS api_call_count
FROM codemie_analytics.coding_agent_cost_daily
WHERE day BETWEEN {from_date:Date} AND {to_date:Date}
  [AND user_email = {user:String}]
  [AND team_name  = {team:String}]
  [AND model_name = {model:String}]
  AND user_email != ''
GROUP BY day, user_email, team_name, model_name, query_source
ORDER BY day DESC, user_email, model_name
```

**Response (`CostResponse`):**
```json
{
  "data": [
    {
      "day": "2026-07-20",
      "user_email": "dev@example.com",
      "team_name": "backend",
      "model_name": "claude-sonnet-4-6",
      "query_source": "repl_main_thread",
      "cost_usd": 1.42,
      "input_tokens": 12000,
      "output_tokens": 3400,
      "cache_read_tokens": 5000,
      "cache_creation_tokens": 800,
      "api_call_count": 8
    }
  ],
  "total_cost_usd": 847.32,
  "total_api_calls": 1240
}
```

---

### 7.3 `GET /v1/analytics/coding-agents/sessions` — session list

Maps to POC `GET /api/v2/report` → `ReportPayload`.

**Filters:** `from_date`, `to_date`, `user` (filters `developer_name`), `team`.
Additional params: `limit` (default 500, max 1000), `offset` (default 0).

**Sources:** `coding_agent_hook_events` + `coding_agent_logs`

**3 queries, assembled in handler:**

**Query 1 — main session CTE (returns one row per session):**
```sql
WITH
    starts AS (
        SELECT
            session_id,
            developer_name,
            team_name,
            anyLast(cwd)             AS cwd,
            anyLast(git_branch)      AS git_branch,
            anyLast(permission_mode) AS permission_mode,
            min(Timestamp)           AS started_at
        FROM codemie_analytics.coding_agent_hook_events
        WHERE event_type = 'agent.session.start'
          AND TimestampDate BETWEEN {from_date:Date} AND {to_date:Date}
          [AND developer_name = {user:String}]
          [AND team_name      = {team:String}]
        GROUP BY session_id, developer_name, team_name
    ),
    stops AS (
        SELECT session_id, max(Timestamp) AS ended_at
        FROM codemie_analytics.coding_agent_hook_events
        WHERE event_type = 'agent.session.stop'
          AND session_id IN (SELECT session_id FROM starts)
        GROUP BY session_id
    ),
    first_prompts AS (
        SELECT
            session_id,
            argMin(prompt_body, turn_number) AS first_prompt
        FROM codemie_analytics.coding_agent_hook_events
        WHERE event_type = 'agent.prompt.submit'
          AND prompt_body != ''
          AND session_id IN (SELECT session_id FROM starts)
        GROUP BY session_id
    ),
    turns AS (
        SELECT session_id, count() AS turn_count
        FROM codemie_analytics.coding_agent_hook_events
        WHERE event_type = 'agent.prompt.submit'
          AND session_id IN (SELECT session_id FROM starts)
        GROUP BY session_id
    ),
    tool_counts AS (
        SELECT
            session_id,
            countIf(event_type = 'agent.tool.start') AS tool_calls_total,
            countIf(event_type = 'agent.tool.error') AS tool_calls_failure
        FROM codemie_analytics.coding_agent_hook_events
        WHERE event_type IN ('agent.tool.start', 'agent.tool.end', 'agent.tool.error')
          AND session_id IN (SELECT session_id FROM starts)
        GROUP BY session_id
    ),
    cost AS (
        SELECT
            session_id,
            any(user_email)                                              AS user_email,
            sum(toFloat64OrZero(LogAttributes['cost_usd']))              AS cost_usd,
            sum(toUInt64OrZero(LogAttributes['input_tokens']))           AS input_tokens,
            sum(toUInt64OrZero(LogAttributes['output_tokens']))          AS output_tokens,
            sum(toUInt64OrZero(LogAttributes['cache_read_tokens']))      AS cache_read_tokens,
            sum(toUInt64OrZero(LogAttributes['cache_creation_tokens']))  AS cache_creation_tokens,
            toUnixTimestamp64Milli(min(Timestamp))                       AS min_ts_ms,
            toUnixTimestamp64Milli(max(Timestamp))                       AS max_ts_ms,
            groupUniqArray(model_name)                                   AS model_names
        FROM codemie_analytics.coding_agent_logs
        WHERE event_name = 'api_request'
          AND session_id IN (SELECT session_id FROM starts)
        GROUP BY session_id
    )
SELECT
    s.session_id,
    s.developer_name,
    s.team_name,
    s.cwd,
    s.git_branch,
    s.permission_mode,
    coalesce(c.min_ts_ms,
             toUnixTimestamp64Milli(s.started_at))                    AS start_time_ms,
    greatest(0, coalesce(
        greatest(c.max_ts_ms, toUnixTimestamp64Milli(stop.ended_at)),
        c.max_ts_ms,
        toUnixTimestamp64Milli(stop.ended_at),
        toUnixTimestamp64Milli(s.started_at)
    ) - coalesce(c.min_ts_ms, toUnixTimestamp64Milli(s.started_at))) AS duration_ms,
    coalesce(fp.first_prompt, '')                                      AS first_prompt,
    coalesce(t.turn_count, 0)                                          AS turns,
    coalesce(tc.tool_calls_total, 0)                                   AS tool_calls_total,
    coalesce(tc.tool_calls_failure, 0)                                 AS tool_calls_failure,
    coalesce(c.user_email, '')                                         AS user_email,
    coalesce(c.cost_usd, 0.0)                                          AS cost_usd,
    coalesce(c.input_tokens, 0)                                        AS input_tokens,
    coalesce(c.output_tokens, 0)                                       AS output_tokens,
    coalesce(c.cache_read_tokens, 0)                                   AS cache_read_tokens,
    coalesce(c.cache_creation_tokens, 0)                               AS cache_creation_tokens,
    coalesce(c.model_names, [])                                        AS model_names
FROM starts s
LEFT JOIN stops stop       ON s.session_id = stop.session_id
LEFT JOIN first_prompts fp ON s.session_id = fp.session_id
LEFT JOIN turns t          ON s.session_id = t.session_id
LEFT JOIN tool_counts tc   ON s.session_id = tc.session_id
LEFT JOIN cost c           ON s.session_id = c.session_id
ORDER BY start_time_ms DESC
LIMIT  {limit:UInt32}
OFFSET {offset:UInt32}
```

Companion **COUNT query** (same WHERE, no LIMIT) returns `total_count` for pagination.

**Query 2 — per-model cost for returned session IDs:**
```sql
SELECT
    session_id,
    model_name,
    sum(toFloat64OrZero(LogAttributes['cost_usd']))             AS cost_usd,
    sum(toUInt64OrZero(LogAttributes['input_tokens']))           AS input_tokens,
    sum(toUInt64OrZero(LogAttributes['output_tokens']))          AS output_tokens,
    sum(toUInt64OrZero(LogAttributes['cache_read_tokens']))      AS cache_read_tokens,
    sum(toUInt64OrZero(LogAttributes['cache_creation_tokens']))  AS cache_creation_tokens
FROM codemie_analytics.coding_agent_logs
WHERE event_name = 'api_request'
  AND session_id IN {session_ids:Array(String)}
GROUP BY session_id, model_name
```

**Query 3 — per-session tool stats for returned session IDs:**
```sql
SELECT
    session_id,
    tool_name,
    countIf(event_type = 'agent.tool.start') AS total_calls,
    countIf(event_type = 'agent.tool.end')   AS success_count,
    countIf(event_type = 'agent.tool.error') AS failure_count
FROM codemie_analytics.coding_agent_hook_events
WHERE session_id IN {session_ids:Array(String)}
  AND tool_name != ''
  AND event_type IN ('agent.tool.start', 'agent.tool.end', 'agent.tool.error')
GROUP BY session_id, tool_name
```

**Handler assembly (Python):**
1. Build `SessionSummary` objects from Query 1 rows
2. Attach `per_model_cost` from Query 2 (keyed by session_id)
3. Compute `ModelCost.tokens.total` and `unpriced = cost_usd == 0`
4. Compute `cache_read_cost_usd = sum(cache_read_cost(m.model_name, m.tokens.cache_read) for m in per_model_cost)`
5. Attach `tools: ToolStats[]` from Query 3 (keyed by session_id), compute `success_rate`
6. Derive `title` from `first_prompt`: strip leading `<...>` tag, take first 10 words, cap at 120 chars
7. Set stub fields: all line/file counts = 0, `languages = []`, `active_ms = None`, `agent/skill/command_invocations = []`, `dispatches = []`
8. Set constants: `agent_name = 'claude-code'`, `provider = 'anthropic'`, `priced = cost_usd > 0`
9. Compute `SessionsMeta.totals` by summing over returned sessions
10. Compute `meta.agents`, `meta.projects`, `meta.unpriced_models` from returned sessions

**Response (`SessionsResponse`):**
```json
{
  "meta": {
    "generated_at": "2026-07-20T12:00:00Z",
    "range_label": "2026-06-20..2026-07-20",
    "scope": "all",
    "user": null,
    "agents": ["claude-code"],
    "projects": ["/home/dev/myproject", "/home/dev/other"],
    "totals": {
      "sessions": 47,
      "duration_ms": 12340000,
      "turns": 456,
      "files": 0,
      "net_lines": 0,
      "tool_calls_total": 1234,
      "tool_success_rate": 96.8,
      "total_cost_usd": 847.32,
      "cache_read_cost_usd": 12.40,
      "priced_sessions": 45
    },
    "unpriced_models": []
  },
  "sessions": [
    {
      "session_id": "abc123",
      "user": "dev@example.com",
      "developer_name": "dev@example.com",
      "agent_name": "claude-code",
      "provider": "anthropic",
      "team_name": "backend",
      "project": "/home/dev/myproject",
      "branch": "feature/auth",
      "cwd": "/home/dev/myproject",
      "permission_mode": "default",
      "title": "Fix the authentication bug in login",
      "first_prompt": "Fix the authentication bug in login flow",
      "start_time": 1753001600000,
      "duration_ms": 2700000,
      "active_ms": null,
      "turns": 14,
      "file_ops": 0, "lines_added": 0, "lines_removed": 0,
      "lines_modified": 0, "net_lines": 0,
      "files_changed": 0, "files_written": 0, "files_edited": 0,
      "tool_calls_total": 37,
      "tool_calls_success": 35,
      "tool_calls_failure": 2,
      "tools": [
        {"tool_name": "Bash", "total_calls": 20, "success_count": 19, "failure_count": 1, "success_rate": 95.0}
      ],
      "models": ["claude-sonnet-4-6"],
      "languages": [],
      "tokens": {"input": 28000, "output": 7200, "cache_read": 5000, "cache_creation": 800, "total": 41000},
      "cost_usd": 3.21,
      "cache_read_cost_usd": 0.0015,
      "per_model_cost": [
        {
          "model_name": "claude-sonnet-4-6",
          "tokens": {"input": 28000, "output": 7200, "cache_read": 5000, "cache_creation": 800, "total": 41000},
          "cost_usd": 3.21,
          "unpriced": false
        }
      ],
      "priced": true,
      "agent_invocations": [],
      "skill_invocations": [],
      "command_invocations": []
    }
  ],
  "total_count": 47
}
```

---

### 7.4 `GET /v1/analytics/coding-agents/sessions/{session_id}` — session detail

Maps to POC `GET /api/v2/sessions/:id` → `SessionDetail`.

**Sources:** same as session list, plus `coding_agent_logs` cost series + `coding_agent_hook_events` tool events

**4 queries:**

1. **Main session query** — same CTE as session list filtered by `session_id = {session_id:String}` instead of date range. Returns 0 rows if not found → 404.
2. **Per-model cost** — same as Query 2 in section 7.3, single session_id.
3. **Tool events** — raw hook events for the session timeline:
   ```sql
   SELECT
       toUnixTimestamp64Milli(Timestamp) AS timestamp_ms,
       event_type,
       tool_name,
       tool_use_id,
       tool_input,
       tool_output,
       error_message
   FROM codemie_analytics.coding_agent_hook_events
   WHERE session_id = {session_id:String}
     AND event_type IN ('agent.tool.start', 'agent.tool.end', 'agent.tool.error')
   ORDER BY Timestamp
   ```
4. **Cost series** — api_request events ordered by sequence for the cost timeline:
   ```sql
   SELECT
       event_sequence                                             AS t,
       toFloat64OrZero(LogAttributes['cost_usd'])                AS cost_this_call,
       (toUInt64OrZero(LogAttributes['input_tokens'])
        + toUInt64OrZero(LogAttributes['output_tokens'])
        + toUInt64OrZero(LogAttributes['cache_read_tokens'])
        + toUInt64OrZero(LogAttributes['cache_creation_tokens'])) AS tokens_this_call,
       LogAttributes['agent.name']                               AS agent_name,
       LogAttributes['skill.name']                               AS skill_name
   FROM codemie_analytics.coding_agent_logs
   WHERE event_name = 'api_request'
     AND session_id = {session_id:String}
   ORDER BY event_sequence
   ```
   Handler computes cumulative `cost` and `tokens` in Python to produce `CostSeriesPoint` objects.

Returns 404 if session not found. Response shape = `SessionDetail` (all `SessionSummary` fields + `cost_series`, `dispatches: []`, `tool_events`).

---

### 7.5 `GET /v1/analytics/coding-agents/tools` — aggregate tool usage

Maps to the Tools tab in the POC UI (aggregate across all sessions, not per-session).

**Source:** `coding_agent_hook_events`

**Query:**
```sql
SELECT
    developer_name,
    team_name,
    tool_name,
    countIf(event_type = 'agent.tool.start') AS total_calls,
    countIf(event_type = 'agent.tool.end')   AS success_count,
    countIf(event_type = 'agent.tool.error') AS failure_count
FROM codemie_analytics.coding_agent_hook_events
WHERE event_type IN ('agent.tool.start', 'agent.tool.end', 'agent.tool.error')
  AND tool_name != ''
  AND TimestampDate BETWEEN {from_date:Date} AND {to_date:Date}
  [AND developer_name = {user:String}]
  [AND team_name      = {team:String}]
GROUP BY developer_name, team_name, tool_name
ORDER BY total_calls DESC
```

`success_rate` computed in handler: `round(success_count / total_calls * 100, 1) if total_calls > 0 else 0.0`.

**Response (`ToolsResponse` — items are `AggregateToolRow`, not `ToolStats`):**
```json
{
  "tools": [
    {
      "tool_name": "Bash",
      "developer_name": "dev@example.com",
      "team_name": "backend",
      "total_calls": 142,
      "success_count": 138,
      "failure_count": 4,
      "success_rate": 97.2
    }
  ],
  "total_tool_calls": 891
}
```

---

### 7.6 `GET /v1/analytics/coding-agents/users` — leaderboard

Maps to POC `GET /api/v2/users` → `UsersRow[]`.

**Sources:** `coding_agent_cost_daily` (cost/tokens) + `coding_agent_logs` (session count, last active). Merged in handler by `user_email`.

**Query 1 — cost and tokens:**
```sql
SELECT
    user_email,
    team_name,
    sum(cost_usd)               AS total_cost_usd,
    sum(api_call_count)         AS total_api_calls,
    sum(input_tokens)           AS total_input_tokens,
    sum(output_tokens)          AS total_output_tokens,
    sum(cache_read_tokens)      AS total_cache_read_tokens,
    sum(cache_creation_tokens)  AS total_cache_creation_tokens
FROM codemie_analytics.coding_agent_cost_daily
WHERE day BETWEEN {from_date:Date} AND {to_date:Date}
  [AND team_name = {team:String}]
  AND user_email != ''
GROUP BY user_email, team_name
ORDER BY total_cost_usd DESC
```

**Query 2 — session count and last active:**
```sql
SELECT
    user_email,
    count(DISTINCT session_id)             AS total_sessions,
    toUnixTimestamp64Milli(max(Timestamp)) AS last_active_ms
FROM codemie_analytics.coding_agent_logs
WHERE event_name = 'api_request'
  AND TimestampDate BETWEEN {from_date:Date} AND {to_date:Date}
  AND user_email != ''
GROUP BY user_email
```

**Response (`UsersResponse`):**
```json
{
  "users": [
    {
      "user_email": "dev@example.com",
      "team_name": "backend",
      "total_cost_usd": 142.80,
      "total_api_calls": 312,
      "total_input_tokens": 2800000,
      "total_output_tokens": 740000,
      "total_tokens": 3850000,
      "total_sessions": 28,
      "turns": 0,
      "tool_calls_total": 0,
      "net_lines": 0,
      "last_active_ms": 1753001600000
    }
  ]
}
```

`total_tokens = total_input_tokens + total_output_tokens + total_cache_read_tokens + total_cache_creation_tokens`.
`turns`, `tool_calls_total`, `net_lines` are `0` in v1 — identity gap between `user_email` (Path A) and `developer_name` (Path B) makes cross-path aggregation unreliable without explicit identity resolution (future work).

---

## 8. Query Safety

All SQL uses ClickHouse `{name:Type}` parameterized syntax throughout. Optional WHERE clauses are built by appending to a `conditions: list[str]` and a `params: dict` in Python:

```python
conditions = ["TimestampDate BETWEEN {from_date:Date} AND {to_date:Date}"]
params: dict = {"from_date": from_date, "to_date": to_date}
if user:
    conditions.append("developer_name = {user:String}")
    params["user"] = user[:256]
if team:
    conditions.append("team_name = {team:String}")
    params["team"] = team[:256]
where = " AND ".join(conditions)
```

No string concatenation of user-supplied values anywhere.

---

## 9. Router Registration

```python
# src/codemie/rest_api/main.py
from codemie.rest_api.routers import coding_agents_analytics
app.include_router(coding_agents_analytics.router)
```

New router:
```python
# src/codemie/rest_api/routers/coding_agents_analytics.py
router = APIRouter(
    tags=["Coding Agents Analytics"],
    prefix="/v1/analytics/coding-agents",
    dependencies=[Depends(authenticate)],
)
```

---

## 10. v1 Limitations

| Feature | Status | Unblocked by |
|---|---|---|
| `active_ms` per session | `null` | Traces: `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` |
| File/line metrics (`file_ops`, `lines_*`, `files_*`) | all `0` | Traces: `claude_code.tool` spans with `file_path` |
| `languages` per session | `[]` | Traces: file path → extension mapping |
| `agent/skill/command_invocations` | `[]` | Traces: `subagent_type`, `skill_name` span attributes |
| `dispatches` in session detail (+ per-dispatch `tokens`/`cost_usd`/`tools`) | `[]` | Traces: `claude_code.tool` spans with `subagent_type`; per-dispatch cost from `coding_agent_logs` `query_source='agent:*'` |
| `turns` and `tool_calls_total` in `/users` | `0` | Identity unification: `user_email` ↔ `developer_name` |
| `projects` in root `/` endpoint | `[]` | Dedicated query over `coding_agent_hook_events.cwd` |

---

## 11. Testing

All development and query testing is done against the local Docker ClickHouse instance. The read endpoints work regardless of how data entered ClickHouse.

Seed data is loaded manually using `INSERT INTO ... VALUES` or via `clickhouse-client` against the tables defined in `deployment/clickhouse/schema.sql`. The smoke test at `deployment/clickhouse/smoke.sh` verifies table existence.

The ingest pipeline (OTel Collector, OTLP proxy, bash hooks) is not required to be running for read-endpoint development.

---

## 12. Decision Record — 2026-07-21 (schema-completeness follow-up)

Follow-up to the UI coverage audit: confirm both schema layers (response models + ClickHouse
columns) are structurally complete so v2 data lands without another schema rework. Scope is the
read side only — data collection/population is a separate owner (OTel Collector / pipeline).

### B1 — `DispatchEvent` extended (APPLIED)
Added `tokens: TokenUsage`, `cost_usd: float`, `tools: list[ToolStats]` (defaulted, backward-compatible)
so the POC session Gantt drill-down can render per-dispatch cost/tokens/tools without a later schema
change or frontend rework. No query change made — v1 still emits `dispatches: []`. Data is later
readable from `coding_agent_traces` (child tool spans) + `coding_agent_logs` (`query_source='agent:*'`).
Covered by tests in `tests/codemie/rest_api/models/test_coding_agents_analytics.py`.

### A1 — line/file metrics: **collectible, no new column needed**
The solution design (§6, "Analytics and Telemetry Data") lists `claude_code.lines_of_code.count` as a
**current** native OTel metric (not future), and the metrics ingest endpoint explicitly receives "LOC".
Collector in use: `otel/opentelemetry-collector-contrib:0.105.0`. This metric lands in
`coding_agent_metrics_sum` with `session.id`/`type` carried in the `Attributes` `Map(String,String)`.
**Recommendation:** keep reading from the metrics table later; do **not** add a promoted column — the
metrics tables are created/owned by the otelcol ClickHouse exporter, so a `MATERIALIZED` column there
would fight the managed schema and risk insert breakage on collector upgrade. If clean per-session
querying is wanted in v2, add a derived table/MV we own (mirroring `coding_agent_cost_daily` /
`mv_cost_daily`) — query work, not a schema-completeness gap. No DDL change required now.

### A2 — `languages[]`: keep (derivable, not a dead field)
No native language attribute is emitted anywhere in the solution design or collector plan, **but** the
field is derivable from `coding_agent_traces.file_path` (extension → language mapping), unblocked by the
same traces integration that lights up file/line metrics (§10). It matches the POC contract and is not
in the Jira ACs (non-blocking). **Recommendation:** keep the field — dropping then re-adding it when
traces land would churn the response contract and the frontend port for no benefit.
