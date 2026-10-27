# Technical Research

**Task**: marketplace publish gate assistant quality-gates
**Generated**: 2026-09-18T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Implement a new side-effect-free endpoint POST /v1/assistants/{id}/marketplace/publish/gate-check that accepts the same request body as PUT /v1/assistants/{id} (CreateAssistantDto) plus a required `categories` field, runs the same deterministic marketplace quality gates against the *request body values* (not persisted assistant), and returns either {passed: true, gate_findings: []} (200) or the same gate-findings 422 error structure as the existing POST …/publish endpoint. No side effects: must not persist, publish, queue, or change marketplace_status. The endpoint is needed so the FE can validate unsaved form values before writing to the DB.

---

## 2. Codebase Findings

### Existing Implementations

- `/src/codemie/rest_api/routers/assistant.py` — owns all assistant CRUD and publish/marketplace sub-routes. Relevant existing endpoints:
  - `POST /assistants/{assistant_id}/marketplace/publish/validate` (line 1563) — side-effect-free inline-credentials check; the precedent for a pure check endpoint.
  - `POST /assistants/{assistant_id}/marketplace/publish` (line 1864) — the full stateful publish action.
  - `PUT /assistants/{assistant_id}` (line 888) — uses `AssistantRequest` as the request body.
- `/src/codemie/rest_api/models/assistant.py` — `AssistantRequest` (line 315) — the Python equivalent of CreateAssistantDto. Has `categories: list[str]` already (max_length=3, not required by default). `GateFinding` (line 172) and `PublishToMarketplaceRequest` (line 143) are also defined here.
- `/src/codemie/service/marketplace_management/validator/gates.py` — `run_all(record: AssistantRecord, cfg: Config) -> list[Finding]` — the pure, deterministic gate runner. 8 gate functions: `gate_name_length`, `gate_name_not_banned`, `gate_description_length`, `gate_description_not_equal_name`, `gate_system_prompt_length`, `gate_at_least_one_category`, `gate_substance`, `gate_no_inline_credentials`.
- `/src/codemie/service/marketplace_management/validator/models.py` — `AssistantRecord` dataclass (line 196), `Finding` dataclass (line 92) with `rule`, `severity`, `message`, `detail` and `.to_dict()`, `Severity` enum (`CRITICAL`, `OPTIONAL`).
- `/src/codemie/service/marketplace_management/entity_adapter.py` — `assistant_to_record(assistant: Assistant) -> AssistantRecord` converts an ORM row to the gate input dataclass.
- `/src/codemie/service/marketplace_management/validator/config.py` — `Config`, `GatesConfig`, `from_marketplace_management()`.
- `/src/codemie/configs/marketplace_management.py` — `get_marketplace_management_config(kind)` merges code defaults with DB-stored admin overrides from `customer_config`.
- `_publish_gate_findings(assistant: Assistant) -> list[dict]` (assistant.py, line 1652) — existing private helper that encapsulates the canonical gate invocation (config load → `assistant_to_record` → `gates.run_all`). No writes. Reusable directly.
- `_enforce_publish_gates(assistant, user, is_bypassing, force_publish)` (assistant.py, line 1709) — raises `ExtendedHTTPException(422)` when CRITICAL gates fire. The gate-check endpoint does NOT use this; it returns findings without raising.
- `/src/codemie/core/exceptions.py` — `ExtendedHTTPException` (line 28) — standard for user-facing HTTP errors.

### Architecture and Layers Affected

- **API / Router layer** (`assistant.py`): one new route decorator + handler function. Declared before `POST .../publish` to avoid path shadowing.
- **Service / Business-logic layer**: no new service class needed; reuses `_publish_gate_findings` (existing private helper) and the gate engine directly.
- **Repository / Data-access layer**: read-only access via `_get_assistant_by_id_or_raise` (DB SELECT) and `get_marketplace_management_config` (DB read of `customer_config`). No writes.
- **Validator sub-package** (`service/marketplace_management/validator/`): pure computation, no changes needed.

