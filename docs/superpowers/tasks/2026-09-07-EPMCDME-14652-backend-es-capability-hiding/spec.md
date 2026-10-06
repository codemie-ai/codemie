# EPMCDME-14652 — Backend ES Capability Hiding

**Original complexity**: L (score 21); **Revised scoped estimate**: L (~4–5 days, see §Revised Complexity)
**Scope**: backend enforcement guards + `GET /v1/config` projection for ES-dependent capabilities
**Companion**: EPMCDME-14657 (frontend consumption)
**Yevhenii boundary**: retrieval, knowledge-base, data-source, indexing, ES-free startup

---

## Problem

Several ES-dependent capabilities have no API-level enforcement guards and are absent from
`GET /v1/config`. When ES or enterprise features are absent, requests reach ES-calling code and
the frontend has no signal to hide relevant UI. Metrics Analytics (`analytics.router`) has no
backend enforcement gate — any deployment, including PostgreSQL-only, can invoke Metrics Analytics
endpoints which depend on `MetricsElasticRepository`. The same router also contains AI Adoption
endpoints (`/ai-adoption-*`) that are PostgreSQL-only; these must remain reachable regardless of
enterprise presence. `AnalyticsService.__init__` eagerly constructs `MetricsElasticRepository`,
coupling AI Adoption to ES at construction time even though AI Adoption never accesses it (D16
— confirmed defect, in scope for this ticket). Two capabilities (Stale-Datasource Detection and
Conversation Analytics) already have scheduler guards. Stale-Datasource has no public API surface.

---

## Capability Inventory

| Capability | Existing flag | Gap | Action |
|---|---|---|---|
| Leaderboard | `LEADERBOARD_ENABLED` | Read endpoints ungated; not in config response | Add router guard; project with ceiling |
| Conversation Analytics | `CONVERSATION_ANALYSIS_ENABLED` | Trigger returns 400 ✓; non-trigger endpoints return empty; not in config response | Project; existing 400 preserved |
| **Metrics Analytics** | *(none)* | Router unconditional; AI Adoption routes share the router; no enterprise guard; D16 eager ES construction | Add `_require_metrics_analytics_enabled()` to Group A routes; make `_repository` lazy; add `features:metricsAnalytics` projection |
| Smart Tool Selection | `TOOL_SELECTION_ENABLED` | `get_tools_by_query()` ungated at request time; not in config response | Add service guard; project |
| Admin Log Lookup | *(none)* | `POST /logs` completely ungated | New flag + router guard; no config projection |
| Stale-Datasource Detection | `STALE_DATASOURCE_ENABLED` | Scheduler guard exists; no public API | **No change required; no config projection** |

---

## Design

### 1 — New config flag

Add to `src/codemie/configs/config.py`:

```python
ADMIN_LOG_LOOKUP_ENABLED: bool = True
```

Default is `True` for backward compatibility (see §Rollout below). No other new flags. Four
existing flags (`LEADERBOARD_ENABLED`, `CONVERSATION_ANALYSIS_ENABLED`, `TOOL_SELECTION_ENABLED`,
`STALE_DATASOURCE_ENABLED`) are extended in scope only. `METRICS_ANALYTICS_ENABLED` is **not**
introduced — enterprise package presence is the gate.

**Expected overlap with EPMCDME-14659**: `config.py` and `customer_config.py` are shared files
with that ticket's workstream. Flag additions and `_get_runtime_config()` edits here must be
flagged to EPMCDME-14659 reviewers to avoid merge conflicts.

#### Rollout strategy for ADMIN_LOG_LOOKUP_ENABLED

`POST /logs` currently has no flag and is always reachable. Setting `ADMIN_LOG_LOOKUP_ENABLED`
to `False` by default would silently disable the endpoint for every existing ES-enabled deployment.

**Convention context**: All other capability flags default to `False` (opt-in). Admin Log Lookup
is the sole exception where a flag is introduced for an already-live capability with no prior gate.

**Chosen strategy: Default True (backward-compatible).** Existing ES-enabled deployments retain
current behavior without a config change. Future tightening to `False` default must be coordinated.

