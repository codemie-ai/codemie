# Technical Research

**Task**: clickhouse analytics rest-api fastapi endpoints
**Generated**: 2026-07-20T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Implement 6 read-only GET endpoints under /v1/analytics/coding-agents/ for Claude Code analytics. The endpoints read from ClickHouse tables (coding_agent_cost_daily, coding_agent_hook_events, coding_agent_logs) and return Pydantic response models. New files needed: src/codemie/clients/clickhouse.py (singleton async client), src/codemie/repository/coding_agent_analytics_repository.py (all CH queries), src/codemie/rest_api/models/coding_agents_analytics.py (Pydantic models), src/codemie/rest_api/routers/coding_agents_analytics.py (APIRouter with 6 endpoints), src/codemie/service/analytics/handlers/coding_agents_handler.py (assembly logic), src/codemie/service/analytics/handlers/coding_agent_pricing.py (model pricing table). Modifications: src/codemie/configs/config.py (+5 CLICKHOUSE_* env vars), src/codemie/rest_api/main.py (+include_router), pyproject.toml (+clickhouse-connect). Auth: router-level Depends(authenticate) identical to existing analytics.py router.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/routers/analytics.py` — The primary analytics router (3416 lines). Defines the canonical patterns for all new endpoints: `APIRouter` with `prefix="/v1/analytics"` and `dependencies=[Depends(authenticate)]`; `@handle_analytics_errors(...)` decorator; `_create_response(data, ModelClass)` helper that validates via Pydantic and adds Cache-Control/ETag headers; `AnalyticsQueryParams` Pydantic `Depends` class for common query parameters.
- `src/codemie/rest_api/main.py` — App entry point. All routers registered via `app.include_router(...)` at module level (lines 807–870). The import style is `from codemie.rest_api.routers import <module>` followed by `app.include_router(<module>.router)`.
- `src/codemie/configs/config.py` — Single `Config(BaseSettings)` class (pydantic-settings). All env vars declared as typed fields with defaults. No section for ClickHouse exists yet.
- `src/codemie/rest_api/models/analytics.py` — Pydantic response models used by the existing analytics router: `SummariesResponse`, `TabularResponse`, `AnalyticsDetailResponse`, `UsersListResponse`, `ResponseMetadata`, `PaginationMetadata`. The new file must follow the same import style and `BaseModel` inheritance.
- `src/codemie/service/analytics/analytics_service.py` — Facade service; lazy-loads handler instances. Pattern: `AnalyticsService(user)` constructs the service with a user context; each analytics domain gets its own handler. The coding-agents feature gets a **standalone handler** (not grafted into `AnalyticsService`) per the spec.
- `src/codemie/service/analytics/handlers/summary_handler.py` — Representative handler: takes `User` + `MetricsElasticRepository` in `__init__`, exposes async methods returning `dict` that routers pass to `_create_response`.
- `src/codemie/clients/postgres.py` — Singleton client pattern used for PostgreSQL. The `PostgresClient` class holds module-level engine references and provides `get_engine()` / `get_async_engine()`. The ClickHouse client will follow a simpler version of this singleton pattern (global `_client` variable + `get_client()` function as documented in the spec).
- `src/codemie/clients/elasticsearch.py` — Existing async client; demonstrates `asyncio.to_thread` wrapping for sync SDK calls, which is the exact approach required for `clickhouse-connect`.
- `src/codemie/repository/metrics_elastic_repository.py` — Analytics repository for ES. The new `coding_agent_analytics_repository.py` follows this as a structural model (class wrapping all queries for a single data store).
- `deployment/clickhouse/schema.sql` — Source of truth for all three tables: `coding_agent_cost_daily` (SummingMergeTree), `coding_agent_hook_events` (MergeTree), `coding_agent_logs` (MergeTree). Schema committed in EPMCDME-13554.

### Architecture and Layers Affected

| Layer | Components |
|---|---|
| **API (Router)** | NEW `src/codemie/rest_api/routers/coding_agents_analytics.py`; MOD `src/codemie/rest_api/main.py` (one `include_router` line) |
| **Pydantic Models** | NEW `src/codemie/rest_api/models/coding_agents_analytics.py` (12 models: `TokenUsage`, `ToolStats`, `NamedInvocationStats`, `ModelCost`, `CostSeriesPoint`, `DispatchEvent`, `SessionSummary`, `SessionDetail`, `AggregateToolRow`, `SessionsResponse`, `CostResponse`, `ToolsResponse`, `UserRow`, `UsersResponse`, `AnalyticsRootResponse`, `ReportTotals`, `SessionsMeta`) |
| **Service / Handler** | NEW `src/codemie/service/analytics/handlers/coding_agents_handler.py`; NEW `src/codemie/service/analytics/handlers/coding_agent_pricing.py` |
| **Repository** | NEW `src/codemie/repository/coding_agent_analytics_repository.py` |
| **Client** | NEW `src/codemie/clients/clickhouse.py` |
| **Configuration** | MOD `src/codemie/configs/config.py` (+5 fields: `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, `CLICKHOUSE_QUERY_TIMEOUT_SECONDS`) |
| **Dependency** | MOD `pyproject.toml` (+`clickhouse-connect`) |

