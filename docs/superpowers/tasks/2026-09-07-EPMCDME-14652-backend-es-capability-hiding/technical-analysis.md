# Technical Research

**Task**: elasticsearch capability-gating settings configuration
**Generated**: 2026-09-07T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

**Ticket**: EPMCDME-14652 — Backend ES capability hiding

**Goal**: Ensure unsupported capabilities are reported as unavailable and cannot execute through backend APIs or background processing when Elasticsearch is not available or not configured for those capabilities.

**Scope — 6 capabilities**:
1. Leaderboard
2. Conversation Analytics
3. Metrics Analytics (in-product usage dashboards)
4. Smart Tool Selection
5. Admin Log Lookup
6. Stale-Datasource Detection

**Companion ticket**: EPMCDME-14657 — Frontend visibility behavior (Ivan's scope includes both backend gating and frontend visibility).

**Research inputs (as named in task)**:
1. ES Consumer Inventory: `C:/Users/IvanKryzhanovskyi/Downloads/2026-08-31-es-consumer-inventory.md`
2. Frontend capability audit: `C:/codemie-dev/codemie-ui/docs/superpowers/tasks/2026-09-03-epmcdme-14679-capability-building-blocks-ui/technical-analysis.md`
3. Consolidated backend/frontend capability research: `C:/codemie-dev/codemie/local/EPMCDME-14679-capability-building-blocks-draft.md`
4. Build and startup spike report: `C:/codemie-dev/codemie-standalone/local/spike-report.md`

**Constraints from task**:
- Do NOT repeat the ES kill-switch experiment; do not infer that a capability requires disabling merely because it imports an ES-related class.
- The inventory's synthetic kill-switch experiment is NOT equivalent to normal ES absence.
- pgvector: out of scope.
- ClickHouse: out of scope.
- Dynamic ES detection: optional, introduce only if a clearly reusable low-risk mechanism already exists.
- Prefer existing config/feature-gating patterns.
- Existing ES-enabled deployments must preserve current behavior.
- The consolidated draft has outdated architecture/pgvector assumptions. Do NOT treat them as approved design.

---

## 2. Codebase Findings

### Existing Implementations

**Elasticsearch client** (`src/codemie/clients/elasticsearch.py`)
- Provides `ElasticSearchClient.get_client()` (sync) and `ElasticSearchClient.get_async_client()` (async) class methods.
- Hard-imports the `elasticsearch` package — no install-check guard at the main app level.
- 21 confirmed production/script call sites across the codebase (per ES Consumer Inventory, 2026-08-31).
- `ELASTIC_URL`, `ELASTIC_USERNAME`, `ELASTIC_PASSWORD` consumed from `config`.

**Config flags for the 6 capabilities** (`src/codemie/configs/config.py`)

| Capability | Config flag | Default | What it gates |
|---|---|---|---|
| Leaderboard | `LEADERBOARD_ENABLED` (line 797) | `False` | `LeaderboardScheduler` startup only |
| Conversation Analytics | `CONVERSATION_ANALYSIS_ENABLED` (line 780) | `False` | `ConversationAnalysisScheduler` startup; `POST /v1/conversation-analysis/trigger` endpoint guard |
| Metrics Analytics | **No `_ENABLED` flag found** | — | `analytics.router` unconditional; no scheduler flag for the dashboard query path |
| Smart Tool Selection | `TOOL_SELECTION_ENABLED` (line 754) | `False` | Startup ES vector-index creation only |
| Admin Log Lookup | **No flag found** | — | `POST /logs` router unconditional |
| Stale-Datasource Detection | `STALE_DATASOURCE_ENABLED` (line 827), `STALE_DATASOURCE_DELETION_ENABLED` (line 833) | both `False` | `StaleDatasourceScheduler` startup; deletion phase sub-gate |

Additional ES-related flags present: `METRICS_ROTATION_ENABLED: bool = False` (line 804) gates `MetricsRotationScheduler` (ES index rotation job) — separate from the analytics dashboard path.

**Service-layer implementations**

- `src/codemie/service/analytics/analytics_service.py` — `AnalyticsService` eagerly constructs `MetricsElasticRepository()` in `__init__` regardless of which analytics handler is invoked. All usage-metrics query handlers route through this repository.
- `src/codemie/service/logs.py` — `LogService.get_logs_by_target_field()` calls `ElasticSearchClient.get_client()` directly; queries `config.ELASTIC_LOGS_INDEX`.
- `src/codemie/service/stale_datasource/stale_datasource_service.py` — `_fetch_lifecycle_metrics()` and `_fetch_tool_usage_metrics()` use `MetricsElasticRepository`; `_delete_stale_indexes()` calls `ElasticSearchClient.get_client()` directly.
- `src/codemie/service/stale_datasource/scheduler.py` — `StaleDatasourceScheduler.start()` re-checks `config.STALE_DATASOURCE_ENABLED` defensively.
- `src/codemie/service/conversation_analysis/conversation_analytics_elasticsearch_service.py` — `create_index_if_not_exists()`, `index_analytics()`, `delete_analytics()` call `get_client()` directly. ES errors are swallowed in `index_analytics()`/`delete_analytics()` ("Postgres is source of truth"). `create_index_if_not_exists()` is called at startup when `CONVERSATION_ANALYSIS_ENABLED=True`; that call is wrapped at `main.py:438-441` and fails soft.
- `src/codemie/service/tools/toolkit_lookup_service.py` — `_setup_elasticsearch_index()` creates an ES vector store at startup; `get_tools_by_query()` performs hybrid vector search. Both call `ElasticSearchClient.get_client()`.
- `src/codemie/agents/smart_tool_selector.py` — `SmartToolSelector.select_tools()` calls `toolkit_lookup_service.get_tools_by_query()` — requires ES at call time.
- `src/codemie/repository/metrics_elastic_repository.py` — `MetricsElasticRepository.__init__` (line 69) calls `ElasticSearchClient.get_async_client()`; used by `StaleDatasourceService` and `AnalyticsService`. Catches `NotFoundError` and `ApiError` in query methods (lines 156, 211, 101, 159, 214).

**Leaderboard read path** (`src/codemie/service/analytics/handlers/leaderboard_handler.py`)
- `LeaderboardHandler` reads exclusively from PostgreSQL via async SQLAlchemy — no ES dependency.
- Leaderboard **computation** uses `MetricsElasticRepository`; runs in `LeaderboardScheduler` gated by `LEADERBOARD_ENABLED`.

**AI Adoption handler** (`src/codemie/service/analytics/handlers/ai_adoption_handler.py`)
- `AIAdoptionHandler` uses PostgreSQL/AsyncSession only — no direct ES dependency.
- However it is invoked through `AnalyticsService`, whose `__init__` eagerly constructs `MetricsElasticRepository` (ES dependency on construction of the service, not on the handler call).

**`/v1/config` frontend contract** (`src/codemie/rest_api/routers/customer_config.py`)
- `GET /v1/config` calls `resolve_components()` and returns `List[Component]` (id + settings.enabled).
- Runtime-computed components defined in `CONFIG_IDS` and `_get_runtime_config()` in `src/codemie/configs/customer_config.py`:
  - `features:enterpriseEdition` — from `importlib.metadata.version("codemie-enterprise")`
  - `features:userManagement` — from `config.ENABLE_USER_MANAGEMENT`
  - `idpProvider` — from `config.IDP_PROVIDER`
  - `mcpAuthOrigin` — from `config.CALLBACK_API_BASE_URL`
  - `features:chatContextualNaming` — from `config.CHAT_CONTEXTUAL_NAMING_ENABLED`
  - `features:budgetSoftLimitNotification` — from `config.BUDGET_SOFT_LIMIT_NOTIFICATION_ENABLED`
- None of the 6 ES-gated capabilities are currently represented in `CONFIG_IDS` or `_get_runtime_config()`.
- Additional YAML-defined components are loaded from `customer-config.yaml` (deployment-supplied, path `config.CUSTOMER_CONFIG_DIR/customer-config.yaml`).

**ES global application handler** (`src/codemie/rest_api/main.py:1026-1041`)
- `@app.exception_handler(ApiError)` maps any uncaught `elasticsearch.ApiError` to HTTP 503 "Elastic service unavailable". This is the only app-wide ES error contract.

### Architecture and Layers Affected

| Layer | Components |
|---|---|
| **Config** | `src/codemie/configs/config.py` (capability flags), `src/codemie/configs/customer_config.py` (CONFIG_IDS, `_get_runtime_config`) |
| **REST API (routers)** | `src/codemie/rest_api/routers/logs.py`, `analytics.py`, `customer_config.py`, `conversation_analysis.py` |
| **REST API (startup/assembly)** | `src/codemie/rest_api/main.py` — scheduler setup guards, router registration guards |
| **Service** | `analytics_service.py`, `logs.py`, `stale_datasource_service.py`, `toolkit_lookup_service.py`, `conversation_analytics_elasticsearch_service.py` |
| **Repository** | `metrics_elastic_repository.py` |
| **Agent** | `src/codemie/agents/smart_tool_selector.py` |

### Integration Points

- **`ElasticSearchClient`** — central ES connection factory; consumed directly by service and repository code for all 6 capabilities.
- **`MetricsElasticRepository`** — used by `AnalyticsService` (all usage-metrics handlers) and `StaleDatasourceService`.
- **APScheduler** — manages `LeaderboardScheduler`, `ConversationAnalysisScheduler`, `StaleDatasourceScheduler`, `MetricsRotationScheduler`; each is conditionally registered in `main.py`.
- **`GET /v1/config`** — backend's single authoritative surface for exposing capability state to the frontend; currently does not include the 6 ES-gated capabilities.

### Patterns and Conventions

- **Per-feature `config.*_ENABLED: bool = False`** in `Config` (pydantic-settings `BaseSettings`) is the established pattern for gating optional capabilities. See `LEADERBOARD_ENABLED`, `STALE_DATASOURCE_ENABLED`, `CONVERSATION_ANALYSIS_ENABLED`, `TOOL_SELECTION_ENABLED`.
- **Scheduler startup guard at `main.py`**: each scheduler setup function checks its own `config.*_ENABLED` before registering with APScheduler.
- **Router registration guard at `main.py`**: used for `ENABLE_USER_MANAGEMENT`, `LLM_PROXY_BUDGET_CHECK_ENABLED`, `MCP_AUTH_ENABLED`. Routers excluded at startup rather than guarded per-request.
- **Runtime component projection via `_get_runtime_config()`**: the extension point for exposing backend-resolved capability state to the frontend via `GET /v1/config`.
- **`OverrideCache` (customer-config service)**: allows database overrides of declared config components; TTL-based; used for YAML-based components, not runtime-computed ones.
- **Swallowed-exception pattern**: rows 9, 10, 11 in the ES inventory (stale datasource, conversation analytics, metrics rotation schedulers) each wrap their entire body in `try/except Exception → logger.error(...)`, causing silent no-ops on ES failure.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/data/elasticsearch-integration.md` — short; establishes that ES query bodies belong in repositories/services, not routers. Documents `BaseElasticRepository` as the intended repository-access pattern. Notes that index creation belongs in startup/dedicated services.
- `.ai-run/guides/development/configuration-patterns.md` — instructs gating optional behavior through config at assembly points or service boundaries; not scattered in unrelated modules. References `main.py:706` as the established pattern for router-level gating.
- `.ai-run/guides/architecture/layered-architecture.md` — not read in full; referenced for layer boundaries.

### Architectural Decisions

- **`BaseElasticRepository`** is documented in the ES guide as the intended pattern but is confirmed dead in production: only `DummyElasticRepository` in tests extends it. `BaseModelWithElasticSupport` and `MetricsElasticRepository` are the live patterns.
- **`features:enterpriseEdition`** is projected as a runtime component derived from package presence (`importlib.metadata`), not a configurable boolean. This establishes the pattern for capability state derived from system state rather than explicit env-var.

### Derived Conventions

- Capability flags default to `False`; ES-backed capabilities opt in explicitly per deployment.
- Scheduler-level `*_ENABLED` guards are the current ES-gating mechanism for background jobs; they function as feature gates, not ES-availability toggles.
- The `GET /v1/config` response is the intended single source of truth for the frontend. Adding a new capability flag to the frontend requires: a new CONFIG_IDS entry + `_get_runtime_config()` entry in `customer_config.py`, and a corresponding `FEATURE_FLAGS` constant in the frontend.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/analytics/handlers/test_leaderboard_handler.py` — covers `LeaderboardHandler` PostgreSQL read path.
- `tests/codemie/agents/test_smart_tool_selection.py` — covers `SmartToolSelector.select_tools()`.
- `tests/codemie/service/test_customer_config_service.py` — covers `resolve_components()`, override merging, enabled filtering.
- `tests/codemie/repository/test_base_elastic_repository.py` — covers `DummyElasticRepository` only; `BaseElasticRepository` has no production coverage.
- `tests/codemie/service/analytics/handlers/test_budget_usage_service.py` — uses `sys.modules` mock pattern for enterprise package; established precedent for enterprise-absent test scenarios.

### Testing Framework and Patterns

- pytest; async tests via `pytest-asyncio`.
- `sys.modules` mocking for enterprise package absence (established in budget usage tests).
- No fixture established for "ES-absent" router surface testing.

### Coverage Gaps

- `LogService.get_logs_by_target_field()` — no unit test found for the ES call path.
- `StaleDatasourceService` — no unit test found.
- `AnalyticsService` construction-time `MetricsElasticRepository` dependency — no test covering the ES-coupling behavior.
- Scheduler startup gates in `main.py` for the 6 capabilities — no integration test exercises `_setup_*_scheduler` with the flag combinations relevant to this ticket.
- No integration test exercises the full router surface with ES absent or with capability flags set to `False`.

---

## 5. Configuration and Environment

### Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `ELASTIC_URL` | `"http://localhost:9200"` | ES cluster endpoint |
| `ELASTIC_USERNAME` | `""` | ES auth username |
| `ELASTIC_PASSWORD` | `""` | ES auth password |
| `ELASTIC_LOGS_INDEX` | `"logs-codemie-infra*"` | Index queried by Admin Log Lookup |
| `ELASTIC_METRICS_INDEX` | `"codemie_metrics_logs*"` | Index queried by analytics/stale-datasource |
| `LEADERBOARD_ENABLED` | `False` | Scheduler gate for leaderboard computation |
| `CONVERSATION_ANALYSIS_ENABLED` | `False` | Scheduler gate + trigger endpoint gate |
| `STALE_DATASOURCE_ENABLED` | `False` | Scheduler gate for stale-datasource detection |
| `STALE_DATASOURCE_DELETION_ENABLED` | `False` | Sub-gate for deletion phase (requires `STALE_DATASOURCE_ENABLED=True`) |
| `TOOL_SELECTION_ENABLED` | `False` | Gates startup ES vector-index creation for smart tool selection |
| `METRICS_ROTATION_ENABLED` | `False` | Gates MetricsRotationScheduler (separate from analytics dashboard) |
| `TOOLS_INDEX_NAME` | `"codemie_tools"` | ES index name for smart tool vector store |

### Configuration Files

- `src/codemie/configs/config.py` — single pydantic-settings `Config` class; all capability flags live here; supports `.env` and `.env.local` overrides.
- `customer-config.yaml` (deployment-supplied, path `config.CUSTOMER_CONFIG_DIR`) — YAML-defined components served via `GET /v1/config`; overridable via database through `PUT /v1/config/declarations/{component_id}`.

### Feature Flags and Deployment Concerns

- All 6 capability flags default to `False` — existing ES-enabled deployments opt in explicitly.
- The `finalize_settings()` model validator in `Config` validates `STALE_DATASOURCE_SCHEDULE` cron syntax when `STALE_DATASOURCE_ENABLED=True`, and enforces `STALE_DATASOURCE_DELETION_ENABLED` requires `STALE_DATASOURCE_ENABLED`.
- `features:enterpriseEdition` is derived at runtime from package presence, not from a YAML or env var — established precedent for runtime-computed capability state.
- No restart is required for YAML-component overrides (TTL cache), but scheduler registration and router registration happen at startup only — a flag change without restart has no effect on already-registered routes/schedulers.

---

## 6. Risk Indicators

- **Row 17 blocker (out of scope here, but upstream context)**: `GoogleDocDatasourceProcessor.client = ElasticSearchClient.get_client()` is a class attribute evaluated at import time, causing app-wide boot failure under any ES kill-switch. Per ES Consumer Inventory, this must be resolved before any per-capability ES gating can take effect at runtime. This ticket's scope is capability gating for the 6 named features, not this boot-path issue — but it is the upstream blocker for ES-free deployment.

- **`manage_preconfigured_assistants()` startup blocker (out of scope here)**: Per spike report, the upstream image fails at `es.indices.create()` inside `manage_preconfigured_assistants()` on a fresh database with no ES. This is a separate startup dependency from the 6 capabilities in scope.

- **No `ELASTICSEARCH_ENABLED` global flag exists**: There is no single switch that gates all ES usage. Adding one is not an existing pattern; the per-capability `*_ENABLED` flag approach is the established convention and should be extended.

- **Admin Log Lookup has no config flag**: `POST /logs` in `src/codemie/rest_api/routers/logs.py` has no `*_ENABLED` gate at any layer — neither router-registration nor endpoint-level. Unlike the other 5 capabilities, there is no existing guard to extend.

- **Smart Tool Selection `TOOL_SELECTION_ENABLED` gates startup indexing only**: The flag controls whether the ES vector index is created at startup (`ToolkitLookupService._setup_elasticsearch_index()`). It does not gate the `get_tools_by_query()` call path at request time. Speculative: if a router-level or service-level gate is added, it would need to cover the search call path, not just startup indexing.

- **`AnalyticsService` eager `MetricsElasticRepository` construction (D16)**: `AnalyticsService.__init__` constructs `MetricsElasticRepository()` unconditionally. Any request to `analytics.router` — including AI Adoption queries, which are PostgreSQL-backed — triggers an `get_async_client()` call on service construction. This couples AI Adoption to ES at the service layer even though the handler itself is ES-independent.

- **`conversation_analysis.router` registered unconditionally**: `CONVERSATION_ANALYSIS_ENABLED=False` disables the scheduler but the router remains registered and endpoint-accessible. The trigger endpoint (`POST /v1/conversation-analysis/trigger`) has an explicit flag check and returns 400 when disabled. Other endpoints return empty data.

- **4 swallowed-exception sites produce silent no-ops under ES failure**: Rows 3, 9, 10, 11 in the ES inventory. For the 6 scoped capabilities: stale-datasource scheduler (row 9) and conversation analytics (row 10) swallow errors and report success to APScheduler. An ES outage causes these jobs to log one ERROR line and return normally — no scheduler-visible failure signal.

- **`aiChampionsLeaderboard` is an unregistered inline flag**: Used as a literal string in `AnalyticsPage.tsx`; not in the `FEATURE_FLAGS` constant registry. Invisible to TypeScript type checks. If this capability needs a backend-projected state, the inline string and `FEATURE_FLAGS` gap both require attention (D10 in building blocks draft).

- **None of the 6 capability states are exposed via `GET /v1/config`**: The frontend currently has no way to check whether these capabilities are available without an independent flag. The `_get_runtime_config()` extension point exists and is used for 6 other capabilities (`enterpriseEdition`, `userManagement`, etc.) but has not been extended for these 6.

- **No test coverage for Log Lookup and StaleDatasourceService**: The two capabilities with the least test coverage are also the ones with no existing gating mechanism (`logs.py` is completely ungated; `StaleDatasourceService` has no unit tests found).

- **Asymmetric scheduler flag re-check**: `StaleDatasourceScheduler.start()` and `ConversationAnalysisScheduler.start()` re-check their own flag defensively; `MetricsRotationScheduler.start()` does not (relies solely on `main.py:564` caller check). Not a live defect for this ticket, but relevant to consistency when adding new gates.

---

## 7. Summary for Complexity Assessment

The ticket requires adding backend capability gating for 6 Elasticsearch-backed features. The codebase has a well-established per-feature gating pattern: `config.*_ENABLED: bool = False` in `Config`, scheduler startup guards in `main.py`, and runtime component projection via `_get_runtime_config()` in `customer_config.py`. Four of the six capabilities (Leaderboard, Conversation Analytics, Smart Tool Selection, Stale-Datasource Detection) already have `*_ENABLED` flags that gate their scheduler startup; the gaps are that these flags do not gate API-level execution and are not projected via `GET /v1/config` to the frontend. Two capabilities (Metrics Analytics and Admin Log Lookup) have no flag at all — their routers are unconditional and their service code calls ES without any availability guard.

The frontend visibility side (EPMCDME-14657) requires adding capability flags to `GET /v1/config` via `CONFIG_IDS` and `_get_runtime_config()` (backend) and corresponding `FEATURE_FLAGS` constants plus component consumers (frontend). The `features:enterpriseEdition` and `features:chatContextualNaming` entries are direct precedents for this pattern. The key complication is that the current `FeatureGuard(features:enterpriseEdition)` on all `/analytics/*` routes is implementation coupling that would need to be disentangled — the analytics dashboard area would need per-sub-capability flags rather than the blanket enterprise-edition gate. The `aiChampionsLeaderboard` inline flag is a pre-existing gap (D10) that would surface here.

The risk surface is moderate. The 6 capabilities span 3 architectural layers (config, service, REST API) plus the frontend config contract. The most complex areas are: (1) the `AnalyticsService` eager ES coupling (D16) which affects AI Adoption even though AI Adoption is PostgreSQL-backed, (2) Smart Tool Selection's `TOOL_SELECTION_ENABLED` flag gating only the startup path rather than the request-time search path, and (3) the complete absence of any gating for Admin Log Lookup. The 4 swallowed-exception sites in schedulers mean that adding a flag gate without also preventing the scheduler from starting with ES unavailable would result in silent no-ops rather than visible failures. Tests are weakest for Log Lookup and StaleDatasourceService, meaning any gating code in those paths would be delivered without existing test scaffolding to verify correctness.

---

## 8. External References

### 1. ES Consumer Inventory
**Path**: `C:/Users/IvanKryzhanovskyi/Downloads/2026-08-31-es-consumer-inventory.md`
**Resolved**: Yes

Key facts:
- 21 confirmed production/script call sites of `ElasticSearchClient`; **none have an ES-specific toggle**.
- 3 of the 6 scoped capabilities sit behind a feature-level gate: rows 9 (`STALE_DATASOURCE_ENABLED`), 10 (`CONVERSATION_ANALYSIS_ENABLED`), 11 (`METRICS_ROTATION_ENABLED`). These gate the entire feature, not just its ES call.
- **Row 17** (`GoogleDocDatasourceProcessor.client = ElasticSearchClient.get_client()` as a class attribute) is the only confirmed app-wide boot failure — fires during routine startup import chain before the ASGI app object exists. This is an upstream blocker for ES-free operation but is outside this ticket's scope.
- **4 swallowed-exception sites**: row 3 (routers/index.py — misleading 404), row 9 (stale datasource scheduler — silent no-op), row 10 (conversation analytics — swallowed in `index_analytics`/`delete_analytics`), row 11 (metrics rotation scheduler — silent no-op).
- Row 9's first ES entry point is `MetricsElasticRepository.__init__` (via `get_async_client()`), not `stale_datasource_service.py:467` as originally documented — the service never reaches line 467 when the repository constructor fails.
- Rows 1, 2, 4–8, 13–16 remain unreachable live behind row 17 and are confirmed statically only.
- 14 additional files are coupled to the `elasticsearch` library directly (NotFoundError, ApiError, helpers.bulk/scan) without calling `ElasticSearchClient` — 9 net-new files beyond the 21 consumer table rows.
- The kill-switch used in the inventory is explicitly NOT production-equivalent to normal ES absence; findings on swallowed exceptions and boot behavior are valid, but the kill-switch experiment scope must not be re-run.

### 2. Frontend Capability Audit
**Path**: `C:/codemie-dev/codemie-ui/docs/superpowers/tasks/2026-09-03-epmcdme-14679-capability-building-blocks-ui/technical-analysis.md`
**Resolved**: Yes

Key facts:
- `GET /v1/config` is consumed by `src/hooks/useFeatureFlags.ts` via `appInfoStore`; `isConfigItemEnabled(configs, featureFlag)` is the evaluation function.
- `FeatureGuard` (route-level, throws 404 via ErrorBoundary) is applied to exactly 5 routes: `/analytics/*` (×3, `features:enterpriseEdition`), `/settings/administration/ai-adoption-config` (`features:enterpriseEdition`), `/settings/administration/cost-centers/*` (×2, `features:costCenters`).
- All analytics routes are currently gated by `features:enterpriseEdition` — this is implementation coupling from the enterprise package presence check, not a per-sub-capability flag.
- `aiChampionsLeaderboard` is used as an inline literal string in `AnalyticsPage.tsx` (not in `FEATURE_FLAGS` constant); guards the Leaderboard tab within the analytics page.
- `features:workflowAI`, `features:subWorkflow`, `mcpConnect`, `features:userManagement`, `features:budgetManagement`, `features:costCenters`, `features:chatContextualNaming` are all registered in `FEATURE_FLAGS`.
- No existing frontend flag for: Metrics Analytics (usage dashboards), Admin Log Lookup, Stale-Datasource Detection, Conversation Analytics, or Smart Tool Selection.
- Nav items are hidden by `isEnterpriseEdition()` for Analytics; hiding is a discoverability control, not an authorization barrier — backend enforcement is authoritative.

### 3. Consolidated Backend/Frontend Capability Research (Building Blocks Draft)
**Path**: `C:/codemie-dev/codemie/local/EPMCDME-14679-capability-building-blocks-draft.md`
**Resolved**: Yes

Key facts relevant to this ticket (facts only; draft proposals are marked Speculative in Section 6):
- `analytics.router` is always registered (unconditional in `main.py`); no existing router-level gate.
- `conversation_analysis.router` is always registered; `CONVERSATION_ANALYSIS_ENABLED` disables scheduler only.
- Leaderboard read APIs are backed by PostgreSQL snapshots; computation uses `MetricsElasticRepository`. Per parent requirements, Leaderboard is excluded from standalone deployment.
- Smart Tool Selection is excluded from standalone deployment and pgvector migration scope by parent requirements.
- `AnalyticsService.__init__` eagerly constructs `MetricsElasticRepository` regardless of handler (D16 — implementation gap, not product constraint).
- Capability state model in draft (D11): backend resolves effective capability state and exposes via `/v1/config`; frontend consumes only that resolved backend state. This is the proposed design direction, not yet implemented.
- `CONFIG_IDS` and `_get_runtime_config()` are the confirmed extension points for adding new capability components to the `GET /v1/config` response.
- Draft notes that the consolidated document has "outdated architecture/pgvector assumptions" — confirmed constraint in task context.

### 4. Build and Startup Spike Report
**Path**: `C:/codemie-dev/codemie-standalone/local/spike-report.md`
**Resolved**: Yes

Key facts:
- Build compatibility confirmed: existing standalone Dockerfile builds current upstream `codemie@e953905` + `codemie-ui@1bf0010` without modification.
- **ES-free runtime not confirmed**: upstream image fails at `manage_preconfigured_assistants()` → `create_context_from_index()` → `es.indices.create()` during `_initialize_preconfigured_content()` lifespan initialization. This is a startup-time unconditional ES call on a fresh database with no Elasticsearch — separate from the 6 gated capabilities.
- Upstream `ELASTIC_URL` defaults to `http://localhost:9200` (syntactically valid URL); Alembic migration `303600fb4430` passes URL validation. Standalone snapshot uses an empty string default causing a `ValueError` in that migration.
- The `codemie.enterprise.*` namespace in `backend/src/codemie/enterprise/` is the OSS compatibility shim (`loader.py` wraps all enterprise imports in `try/except ImportError`); startup does not fail when `codemie_enterprise` is absent.
- CRLF portability bug in `docker/entrypoint.sh` on Windows — packaging finding, not a runtime dependency.