> **Deployment dependency — ADMIN_LOG_LOOKUP_ENABLED**
>
> Standalone or plain-PostgreSQL deployments **MUST** explicitly set
> `ADMIN_LOG_LOOKUP_ENABLED=False` to suppress Admin Log Lookup. This does **not** happen
> automatically from the flag default. Ownership: delivery pipeline / deployment profile owner.
> This ticket owns the flag and backend guard implementation only. A process restart is required
> for the value to take effect.

### 2 — API-level enforcement

**Leaderboard** (`src/codemie/rest_api/routers/analytics.py`, leaderboard route group):
Return HTTP 503 `{"detail": "Leaderboard is not enabled"}` when `config.LEADERBOARD_ENABLED` is
`False`. The `LeaderboardHandler` read path is PostgreSQL-only; the guard prevents serving
computation-stale data and backs the frontend `aiChampionsLeaderboard` capability state.

**Conversation Analytics** (`src/codemie/rest_api/routers/conversation_analysis.py`):
The trigger endpoint already returns HTTP 400 when `CONVERSATION_ANALYSIS_ENABLED=False`. Preserve
this — do not change to 503. Non-trigger endpoints return empty data when disabled; unchanged.

**Smart Tool Selection** (`src/codemie/service/tools/toolkit_lookup_service.py`,
`get_tools_by_query()`):
Add `TOOL_SELECTION_ENABLED` check at the top of `get_tools_by_query()` and return `[]` when
`False`.

**Caller inventory and safety analysis:**

Three confirmed direct callers of `get_tools_by_query()`:

1. `SmartToolSelector.select_tools()` — ES-recommended candidates. When `[]`: no ES call; selector
   uses explicitly assigned and default tools only. Behavior: acceptable.
2. `ToolkitService` — augments toolkit with ES tool suggestions. When `[]`: no ES call; configured
   toolkits unaffected. Behavior: acceptable.
3. `ValidateToolsNode` — validates against ES-indexed tools. When `[]`: no recommendations
   validated; validation passes with zero recommendations. Behavior: acceptable.

Perform a grep for `get_tools_by_query` at implementation time to confirm no callers were added
after this spec was written.

The startup indexing guard in `main.py` (gated by `TOOL_SELECTION_ENABLED`) is unchanged.

**Admin Log Lookup** (`src/codemie/rest_api/routers/logs.py`, `POST /logs`):
Return HTTP 503 `{"detail": "Admin Log Lookup is not enabled"}` when
`config.ADMIN_LOG_LOOKUP_ENABLED=False`. No `GET /v1/config` projection — no confirmed frontend
consumer exists.

**Stale-Datasource Detection**: the `main.py` scheduler guard already prevents ES execution.
No public API exists. No further action required.

**Metrics Analytics** (`src/codemie/rest_api/routers/analytics.py`):
Add `_require_metrics_analytics_enabled()` as a FastAPI dependency on all Group A routes (defined
below). The HTTP 503 status code is isolated in `_require_metrics_analytics_enabled()` so it can
be changed consistently if the 404-vs-503 cross-ticket decision (pending) resolves differently.

#### Analytics route classification — exhaustive and complete

The following four groups are verified by tracing the call graph from the router through
`AnalyticsService` to each concrete handler's constructor signature. Implementation must follow
this classification exactly; no further confirmation step is required.

**Group A — Metrics Analytics (Elasticsearch-backed): MUST be gated**

Routes that create `AnalyticsService(user)` and delegate to a handler whose `__init__` receives
`repository: MetricsElasticRepository`. Each must declare `Depends(_require_metrics_analytics_enabled)`
before any `AnalyticsService` is instantiated:

- `GET /summaries` (line 677): `_summary_handler` (SummaryHandler) + `_engagement_handler` (EngagementHandler) — both take `repository`
- `GET /assistants-chats` (line 760): `_assistant_handler` (AssistantHandler) — takes `repository`
- `GET /workflows` (line 782): `_workflow_handler` (WorkflowHandler) — takes `repository`
- All routes from line 802 through line 2094 that call `AnalyticsService(user)` and delegate to ES-backed handlers
- `GET /cli-insights-by-enriched-user-primary-skill` (line 2146)
- `GET /cli-insights-by-enriched-user-country` (line 2156)
- `GET /cli-insights-by-enriched-user-city` (line 2166)
- `GET /cli-insights-by-enriched-user-job-title` (line 2176)
- `GET /cli-insights-by-enriched-user-job-title-group` (line 2186)
- `GET /engagement/weekly-histogram` (line 3225): `_engagement_handler` (EngagementHandler) — takes `repository`
- `GET /spending/by-users/platform` (line 3258): `_user_handler` (UserHandler) — takes `repository`
- `GET /spending/by-users/cli` (line 3285): `_user_handler` (UserHandler) — takes `repository`

