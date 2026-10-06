# Spec: Hide Elasticsearch Retrieval Features (Backend) — EPMCDME-14659

**Branch**: `EPMCDME-14659_hide-elastic-retrieval-features` (from `EPMCDME-14347_standalone-codemie-released-artifact`)
**Scope**: Backend only. Frontend capability fencing is a separate ticket and derives its behavior from the contract this spec publishes (`GET /v1/config`).

## Problem

CodeMie must be able to run with PostgreSQL only, without pgvector/Elasticsearch as a vector store. Today, if Elasticsearch is unreachable, the application either fails to boot (one class-attribute ES client construction runs at import time) or serves features that depend on retrieval and lets them fail at request/chat time with confusing errors. Neither is acceptable for a standalone, storage-degraded deployment.

## Goal and acceptance criteria

- **AC26**: Starting against a PostgreSQL that cannot provide vector storage yields a running container serving everything that does not depend on retrieval, including chat with assistants that need none.
- **AC27**: Startup output names retrieval as the unavailable capability and states what would make it available.
- **AC29**: Knowledge bases, datasources and code indexing are absent from the product, not present and failing when used.

## Out of scope

- The retrieval implementation itself (schema, queries, ranking, fusion) — separate work.
- AC28 (adding vector storage and hot-enabling retrieval with nothing reinstalled) — moved to the clone ticket EPMCDME-14759.
- Gating of other Elasticsearch-dependent areas not named here: leaderboard, conversation analytics, metrics rotation/analytics, admin log lookup, stale-datasource detection, marketplace similarity. These already fail soft per the EPMCDME-14564 consumer audit and are explicitly excluded by the ticket.
- Frontend capability fencing (UI hiding of Knowledge Bases / Datasources / Code Indexing) — separate ticket, consumes the contract this spec produces.
- `codemie_tools/data_management/elastic/elastic_wrapper.py` — a hand-rolled ES client that bypasses `ElasticSearchClient` entirely; out of scope, and AC26/AC29 verification should not claim "no ES contacted whatsoever" because of it.

## Design

### 1. Detection — `RETRIEVAL_BACKEND` config key

Add to `src/codemie/configs/config.py`, adjacent to the existing `ELASTIC_*` keys:

```python
RETRIEVAL_BACKEND: str = "elasticsearch"  # elasticsearch | none  (pgvector later)
```

Default preserves every existing deployment's behavior unchanged. `"none"` means retrieval is unavailable. The name won over the alternative `SEARCH_BACKEND` used in older, unmerged planning docs — those docs never shipped, this ticket sets the real precedent, and "retrieval" matches the capability name used throughout the ticket's own acceptance criteria.

A single helper is the one source of truth every other layer reads:

```python
def retrieval_available(cfg) -> bool:
    return cfg.RETRIEVAL_BACKEND != "none"
```

Location: a small function in `src/codemie/configs/config.py` (or immediately adjacent), imported everywhere else that needs the check. No layer re-derives this condition independently.

### 2. FE contract — three runtime customer-config components

Add three entries to `CONFIG_IDS` in `src/codemie/configs/customer_config.py` and append them in `CustomerConfig._get_runtime_config()`, each `enabled=retrieval_available(config)`:

- `features:knowledgeBases`
- `features:datasources`
- `features:codeIndexing`

These are runtime-computed components, structurally excluded from YAML/dynamic-config-DB override in both resolution paths (`get_enabled_components()` and the actual FE-facing `customer_config_service.resolve_components()`), which is the correct property: an admin must not be able to switch these back on when there is no storage behind them. Because both paths share `CustomerConfig.get_runtime_components()`, adding the components here satisfies the ticket's contract regardless of which of the two functions is read as "the" `get_enabled_components()` the ticket names — `resolve_components()` is confirmed as the one that actually backs `GET /v1/config`.

Drive-by fix: `is_feature_enabled()`'s docstring currently claims unknown features default to `True`; the code defaults to `False` (`:262-265`), which is the behavior this design relies on. Correct the docstring while this file is already being edited.

### 3. Router surface — `index.router` conditionally registered

In `src/codemie/rest_api/main.py`, wrap the existing `app.include_router(index.router)` call:

```python
if retrieval_available(config):
    app.include_router(index.router)
```

This mirrors the existing precedent at `main.py:935` (`is_litellm_enabled() and ...`) and `:948` (`config.ENABLE_USER_MANAGEMENT`). When unavailable, every `/v1/index/*` route 404s and the "Indexing" tag disappears from OpenAPI — genuinely absent, not present-and-failing.

**Testing**: there is no `create_app()` factory in this codebase — `app` and every `include_router` call are module scope, and the existing router test imports that singleton at import time. Rather than invent a reload/subprocess-based black-box test with no precedent in this suite, this design tests `retrieval_available()` directly as a pure function against both config values. The registration line itself stays a simple, visually-obvious `if`, consistent with how the other two conditional registrations in this file are (not) tested today.

