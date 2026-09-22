# Technical Research

**Task**: workflow guardrail validation persistence
**Generated**: 2026-09-14T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

Fix PUT /workflows/{id} to validate guardrail assignments before persisting workflow changes to DB. Currently, workflow_service.update_workflow() commits to DB first (line 455 in src/codemie/rest_api/routers/workflow.py), then GuardrailService.sync_guardrail_assignments_for_entity() validates and syncs. If guardrail validation fails, API returns 400 but workflow changes are already committed. The fix should run guardrail validation (dry_run=True already exists on the method) before persisting the workflow, so nothing is committed if guardrail validation fails.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/routers/workflow.py` lines 415–500 — `async def update_workflow(...)` route handler for `PUT /workflows/{workflow_id}`. At line 455 it calls `workflow_service.update_workflow()` which commits to DB; at lines 457–463 it calls `GuardrailService.sync_guardrail_assignments_for_entity()` which can raise `ExtendedHTTPException` (400) after the commit has occurred. The handler also contains `_run_pre_persist_update_validation` at line 453 which gates MCP, autonomous-mode, and executor validation before the DB write.
- `src/codemie/rest_api/routers/workflow.py` lines 101–126 — `_run_pre_persist_update_validation` helper: centralises pre-persist validation; raises `ExtendedHTTPException`, `ValidationException`, or `NotFoundException` before any DB write. The guardrail dry-run check is not currently included here.
- `src/codemie/service/workflow_service.py` line 178 — `WorkflowService.update_workflow(stored_config, updated_workflow_config, user)`: delegates to `_update_workflow_values` and returns `stored_config`. No exception handling at this layer for guardrail concerns.
- `src/codemie/service/workflow_service.py` lines 726–782 — `_update_workflow_values`: mutates `stored_config` fields then calls `stored_config.update()` (with or without `yaml_config_history`) which persists to PostgreSQL. This is the exact commit point that must succeed only after guardrail validation passes.
- `src/codemie/service/guardrail/guardrail_service.py` lines 175–313 — `GuardrailService.sync_guardrail_assignments_for_entity(user, entity_type, entity_id, entity_project_name, guardrail_assignments, dry_run=False)`: when `guardrail_assignments is None` it returns immediately (no-op). When not None, it runs Steps 1–5 (query existing assignments, build desired set, diff, validate permissions via `_validate_guardrail_user_and_project_permissions`). At line 284–285 it returns early if `dry_run=True`, skipping the actual DB writes (Steps 6–7). If permission or cross-project checks fail, `ExtendedHTTPException` with HTTP 400, 403, or 404 is raised from within Step 5.
- `src/codemie/service/guardrail/guardrail_service.py` lines 742–769 — `_validate_guardrail_user_and_project_permissions`: raises `ExtendedHTTPException` (404 if guardrail not found, 400 for cross-project mismatch, 403 for missing DELETE permission). This is the source of the 400 that fires after the DB write in the current code.

### Architecture and Layers Affected

| Layer | Component |
|---|---|
| API (router) | `src/codemie/rest_api/routers/workflow.py` — the `update_workflow` route handler and `_run_pre_persist_update_validation` helper |
| Service | `src/codemie/service/workflow_service.py` — `WorkflowService.update_workflow` and `_update_workflow_values` |
| Service | `src/codemie/service/guardrail/guardrail_service.py` — `GuardrailService.sync_guardrail_assignments_for_entity` |
| Data model | `src/codemie/core/workflow_models/workflow_config.py` — `WorkflowConfig.update()` (inherits `BaseModelWithSQLSupport`) |

The fix is entirely within the API layer (router handler ordering) and does not require changes to the service or data layers.

### Integration Points

- `GuardrailService.sync_guardrail_assignments_for_entity` is called by 5 other callers: `workflow_generator_service.py`, `base_datasource_processor.py`, `index.py` (router), `assistant.py` (router), and `workflow.py` (router, POST and PUT). The `dry_run=True` parameter is already part of the public signature but is only used in existing tests; no production caller currently passes `dry_run=True`.
- `WorkflowConfig.update()` is a SQLModel helper on `BaseModelWithSQLSupport` which writes to PostgreSQL synchronously within the call.
- The `GuardrailRepository` operations inside `sync_guardrail_assignments_for_entity` are independent DB operations (not wrapped in the same session or transaction as the workflow update). The docstring notes this explicitly: "The database updates are not done in a single transaction…eventual consistency."

### Patterns and Conventions

- Pre-persist validation is collected in `_run_pre_persist_update_validation` (lines 101–126). The guard raises before any DB write and re-raises known exceptions verbatim.
- The POST `/workflows` handler (lines 355–412) also calls `sync_guardrail_assignments_for_entity` without `dry_run` after creating the workflow. POST is less affected by this ordering bug because the entity is new, but the same architectural exposure exists.
- `ExtendedHTTPException` carries `.code`, `.message`, `.details`, and `.help`. The outer `try/except` in `update_workflow` catches it at line 479–480 and re-raises verbatim; the 400 response the task describes is produced by this path.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/architecture/service-layer-patterns.md` — confirms services coordinate repositories and domain decisions; routers handle request parsing/response formatting.
- `.ai-run/guides/data/database-patterns.md` — confirms SQLModel sessions are scoped; no long-lived sessions in global state; commits happen inside model helpers.

