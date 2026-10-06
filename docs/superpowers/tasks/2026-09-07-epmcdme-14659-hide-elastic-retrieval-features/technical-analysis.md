# Technical Research

**Task**: customer-config components, elasticsearch retrieval, knowledge-base, datasource, code-indexing, toolkit-gating, startup-router-registration
**Generated**: 2026-09-07
**Research path**: codegraph (dimensions 3 and 6 completed by direct reads — codegraph explore budget exhausted)
**Branch**: `EPMCDME-14659_hide-elastic-retrieval-features` (from `EPMCDME-14347_standalone-codemie-released-artifact`)

---

## 1. Original Context

EPMCDME-14659 — Standalone container: [Capability Hiding 3] - Hide Elastic search retrieval related features FE + BE.
Sub-task of EPMCDME-14347 "Standalone CodeMie as a Released Artifact". Status: In Progress. Cloned by EPMCDME-14759 ("Degraded vector retrieval mode — TBD, review scope"), which is where AC28 was moved.

**This run implements the backend scope only. Frontend is a separate effort.**

Ticket description, verbatim:

```text
*Goal* Deliver the operator-facing surface this ticket owns: features that are utilizing Elastic search retrieval are disabled when no Elastic Search available. Codemie expected to work only with ProstgreSQL without pgvector extension

*Scope*

＃ {*}Backend degraded mode{*}: the container starts and serves everything not depending on retrieval when vector storage is unavailable; startup output names retrieval as the unavailable capability and what would make it available; knowledge bases, datasources and code indexing are absent rather than present and failing. Built the existing {{configs/customer_config.py}} component mechanism.
Detection of unavailable storage should happen based on the config values passed to a codemie.
＃ {*}Frontend / Capability Fencing{*}: when retrieval is unavailable, Knowledge Bases, Datasources and Code Indexing must be absent from the UI — not visible, selectable, discoverable, or navigable through any entry point. The UI derives this directly from {{get_enabled_components()}} above; no separate frontend detection logic. Non-retrieval functionality (chat with assistants that need none, etc.) continues working unaffected.

*Out of scope*
 * The retrieval implementation itself (schema, queries, ranking, fusion).
 * Adding vector storage and restarting makes retrieval available with nothing reinstalled or recreated. — feature *AC28* - Depends on pgvector implementation
 * Feature gating of functional areas dependent on other Elasticsearch interaction or not directly mentioned in this task

*Acceptance criteria*
 * Starting against a PostgreSQL that cannot provide vector storage yields a running container serving everything that does not depend on retrieval, including chat with assistants that need none. — feature *AC26*
 * Startup output names retrieval as the unavailable capability and states what would make it available.  — feature *AC27*
 * Knowledge bases, datasources and code indexing are absent from the product, not present and failing when used. — feature *AC29*
 * Negative: Given retrieval is unavailable, when a user navigates the application, then knowledge bases, datasources and code indexing are not visible, selectable, discoverable, or navigable — the UI-facing corollary of {*}AC29{*}. — feature *AC29*

*Depends on*
EPMCDME-14564
```

Note: the Jira **Acceptance Criteria field is empty**; the ACs above live in the description body.

### Locked design decisions (agreed with the user before research; not to be re-litigated)

| # | Decision |
|---|---|
| **D1** | New config key `RETRIEVAL_BACKEND: str = "elasticsearch"` in `src/codemie/configs/config.py`. Value `"none"` means retrieval unavailable. Forward-compatible with `"pgvector"`. Default preserved so existing deployments are unchanged. |
| **D2** | Three separate customer-config components — `features:knowledgeBases`, `features:datasources`, `features:codeIndexing` — runtime-computed (`CONFIG_IDS` entries), all driven by `RETRIEVAL_BACKEND` today, independently togglable later. All three **absent** from `GET /v1/config` when retrieval is unavailable. |
| **D3** | Do not register `index.router` when retrieval is unavailable — `/v1/index/*` 404s and vanishes from OpenAPI. Follow the existing conditional `include_router` precedent in `main.py`. |
| **D4** | Blast radius: (a) fix the import-time ES client construction that is the app-wide boot failure; (b) fence the `/v1/index` router and KB/datasource/code-indexing services; (c) withhold retrieval-dependent agent toolkits so a KB-enabled assistant does not crash mid-chat. Out of scope: leaderboard, conversation analytics, metrics rotation, admin logs, stale-datasource job. |

