# Technical Research

**Task**: analytics elasticsearch clickhouse graceful-degradation
**Generated**: 2026-09-17T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

Implement EPMCDME-14347 Analytics access and graceful degradation — BACKEND repository (codemie) only.

Requirements (from the authoritative task file, backend scope):

1. Remove the branch-only coupling between Metrics Analytics and has_enterprise(). The current feature
   branch added `_require_metrics_analytics_enabled()` backed by `has_enterprise()` and attached it to
   many Metrics Analytics endpoints, producing 503. This must be removed or redesigned so gating is
   connector-based, not Enterprise-package-based.
2. Add the smallest centralized fallback boundary that can produce correct schema-valid empty responses
   for all relevant ES-backed and ClickHouse-backed UI read endpoints:
   - If Elasticsearch is not configured/absent/unavailable: ES-backed Analytics read/query endpoints
     consumed by the UI must return HTTP 200 with valid empty responses (empty tables/charts, zero/empty
     summary values) — using each endpoint's actual response model, not one generic empty object.
   - If features:cliAnalytics is enabled but ClickHouse is not configured/absent/unavailable: CLI Analytics
     reads must return HTTP 200 with valid empty responses.
   - Preserve authentication, authorization, request validation, and role checks — never turn 401/403/422
     into 200.
   - Do not broadly swallow programming errors or unrelated failures — handle only recognized
     missing/unavailable connector conditions.
   - Scope is read/query endpoints only, not ingestion/write endpoints (unless current contracts already
     require it).
   - Reuse existing response factories/models and connector exception types where possible.
3. Keep AI/Run Adoption PostgreSQL paths independent from Elasticsearch — retain the feature branch's LAZY
   `MetricsElasticRepository` initialization so PostgreSQL- and ClickHouse-only paths never eagerly
   construct/connect to Elasticsearch.
4. Keep normal Enterprise/full-deployment behavior and payloads unchanged when connectors ARE available.
5. Preserve existing role/feature-flag rules for CLI Analytics (features:cliAnalytics + Admin/Project Admin),
   AI/Run Adoption (Admin/Auditor), Leaderboard (Admin/Auditor + feature flag), and dashboard customization.
   Do not change Auditor behavior in this task.
6. Direct Enterprise imports already on origin/main (e.g. personal LiteLLM spending, leaderboard computation)
   are not automatically in scope — only touch them if they sit on a visible tab's read path and block the
   confirmed graceful-degradation behavior; document any such decision as a risk/open question.