**Group B — AI Adoption (PostgreSQL-only): NOT gated**

Routes at lines 2196–2890 — all `/ai-adoption-*` routes (POST and GET variants). These delegate
to `AnalyticsService._adoption_handler` = `AIAdoptionHandler(self._user)` which takes no
`repository` argument and never touches `MetricsElasticRepository`. Do NOT apply
`_require_metrics_analytics_enabled()` to these routes.

**Group C — Leaderboard (PostgreSQL-backed, already gated): already handled**

Routes at lines 3312–3618 — all `/leaderboard/*` routes. These delegate to
`AnalyticsService._leaderboard_handler` = `LeaderboardHandler(self._user)` which takes no
`repository` argument. Already gated by the existing `_require_leaderboard_enabled()` dependency.
No new gating needed.

**Group D — Independent budget/spending routes (LiteLLM or PostgreSQL): NOT gated**

- `GET /spending` (line 2926): `get_customer_spending` from `codemie.enterprise.litellm.dependencies` — LiteLLM-backed, no `AnalyticsService`
- `GET /budget_usage` (line 3057): `budget_usage_service` — PostgreSQL-backed, no `AnalyticsService`
- `GET /user-project-spending` (line 3131): `member_spend_service` — PostgreSQL-backed, no `AnalyticsService`
- `GET /project-member-spending` (line 3181): `member_spend_service` — PostgreSQL-backed, no `AnalyticsService`

Do NOT apply `_require_metrics_analytics_enabled()` to Group D.

### 3 — Metrics Analytics enforcement (D16 resolution + enterprise gate)

Three coordinated changes implement Metrics Analytics enforcement:

**a) Lazy `MetricsElasticRepository` in `AnalyticsService`** (`analytics_service.py`):
Remove `self._repository = MetricsElasticRepository()` from `__init__`. Add
`self._repository_instance: MetricsElasticRepository | None = None`. Add a `@property _repository`
that creates the instance on first access (same lazy pattern as the existing handler properties at
lines 79-83 and 183-188). AI Adoption requests construct `AnalyticsService` but never access
`_repository`, so no `MetricsElasticRepository` is created. Metrics Analytics handlers access
`_repository` and trigger construction as before — only later, at the point of actual use.

**b) `has_enterprise()` helper in `loader.py`** (`src/codemie/enterprise/loader.py`):
Following the exact pattern already used for `HAS_LANGFUSE`/`has_langfuse()`,
`HAS_LITELLM`/`has_litellm()`, etc. (`importlib.metadata.version` and `PackageNotFoundError` are
already imported in `loader.py`):

```python
try:
    version("codemie-enterprise")
    HAS_ENTERPRISE = True
except PackageNotFoundError:
    HAS_ENTERPRISE = False

def has_enterprise() -> bool:
    """Check if the codemie-enterprise package is installed."""
    return HAS_ENTERPRISE
```

Export `has_enterprise` from `src/codemie/enterprise/__init__.py` — add it to both the import
list and `__all__`, following the existing pattern for `has_langfuse`, `has_litellm`, `has_idp`.

Update `src/codemie/configs/customer_config.py` — replace the inline
`version("codemie-enterprise") / PackageNotFoundError` try/except block in `_get_runtime_config()`
with `from codemie.enterprise import has_enterprise` and `is_enterprise = has_enterprise()`.
Remove the now-unused `PackageNotFoundError` import from `customer_config.py` if it is not used
elsewhere in that file. No private config-rendering helper is defined in `customer_config.py` for
this purpose.

**c) `_require_metrics_analytics_enabled()` dependency** (`analytics.py` router):
FastAPI dependency function. Imports and calls `has_enterprise()` from `codemie.enterprise`.
Raises `HTTPException(status_code=503, detail="Metrics Analytics is not available")` when
enterprise package is absent. Status code is isolated in this function (single change point).

### 4 — Config projection (`GET /v1/config`)

**Existing contract semantics (preserved)**: `resolve_components()` returns only components whose
effective `enabled` is `True`. Presence = available; absence = unavailable.