### Integration Points

- **DB read — assistants table**: `_get_assistant_by_id_or_raise(assistant_id, user)` to verify existence and ownership.
- **DB read — customer_config table**: `get_marketplace_management_config("assistants")` loads DB-overridden thresholds.
- **Gate engine**: `gates.run_all(AssistantRecord, Config) -> list[Finding]` — pure, no I/O.
- **No external service calls**: LLM validation (`_llm_validate_on_marketplace_publish`), Elasticsearch indexing, and metrics emission are all skipped.

### Patterns and Conventions

- New endpoint declared in `assistant.py`, placed before `POST .../publish` to avoid Starlette path-matching shadowing.
- `Depends(authenticate)` + `_check_user_can_access_assistant(user, assistant, "write", Action.WRITE)` — same auth pattern as `validate` and `publish`.
- Handler is synchronous (matching `validate_assistant_for_marketplace`); gate functions and config load are synchronous.
- Request model: a new `GateCheckRequest` Pydantic model with `categories: list[str]` (required, min_length=1) and the gate-relevant fields from `AssistantRequest` (`name`, `description`, `system_prompt`, optionally others). Alternatively: load the assistant from DB, then override with request body values before building `AssistantRecord` — this is the simplest correct path.
- Response model: new `GateCheckResponse(BaseModel)` with `passed: bool` and `gate_findings: list[GateFinding]` (reusing the existing `GateFinding` model).
- Return `200` in all cases (pass and findings-advisory). Raise `ExtendedHTTPException(422)` only for pre-conditions (not for gate failures themselves) — matching the `validate` endpoint's pattern.
- `AssistantRecord` for gate-check is built by loading the DB assistant, then substituting request-body values for the fields the gates examine. Numeric usage fields (`unique_users_count`, etc.) remain from the DB record (or default 0 for fully unsaved cases).

---

## 3. Documentation Findings

### Guides and Architecture Docs

The following `.ai-run/guides/` files are directly relevant:
- `.ai-run/guides/api/rest-api-patterns.md` — FastAPI router patterns, dependency injection, response models.
- `.ai-run/guides/api/endpoint-conventions.md` — route naming, response shape conventions.
- `.ai-run/guides/development/error-handling.md` — `ExtendedHTTPException` usage and the 422 details schema.
- `.ai-run/guides/architecture/layered-architecture.md` — layer taxonomy and where new code belongs.
- `.ai-run/guides/architecture/service-layer-patterns.md` — service orchestration patterns.

### Architectural Decisions

- **F-83 / EXECUTION-PLAN 7.1**: Gates always run; `ignore_recommendations` cannot skip them.
- **P1-01 / P1-05**: CRITICAL gate failure = blocking 422. For the gate-check endpoint this translates to returning the findings with `passed: false` rather than raising (per the validate endpoint precedent).
- **CR-004**: Gate check and DB commit must be in the same transaction for the full publish path. Not applicable to gate-check (no commit).
- **EXECUTION-PLAN 8.1**: The `validate` endpoint is the predecessor pattern; gate-check is its successor running the 8-gate deterministic suite.
- All surfaces (batch auditor, approve gate, publish gate, detail view) call `gates.run_all` through `assistant_to_record` + `Config.from_marketplace_management` — findings are never allowed to diverge between surfaces.

### Derived Conventions

- Error structure for 422 pre-conditions follows `ExtendedHTTPException` with `details={"assistant_id": ..., "gate_findings": [...], "failed_rules": [...]}`.
- Side-effect-free endpoints in the publish sub-path return structured `200` responses rather than raising on advisory findings.
- Lazy imports inside helper functions (as in `_publish_gate_findings`) are acceptable for the gate engine imports.

---

## 4. Testing Landscape

### Existing Coverage