---

## 2. Codebase Findings

### Existing Implementations

**Customer-config component mechanism** — `src/codemie/configs/customer_config.py`
- `CONFIG_IDS` dict at `:23-30` — 6 entries today (`enterpriseEdition`, `userManagement`, `idpProvider`, `mcpAuthOrigin`, `chatContextualNaming`, `budgetSoftLimitNotification`).
- `_get_runtime_config()` `:131-188` — appends one `Component` per `CONFIG_IDS` key, each `Component(id=CONFIG_IDS[...], settings=ComponentSetting(enabled=<config attr>))`.
- `get_runtime_components()` `:190-192` — thin wrapper; docstring: "never overridable from YAML or the database".
- `get_enabled_components()` `:194-208` — YAML components filtered by `component.id not in runtime_config_ids` (`:202`), then `+ [c for c in self._get_runtime_config() if c.settings.enabled]` (`:206`).
- `is_component_enabled()` `:249-265` — runtime dict consulted first (`:255-259`), YAML fallback (`:262-265`), **defaults to `False`**.
- `is_feature_enabled()` `:267-279` — prefixes `features:`.
- Module singleton `customer_config = CustomerConfig()` at `:297` — **constructed at import time**.
- `ComponentSetting` allows extra fields (`model_config = ConfigDict(extra="allow")`, `:41`), so a `reason`/`value` field can ride along. Precedent: `idpProvider` `:160-165`, `mcpAuthOrigin` `:167-172`.

**The actual frontend contract path** — `src/codemie/service/customer_config_service.py`
- `resolve_components()` `:147-158` is what the FE reads, **not** `get_enabled_components()`:
  - `runtime_ids = set(CONFIG_IDS.values())` `:151`
  - overrides merged onto YAML `:153`
  - `enabled_yaml = [c for c in merged if c.settings.enabled and c.id not in runtime_ids]` `:155`
  - `enabled_runtime = [c for c in customer_config.get_runtime_components() if c.settings.enabled]` `:156`
  - returns `enabled_yaml + enabled_runtime` `:158`
- `src/codemie/rest_api/routers/customer_config.py` — `GET /v1/config` `:46-48` calls `resolve_components()`. `GET /v1/applications` `:86-110` also calls it.
- **Consequence for D2**: adding the three components to `_get_runtime_config()` reaches *both* paths, because `resolve_components()` delegates to `get_runtime_components()` at `:156`. The ticket's wording ("the UI derives this from `get_enabled_components()`") names the wrong function, but the fix lands in the shared upstream so the contract is satisfied either way.

**Elasticsearch client** — `src/codemie/clients/elasticsearch.py`
- `get_client()` `:26-39`, `get_async_client()` `:41-56`. Both classmethods with per-PID caches. This is the single choke point a `RETRIEVAL_BACKEND == "none"` guard could sit behind.

**Config** — `src/codemie/configs/config.py`
- `ELASTIC_URL` / `ELASTIC_PASSWORD` / `ELASTIC_USERNAME` / `ELASTIC_DATASOURCE_REPLICAS` at `:74-77` — natural insertion point for `RETRIEVAL_BACKEND`.
- `finalize_settings` model_validator `:933-967` — precedent for rejecting an invalid value at construction.
- `to_safe_dict()` `:1015-1036` — `RETRIEVAL_BACKEND` is non-sensitive, so it appears in the startup log at `main.py:707` automatically.

**Application assembly** — `src/codemie/rest_api/main.py`
- `app = FastAPI(lifespan=lifespan)` at `:860` — **module scope; there is no `create_app()` factory.**
- `StateImportService().import_indexes()` at `:887` — **module scope, ES-backed** (verified live during this research).
- Router registration block `:889-956`; `app.include_router(index.router)` at `:894`.
- Existing conditional precedents: `if is_litellm_enabled() and config.LLM_PROXY_BUDGET_CHECK_ENABLED:` `:935-936`; `if config.ENABLE_USER_MANAGEMENT:` `:948-953` with nested `if config.IDP_PROVIDER == "local":` `:951-952`.
- `lifespan` `:705-821`; first log line `:707` — `Starting CodeMie application. Config={config.to_safe_dict()}`. Best insertion point for the AC27 capability banner.
- ES `ApiError` exception handler `:1029-1044`; `custom_openapi()` `:824-846`.