#### `aiChampionsLeaderboard` — customer-config primary control + LEADERBOARD_ENABLED ceiling

`customer-config.yaml` already defines `aiChampionsLeaderboard` as a YAML-based component.
`LEADERBOARD_ENABLED` acts as a **capability ceiling**: when `False`, `aiChampionsLeaderboard`
must be absent from the resolved config response regardless of customer-config setting.
Effective availability = customer-config enabled **AND** `LEADERBOARD_ENABLED=True`.

Minimal implementation: post-resolution suppression in `resolve_components()` or
`_get_runtime_config()` that removes `aiChampionsLeaderboard` when `config.LEADERBOARD_ENABLED=False`.
Customer-config remains the primary control when `LEADERBOARD_ENABLED=True`. Do not replace
the YAML-based component with a non-overridable runtime CONFIG_IDS entry.

#### `conversationAnalytics`, `smartToolSelection`, `features:metricsAnalytics` — runtime entries

`conversationAnalytics` and `smartToolSelection` are confirmed already in `CONFIG_IDS`. Add
`"metricsAnalytics": "features:metricsAnalytics"` to `CONFIG_IDS`. Verify `metricsAnalytics` is
absent before adding. Add to `_get_runtime_config()`:

| Component ID | `enabled` condition |
|---|---|
| `conversationAnalytics` | `config.CONVERSATION_ANALYSIS_ENABLED` |
| `smartToolSelection` | `config.TOOL_SELECTION_ENABLED` |
| `features:metricsAnalytics` | `has_enterprise()` |

If `conversationAnalytics` or `smartToolSelection` already exist in `customer-config.yaml`, apply
the same ceiling pattern as `aiChampionsLeaderboard`.

**Not projected**: Admin Log Lookup (no frontend surface), Stale-Datasource Detection (scheduler-only).

**Example** — `GET /v1/config` additions when all active:

```json
[
  { "id": "aiChampionsLeaderboard",     "settings": { "enabled": true } },
  { "id": "conversationAnalytics",      "settings": { "enabled": true } },
  { "id": "smartToolSelection",         "settings": { "enabled": true } },
  { "id": "features:metricsAnalytics",  "settings": { "enabled": true } }
]
```

### 5 — Scheduler guards

`LeaderboardScheduler`, `ConversationAnalysisScheduler`, `StaleDatasourceScheduler`, and
`MetricsRotationScheduler` already have startup guards in `main.py`. No scheduler logic changes
required. Swallowed-exception behavior in stale-datasource and conversation analytics schedulers
is preserved unchanged.

---

## Shared Availability Pattern

> Authoritative for EPMCDME-14657 (frontend) and Yevhenii's workstream.

1. Backend resolves effective availability using config flags and package presence.
2. `GET /v1/config` communicates resolved state: component present = available; component absent = unavailable.
3. Absence means unavailable — no `enabled: false` entries.
4. Frontend does not probe infrastructure (no direct ES/pgvector health checks in UI code).
5. Customer configuration is the primary control; backend capability flags act as a ceiling.

**Ivan's capabilities in this contract (EPMCDME-14652)**: `aiChampionsLeaderboard`,
`conversationAnalytics`, `smartToolSelection`, `features:metricsAnalytics`.

**Yevhenii's capabilities (separate ticket, different IDs)**: retrieval, knowledge base, data
sources, code search — component IDs TBD by Yevhenii's ticket.

Frontend check pattern for EPMCDME-14657:

```ts
isConfigItemEnabled(configs, 'aiChampionsLeaderboard')    // Leaderboard
isConfigItemEnabled(configs, 'conversationAnalytics')     // Conversation Analytics
isConfigItemEnabled(configs, 'smartToolSelection')        // Smart Tool Selection
isConfigItemEnabled(configs, 'features:metricsAnalytics') // Metrics Analytics
```

Frontend must not independently probe ES or maintain a competing capability state.

---

## Test Scope