- `/tests/codemie/rest_api/routers/test_publish_runs_deterministic_gates.py` — 4 DB-backed integration tests for `POST .../publish`: CRITICAL gate blocks 422, OPTIONAL gate is advisory 200, gate results persisted, stale-queue repro.
- `/tests/codemie/rest_api/routers/test_publish_bypass.py` — 3 DB-backed integration tests for bypass scenarios.
- `/tests/codemie/rest_api/routers/test_assistant_marketplace.py` — unit/async tests for publish and validate endpoints using `MagicMock` + `app.dependency_overrides`.
- `/tests/codemie/rest_api/routers/conftest.py` — shared fixtures: `user`, `assistant`, `marketplace_publish_env` (patches `Assistant.find_by_id`, `Ability.can`, config flags, category service, metrics, indexing, `assistant.update`), `marketplace_publish_payload()` helper, session-scoped `mm_publish_engine` (real Alembic migrations on throwaway PostgreSQL).

### Testing Framework and Patterns

- pytest with `fastapi.testclient.TestClient` (sync) and `httpx.AsyncClient` + `ASGITransport` (async).
- `app.dependency_overrides` to stub `authenticate`.
- `MagicMock` for ORM assistant objects in unit tests; real DB via `mm_publish_engine` for integration tests.
- `monkeypatch` for patching config flags and service calls.

### Coverage Gaps

The `POST /v1/assistants/{id}/marketplace/publish/gate-check` endpoint does not exist yet. New tests needed:
1. Happy path (all gates pass) → 200 `{passed: true, gate_findings: []}`.
2. CRITICAL gate fires (short name/description) → 200 `{passed: false, gate_findings: [{severity: "CRITICAL", ...}]}`.
3. OPTIONAL gate fires → 200 `{passed: true, gate_findings: [{severity: "OPTIONAL", ...}]}` (advisory).
4. Missing `categories` field → 422 (Pydantic validation).
5. No persistence side-effects — assert no rows written to `assistants`, `mm_review_decision`, `mm_marketplace_event`.
6. Unauthenticated request → 401/403.
7. Non-existent assistant ID → 404.

The existing `mm_publish_db` fixture and `marketplace_publish_env` pattern are reusable.

---

## 5. Configuration and Environment

### Environment Variables

| Variable | Type | Default | Relevance |
|---|---|---|---|
| `MARKETPLACE_MANAGEMENT_ENABLED` | bool | `True` | Kill switch — when False, marketplace management router is not mounted. Gate-check endpoint should respect this. |
| `MARKETPLACE_APPROVAL_GATE_ENABLED` | bool | `True` | Direct feature flag for gate logic. When False, skip `run_all` and return `{passed: true, gate_findings: []}`. |
| `MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED` | bool | `True` | LLM advisory check — NOT used by gate-check (no LLM calls). |

### Configuration Files

- `/src/codemie/configs/config.py` — `Config(BaseSettings)` — all env vars above.
- `/src/codemie/configs/marketplace_management.py` — `MarketplaceManagementConfig`, `get_marketplace_management_config()`, `VerificationCoreGates` / `VerificationPolishGates` — all gate thresholds.
- `/src/codemie/service/marketplace_management/validator/config.py` — credential pattern constants: `CREDENTIAL_PATTERNS_MAX=32`, `CREDENTIAL_PATTERN_MAX_LENGTH=200`, `CREDENTIAL_PATTERN_MATCH_TIMEOUT_SECONDS=0.25`, `CREDENTIAL_HAYSTACK_MAX_CHARS=12000`.

### Feature Flags and Deployment Concerns

- Both `MARKETPLACE_MANAGEMENT_ENABLED` and `MARKETPLACE_APPROVAL_GATE_ENABLED` must be True for gate logic to run. The handler should short-circuit with `{passed: true, gate_findings: []}` when either is False, mirroring the publish endpoint behaviour.
- Gate thresholds (`gates.name_min=5`, `core_gates.description_min=100`, `core_gates.system_prompt_min=200`, `polish_gates.categories_min=2`, etc.) are DB-overridable via `customer_config` key `marketplaceThresholds`. The `get_marketplace_management_config` call handles this transparently.
- No secrets or credentials involved beyond standard DB access.