### Architectural Decisions

No ADR specific to guardrail/workflow ordering was found. The `sync_guardrail_assignments_for_entity` docstring is the closest recorded decision; it explicitly documents that `dry_run=True` exists to allow "existence, project, and permission checks without writing."

### Derived Conventions

- Pre-persist validation concerns belong in `_run_pre_persist_update_validation` or an equivalent early-exit block before the service layer call.
- The `dry_run=True` parameter was designed for exactly this use case (validate before a caller's own write).
- Known typed exceptions (`ExtendedHTTPException`, `ValidationException`, `NotFoundException`) are re-raised as-is; generic exceptions are wrapped.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/routers/test_workflow.py` — `test_update_workflow` (happy path PUT), `test_update_workflow_no_permissions` (401), `test_update_workflow_not_found` (404), `test_update_workflow_exception` (generic exception → 400), `test_update_workflow_pins_id_from_path_param`, `test_update_workflow_ignores_mismatched_body_id`, `test_update_published_workflow_with_external_assistant_blocked`, `test_update_non_published_workflow_skips_marketplace_validation`.
- `tests/codemie/service/test_workflow_service.py` — covers `update_workflow`, `_update_workflow_values`, yaml history, and field-skipping behavior.
- `tests/codemie/service/guardrail/test_guardrail_service.py` — covers `sync_guardrail_assignments_for_entity` including dry_run, cross-project, and permission scenarios.

### Testing Framework and Patterns

- pytest with `pytest-asyncio`; async routes tested via `httpx.AsyncClient` + `ASGITransport(app=app)`.
- Authentication overridden via `app.dependency_overrides[authenticate]` in `autouse` fixture.
- Service-layer calls patched via `unittest.mock.patch` at the fully-qualified import path.

### Coverage Gaps

- No test asserts that `workflow_service.update_workflow` is NOT called when guardrail validation fails on PUT. The specific scenario — guardrail validation raises an error and the workflow DB state is checked — has no test coverage.
- No test exercises the ordering: dry_run check before persist, then real sync after persist.

---

## 5. Configuration and Environment

### Environment Variables

No environment variables govern the guardrail validation or workflow persistence ordering.

### Configuration Files

- `src/codemie/configs/` — application config; no feature flags for guardrail validation ordering found.

### Feature Flags and Deployment Concerns

No feature flags found for this area. The change affects the synchronous request path of an existing endpoint with no deployment-time coordination needed.

---

## 6. Risk Indicators

- **Ordering gap is the stated bug**: `workflow_service.update_workflow()` at router line 455 commits to DB before `GuardrailService.sync_guardrail_assignments_for_entity()` validates. The fix is a call-order change in a single function in `workflow.py`.
- **No transactional atomicity between workflow update and guardrail sync**: the docstring on `sync_guardrail_assignments_for_entity` acknowledges eventual consistency. Adding a `dry_run=True` pre-check reduces the window but does not eliminate the theoretical TOCTOU gap (guardrail could be deleted between the dry_run and the real sync). This is pre-existing and documented, not introduced by this fix.
- **POST /workflows has the same structural exposure**: the POST handler creates the workflow before calling `sync_guardrail_assignments_for_entity` without `dry_run`. A similar fix could be applied for consistency, but it is not within the stated task scope.
- **`dry_run=True` is not currently called in production**: the parameter exists and is covered by tests, but no production caller uses it. The code path is correct (returns early at line 284); the risk is low.
- **No existing test for the failure scenario**: the test gap means the bug regression cannot be caught by the current suite. A test asserting `update_workflow` is not called when guardrail validation fails on PUT is missing.
- **`_run_pre_persist_update_validation` is the natural home for the dry_run call**: adding the guardrail dry_run check there would keep all pre-persist concerns in one place. If placed directly in the route handler instead, there is a small consistency risk that a future refactor of `_run_pre_persist_update_validation` omits the guardrail check.

---

## 7. Summary for Complexity Assessment

The change is scoped to a single function in one file: the `update_workflow` route handler in `src/codemie/rest_api/routers/workflow.py` (approximately lines 430–500). The `dry_run=True` parameter already exists on `GuardrailService.sync_guardrail_assignments_for_entity` and is fully tested at the service layer. The fix is a call-order change: invoke `sync_guardrail_assignments_for_entity(..., dry_run=True)` before calling `workflow_service.update_workflow()`, then invoke the real sync afterward. No new abstractions, schema changes, or cross-service contracts are needed.

The pre-persist validation pattern is well-established (`_run_pre_persist_update_validation` is the existing gateway), and the fix can either extend that helper or add the dry_run call directly in the try block before the DB write. The primary decision is whether to place the dry_run invocation inside `_run_pre_persist_update_validation` (centralized, consistent with the helper's purpose) or inline in the route handler (visible, minimal surface). Either way, the file change surface is one function in one file.

Test coverage requires one new router-layer test: assert that when `sync_guardrail_assignments_for_entity` raises on dry_run, `workflow_service.update_workflow` is not called and the response is 400. The existing service-level dry_run tests do not need to change. The overall risk is low: the logic path being added is already exercised by the test suite at the service layer; only the integration test at the router layer is missing.

---

## 8. External References

None named by the task.