Explicitly out of scope: do not modify codemie-sdk; do not perform the CLI Analytics config migration from
.env.standalone to customer-config.yaml (do not modify FEATURE_CLI_ANALYTICS, standalone/.env.standalone,
standalone/.env.standalone.example, or the features:cliAnalytics entry in customer-config.yaml for that
migration purpose).

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/routers/analytics.py:677-685` — `_require_metrics_analytics_enabled()`, a FastAPI
  dependency that raises HTTP 503 when `has_enterprise()` is False. Attached as a dependency starting at the
  `@router.get(...)` block beginning line 688 (decorator list truncated by explore output — full enumeration
  of every attachment point needs a direct grep/Read of `analytics.py`, not confirmed exhaustively here).
- `src/codemie/enterprise/loader.py:219-229` — `HAS_ENTERPRISE` (set via `importlib.metadata.version("codemie-enterprise")` / `PackageNotFoundError`) and `has_enterprise()` which just returns that flag. This is a
  package-presence check, unrelated to connector availability.
- `src/codemie/service/analytics/analytics_service.py:45-195+` — `AnalyticsService`, a facade over many
  lazy-loaded handlers (`AIAdoptionHandler`, `SummaryHandler`, `AssistantHandler`, `WorkflowHandler`,
  `ToolsHandler`, `UserHandler`, `ProjectHandler`, `CLIHandler`, `CLIInsightsHandler`, `BudgetHandler`,
  `WebhookHandler`, `MCPHandler`, `LLMHandler`, `EmbeddingsHandler`, `EngagementHandler`,
  `LeaderboardHandler`). Each handler except `AIAdoptionHandler` and `LeaderboardHandler` is constructed with
  `self._repository`, an `@property` (lines 78-83) that lazily instantiates `MetricsElasticRepository()` on
  first access and caches it in `self._repository_instance`. This lazy property is already the mechanism
  requirement 3 asks to retain.
- `src/codemie/repository/metrics_elastic_repository.py:64` — `MetricsElasticRepository`, 38 callers across
  handler files (`assistant_handler.py`, `budget_handler.py`, `handlers/cli/base_handler.py`,
  `handlers/cli/handler.py`, and ~19 more). This is the central ES-backed data-access class most ES read
  endpoints route through.
- `src/codemie/clients/elasticsearch.py:22-56` — `ElasticSearchClient` with `get_client()` /
  `get_async_client()`, per-process cached `Elasticsearch`/`AsyncElasticsearch` clients built from
  `config.ELASTIC_URL` / `ELASTIC_USERNAME` / `ELASTIC_PASSWORD`. No presence/health check is visible in the
  explored slice; connection errors would surface as exceptions from the `elasticsearch` client library at
  call time, not at construction time.
- `src/codemie/clients/clickhouse.py:1-45` — `get_client()` (thread-local cached `clickhouse_connect` client
  built from `config.CLICKHOUSE_HOST/PORT/USER/PASSWORD`) and `ch_query(sql, params)` which runs a query via
  `asyncio.to_thread`. This is the single ClickHouse access chokepoint for CLI Analytics.
- `src/codemie/repository/cli_analytics_repository.py:65-450+` — `LocalAnalyticsFilter` and
  `LocalAnalyticsRepository` (read-only ClickHouse access, `QueryFn`-injected, ~20+ `get_*` query methods:
  `get_cost_kpis`, `get_model_breakdown`, `get_cost_by_user`, `get_users`, `get_users_daily_activity`,
  `get_users_last_active`, `get_lines_totals`, `get_lines_daily`, `get_lines_by_user`,
  `get_lines_by_session`, `get_turns_by_session`, `get_file_facts_by_session`, `get_tool_success_by_session`,
  `get_tool_usage`, `get_invocations`, etc.). All queries go through the injected `QueryFn` (bound to
  `ch_query` in production), giving one seam to intercept ClickHouse-unavailable conditions.
- `src/codemie/service/analytics/handlers/cli_analytics_handler.py:125` — `LocalAnalyticsHandler`, 10 callers
  in `src/codemie/rest_api/routers/cli_analytics.py`. This wraps `LocalAnalyticsRepository` for the CLI
  Analytics endpoints.
- `src/codemie/service/analytics/handlers/ai_adoption_handler.py:41-190+` — `AIAdoptionHandler`, confirmed
  PostgreSQL-only: every read path uses `AsyncSession(PostgresClient.get_async_engine())`; no Elasticsearch
  import or call found in the explored source (`get_ai_adoption_overview`, `get_ai_adoption_maturity`,
  `get_ai_adoption_user_engagement`, `get_user_engagement_users`). It already has an internal empty-response
  pattern: `_empty_maturity_response()` builds a `MockRow` with zero/`'N/A'` values and reuses
  `_build_metrics_from_row()` to produce a schema-correct empty `SummariesResponse`. This is a useful existing
  precedent for "reuse the real response-building code path with zeroed input" rather than hand-building a
  parallel empty payload.
- `src/codemie/service/analytics/response_formatter.py:66-161` — `ResponseFormatter` with
  `create_metadata`, `create_pagination`, `format_summary_response`, `format_tabular_response`. These are
  the existing shared response-shaping helpers already used to build summary/tabular payloads; an empty-path
  builder should reuse these rather than construct raw dicts.
- `src/codemie/rest_api/models/analytics.py` — the real Pydantic response models: `SummariesResponse` /
  `SummariesData` / `Metric`, `KeySpendingResponse` / `KeySpendingData` / `KeySpendingItem`,
  `TabularResponse` / `TabularData` / `ColumnDefinition`, `CliSummaryResponse` / `CliSummaryData`,
  `AnalyticsDetailResponse`, `UsersListResponse` / `UsersListData` / `UserListItem`, `ErrorResponse` /
  `ErrorDetail`. An empty-response builder needs to construct these exact classes (e.g.
  `SummariesData(metrics=[])`, `TabularData(columns=[...], rows=[], totals=None)`) — not a single generic
  empty object, matching requirement 2.
- `src/codemie/rest_api/models/cli_analytics.py` — CLI Analytics response models: `LocalAnalyticsUsersData`
  (`rows: list[LocalAnalyticsUserRow]`), `LocalAnalyticsCostData` (`kpis: LocalAnalyticsCostKPIs`,
  `cost_by_user`, `cost_by_model`), `LocalAnalyticsToolsData` (`tool_usage`, `tokens_by_model`,
  `skills_invoked`, `agent_subtypes`, `slash_commands`), plus per-response wrapper classes. `LocalAnalyticsCostKPIs`
  has no defaults (`total_sessions: int`, `total_cost_usd: float`, etc. — all required, non-optional), so an
  empty-payload builder must supply explicit zero values, not rely on field defaults.
- `src/codemie/configs/customer_config.py:364-393` — `CustomerConfig.is_feature_enabled(feature_key)` →
  `is_component_enabled(f"features:{feature_key}")`; the module-level singleton `customer_config`. This is the
  existing mechanism for `features:cliAnalytics` and is unrelated to `has_enterprise()`, confirming the two
  concerns (feature flag vs. Enterprise package) are already architecturally separate — the fix mainly needs
  to stop conflating "Enterprise package installed" with "Metrics Analytics usable."

### Architecture and Layers Affected

- **API/router layer**: `src/codemie/rest_api/routers/analytics.py` (Metrics Analytics endpoints and the
  `_require_metrics_analytics_enabled` dependency to remove/redesign) and
  `src/codemie/rest_api/routers/cli_analytics.py` (CLI Analytics endpoints, 10+ callers of
  `LocalAnalyticsHandler`).
- **Service/handler layer**: `src/codemie/service/analytics/analytics_service.py` (facade + lazy
  `_repository` property) and its handler classes under `src/codemie/service/analytics/handlers/` (13+
  handler files sharing the lazily-constructed `MetricsElasticRepository`), plus
  `src/codemie/service/analytics/handlers/cli_analytics_handler.py` for CLI Analytics.
- **Repository/connector layer**: `src/codemie/repository/metrics_elastic_repository.py` (ES),
  `src/codemie/repository/cli_analytics_repository.py` (ClickHouse, via injected `QueryFn`),
  `src/codemie/clients/elasticsearch.py`, `src/codemie/clients/clickhouse.py`.
- **Enterprise package boundary**: `src/codemie/enterprise/loader.py` / `src/codemie/enterprise/__init__.py`
  — `has_enterprise()`/`HAS_ENTERPRISE`, currently misused as a Metrics Analytics gate.
- **Config layer**: `src/codemie/configs/customer_config.py` — `is_feature_enabled` /
  `is_component_enabled` for `features:cliAnalytics` and other component flags.
- **PostgreSQL-only path (must stay isolated)**: `src/codemie/service/analytics/handlers/ai_adoption_handler.py`
  using `PostgresClient.get_async_engine()`.

### Integration Points

- `AnalyticsService._repository` property is the single lazy-init seam for `MetricsElasticRepository` shared
  by most ES-backed handlers — the natural point to also detect "ES unavailable" without touching every
  handler individually, if that pattern is chosen.
- `LocalAnalyticsRepository.__init__(self, query_fn: QueryFn)` takes an injected query function (bound to
  `ch_query` in the real router/handler wiring) — the natural seam for a ClickHouse-unavailable check, since
  swapping/wrapping `query_fn` (or catching around its call sites) doesn't require touching each of the 20+
  `get_*` query methods individually.
- `_require_metrics_analytics_enabled` is imported and called from inside `analytics.py`; every endpoint that
  currently declares it as a FastAPI `Depends(...)` is a removal/redesign target — the full list of
  attachment points was not exhaustively enumerated by the codegraph excerpts returned (only the function
  definition and the start of the next `@router.get(...)` decorator at line 688 were shown); a full grep of
  `_require_metrics_analytics_enabled` usages across `analytics.py` is needed before editing.
- `execute_esql_query` (referenced in the exploration output, in `analytics.py`) already uses
  `ExtendedHTTPException` and constants like `ANALYTICS_QUERY_FAILED_MSG` /
  `UNEXPECTED_ERROR_DETAILS_PREFIX` / `LOG_ESQL_UNEXPECTED_ERROR_MSG` — an existing pattern for
  distinguishing/logging ES query failure modes that a new "ES unavailable" branch could extend rather than
  duplicate.

### Patterns and Conventions

- Lazy `@property` initialization for connector-backed dependencies (`AnalyticsService._repository`,
  handler `_*_handler` properties) — instantiate-on-first-access, cache on the instance.
- Facade + delegated-handler pattern: `AnalyticsService` exposes public async methods that thinly delegate to
  private handler instances, one handler class per analytics domain.
- Existing "build empty response through the real formatting code path" precedent in
  `AIAdoptionHandler._empty_maturity_response()` (zeroed mock row fed through the same metric-building
  method used for real rows) — a strong candidate pattern to replicate for ES/ClickHouse empty responses,
  satisfying requirement 2's "reuse existing response factories/models" instruction.
- Shared response-shaping helpers in `ResponseFormatter` (`format_summary_response`,
  `format_tabular_response`) already centralize metadata/pagination construction.
- `ExtendedHTTPException` and typed exceptions are the established error-handling convention per
  `.ai-run/guides/development/error-handling.md` (avoid raw `Exception`, avoid swallowing without context,
  give each distinct failure mode its own message).

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/data/elasticsearch-integration.md` — states ES query/index details belong in
  repository/service classes, not routers, and that index-creation failures should log actionable context
  rather than fail silently. Directly relevant: any new "ES unavailable" detection should live in the
  repository/service layer (e.g. `MetricsElasticRepository` or `AnalyticsService._repository`), not be
  scattered into router code.