**Boot failure (the row-17 site)** — `src/codemie/datasource/google_doc/google_doc_datasource_processor.py`
- `client = ElasticSearchClient.get_client()` at `:49` — **class-body scope** inside `class GoogleDocDatasourceProcessor` (`:48`). Evaluated during module import. Also `from elasticsearch.helpers import bulk` `:21` and `bulk(self.client, ...)` `:194`.
- Import chain: `main.py` → routers → services → `agents/tools/kb/kb_toolkit.py:19` → `agents/tools/kb/search_kb.py:30` → this processor. A routine startup path, not an edge case.

**Safe-by-contrast base** — `src/codemie/datasource/base_datasource_processor.py`
- `self.client = ElasticSearchClient.get_client()` at `:96`, inside `__init__` (`:80-106`). Lazy, and the natural chokepoint for a "retrieval unavailable" rejection covering every processor.

**The fenced router** — `src/codemie/rest_api/routers/index.py`
- `router = APIRouter(tags=["Indexing"], prefix="/v1", dependencies=[Depends(authenticate)])` `:185`. All 40+ routes are `/v1/index/*` — the router is 100% retrieval surface, so D3 loses nothing unrelated.
- Imports every datasource processor `:43-78`, provider datasource services `:59-65` and `:117-120`, `IndexStatusService` `:128`, `IndexHealthCheckService` `:129`, `IndexEncryptedSettingsService` `:137-140`, `FileDatasourceService` `:143`, `UpdateFileDatasourceUseCase` `:145`, `ToolExecutionService` `:136`. `from elasticsearch.exceptions import NotFoundError` `:23`.

**Toolkit assembly** — `src/codemie/service/tools/toolkit_service.py`
- Existing customer-config gating shape (the pattern to copy): `customer_config` imported at module scope `:43` and re-imported locally inside `_augment_toolkits_with_feature_flags` `:239`; `enable_web_search = request.enable_web_search is True and customer_config.is_feature_enabled("webSearch")` `:260`; `dynamicCodeInterpreter` `:281-285`; `interactiveElements` `:620` inside `_append_request_user_input_tool_if_enabled` `:604-631`.
- Runtime seams that must be fenced: `add_context_tools()` `:1148-1205` dispatches on `context.context_type` — `ContextType.KNOWLEDGE_BASE → _add_kb_tools` `:1178-1179`, `ContextType.CODE → _add_code_tools` `:1192-1193`. `_add_kb_tools` `:1396-1415`; `_add_code_tools` `:1483-1550`.
- Today's "present and failing" behaviour AC29 forbids: `_find_index` `:1652-1673` and `_find_code_index` `:1597-1614` return `None` or raise; `_get_code_fields` `:1616-1650` raises `ToolException("Repository: ... is not found...")` mid-chat.

**Toolkit catalogue served to clients** — `src/codemie/service/tools/tools_info_service.py`
- `get_tools_info()` `:34-89`; `KBToolkit.get_tools_ui_info()` appended when `not show_for_ui` `:78-79`; `_merge_code_toolkit()` `:76` / `:104-117` **always** adds code tools.
- `exclude_toolkits` parameter `:38`, `:86-87` — a ready-made filter seam.
- `src/codemie/rest_api/routers/tool.py` — `GET /v1/tools` `:31-43` calls `get_tools_info(user=user)` with default `show_for_ui=False`, so **KB search is advertised to clients today with no customer_config check anywhere in that file**.

**Other retrieval call sites in scope** — `src/codemie/agents/utils.py`
- Three ES code-search sites: `get_repo_tree` `:199-209` (client at `:201`), `get_repo_tree_by_search_phrase_path` `:212-222` (`:214`), `get_repo_files_by_search_phrase_path` `:225-245` (`:227`).

### Architecture and Layers Affected