| Case | Method | Location |
|---|---|---|
| Leaderboard: 503 when `LEADERBOARD_ENABLED=False` | `patch` config flag | router test |
| Leaderboard: 200 when enabled, ES mocked | mock handler | router test |
| Admin Log Lookup: 503 when `ADMIN_LOG_LOOKUP_ENABLED=False` | `patch` config flag | router test |
| Admin Log Lookup: reachable when `ADMIN_LOG_LOOKUP_ENABLED=True`, ES mocked | `patch` config flag | router test |
| `get_tools_by_query()` returns `[]` when `TOOL_SELECTION_ENABLED=False` | `patch` config flag | service unit test |
| `test_get_tools_by_query_handles_malformed_data` **rewrite (CR-3)**: patch config with `TOOL_SELECTION_ENABLED=True`; assert `SearchAndRerankTool` class mock instantiated once; assert tool mock `.execute()` called once; assert malformed results return `[]` | `patch` config + class mock | `test_toolkit_lookup_service.py` |
| `ToolkitService` receiving `get_tools_by_query() = []` does not remove configured toolkits | mock `get_tools_by_query` | service unit test |
| `ValidateToolsNode` receiving `get_tools_by_query() = []` does not fail validation | mock `get_tools_by_query` | workflow/validation test |
| `aiChampionsLeaderboard` absent from `GET /v1/config` when `LEADERBOARD_ENABLED=False` | `patch` config flag | `test_customer_config_service.py` |
| `conversationAnalytics` present/absent when `CONVERSATION_ANALYSIS_ENABLED` True/False | extend existing | `test_customer_config_service.py` |
| `smartToolSelection` present/absent when `TOOL_SELECTION_ENABLED` True/False | extend existing | `test_customer_config_service.py` |
| `features:metricsAnalytics` present in `GET /v1/config` when enterprise present | extend existing | `test_customer_config_service.py` |
| `features:metricsAnalytics` absent from `GET /v1/config` when enterprise absent | `patch codemie.enterprise.loader.HAS_ENTERPRISE = False` | `test_customer_config_service.py` |
| Metrics Analytics route (Group A): 503 when enterprise absent | `patch codemie.enterprise.loader.HAS_ENTERPRISE = False` | router test |
| Metrics Analytics route (Group A): 200 when enterprise present, ES mocked | mock repo | router test |
| AI Adoption route (Group B): 200 when enterprise absent (no enterprise guard) | `patch codemie.enterprise.loader.HAS_ENTERPRISE = False` | router test |
| `test_ai_adoption_overview_200_without_enterprise` **rewrite (CR-4, depends on CR-1b)**: simulate enterprise absence by patching `codemie.enterprise.loader.HAS_ENTERPRISE = False`; do NOT mock `MetricsElasticRepository` constructor; exercise real `AnalyticsService` construction; assert `MetricsElasticRepository.__init__` never called; assert AI Adoption operation returns expected result; mock only async DB call inside `AIAdoptionHandler` | `patch HAS_ENTERPRISE` + AIAdoptionHandler DB mock | `test_analytics.py` |
| Conversation Analytics trigger: 400 preserved when disabled | verify existing or add | router test |

No new test infrastructure required. Use `patch codemie.enterprise.loader.HAS_ENTERPRISE = False`
for enterprise-absence simulation and `unittest.mock.patch` for config flags — both patterns are
established in the codebase.

---

## Acceptance Criteria

### SECTION A — EPMCDME-14652 Implementation (Ivan's new work, independently completable)