The task does **not** touch Elasticsearch, PostgreSQL, LangChain, LangGraph, authentication, or any existing analytics handler.

### Integration Points

- **ClickHouse HTTP driver** — `clickhouse-connect` library (not yet in `pyproject.toml`). Uses HTTP port 8123. All queries are parameterized with `{name:Type}` ClickHouse syntax; the driver resolves them server-side. `asyncio.to_thread` wraps the synchronous `client.query()` call.
- **`codemie.rest_api.security.authentication.authenticate`** — FastAPI `Depends` used at router level. Identical to the existing `analytics.py` router declaration at line 336.
- **`codemie.core.exceptions.ExtendedHTTPException`** — Used by the `handle_analytics_errors` decorator imported from `analytics.py`.
- **`codemie.configs.config.config`** — Singleton config instance consumed by `clickhouse.py` to read the five new env vars.
- No database (PostgreSQL) connections. No Elasticsearch connections. No LLM calls.

### Patterns and Conventions

- **Router declaration**: `router = APIRouter(tags=[...], prefix="/v1/analytics/...", dependencies=[Depends(authenticate)])`. Tag is a new string (`"Coding Agents Analytics"`).
- **Error handling**: Import `handle_analytics_errors` from `codemie.rest_api.routers.analytics` and apply as decorator on each endpoint function.
- **Response creation**: Import `_create_response` from `codemie.rest_api.routers.analytics`. Call as `_create_response(data_dict, ResponseModelClass)`.
- **Query params**: Define a `CodingAgentsQueryParams(BaseModel)` class with `from_date: date`, `to_date: date`, `user: str | None`, `team: str | None` and a `model: str | None` (for the cost endpoint only). Use `Query(...)` descriptors with validators for max-256-char string clamping.
- **Query safety**: Build `conditions: list[str]` + `params: dict`; append optional clauses if param is not None; pass params to `ch_query`. No f-string SQL interpolation.
- **Handler assembly**: Handler class takes no `User` argument (coding-agents data is not scoped to a logged-in user's projects); it accepts direct query parameters. This differs from existing handlers that take `User`.
- **Sessions multi-query flow**: Three concurrent ClickHouse queries per `/sessions` call (main CTE, per-model cost, per-session tool stats), assembled in Python. Use `asyncio.gather()` for the second and third queries after getting session IDs from the first.
- **`from __future__ import annotations`** is used at the top of every Python file in this codebase.
- **Copyright header**: Apache 2.0 copyright block present in every source file.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/api/rest-api-patterns.md` — Router registration, error responses, and authentication dependencies. Directly applicable.
- `.ai-run/guides/api/endpoint-conventions.md` — Route and response conventions.
- `.ai-run/guides/architecture/layered-architecture.md` — Layer taxonomy.
- `.ai-run/guides/data/repository-patterns.md` — Repository access patterns; the new CH repository should follow this structurally.
- `.ai-run/guides/development/configuration-patterns.md` — Config and environment patterns (pydantic-settings `BaseSettings`).
- `docs/superpowers/specs/2026-07-20-epmcdme-13561-coding-agents-read-endpoints-design.md` — **Primary implementation spec**. Contains exact SQL for all 6 endpoints, response model definitions, handler assembly steps, and file map. This is the authoritative design document for this task.
- `deployment/clickhouse/schema.sql` — ClickHouse table definitions (EPMCDME-13554, committed).

### Architectural Decisions

- **`clickhouse-connect` over `aiochconnect`**: The spec explicitly mandates `clickhouse-connect` (official driver, HTTP port 8123). No async native driver — uses `asyncio.to_thread` wrapper. This is consistent with the existing `postgres.py` and `elasticsearch.py` singleton patterns.
- **Standalone router** (`/v1/analytics/coding-agents/`) separate from the existing `/v1/analytics/` router — prevents coupling to the Elasticsearch-backed `AnalyticsService`.
- **Handler is not grafted into `AnalyticsService`**: The existing service is ES-centric. The new `coding_agents_handler.py` is invoked directly from the router, not via `AnalyticsService`. This is an intentional architectural boundary: ClickHouse vs. Elasticsearch data stores.
- **`SummingMergeTree` awareness**: `coding_agent_cost_daily` uses SummingMergeTree — all queries must use `GROUP BY + sum()` to merge unmerged parts. This is noted in the schema and the spec.
- **v1 limitations are explicit stubs**: `active_ms = None`, all file/line counts `= 0`, `languages = []` — these are intentional, not bugs.
- **No pagination on `/cost` or `/tools`** — these return all matching rows. Pagination (`limit`/`offset`) only on `/sessions`.

### Derived Conventions

- All handler `__init__` methods accept dependencies as constructor arguments rather than importing globals directly (from `summary_handler.py`).
- `asyncio.gather()` is the standard pattern for parallel independent queries (confirmed in `summary_handler.py`).
- Response dicts are validated through Pydantic models before being returned via `JSONResponse` — the `_create_response` helper does this.
- Model file names in `rest_api/models/` are snake_case matching the feature domain.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/routers/test_analytics.py` — Tests for existing analytics router: error handling decorator behavior (`handle_analytics_errors`), response formatting (`_create_response`), caching headers, query parameter handling. Uses `pytest`, `AsyncMock`, `MagicMock`, `patch`. Imports and tests utility functions from the router module directly.
- `tests/codemie/service/analytics/test_analytics_service.py` — Tests for `AnalyticsService`.
- `tests/unit/routers/test_analytics_enriched_user.py` — Enriched user scope tests.

### Testing Framework and Patterns

- **Framework**: `pytest` with `pytest-asyncio`.
- **HTTP client**: `pytest-httpx` for async HTTP mocking; `fastapi.testclient.TestClient` or async test client for router-level tests.
- **Mocking**: `unittest.mock.AsyncMock` / `MagicMock` / `patch` from stdlib.
- **Fixtures**: `pytest.fixture` functions; `mock_user` fixture returns a `MagicMock(spec=User)` with relevant attributes populated.
- **Assertion style**: Direct assertions on `response.status_code`, `response.json()`.

### Coverage Gaps

- **No existing tests** for anything ClickHouse-related — no `clickhouse.py` client, no `coding_agent_analytics_repository.py`, no `coding_agents_handler.py`. These are entirely new files with zero coverage.
- **No test for the new router** `coding_agents_analytics.py` — will need new test file following the pattern in `test_analytics.py`.
- **No test for `coding_agent_pricing.py`** — the pricing lookup and `normalize_model` function need unit tests (pure functions, easy to test).
- The session assembly logic in `coding_agents_handler.py` is complex (multi-query join in Python, title derivation, cost series cumulation) and has no coverage at all.

---

## 5. Configuration and Environment

### Environment Variables

Five new env vars required (none exist yet in `config.py`):

| Variable | Type | Default | Description |
|---|---|---|---|
| `CLICKHOUSE_HOST` | `str` | `"localhost"` | ClickHouse server hostname |
| `CLICKHOUSE_PORT` | `int` | `8123` | ClickHouse HTTP port |
| `CLICKHOUSE_USER` | `str` | `"default"` | ClickHouse username |
| `CLICKHOUSE_PASSWORD` | `str` | `""` | ClickHouse password |
| `CLICKHOUSE_QUERY_TIMEOUT_SECONDS` | `int` | `30` | Max query execution time |

### Configuration Files

- `src/codemie/configs/config.py` — The single `Config(BaseSettings)` class; `pydantic-settings` reads from `.env` file (via `find_dotenv`) and environment. New fields added here are automatically available in local `.env` and in production via environment injection. No separate config file is needed.
- `.env` (git-ignored, locally present) — Will need `CLICKHOUSE_*` entries for local development. The `.env` file is already marked modified in `git status`.

### Feature Flags and Deployment Concerns

- No feature flag is specified for the ClickHouse integration. The endpoints are always registered once `include_router` is added.
- The `clickhouse-connect` package must be added to `pyproject.toml` and installed. This adds a new transitive dependency set (ClickHouse HTTP client over `urllib3`/`requests`) that is orthogonal to the existing httpx-based stack.
- ClickHouse availability is **not** guarded at startup. If ClickHouse is unavailable, queries will fail at request time with a connection error that will be caught by `handle_analytics_errors` and returned as HTTP 500.
- Local development targets Docker ClickHouse at `localhost:8123` per the spec. No production ClickHouse deployment is in scope for this ticket.

---

## 6. Risk Indicators

- No existing test coverage for any ClickHouse-related code — `clickhouse.py`, `coding_agent_analytics_repository.py`, and `coding_agents_handler.py` are all new with zero tests. The session assembly handler is the most logic-heavy component and has the highest coverage risk.
- `clickhouse-connect` is not yet in `pyproject.toml` — dependency must be added and `poetry.lock` regenerated before the code can run. This is a hard blocker for CI.
- The `/sessions` endpoint executes **three ClickHouse queries** (main CTE + per-model cost + tool stats), each potentially touching millions of rows. The CTE itself is a 60-line query with 6 sub-CTEs and 5 LEFT JOINs. If ClickHouse is not running or the tables are empty, this path is untestable without mocks.
- `SummingMergeTree` behavior: `coding_agent_cost_daily` has unmerged parts that must be handled via `GROUP BY + sum()`. Incorrect queries that omit `sum()` wrappers will silently return inflated counts on recently-inserted data. No test can catch this without a real ClickHouse instance.
- The sessions main CTE query uses `IN (SELECT session_id FROM starts)` sub-selects (correlated across CTEs). On large datasets without proper ClickHouse `bloom_filter` index warm-up, these can be slow. The schema has bloom filter indexes defined — they must exist for acceptable performance.
- Identity gap: `user_email` (Path A, Anthropic account) and `developer_name` (Path B, git/OS identity) are different fields. The `/users` endpoint intentionally returns `turns=0`, `tool_calls_total=0` because cross-path aggregation is unreliable. This is a known v1 limitation documented in the spec, not a bug to fix now.
- The `handle_analytics_errors` and `_create_response` helpers are imported from `analytics.py` — this creates a cross-module import dependency. If `analytics.py` is refactored, the new router would break. Low risk currently but worth noting.
- `coding_agent_pricing.py` uses prefix matching for model name normalization (e.g., `claude-sonnet-4` matches `claude-sonnet-4-6`). The matching logic must handle AWS Bedrock model IDs (`anthropic.claude-...`). If a model is unpriced, `cache_read_cost_usd` is silently 0. No alerting for new unpriced models.
- No integration test infrastructure for ClickHouse exists in this repository. Tests will need to mock `ch_query` at the repository layer or use a test ClickHouse container (not currently in `docker-compose` or CI).
- The `session_id` path parameter in `GET /sessions/{session_id}` returns 404 on empty result — this 404 logic must be explicitly implemented in the handler (not automatic from ClickHouse).

---

## 7. Summary for Complexity Assessment

This task introduces a **new external data store integration** (ClickHouse) into an otherwise Elasticsearch + PostgreSQL codebase. It touches five architectural layers: client (new `clickhouse.py` singleton), repository (new `coding_agent_analytics_repository.py` with ~8 distinct SQL queries), service/handler (new `coding_agents_handler.py` with multi-query assembly and `coding_agent_pricing.py`), API models (new Pydantic file with ~17 models), and router (new `coding_agents_analytics.py` with 6 endpoints). Modifications to existing files are minimal and low-risk: three config fields added to `Config`, one `include_router` call in `main.py`, one dependency line in `pyproject.toml`. The total new-file line count is estimated at 600–900 lines. The spec is complete and detailed, so no design ambiguity exists.

The task does **not** follow a well-worn existing pattern: ClickHouse is a new data source with no prior client, repository, or handler in this codebase. However, the existing patterns for ES and PostgreSQL clients, combined with the detailed design spec, provide strong structural guidance. The most technically novel elements are (a) the ClickHouse async wrapper using `asyncio.to_thread`, (b) the `/sessions` endpoint which requires assembling three parallel ClickHouse queries in Python and performing complex derivations (title extraction, cost series accumulation, pricing table lookup), and (c) the `SummingMergeTree`-aware query patterns. These introduce meaningful implementation complexity above a typical "add new ES analytics endpoint" task.

Test coverage posture is **poor** for the new code: zero existing tests cover ClickHouse or the new handler. The pricing module and query-building helpers are pure Python and readily unit-testable. The repository and handler assembly logic require mocking `ch_query` at the async boundary. The `/sessions` assembly path (3 queries + 10 derivation steps) is the highest-risk area for logic bugs. Key risk factors for complexity scoring: new external dependency requiring `poetry.lock` regeneration, no existing ClickHouse test infrastructure, a 60-line CTE query in the sessions endpoint, and the identity-gap design decision that produces intentional `0`/null stub fields which must be correctly implemented rather than treated as bugs.
