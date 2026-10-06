# Hide Elasticsearch Retrieval Features (Backend) — EPMCDME-14659 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `RETRIEVAL_BACKEND=none`, Knowledge Bases, Datasources and Code Indexing are genuinely absent (not present-and-failing) everywhere on the backend; the container still boots and serves everything else; startup output names the disabled capability.

**Architecture:** One config key (`RETRIEVAL_BACKEND`) and one pure helper `retrieval_available(cfg)` are the single source of truth. Every other change either reads that helper to skip registering/advertising/executing retrieval-backed code, or removes an import-time Elasticsearch construction that would crash boot before the helper is even reachable.

**Tech Stack:** FastAPI, Pydantic Settings, pytest + `unittest.mock.patch`, existing `ExtendedHTTPException` convention.

## Global Constraints

- Default `RETRIEVAL_BACKEND` is `"elasticsearch"` — existing deployments are unaffected unless `RETRIEVAL_BACKEND=none`.
- The three new customer-config components (`features:knowledgeBases`, `features:datasources`, `features:codeIndexing`) are runtime-computed only — no YAML entry, not overridable from YAML or the dynamic-config DB.
- No `create_app()` factory exists; `app` and all `include_router` calls in `main.py` are module scope. No reload/subprocess-based router-absence test is planned — none needed, per the spec's Testing subsection.
- Commit per task using the repository's existing convention.
- Out of scope, do not touch: retrieval implementation itself; AC28/pgvector; leaderboard, conversation analytics, metrics rotation, admin logs, stale-datasource, marketplace similarity (already fail-soft, excluded by the ticket); frontend; `codemie_tools/data_management/elastic/elastic_wrapper.py`; `triggers/actors/datasource.py` (unreachable once creation is gated); `agents/utils.py`'s three ES code-search sites (reached only via the code toolkit, closed by Task 5); datasource/KB creation itself — it lives entirely inside `index.py` under `index.router`, already fenced by Task 3.

---

### Task 1: `RETRIEVAL_BACKEND` config key and `retrieval_available()` helper

**Files:** Modify `src/codemie/configs/config.py:74-77`. Test: `tests/codemie/configs/test_config.py` (create if absent).

**Produces:** `retrieval_available(cfg) -> bool`, importable as `from codemie.configs.config import retrieval_available`. Every later task imports this.

Test-first: yes — `test_retrieval_available_true_for_elasticsearch` (`RETRIEVAL_BACKEND="elasticsearch"` → `True`) and `test_retrieval_available_false_for_none` (`RETRIEVAL_BACKEND="none"` → `False`), against a bare object/Namespace stand-in for `cfg`.

- [ ] Write the two failing tests; run `pytest tests/codemie/configs/test_config.py -k retrieval_available -v` — expect FAIL.
- [ ] Add, adjacent to the `ELASTIC_*` block:

```python
RETRIEVAL_BACKEND: str = "elasticsearch"  # elasticsearch | none  (pgvector later)


def retrieval_available(cfg) -> bool:
    return cfg.RETRIEVAL_BACKEND != "none"
```

- [ ] Re-run — expect PASS. Commit.

---

### Task 2: Three runtime customer-config components + docstring fix

**Files:** Modify `src/codemie/configs/customer_config.py:23-30` (`CONFIG_IDS`), `:131-188` (`_get_runtime_config`), `:267-271` (`is_feature_enabled` docstring). Test: `tests/codemie/configs/test_customer_config.py`.

**Consumes:** `retrieval_available(cfg)` (Task 1). **Produces:** `CONFIG_IDS["knowledgeBases"|"datasources"|"codeIndexing"]` → `"features:knowledgeBases"|"features:datasources"|"features:codeIndexing"`, surfaced through `get_runtime_components()` / `get_enabled_components()` / `is_component_enabled()`.

Test-first: yes — three new components each tested `enabled=True` with `mock_config.RETRIEVAL_BACKEND = "elasticsearch"` and `enabled=False` with `mock_config.RETRIEVAL_BACKEND = "none"`. **Every test in this file that patches `codemie.configs.customer_config.config` with a `MagicMock` and exercises the disabled branch must set `mock_config.RETRIEVAL_BACKEND = "none"` explicitly** — an unset `MagicMock` attribute is truthy and would silently report retrieval as available.