1. `ADMIN_LOG_LOOKUP_ENABLED: bool = True` added to `Config`; default True for backward compatibility.
2. `POST /logs` returns HTTP 503 when `ADMIN_LOG_LOOKUP_ENABLED=False`; no ES call made.
3. Leaderboard read endpoints return HTTP 503 when `LEADERBOARD_ENABLED=False`.
4. `get_tools_by_query()` returns `[]` when `TOOL_SELECTION_ENABLED=False`; ordinary toolkit resolution is unaffected.
5. `conversationAnalytics` and `smartToolSelection` confirmed in `CONFIG_IDS` and `_get_runtime_config()`; `"metricsAnalytics": "features:metricsAnalytics"` added to `CONFIG_IDS`.
6. `aiChampionsLeaderboard` is absent from `GET /v1/config` when `LEADERBOARD_ENABLED=False`, regardless of customer-config setting; customer-config remains primary control when `LEADERBOARD_ENABLED=True`.
7. `conversationAnalytics` absent from `GET /v1/config` when `CONVERSATION_ANALYSIS_ENABLED=False`; present when `True`.
8. `smartToolSelection` absent from `GET /v1/config` when `TOOL_SELECTION_ENABLED=False`; present when `True`.
9. `has_enterprise()` added to `src/codemie/enterprise/loader.py` following the `HAS_LANGFUSE`/`has_langfuse()` pattern; exported from `src/codemie/enterprise/__init__.py` (import list + `__all__`); `customer_config.py` updated to use `from codemie.enterprise import has_enterprise`; no private config-rendering helper defined in `customer_config.py` for this purpose; no duplicated `importlib.metadata.version("codemie-enterprise")` try/except.
10. `features:metricsAnalytics` added to `CONFIG_IDS` and `_get_runtime_config()`; value reflects `has_enterprise()`.
11. `features:metricsAnalytics` present in `GET /v1/config` when enterprise package installed; absent when not installed.
12. `_require_metrics_analytics_enabled()` FastAPI dependency added in `analytics.py`; imports `has_enterprise` from `codemie.enterprise`; returns HTTP 503 when enterprise absent; status code isolated for easy change.
13. All Group A Metrics Analytics routes (as enumerated in §Analytics route classification) declare `Depends(_require_metrics_analytics_enabled)` and return HTTP 503 when enterprise package absent. Group B (AI Adoption), Group C (Leaderboard, existing gate), and Group D (independent budget/spending) routes are NOT gated by `_require_metrics_analytics_enabled()`.
14. AI Adoption routes (Group B, `/ai-adoption-*`) remain reachable and return expected results regardless of enterprise package presence.
15. `MetricsElasticRepository` is NOT constructed for AI Adoption requests; `AnalyticsService._repository` is lazy (CR-1b).
16. Enabled/full enterprise deployments retain existing Metrics Analytics behavior; no regression.
17. `test_get_tools_by_query_handles_malformed_data` rewritten per CR-3: patches `TOOL_SELECTION_ENABLED=True`, asserts `SearchAndRerankTool` instantiated once, asserts `.execute()` called once, asserts malformed results return `[]`.
18. `test_ai_adoption_overview_200_without_enterprise` rewritten per CR-4: enterprise absence simulated by patching `codemie.enterprise.loader.HAS_ENTERPRISE = False`; `MetricsElasticRepository.__init__` asserted never called; AI Adoption result asserted; only AIAdoptionHandler DB call mocked.
19. Tests cover disabled and enabled states for each new guard and each new/modified projection entry.

### SECTION B — Confirmed current behavior (no new code, observation only)

20. Stale-Datasource scheduler does not register when `STALE_DATASOURCE_ENABLED=False` (existing guard confirmed, no change).
21. Conversation Analytics trigger endpoint returns HTTP 400 (not 503) when `CONVERSATION_ANALYSIS_ENABLED=False` (existing behavior preserved).
22. AI Adoption endpoints return 200 when `codemie-enterprise` is absent; `MetricsElasticRepository` is NOT constructed at service construction time for AI Adoption requests (D16 resolved by lazy `_repository` property — see AC 15).

### SECTION C — Integration dependencies (Yevhenii's scope, required before full system test)