- `.ai-run/guides/development/error-handling.md` — mandates typed exceptions (`ExtendedHTTPException`,
  `ValidationException`, domain-specific exceptions) over raw `Exception`; mandates one distinct message per
  distinct failure mode, and logging full detail while returning a sanitized client message. This directly
  constrains how "ES/ClickHouse unavailable" must be distinguished from "unexpected programming error" per
  requirement 2's "do not broadly swallow" instruction.
- No guide file specifically covers ClickHouse integration, CLI Analytics, or a "graceful degradation
  boundary" pattern — this is a gap; conventions for the ES/ClickHouse empty-response fallback will need to
  be derived from the closest existing precedent (`AIAdoptionHandler._empty_maturity_response`) rather than
  documented guidance.

### Architectural Decisions

- No ADRs or inline comments recording a decision about `has_enterprise()`-based gating were found in the
  explored slices; `_require_metrics_analytics_enabled`'s only comment is `"Dependency: raises 503 when
  codemie-enterprise is not installed."`, i.e. it documents its own (undesired) behavior rather than a
  rationale.
- `AnalyticsService._repository`'s docstring ("Lazy-load Elasticsearch repository (avoids ES coupling when
  only PostgreSQL paths are used)") is itself the recorded intent behind requirement 3 — it's already
  present on the feature branch and should be preserved, not reworked.