- [ ] Write the new component tests, and update the five exact `len()` assertions on `get_enabled_components()` (`:125`, `:263`, `:292`, `:345`, `:393`, each `+3`) plus the four single-component checks (`:428`, `:455`, `:482`, `:507`); run `pytest tests/codemie/configs/test_customer_config.py -v` — expect FAIL.
- [ ] Add the three entries to `CONFIG_IDS`; append three `Component(id=CONFIG_IDS[...], settings=ComponentSetting(enabled=retrieval_available(config)))` blocks to `_get_runtime_config()` following the existing six-entry pattern (`:146-186`), importing `retrieval_available` from `codemie.configs.config`. Fix the `is_feature_enabled()` docstring (`:270`) to say unknown features default to `False`, matching `is_component_enabled()`'s real behavior.
- [ ] Re-run — expect PASS. Commit.

---

### Task 3: Conditional `index.router` registration

**Files:** Modify `src/codemie/rest_api/main.py:894`.

**Consumes:** `retrieval_available(config)` (Task 1) — already covered by Task 1's pure-function tests; no new black-box router-absence test.

Test-first: no — a one-line wiring change around an already-tested pure function, mirroring the existing conditional registrations in this file (`:935`, `:948`).

- [ ] Change `app.include_router(index.router)` (`:894`) to:

```python
if retrieval_available(config):
    app.include_router(index.router)
```

Import `retrieval_available` alongside the existing `config` import at the top of `main.py`. Commit.

---

### Task 4: Two import-time boot-hazard fixes

**Files:** Modify `src/codemie/datasource/google_doc/google_doc_datasource_processor.py:49`, `src/codemie/rest_api/main.py:887`. Test: `tests/codemie/datasource/google_doc/test_google_doc_datasource_processor.py`.

**Consumes:** `retrieval_available(config)` (Task 1), for the `main.py` half.

Test-first: yes — `test_client_is_not_a_class_attribute`: asserts `"client" not in GoogleDocDatasourceProcessor.__dict__`, i.e. `ElasticSearchClient.get_client()` is no longer evaluated at class-body/import time.

- [ ] Write the failing test; run `pytest tests/codemie/datasource/google_doc/test_google_doc_datasource_processor.py -k not_a_class_attribute -v` — expect FAIL.
- [ ] Delete the class-body line `client = ElasticSearchClient.get_client()` (`:49`). `BaseDatasourceProcessor.__init__` (`base_datasource_processor.py:96`) already sets `self.client` lazily, and this subclass's `__init__` (`:78-86`) calls `super().__init__(...)`, so every instance still gets `self.client` — just not at import time. Existing tests that set `processor.client = mock_elastic` or patch the module-level `ElasticSearchClient` around construction are unaffected.
- [ ] In `main.py`, wrap the module-scope call at `:887`:

```python
if retrieval_available(config):
    StateImportService().import_indexes()
```

Reuses Task 3's wiring pattern — no black-box startup test, consistent with the spec's Testing subsection.
- [ ] Re-run the processor test — expect PASS, no regressions. Commit.

---

### Task 5: Toolkit gating — catalogue and chat runtime

**Files:** Modify `src/codemie/service/tools/tools_info_service.py:76,78-79`, `src/codemie/service/tools/toolkit_service.py:1148-1205`. Test: `tests/codemie/service/tools/test_tools_info_service.py` (new), `tests/codemie/service/tools/test_toolkit_service.py`.

**Consumes:** `retrieval_available(config)` (Task 1). **Produces:** `ToolsInfoService.get_tools_info()` omits the KB toolkit and skips the code-toolkit merge when unavailable. `ToolkitService.add_context_tools()` raises `ToolException("Knowledge base / code context is unavailable: retrieval backend is disabled.")` before dispatching to `_add_kb_tools`/`_add_code_tools` when unavailable and `context.context_type` is `KNOWLEDGE_BASE` or `CODE`.

Test-first: yes — `test_get_tools_info_excludes_kb_and_code_toolkits_when_retrieval_unavailable` + `test_get_tools_info_includes_them_by_default` (patch `codemie.service.tools.tools_info_service.config`). `test_add_context_tools_raises_when_retrieval_unavailable_for_kb_context`, `..._for_code_context`, and `test_add_context_tools_unaffected_when_retrieval_available` (patch `codemie.service.tools.toolkit_service.config`; assert `_add_kb_tools`/`_add_code_tools` never called when raising).