23. `GoogleDocDatasourceProcessor` class-attribute ES call resolved (Yevhenii's scope; ticket TBD).
24. `manage_preconfigured_assistants()` startup ES call resolved (Yevhenii's scope; ticket TBD).

*Ivan does not implement or accept these. They are integration gates for full ES-free system testing.*

### SECTION D — EPMCDME-14657 contract consumers (frontend follow-up)

25. Frontend consumes `aiChampionsLeaderboard`, `conversationAnalytics`, `smartToolSelection`, `features:metricsAnalytics` from `GET /v1/config`.
26. `aiChampionsLeaderboard` registered in `FEATURE_FLAGS` constant (currently an inline literal in `AnalyticsPage.tsx`).

*These are contracts defined in this ticket, to be consumed by EPMCDME-14657.*

---

## Non-Goals

- No global `ELASTICSEARCH_ENABLED` flag.
- No `METRICS_ANALYTICS_ENABLED` env flag; enterprise package presence via `has_enterprise()` is the gate for Metrics Analytics.
- No new general-purpose capability framework, registry, or abstraction layer.
- No dynamic ES availability detection (health-check polling, ping-on-request).
- Retrieval, knowledge-base, data-source, indexing, code-search capabilities — Yevhenii's scope.
- ES-free application startup (`GoogleDocDatasourceProcessor`, `manage_preconfigured_assistants()`) — Yevhenii's scope.
- pgvector, ClickHouse — out of scope entirely.
- No change to swallowed-exception behavior in scheduler bodies.
- No `adminLogLookup` or `staleDatasourceDetection` in `GET /v1/config`.
- No HTTP response code standardization: Conversation Analytics trigger stays 400; existing behavior preserved wherever a guard already exists.
- `STALE_DATASOURCE_DELETION_ENABLED` and `METRICS_ROTATION_ENABLED` not projected.
- No changes to existing `GET /v1/config` components (`features:enterpriseEdition`, `features:userManagement`, etc.).
- No database migrations or new external dependencies.
- No dynamic database-level override (`SettingDeclaration` / `PUT /v1/config/declarations`) for `aiChampionsLeaderboard` — YAML-only operator control; database override is out of scope.
- Standalone/PostgreSQL-only deployment configuration update (setting `ADMIN_LOG_LOOKUP_ENABLED=False` in the deployment profile) — delivery pipeline / deployment profile owner's responsibility; not owned by EPMCDME-14652.
- Do not unregister the analytics router.
- Frontend implementation — EPMCDME-14657 owns that.
- No private enterprise-availability helper in `customer_config.py`; the canonical check is `has_enterprise()` from `codemie.enterprise`.

---

## Open Risks

- **`aiChampionsLeaderboard` ceiling implementation**: `resolve_components()` merge behavior with YAML components is not fully detailed in the analysis. If the merge logic cannot express a runtime ceiling over a YAML component, a post-merge filter or alternate suppression point must be found at implementation time.
- **Smart Tool Selection post-spec callers**: three callers confirmed safe. Implementation-time grep for `get_tools_by_query` must confirm no callers were added after this spec was written.
- **`conversationAnalytics`/`smartToolSelection` in customer-config.yaml**: analysis does not confirm whether these IDs appear in the YAML. If they do, apply the same customer-config primary + ceiling pattern as `aiChampionsLeaderboard`.
- **ADMIN_LOG_LOOKUP_ENABLED default True inconsistency**: other capability flags default `False` (opt-in). This flag defaults `True` (opt-out). Future tightening to `False` default must be coordinated.
- **ADMIN_LOG_LOOKUP_ENABLED=False not set automatically in standalone profile**: deployment profile owner must explicitly apply this; no automated enforcement.
- **Conversation Analytics non-trigger endpoints return empty data when disabled**: preserved behavior. If blocking these entirely is a product requirement, scope must be extended.
- **Scheduler restart requirement**: capability flag changes take effect only after a process restart. Not a regression; operators must be aware.
- **EPMCDME-14659 merge conflict risk**: `config.py`, `customer_config.py`, and `test_customer_config_service.py` are shared with the EPMCDME-14659 workstream. Coordinate additions to avoid conflicts.

---

## Revised Complexity Estimate

**Size: L | ~4–5 days** (revised upward from M due to CR-1 Metrics Analytics enforcement scope)

Remaining implementation scope:
- 1 new config flag (`ADMIN_LOG_LOOKUP_ENABLED=True`)
- D16 fix: lazy `_repository` property in `AnalyticsService`
- `has_enterprise()` in `loader.py` + `__init__.py` export + `customer_config.py` migration
- `_require_metrics_analytics_enabled()` FastAPI dependency + Group A route classification in `analytics.py`
- 3 enforcement guards: Leaderboard router, Admin Log Lookup router, `get_tools_by_query()` service
- 3 new/confirmed runtime CONFIG_IDS entries (`features:metricsAnalytics`, confirm `conversationAnalytics`/`smartToolSelection`)
- 1 ceiling suppression for `aiChampionsLeaderboard` in `resolve_components()` / `_get_runtime_config()`
- Test rewrites: CR-3 (malformed smart-tool), CR-4 (AI Adoption enterprise isolation)
- New tests: Metrics Analytics enforcement (disabled/enabled), AI Adoption isolation, config projection
- Implementation-time caller inventory grep (low effort)

Removed from original L estimate: pgvector/ClickHouse scope, `METRICS_ANALYTICS_ENABLED` env flag
infrastructure, frontend projections for Admin Log Lookup and Stale-Datasource Detection,
400→503 standardization scope, Yevhenii-owned capabilities.