### Derived Conventions

- Handler classes take `(user, repository)` or just `(user)` in their constructors and are exposed as lazy
  `@property` on `AnalyticsService`; a new empty-response-fallback layer should fit this same shape rather
  than introduce a new construction pattern.
- Response models mirror OpenAPI shapes closely (module docstring in `models/analytics.py`: "matching the
  OpenAPI specification exactly") — empty payloads must be constructed as valid instances of these same
  classes, not simplified substitutes.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/enterprise/test_loader.py` — covers `has_enterprise()` / `HAS_ENTERPRISE` directly
  (`test_has_enterprise_returns_false_when_patched`, `test_has_enterprise_returns_true_when_patched`).
- `tests/codemie/rest_api/routers/test_analytics_capability_guards.py` — exists and is associated with
  `AnalyticsService` in the blast-radius data; very likely the current test file exercising
  `_require_metrics_analytics_enabled` / 503 behavior on Metrics Analytics endpoints and thus a primary file
  to update when that gate is removed/redesigned. Contents were not read in this pass — read it directly
  before editing.
- `tests/codemie/repository/test_metrics_elastic_repository.py` — unit tests for `MetricsElasticRepository`.
- `tests/codemie/service/analytics/test_analytics_service.py` — facade-level tests for `AnalyticsService`.
- `tests/codemie/service/analytics/handlers/test_ai_adoption_handler.py` — covers `AIAdoptionHandler`
  including presumably the empty/maturity fallback path.
- `tests/codemie/service/analytics/handlers/test_assistant_handler.py`,
  `test_budget_handler.py`, `test_cli_cost_processor.py` and others — per-handler ES-backed handler tests.
- `tests/codemie/repository/test_cli_analytics_repository.py` and
  `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` — cover `LocalAnalyticsFilter` /
  `LocalAnalyticsRepository` / `LocalAnalyticsHandler` (ClickHouse/CLI Analytics path), including filter
  resolution and delegation tests (`test_is_unattributed_filter_delegates_to_repository`,
  `test_branch_filter_forwarded_to_get_session_cost_facts`, etc.).
- `tests/codemie/clients/test_clickhouse.py` — covers the ClickHouse `get_client`/`ch_query` module.

### Testing Framework and Patterns

- pytest-based, per `.ai-run/guides/testing/testing-patterns.md` (not read in full this pass — load before
  writing tests). Handler tests use factory-style fixtures (`make_handler`, `make_repo_handler`,
  `make_filter`) and mock user/repository fixtures (`mock_user`, `mock_repository`) visible in the
  cli_analytics handler/repository test blast-radius data.

### Coverage Gaps

- No test file was found exercising "Elasticsearch unavailable → 200 with empty schema-valid response" or
  "ClickHouse unavailable → 200 with empty schema-valid response" for any Metrics Analytics or CLI Analytics
  endpoint — this is the central new-behavior gap the task must fill with new tests.
- No test file confirming that AI/Run Adoption's PostgreSQL-only handlers never construct/connect to
  Elasticsearch under any code path was found (behavior is inferred from source, not asserted by a test).
- `_require_metrics_analytics_enabled`'s full removal blast radius (every endpoint it currently gates) was
  not exhaustively confirmed by codegraph in this pass — needs a direct search of `analytics.py` before
  editing to ensure no gated endpoint is missed in either removal or test updates.

---

## 5. Configuration and Environment

### Environment Variables

- `ELASTIC_URL`, `ELASTIC_USERNAME`, `ELASTIC_PASSWORD` — consumed by `ElasticSearchClient` in
  `src/codemie/clients/elasticsearch.py` to build the ES client(s).
- `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`,
  `CLICKHOUSE_QUERY_TIMEOUT_SECONDS` — consumed by `get_client()` in `src/codemie/clients/clickhouse.py`.

### Configuration Files

- `src/codemie/configs/customer_config.py` — `CustomerConfig.is_feature_enabled("cliAnalytics")` /
  `is_component_enabled("features:cliAnalytics")`, the existing feature-flag mechanism gating CLI Analytics
  access alongside role checks (task explicitly out-of-scope to modify the flag's config-source migration,
  but the flag read itself remains relevant to preserving role/feature-flag behavior per requirement 5).

### Feature Flags and Deployment Concerns

- `features:cliAnalytics` component flag (read via `customer_config.is_feature_enabled`) combined with
  Admin/Project Admin role checks gates CLI Analytics; this rule must be preserved unchanged per requirement
  5 — only the *connector-unavailable* behavior underneath it changes (200 + empty vs. current failure mode).
- No feature flag equivalent was found gating Metrics Analytics itself in the explored code other than the
  `has_enterprise()`-backed dependency being removed; access to Metrics Analytics endpoints appears otherwise
  controlled by auth/role dependencies elsewhere in `analytics.py` not covered in this research pass.

---

## 6. Risk Indicators

- **Incomplete enumeration of `_require_metrics_analytics_enabled` attachment points.** Codegraph returned
  only the function definition and the start of the next decorator (`analytics.py:688`); the full set of
  endpoints declaring it as a `Depends(...)` was not confirmed. Speculative: this likely spans most/all
  Metrics Analytics GET endpoints in `analytics.py`, but the exact list must be grepped directly before
  removal to avoid leaving a dangling 503 on any endpoint.
- **`tests/codemie/rest_api/routers/test_analytics_capability_guards.py` was not read.** Its exact
  assertions (does it test for 503, for specific endpoints, for a mocked `has_enterprise`?) are unknown; this
  file will almost certainly need substantial rewriting and is a concrete unread dependency for the plan
  phase.
- **No existing "connector unavailable" exception type was located** for either Elasticsearch or ClickHouse
  in the explored code (only generic `elasticsearch`-library/`clickhouse_connect`-library exceptions would
  naturally surface, e.g. connection refused/timeout). Speculative: the task may need a thin recognized-error
  detection step (e.g. catching `elasticsearch.exceptions.ConnectionError`/`TransportError` and
  `clickhouse_connect`'s connection exceptions) rather than a pre-existing typed exception to key off — this
  is a design decision for the spec/plan phase, not confirmed by research.
- **`LocalAnalyticsCostKPIs` and other CLI Analytics models have required (non-optional, no-default) numeric
  fields** (e.g. `total_sessions: int`, `total_cost_usd: float`). An empty-response builder must explicitly
  populate every required field with a zero-equivalent value — omission will fail Pydantic validation, unlike
  models with `Field(default_factory=list)` collections.
- **Requirement 6's named risk (direct Enterprise imports for personal LiteLLM spending / leaderboard
  computation)** was not located or inspected in this research pass — `LeaderboardHandler` was seen only by
  name in `AnalyticsService` (constructed with just `user`, no repository), and its internal implementation
  (and whether it imports Enterprise code directly) was not explored. This is an open question the plan phase
  must resolve, per the task's own instruction to document such a decision as a risk.
- **AI/Run Adoption PostgreSQL isolation is confirmed by source reading but not by a dedicated test** — no
  test asserting "constructing/using `AIAdoptionHandler` never touches Elasticsearch" was found; a regression
  here would be silent unless caught by broader integration behavior.
- **No guide documents a "graceful degradation" or "connector-unavailable empty response" pattern** in this
  repository; the nearest precedent (`AIAdoptionHandler._empty_maturity_response`) is PostgreSQL-specific and
  was not designed as a reusable cross-connector utility — any centralization work is new design, not an
  existing convention to follow mechanically.

---

## 7. Summary for Complexity Assessment

The change spans three layers — API routers (`analytics.py`, `cli_analytics.py`), the service/handler facade
(`AnalyticsService` and ~15 handler classes under `service/analytics/handlers/`), and two connector/repository
seams (`MetricsElasticRepository`/`ElasticSearchClient` for ES, `LocalAnalyticsRepository`/`ch_query` for
ClickHouse) — plus a small, well-isolated edit to the Enterprise-package boundary
(`enterprise/loader.py`/`_require_metrics_analytics_enabled`) and a check that the existing PostgreSQL-only
`AIAdoptionHandler` path is left untouched. The core mechanical fixes (deleting/redesigning the
`has_enterprise()`-based dependency, and confirming the already-present lazy `_repository` property stays
lazy) are small and low-risk. The larger part of the work — a centralized fallback boundary that returns each
endpoint's real, schema-valid empty response on recognized ES/ClickHouse-unavailable conditions — is design
work with two natural seams already present in the code (`AnalyticsService._repository` for ES,
`LocalAnalyticsRepository`'s injected `QueryFn` for ClickHouse) and one internal precedent to generalize from
(`AIAdoptionHandler._empty_maturity_response`), but no existing cross-connector utility to reuse directly.

Test coverage is present for the connector classes individually (`test_metrics_elastic_repository.py`,
`test_cli_analytics_repository.py`, `test_clickhouse.py`) and for the Enterprise-package flag
(`test_loader.py`), and there is an existing capability-guard test file
(`test_analytics_capability_guards.py`) whose current content is unknown and will need rewriting — but there
is a complete gap in tests for the new "connector unavailable → 200 + empty schema-valid response" behavior
across both ES-backed and ClickHouse-backed endpoints, which is the task's central new contract.

Key risk factors: (1) the full list of endpoints gated by `_require_metrics_analytics_enabled` was not
exhaustively confirmed and needs a direct grep before editing; (2) no existing typed exception distinguishes
"connector unavailable" from other ES/ClickHouse errors, so recognizing the right failure mode without
over-broadly swallowing errors is a design decision, not a lookup; (3) CLI Analytics response models contain
required non-defaulted fields that make "empty" construction error-prone if not built carefully per model;
(4) whether `LeaderboardHandler` (or other handlers) contain direct Enterprise imports on a visible tab's read
path — flagged as an explicit open question by the task itself — was not resolved by this research pass and
must be decided in the plan/spec phase.

---

## 8. External References

None named by the task. The task's "authoritative task file" is quoted in full inline within `task_context`
(reproduced verbatim in Section 1 above) rather than referenced as a separate path or URL to open.