- [ ] Write the failing tests above; run `pytest tests/codemie/service/tools/test_tools_info_service.py tests/codemie/service/tools/test_toolkit_service.py -k retrieval -v` — expect FAIL.
- [ ] In `tools_info_service.py`, import `from codemie.configs.config import config, retrieval_available`; guard `:76` (`_merge_code_toolkit` call) and `:78-79` (`KBToolkit.get_tools_ui_info()` append) each with `if retrieval_available(config):`.
- [ ] In `toolkit_service.py`, import `retrieval_available` alongside the existing `config` import (`:42`). At the top of the `for context in assistant.context:` loop (`:1177`), before the `KNOWLEDGE_BASE`/`CODE` branches (`:1178`, `:1192`): if `context.context_type in (ContextType.KNOWLEDGE_BASE, ContextType.CODE)` and `not retrieval_available(config)`, `raise ToolException(...)`.
- [ ] Re-run — expect PASS. Run the full `test_toolkit_service.py` file to confirm no regression in the `PROVIDER`-context and git-tool paths. Commit.

---

### Task 6: Remaining entry-point guards

**Files:** Modify `src/codemie/service/provider/provider_service.py:76-79`, `src/codemie/workflows/utils/utils.py:289-301`. Test: `tests/codemie/service/provider/test_provider_service.py`, `tests/codemie/workflows/utils/test_utils.py` (create if absent).

**Consumes:** `retrieval_available(config)` (Task 1).

Test-first: yes — `test_index_schemas_raises_when_retrieval_unavailable` (patch config `RETRIEVAL_BACKEND = "none"`; assert `ExtendedHTTPException` from `ProviderService.index_schemas`) and `test_get_documents_tree_by_datasource_id_raises_when_retrieval_unavailable` (same patch; assert a clear error before any Elasticsearch call). Each paired with a default-config test asserting unchanged behavior.

- [ ] Write the failing tests; run `pytest tests/codemie/service/provider/test_provider_service.py tests/codemie/workflows/utils/test_utils.py -k retrieval -v` — expect FAIL.
- [ ] In `provider_service.py`, import `retrieval_available`; at the top of `index_schemas` (`:77-79`), raise `ExtendedHTTPException(code=status.HTTP_404_NOT_FOUND, message="Not found", details="Datasource schemas are unavailable: retrieval backend is disabled (RETRIEVAL_BACKEND=none).")` when `not retrieval_available(config)`, mirroring this file's existing `:87-90` shape.
- [ ] In `workflows/utils/utils.py`, import `retrieval_available` and `config`; at the top of `get_documents_tree_by_datasource_id` (`:289`), before the ES `search()` call (`:301`), raise `ValueError("Retrieval backend is disabled (RETRIEVAL_BACKEND=none); cannot search code index.")` when unavailable — matching this function's own not-found convention (`:292-293`).
- [ ] Re-run — expect PASS. Commit.

---

### Task 7: AC27 startup capability banner

**Files:** Modify `src/codemie/rest_api/main.py:707` (inside `lifespan()`). Test: `tests/codemie/rest_api/test_main_lifespan.py` (create if absent).

**Consumes:** `retrieval_available(config)` (Task 1).

Test-first: yes — `test_lifespan_logs_retrieval_unavailable_warning_when_disabled` (patch config `RETRIEVAL_BACKEND = "none"`, capture `logger.warning` during `lifespan()` entry) and `test_lifespan_does_not_log_when_retrieval_available` (default config → no such warning).

- [ ] Write the failing tests; run `pytest tests/codemie/rest_api/test_main_lifespan.py -v` — expect FAIL.
- [ ] Immediately after the existing line `logger.info(f"Starting CodeMie application. Config={config.to_safe_dict()}")` (`:707`), add:

```python
if not retrieval_available(config):
    logger.warning(
        "Retrieval capability unavailable (RETRIEVAL_BACKEND=none). Knowledge bases, "
        "datasources and code indexing are disabled. To enable retrieval, set "
        "RETRIEVAL_BACKEND to a supported backend and configure its connection settings."
    )
```

- [ ] Re-run — expect PASS. Commit.

---

## Negative-constraint pass

- **Out-of-scope areas untouched**: no task modifies `elastic_wrapper.py`, the retrieval implementation, AC28/pgvector, leaderboard, conversation analytics, metrics rotation, admin logs, stale-datasource, marketplace similarity, or any frontend file.
- **"Not overridable from YAML or the database"**: Task 2 adds the three components only to `CONFIG_IDS`/`_get_runtime_config()`, the same structurally-excluded path the existing six runtime components use.
- **"Absent, not present-and-failing" (AC29)**: Task 5 raises before reaching `_find_index`/`_get_code_fields`; Task 6 raises before the ES `.search()` call and before schema listing proceeds.
- **"No black-box router-absence test"**: honored in Task 3 and the `main.py` half of Task 4.
- **Trigger actor / `agents/utils.py` explicitly left unguarded**: no task modifies `triggers/actors/datasource.py` or `agents/utils.py`.
- negative-constraints: all addressed above; none skipped.