1. **Configs** — `Config(BaseSettings)` (new `RETRIEVAL_BACKEND` + validation) and `CustomerConfig` (three new runtime components).
2. **Service** — `customer_config_service.resolve_components()` (reached transitively); `toolkit_service` (runtime tool assembly); `tools_info_service` (catalogue).
3. **REST API** — `main.py` app assembly (conditional router registration + AC27 banner); `routers/index.py` (fenced); `routers/tool.py` (catalogue filtering).
4. **Agents/toolkits** — `agents/tools/kb/*`, `agents/utils.py`.
5. **Datasource ingestion** — `google_doc_datasource_processor.py` (boot fix), `base_datasource_processor.py` (rejection chokepoint).
6. **Clients** — `ElasticSearchClient` as an optional single-guard point.

### Integration Points

`ElasticSearchClient` sits beneath datasource ingestion, `BaseModelWithElasticSupport`, repositories, and `search_and_rerank`. `core/dependecies.py:106` `get_elasticsearch()` → LangChain `ElasticsearchStore` is the primary RAG retrieval path.

**Entry points to retrieval-backed services that survive a router-only fence** (the reason D3 alone is insufficient):

| Entry point | Path |
|---|---|
| `GET /v1/providers/datasource_schemas` | `routers/provider.py:31-33` → `ProviderService.index_schemas` (`provider_service.py:76-79`) → `ProviderDatasourceSchemaService` → `IndexStatusService` |
| `GET /v1/tools`, `POST /v1/tools/{name}/invoke` | `routers/tool.py:31,105` → `ToolExecutionService` |
| Chat runtimes | `assistant.router`, `a2a.router`, `ide.router`, `workflow_executions.router` → `ToolkitService.add_context_tools` → `_add_kb_tools` / `_add_code_tools` |
| Workflow code search | `workflows/utils/utils.py:301` |
| Cron/webhook re-index | `src/codemie/triggers/actors/datasource.py` (no HTTP route) |
| Startup | `main.py:385` `ToolkitLookupService.index_all_tools()`; `main.py:391` `PlatformIndexingService.sync_all_platform_datasources()`; `main.py:887` `StateImportService().import_indexes()` |

### Patterns and Conventions