### 4. Two import-time boot hazards, both fixed

The technical analysis found two ES constructions that run before the ASGI app object exists — not one:

- **`src/codemie/datasource/google_doc/google_doc_datasource_processor.py:49`** — `client = ElasticSearchClient.get_client()` is a **class attribute**, evaluated at import time via the KB toolkit's import chain. Fix: turn it into a lazy `@property`, matching the safe pattern `base_datasource_processor.py:96` already uses inside `__init__`.
- **`src/codemie/rest_api/main.py:887`** — `StateImportService().import_indexes()` runs at **module scope** and is ES-backed (`BaseModelWithElasticSupport` + `helpers.bulk`). Fix: wrap it in `if retrieval_available(config):` so it's skipped entirely in degraded mode.

Both are required for AC26 — fixing only the first still leaves the container unable to boot.

### 5. Toolkit gating — one gate, two seams

Today nothing stops a KB-attached assistant from crashing mid-chat, and `GET /v1/tools` unconditionally advertises the KB toolkit and always merges code tools. One check, reused at both seams:

- **`src/codemie/service/tools/tools_info_service.py`** `get_tools_info()` — skip adding the KB toolkit (`:78-79`) and skip merging code tools (`:76`) when retrieval is unavailable. Closes the catalogue-level AC29 gap.
- **`src/codemie/service/tools/toolkit_service.py`** `add_context_tools()` — when `context.context_type` is `KNOWLEDGE_BASE` or `CODE` and retrieval is unavailable, skip `_add_kb_tools`/`_add_code_tools` with a clean, explicit rejection instead of letting the call fall through to today's `_find_index`/`_get_code_fields`, which return `None` or raise `ToolException` mid-chat. Closes the chat-runtime AC29 gap.

Both call sites reuse the same `retrieval_available(config)` check (or the equivalent `customer_config.is_component_enabled(...)` read where a request already has `customer_config` in scope) — no duplicated logic.

### 6. Remaining entry points

Beyond `index.router`, the technical analysis found seven other paths that reach retrieval-backed services. The toolkit gate above closes the four chat routers (assistant, a2a, ide, workflow_executions — all route through `add_context_tools`). This design additionally guards:

- **Provider datasource-schema listing and datasource/KB creation validation** — reject with a clear "retrieval unavailable" error (`ExtendedHTTPException`, matching the project's existing error-handling convention) rather than an opaque 500, since these are reachable independent of `index.router`.
- **Workflow code search** (`src/codemie/workflows/utils/utils.py:301`) — same rejection pattern as the code-search sites in `agents/utils.py`.

**Explicitly left unguarded**: the cron/webhook-driven trigger actor (`src/codemie/triggers/actors/datasource.py`). It only fires for a datasource that degraded mode already prevents creating, so it has no live trigger path in this feature's scope. Noted as a residual risk rather than built as a guard for an unreachable path — if a future change allows datasource creation through some path this spec didn't anticipate, this actor would need revisiting.

### 7. AC27 — startup capability banner

One `logger.warning(...)` in `lifespan()` (`src/codemie/rest_api/main.py`), immediately after the existing first log line at `:707` (`Starting CodeMie application. Config=...`), fired only when `not retrieval_available(config)`:

```python
logger.warning(
    "Retrieval capability unavailable (RETRIEVAL_BACKEND=none). Knowledge bases, "
    "datasources and code indexing are disabled. To enable retrieval, set "
    "RETRIEVAL_BACKEND to a supported backend and configure its connection settings."
)
```

WARNING level, not INFO or ERROR: this is an intended degraded-mode state (matching how row 10's `create_index_if_not_exists()` already logs a WARNING on ES failure at boot), not a crash, but still something an operator should notice in log output.

## Test-breakage awareness

Two existing-test hazards, both must be handled by the plan, not discovered during implementation:

- **Exact `len()` assertions**: `tests/codemie/configs/test_customer_config.py` has five assertions on `get_enabled_components()` counts that increment by 3 once the new components exist (`:125`, `:263`, `:292`, `:345`, `:393`, plus four single-component checks). These need explicit updates.
- **Mock-safety**: the established test pattern `@patch("codemie.configs.customer_config.config")` installs a `MagicMock`. An unset `mock_config.RETRIEVAL_BACKEND` attribute is a *truthy, non-`"none"` MagicMock*, so `retrieval_available()` naturally returns `True` against it — which matches production's real default (retrieval available unless explicitly disabled) and is therefore safe by construction, not by accident. Tests exercising the "unavailable" branch must set `mock_config.RETRIEVAL_BACKEND = "none"` explicitly.

## Non-goals recap

No YAML changes (`config/customer/customer-config.yaml` needs no new entries — all three components are runtime-computed). No database migration. No changes to the retrieval implementation itself. No frontend work.