---

## 6. Risk Indicators

- **No existing tests for the new endpoint** — full test suite must be written from scratch. The `mm_publish_db` fixture is reusable for side-effect assertions but will require a new `gate_check_env` conftest fixture.
- **`entity_adapter.assistant_to_record` takes an ORM object** — the gate-check endpoint must either load the assistant from DB and override fields, or construct `AssistantRecord` directly from request body values. The direct-construction path requires mapping `AssistantRequest` fields to `AssistantRecord` fields manually and risks drift if `AssistantRecord` gains new fields. The DB-load-then-override path is safer but means the endpoint is not purely stateless (still does a SELECT). The task spec says "not persisted assistant" suggesting direct construction from body values is the intent — this mapping must be explicit and complete.
- **`get_marketplace_management_config` is synchronous and does a DB read** — when called from an async endpoint it must be wrapped in `run_in_threadpool`. The existing `_publish_gate_findings` helper and the sync handler pattern avoids this, but if the endpoint is declared async the wrapping is required.
- **Path ordering in `assistant.py` is critical** — `POST .../publish/gate-check` must be declared before `POST .../publish` to avoid Starlette matching `gate-check` as the `{assistant_id}` path segment of a different route. Starlette resolves by first-match.
- **`MARKETPLACE_APPROVAL_GATE_ENABLED=False` behaviour** — must be explicitly handled. The task says "runs the same gates"; if the flag is False in the environment, returning `passed: true` with no findings is the correct no-op response, but this needs confirmation.
- **`categories` field scope** — `AssistantRequest.categories` already exists but is not required; `PublishToMarketplaceRequest.categories` is required. The new request model must make `categories` required (min_length=1) to ensure `gate_at_least_one_category` has input to evaluate.
- **`gate_no_inline_credentials` regex timeout** — `CREDENTIAL_PATTERN_MATCH_TIMEOUT_SECONDS=0.25` per pattern; on a large system prompt this gate can take up to 8 × 0.25 = 2 s. For a synchronous handler this is acceptable but worth noting for p99 latency.
- **No documentation gap** — the `.ai-run/guides/` API and endpoint-convention guides cover this pattern fully. No guide updates needed for this task.

---

## 7. Summary for Complexity Assessment

The gate-check endpoint is a **narrow, well-bounded addition** to an existing router. All the hard logic — the 8 gate functions, the `Config` dataclass, the `get_marketplace_management_config` DB merge, and the `_publish_gate_findings` private helper — already exist and are fully isolated. The implementation touches a single architectural layer (the router handler in `assistant.py`) plus a new Pydantic request/response model pair. Estimated file change surface: 1 router file, 1 models file, 1–2 new test files. No new service classes, no DB migrations, no new external integrations.

The primary technical novelty is the **`AssistantRecord` construction strategy**: the existing `assistant_to_record` adapter requires an ORM object, but the endpoint spec calls for running gates against request body values rather than persisted data. The implementation must either (a) load the assistant from DB and override the gate-relevant fields with request body values (simpler, one extra SELECT, safe), or (b) build `AssistantRecord` directly from the request body without a DB load (pure, but requires an explicit field mapping that could drift). Option (a) is lower risk and matches the pattern used by `_publish_gate_findings`; the only difference is applying `categories` from the request rather than from the DB row — exactly what the existing publish handler already does at line 1922.

Test coverage posture is **mixed**: the gate engine itself is tested via the existing publish tests, but the new endpoint has zero coverage. The existing `mm_publish_env` conftest fixtures and DB-backed `mm_publish_engine` fixture are directly reusable, reducing test-writing effort. Key test cases are: all-pass 200, CRITICAL finding 200-with-findings, OPTIONAL advisory 200-with-findings, missing-categories 422, no-side-effects assertion on the DB, and auth/404 edge cases. Overall complexity is **low-to-medium**: straightforward router wiring with a non-trivial request model design decision and a full new test suite to write.