- **Runtime components are never overridable**: the same `component.id not in runtime_config_ids` filter is applied in *both* paths (`customer_config.py:202`, `customer_config_service.py:155`). A runtime id cannot be shadowed by YAML or a dynamic-config DB row. This is the correct property here — an admin must not be able to switch KB back on when there is no storage behind it.
- **Conditional router registration** is done at module scope with a plain `if` on a `config` value (`main.py:935`, `:948`, `:951`). No factory, no `create_app()`.
- **Toolkit feature gating** happens in two places today — request-time augmentation (`_augment_toolkits_with_feature_flags` `:212-305`) and tool-append time (`_append_request_user_input_tool_if_enabled` `:604-631`). Neither covers KB/Code context tools; `add_context_tools` has no customer_config check at all.
- `config/customer/customer-config.yaml` has 40+ components and **no** `knowledgeBases`, `datasources` or `codeIndexing` entry — the three new ids are pure additions with no YAML collision, and being runtime-computed they need no YAML entry at all.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/development/configuration-patterns.md` — "Centralize checks near app/router/service assembly"; cites the `main.py` user-management router gating as *the* precedent. **The line reference in the guide is stale** (says `main.py:706`, actual is `main.py:948`).
- `.ai-run/guides/data/elasticsearch-integration.md` — documents `BaseElasticRepository` as the intended repository pattern. **Misleading**: the EPMCDME-14564 audit proved that class is dead in production (its only extender is a test double); `BaseModelWithElasticSupport` and a hand-rolled `MetricsElasticRepository` bypass it.
- `.ai-run/guides/api/rest-api-patterns.md`, `.ai-run/guides/api/endpoint-conventions.md` — router conventions for the fenced `/v1/index` surface.
- `.ai-run/guides/development/error-handling.md` — `ExtendedHTTPException` shape for a "retrieval unavailable" rejection.
- `.ai-run/guides/testing/testing-patterns.md`, `.ai-run/guides/testing/testing-api-patterns.md` — pytest policy.

### Architectural Decisions

Prior art carrying real weight, **all under the gitignored `local/` directory** (`.gitignore:48` = `local/*`, verified). These are *not* shared repo artifacts — reviewers cannot read them from the branch, which is why their load-bearing facts are reproduced in this document:

- `local/docs/2026-08-31-es-consumer-inventory.md` — the EPMCDME-14564 audit this ticket depends on. 21 direct `ElasticSearchClient` consumers plus 14 files coupled to the `elasticsearch` library indirectly (30 files total); 4 swallowed-exception sites; the row-17 boot failure with a full traceback from a live kill-switch spike. Line numbers resolved against `origin/main` tip `e89105ad1`.
- `local/docs/codemie-standalone-tast.md` — AC26/27/28/29 verbatim.
- `local/docs/codemie-standalone-storage-tasks.md` — the parent breakdown. **Names the config seam `SEARCH_BACKEND`, where D1 locks `RETRIEVAL_BACKEND`** (see Risk R-8).
- Memory record (2026-08-28): the storage abstraction lands upstream in `codemie`, backend-selectable by config, not forked into codemie-standalone; ES stays the default for the full platform. Consistent with D1's default-preserving choice.

### Derived Conventions

No merged code, config key, component id, doc or compose file for "standalone" capability hiding exists in this repo. Capability Hiding 1 and 2 (siblings under EPMCDME-14347) have landed nothing. **This ticket sets the precedent** — there is no house style to follow, so the conventions chosen here (`RETRIEVAL_BACKEND` naming, three-component granularity, router-absence semantics) will be copied by the siblings.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/configs/test_customer_config.py` — covers `CustomerConfig`, `is_feature_enabled`, `is_component_enabled`, `get_enabled_components`, runtime features.
- `tests/codemie/service/test_customer_config_service.py` — covers `resolve_components()` and override layering.
- `tests/codemie/rest_api/routers/test_customer_config_router.py` — covers the `GET /v1/config` response contract.
- Also: `test_customer_config_audit.py`, `test_customer_config_declarations.py`, `test_customer_config_validation.py`, `tests/codemie/rest_api/security/test_customer_config_write_guard.py`.
- `tests/codemie/service/tools/test_toolkit_service.py`, `tests/codemie/service/index/test_index_service.py`.

### Testing Framework and Patterns

- pytest + `unittest.TestCase` mixed in the same file; `pytest.mark.asyncio` for async service tests; `fastapi.testclient.TestClient` for routers.
- Config patching: `@patch("codemie.configs.customer_config.config")` replaces the whole config object with a `MagicMock`; each test sets only the attributes `_get_runtime_config` reads (`:115-119`, `:253-257`, `:282-286`).
- YAML patching: `@patch("codemie.configs.customer_config.Path.read_text")` (`:78`, `:88`, `:95`, `:112`, `:246`…) or `patch.object(CustomerConfig, "_read_config_file", ...)` (`:527`, `:544`).
- Service tests: `patch.object(customer_config_service, "customer_config")` with `mock_config.get_runtime_components.return_value = []` (`:56-59`); DB rows via `patch.object(customer_config_service.DynamicConfigService, "alist_by_key_prefix", AsyncMock(...))` (`:62-67`); autouse cache-reset fixture `:36-40`.
- Router tests: **module-scope import of the real singleton app** — `from codemie.rest_api.main import app` (`:21`), `app.include_router(router)` (`:24`), `TestClient(app)` (`:26`); resolver patched (`:35-39`); auth via `app.dependency_overrides[authenticate]` (`:234-237`).

### Coverage Gaps

- **No precedent anywhere in the repo for testing conditional router registration.** Because `app` and every `include_router` call are at module scope (`main.py:860`, `:889-956`) and the router test imports that singleton, asserting "`/v1/index/*` 404s when `RETRIEVAL_BACKEND=none`" requires `importlib.reload`, monkeypatching `config` before first import, or a subprocess. This is the single largest test-design risk in the task.
- `add_context_tools` (`toolkit_service.py:1148-1205`) has no feature-gating coverage because it has no feature gating.
- `tools_info_service.get_tools_info()` has no coverage for toolkit exclusion driven by customer config.
- Datasource-creation validation chain (`provider_datasource_creation_service.py`, `file_datasource_service.py`) was not enumerated by codegraph — budget exhausted; needs a direct read during planning.

---

## 5. Configuration and Environment

### Environment Variables

- `src/codemie/configs/config.py:74-77` — `ELASTIC_URL`, `ELASTIC_PASSWORD`, `ELASTIC_USERNAME`, `ELASTIC_DATASOURCE_REPLICAS`. Add `RETRIEVAL_BACKEND: str = "elasticsearch"` adjacent.
- `:157-170` — ES index-name block.
- Already-fail-soft ES tier, explicitly **out of scope**: `CONVERSATION_ANALYSIS_ENABLED` `:780`, `LEADERBOARD_ENABLED` `:797`, `METRICS_ROTATION_ENABLED` `:804`, `STALE_DATASOURCE_ENABLED` `:827`, plus `TOOL_SELECTION_ENABLED` and `PLATFORM_DATASOURCES_SYNC_ENABLED`.

### Configuration Files

- `config/customer/customer-config.yaml` — **no changes needed for D2**; all three components are runtime-computed.
- `src/codemie/configs/config.py:933-967` `finalize_settings` — where an invalid `RETRIEVAL_BACKEND` value should be rejected at construction.
- `src/codemie/configs/config.py:1015-1036` `to_safe_dict()` — makes `RETRIEVAL_BACKEND` visible in the startup log for free.

### Feature Flags and Deployment Concerns

The three new components are runtime-computed and therefore immune to YAML and dynamic-config DB override by construction — verified in both resolution paths. No migration, no YAML edit, no admin-UI declaration is required.

---

## 6. Risk Indicators

- **R-1 (blocker, not in D4). `main.py:887` `StateImportService().import_indexes()` runs at module scope and is ES-backed** (`BaseModelWithElasticSupport` + `helpers.bulk` at `state_import.py:44`). It will fail during app import even after the `:49` class-attribute fix. D4 named only one boot hazard; there are two. Both must be fenced or AC26 cannot pass.
- **R-2 (test-design). No `create_app()` factory.** `app` and all `include_router` calls are module scope; `test_customer_config_router.py:21` imports the singleton. Testing D3 needs `importlib.reload`, pre-import monkeypatching, or a subprocess — with no existing precedent. Decide the approach during planning, not during implementation.
- **R-3 (silent test breakage). `@patch("codemie.configs.customer_config.config")` installs a `MagicMock`**, so an unset `mock_config.RETRIEVAL_BACKEND` returns a *truthy MagicMock*. A check written as `config.RETRIEVAL_BACKEND != "none"` evaluates `True`, and the three new components silently appear enabled in every one of those tests. Any guard must be written so the mock default is safe, and each affected test must set the attribute explicitly.
- **R-4 (certain test breakage). Exact `len()` assertions** in `tests/codemie/configs/test_customer_config.py` break when three runtime components are added: `:125` (`== 4`), `:263` (`== 6`), `:292` (`== 4`), `:345` (`== 5`), `:393` (`== 3`); plus single-component checks at `:428`, `:455`, `:482`, `:507`.
- **R-5 (AC29 gap, toolkit catalogue). `GET /v1/tools` advertises KB search today** — `tools_info_service.py:78-79` appends `KBToolkit` whenever `show_for_ui=False` (the default used by `routers/tool.py:31`), and `:76` *always* merges code tools. There is no customer_config check in that file. Without this fix, retrieval is "absent" from the UI but still discoverable through the API.
- **R-6 (AC29 gap, chat runtime). `add_context_tools` (`toolkit_service.py:1148-1205`) has no feature check.** A KB-attached assistant reaches `_find_index` (`:1652`, logs an error and returns `None`) or `_get_code_fields` (`:1616`, raises `ToolException` mid-chat) — exactly the "present and failing" state AC29 forbids.
- **R-7 (fence completeness). Fencing `index.router` alone leaves seven other entry points open** — see the Integration Points table. `GET /v1/providers/datasource_schemas`, `GET /v1/tools`, the four chat routers, workflow code search, the trigger actor, and three startup calls.
- **R-8 (naming divergence). Prior art specifies `SEARCH_BACKEND`; D1 locks `RETRIEVAL_BACKEND`.** `local/docs/codemie-standalone-storage-tasks.md` Task 2/Task 3 use `SEARCH_BACKEND` and scope the probe to "applies only when SEARCH_BACKEND selects the PostgreSQL backend", whereas D1/D2 drive the components off `RETRIEVAL_BACKEND == "none"` directly. Since Capability Hiding 1 and 2 have merged nothing, whichever name lands here becomes the precedent for the siblings and for the pgvector ticket. **Worth one explicit confirmation before the key is written**, because renaming a shipped config key is a breaking change.
- **R-9 (documentation trap). `is_feature_enabled` docstring at `customer_config.py:268-271` claims unknown features default to `True`; the code at `:262-265` returns `False`.** The code behaviour is what D2 wants (absent ⇒ disabled). Do not rely on the docstring; consider correcting it.
- **R-10 (guard reach). `src/codemie_tools/data_management/elastic/elastic_wrapper.py` builds its own `Elasticsearch` client** from a user-supplied `ElasticConfig`, bypassing `ElasticSearchClient` entirely. A `get_client()`-level guard cannot reach it. Explicitly out of scope, but it means "no ES is contacted" cannot be asserted from the guard alone — phrase AC26 verification accordingly.
- **R-11 (stale guidance).** `.ai-run/guides/development/configuration-patterns.md` cites `main.py:706` for the router-gating precedent; the real line is `:948`. `.ai-run/guides/data/elasticsearch-integration.md` documents a repository pattern (`BaseElasticRepository`) that is dead in production.
- **R-12 (prior art not shareable).** `local/` is gitignored (`.gitignore:48`). The ES consumer inventory the ticket depends on is not visible to reviewers from this branch. Its load-bearing facts are reproduced in this document for that reason.
- **R-13 (research gap).** No codegraph enumeration of `get_enabled_components()`'s 9 non-FE callers, nor of the datasource-creation validation chain. The three new components will surface in all nine callers; that list needs a direct read during planning.

---

## 7. Summary for Complexity Assessment

**Layers and change surface.** The task spans six layers — configs (one new `Config` key plus three new runtime components in `CustomerConfig`), service (`toolkit_service` runtime assembly, `tools_info_service` catalogue), REST API (`main.py` app assembly, `routers/index.py` fencing, `routers/tool.py` filtering), agents/toolkits, datasource ingestion (two import-time boot fixes), and optionally the `ElasticSearchClient` choke point. The *config* half is genuinely small and well-precedented: `_get_runtime_config()` already does exactly this six times over, and both resolution paths share the runtime-id exclusion filter, so three new components reach the FE contract through one edit in one function. The *fencing* half is where the work actually lives, because the retrieval surface has far more entry points than the single router D3 names — seven additional ones are enumerated above, spanning chat runtimes, the tool catalogue, provider schema listing, workflow execution, a trigger actor, and three module-scope startup calls.

**Technical novelty and the two real unknowns.** Nothing here is algorithmically novel; the difficulty is structural. First, the FastAPI app is assembled entirely at module scope with no `create_app()` factory, and the existing router test imports that singleton — so D3's "router is absent" behaviour has no established way to be tested (R-2). Second, and more consequential for AC26: D4 identified one import-time Elasticsearch construction, but there are **two**. `StateImportService().import_indexes()` at `main.py:887` runs at module scope and is ES-backed; fixing only the `google_doc_datasource_processor.py:49` class attribute leaves the container still unable to boot (R-1). This was found by direct verification during research, not from the audit table, and it materially changes the definition of done for AC26.

**Test posture and risk concentration.** Coverage of the config mechanism is good and the patching conventions are consistent, but two hazards sit directly in the path of this change. Five exact `len()` assertions on `get_enabled_components()` will break the moment the three components are added (R-4) — noisy but mechanical. The subtler one is R-3: the established `@patch("codemie.configs.customer_config.config")` style installs a `MagicMock`, so an unset `RETRIEVAL_BACKEND` attribute is *truthy*, and a naively-written guard would report retrieval as available in every existing test while looking correct. The guard's polarity must be chosen so the mock default fails safe. Beyond tests, the two AC29 gaps (R-5 tool catalogue, R-6 chat runtime) are the difference between "hidden in the UI" and "genuinely absent from the product" — the ticket's own wording ("absent rather than present and failing") makes both mandatory, and neither has any feature-gating code today. One question is worth settling before implementation: the config key name (R-8), since prior art says `SEARCH_BACKEND`, this ticket sets the precedent for its sibling tickets, and renaming a shipped key later is breaking.
