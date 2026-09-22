# Technical Research

**Task**: workflow validation dry-run MCP
**Generated**: 2026-09-08T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

Workflow Restore — dry-run validation (backend): Expose the same pre-persist validation that PUT /v1/workflows/{workflow_id} already runs, without writing the workflow. New endpoint POST /v1/workflows/{workflow_id}/validate that runs MCP validation, autonomous mode check, and WorkflowExecutor.validate_workflow via validate_for_update, returning 200 on success or identical 400 envelope to PUT on failure. Steps: 1) Collapse pre-persist update pipeline steps (MCP validate_on_save, _strip_workflow_mcp_servers, autonomous mode rejection, validate_for_update) into a shared helper used by both PUT and the new dry-run endpoint. 2) Add POST /v1/workflows/{id}/validate endpoint using UpdateWorkflowRequest body and error_format query param. 3) Add tests in tests/codemie/rest_api/routers/test_workflow.py following existing test_update_workflow* patterns.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/routers/workflow.py` — FastAPI router (prefix `/v1`, tag `Workflow`). Contains the full `update_workflow` (PUT `/workflows/{workflow_id}`, lines 380–455) handler including all pre-persist validation steps. Also contains the two router-private helpers:
  - `_collect_workflow_mcp_servers(workflow_config)` (line 79) — collects all `MCPServerDetails` from assistants and tools.
  - `_strip_workflow_mcp_servers(workflow_config)` (line 90) — strips inline config fields from catalog-ref MCP servers in-place via `MCPAccessControlService`.
  - `update_workflow_schema(workflow_config, user)` (line 570) — background diagram generation (not part of the validation pipeline).

- `src/codemie/service/workflow_service.py` — `WorkflowService.validate_for_update(workflow, updated_config, user, error_format)` (line 164, async). Runs marketplace validation when `workflow.is_global`, then calls `WorkflowExecutor.validate_workflow`. Uses a lazy import of `WorkflowExecutor` to avoid a module-level circular dependency (documented comment at line 171).

- `src/codemie/service/mcp/access_control.py` — `MCPAccessControlService.validate_on_save(mcp_servers)` (line 97). Validates MCP servers before any persist. Raises `ValidationException` on violation. Checks restricted mode (`mcpCustomServersDisabled` customer config flag), duplicate `mcp_config_id`s, and catalog entry existence/availability.

- `src/codemie/workflows/workflow.py` — `WorkflowExecutor.validate_workflow(workflow_config, user, error_format)` (line 222, static). Runs: YAML parsing (`validate_workflow_execution_config_yaml`), `parse_execution_config`, states presence check (non-autonomous), and `validate_workflow_config_resources_availability`. Raises `ValueError` in both string and JSON (dict) forms depending on `error_format`. Returns the compiled graph on success.

- `src/codemie/core/workflow_models/workflow_models.py` — `WorkflowMode` enum (AUTONOMOUS/SEQUENTIAL, line 35); `WorkflowErrorFormat` enum (STRING/JSON, line 40).

- `src/codemie/core/workflow_models/__init__.py` — exports `UpdateWorkflowRequest`, `WorkflowConfig`, `WorkflowErrorFormat` (consumed in the router's imports at line 38–46).

### Pre-persist Validation Pipeline in PUT /v1/workflows/{id} (lines 405–428)

The ordered steps inside the single outer `try` block:

1. `WorkflowConfig(**request.model_dump())` + `updated_config.parse_execution_config()` — YAML→structured fields; any failure raises `ExtendedHTTPException(400)` from the inner try/except (lines 405–413).
2. `MCPAccessControlService.validate_on_save(_collect_workflow_mcp_servers(updated_config))` — MCP access control; raises `ValidationException` on failure.
3. `_strip_workflow_mcp_servers(updated_config)` — mutates `updated_config` in-place.
4. Autonomous mode check: `if updated_config.mode == WorkflowMode.AUTONOMOUS: raise ExtendedHTTPException(403)`.
5. `await workflow_service.validate_for_update(workflow, updated_config, user, error_format)` — marketplace + executor validation.
6. `workflow_service.update_workflow(...)` — actual persist (not part of the dry-run).

The outer `except` chain (lines 451–455) re-raises `ValidationException` and `NotFoundException` as-is, and converts all other exceptions to `ExtendedHTTPException(400)`.

### Architecture and Layers Affected

- **REST API layer** (`src/codemie/rest_api/routers/workflow.py`): new endpoint definition, shared helper extraction.
- **Service layer** (`src/codemie/service/workflow_service.py`): `validate_for_update` is a candidate anchor for the shared helper, or the helper lives in the router module.
- **MCP Access Control service** (`src/codemie/service/mcp/access_control.py`): called unchanged; no modification expected.
- **Workflow executor** (`src/codemie/workflows/workflow.py`): called unchanged; `validate_workflow` is already a static method.

### Integration Points

- `WorkflowService.validate_for_update` → `_marketplace_service.validate` (when `workflow.is_global`) → `WorkflowExecutor.validate_workflow`.
- `MCPAccessControlService.validate_on_save` → `MCPConfig.get_by_ids` (DB read for catalog validation).
- `customer_config.is_component_enabled("mcpCustomServersDisabled")` — runtime feature flag controlling MCP restricted mode behavior inside `validate_on_save`.
- FastAPI app-level error handlers convert `ValidationException` → 400 structured body (at `src/codemie/rest_api/main.py:804`) and `ExtendedHTTPException` (at `main.py:812`).

### Patterns and Conventions

- Router-level authenticate + `Ability.can` pattern (same as `update_workflow`, lines 396–403).
- `WorkflowErrorFormat` is a `Query` dependency with a default of `WorkflowErrorFormat.STRING` — identical pattern expected on the new endpoint.
- `AsyncMock` used for `validate_for_update` patches in existing tests (it is an `async def`).
- `ASGITransport` + `AsyncClient` pattern for all router tests in `tests/codemie/rest_api/routers/test_workflow.py`.
- `@pytest.mark.asyncio` on all async test functions in the workflow router test file.
- `@patch` at function level, not class level, throughout the existing test suite.
- Background tasks (`background_tasks.add_task`) are only added in the PUT path, not in the validate path.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/api/rest-api-patterns.md` — covers router registration, error responses (use `ValidationException`/`ExtendedHTTPException`), authentication dependencies.
- `.ai-run/guides/api/endpoint-conventions.md` — route and response conventions.
- `.ai-run/guides/testing/testing-api-patterns.md` — router tests via `ASGITransport`/`AsyncClient`; test both happy paths and error cases including structured error response fields.
- `.ai-run/guides/integration/mcp-integration.md` — MCP config and auth behavior behind existing service/router modules; no new MCP auth logic should be added to unrelated routers.

### Architectural Decisions

- Lazy import of `WorkflowExecutor` inside `validate_for_update` is a documented workaround for circular imports. Any new shared helper that calls `WorkflowExecutor` must preserve this pattern or resolve the import graph another way.
- `ValidationException` is re-raised from the outer `except` chain in `update_workflow`, meaning it surfaces as a 400 structured response at the app level. `ExtendedHTTPException(403)` for autonomous mode is not caught by the inner except chain and propagates as-is.

### Derived Conventions

- Both `validate_for_update` and `validate_on_save` are already isolated from the persist step — the extraction into a shared helper is mostly a composition task, not a logic change.
- The `WorkflowSaveResponse` response model used by PUT is specific to a save operation; the dry-run endpoint will use a simpler response (e.g. `{"message": "Workflow configuration is valid"}`) or `BaseResponse`.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/routers/test_workflow.py` — five tests covering `update_workflow`:
  - `test_update_workflow` — happy path, asserts 200, message, data, `mock_validate.assert_called_once()`.
  - `test_update_workflow_no_permissions` — 401, message `'Access denied'`.
  - `test_update_workflow_not_found` — 404, message `'Workflow not found'`.
  - `test_update_workflow_exception` — generic exception from `update_workflow` service → 400.
  - `test_update_published_workflow_with_external_assistant_blocked` — `ValidationException` from `validate_for_update` → 400, asserts error message.
  - `test_update_non_published_workflow_skips_marketplace_validation` — 200, `mock_validate.assert_called_once()`.

- `tests/unit/service/mcp/test_access_control.py` — unit tests for `MCPAccessControlService.validate_on_save` (restricted/open mode, catalog entries, duplicates).

- `tests/codemie/workflows/test_workflow_mcp_servers.py` — test `validate_workflow` directly (MCP meta in error).

### Testing Framework and Patterns

- `pytest` with `pytest-asyncio`; `@pytest.mark.asyncio` on all async tests.
- `AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")` for HTTP-level testing.
- `@patch` (unittest.mock) at function level for service-layer mocks.
- `AsyncMock` for patching `async def` methods like `validate_for_update`.
- `side_effect=ValidationException(...)` pattern to simulate validation failures.
- Fixtures: `workflow_config`, `update_workflow_request`, `request_header` in `test_workflow.py` (all reusable for new tests).

### Coverage Gaps

- `WorkflowService.validate_for_update` has no direct unit tests (codegraph blast-radius notes "no tests found within 3 caller hops").
- `update_workflow` router function has no test for MCP `validate_on_save` failure path (ValidationException from MCP access control) — this gap will surface when adding tests for the new endpoint.
- The new `POST /v1/workflows/{id}/validate` endpoint has no tests yet.

---

## 5. Configuration and Environment

### Environment Variables

- `MCP_SERVER_INIT_TIMEOUT` (default 300.0 s) — MCP server start timeout; relevant to MCP validation at runtime but not to the dry-run endpoint.
- `SUBWORKFLOW_POOL_ENABLED` — workflow pool feature flag; not directly involved in validation.
- `WORKFLOW_GENERATION_ENABLED` — gates the generate endpoint (conditional router block); no impact on validate endpoint registration style.

### Configuration Files

- `config/mcp/mcp-commands-config.yaml` — loaded by `MCPCommandsConfig`; governs allowed MCP commands at runtime, not at save-time validation.
- Customer config flag `mcpCustomServersDisabled` — read by `MCPAccessControlService.validate_on_save` at call time.

### Feature Flags and Deployment Concerns

- `customer_config.is_component_enabled("mcpCustomServersDisabled")` — controls restricted-mode MCP validation inside `validate_on_save`. The dry-run endpoint will exercise the same branch since it calls the same code path.
- No feature flag currently gates the PUT workflow endpoint; the new validate endpoint is expected to be unconditional.

---

## 6. Risk Indicators

- **Circular import preserved in helper**: `WorkflowService.validate_for_update` already uses a lazy import (`from codemie.workflows.workflow import WorkflowExecutor` inside the method body). Any shared helper that moves this logic must carry the same lazy-import pattern; a module-level import would introduce the circular dependency the comment explicitly avoids.
- **403 vs 400 boundary**: The autonomous mode check raises `ExtendedHTTPException(403, ...)` inside the outer try block. The task says "identical 400 envelope to PUT on failure" but the PUT endpoint actually returns 403 for this case. Behavior alignment needs a decision — the spec says "400 envelope" but the existing PUT returns 403 for autonomous mode.
- **No tests for validate_for_update**: Codegraph confirms zero tests within 3 caller hops. Extracting this into a shared helper and adding the new endpoint without adding direct tests for the helper leaves the refactored unit untested at the service level.
- **_collect_workflow_mcp_servers and _strip_workflow_mcp_servers are router-private**: These helpers are currently module-level private functions in the router file. If the shared validation helper is placed in the service layer, these functions will need to move or be duplicated.
- **Error envelope shape for ValueError from validate_workflow**: `WorkflowExecutor.validate_workflow` raises `ValueError` (not `ExtendedHTTPException`). In PUT, this is caught by the outer `except Exception as e` block and surfaced as 400. The new endpoint must replicate the same catch-and-convert pattern to deliver the "identical 400 envelope".
- **_strip_workflow_mcp_servers mutates updated_config in-place**: The strip step is a mutation of the config object. A shared helper that both PUT and validate call must document that this mutation is intentional on the validate path, even though nothing is persisted.
- Speculative: none — all validation steps already exist and are fully implemented.

---

## 7. Summary for Complexity Assessment

The task touches three layers: REST API router (new endpoint), service layer (shared helper extraction), and the existing MCP access control and workflow executor services (called but not modified). The file change surface is narrow — primarily `src/codemie/rest_api/routers/workflow.py` for the new endpoint and helper extraction, and `tests/codemie/rest_api/routers/test_workflow.py` for new test cases; `src/codemie/service/workflow_service.py` may receive a small change if the shared helper is anchored there. All validation logic already exists; the work is composition and wiring, not new algorithmic development.

The primary technical risk is behavioral parity: the PUT endpoint's outer `except` chain handles `ValueError` from `WorkflowExecutor.validate_workflow` and `ValidationException` from `MCPAccessControlService.validate_on_save` differently (the former is wrapped to 400 via the generic handler; the latter is re-raised). The new dry-run endpoint must replicate this identical error-handling topology to fulfill the "identical 400 envelope" contract. A secondary risk is the 403-vs-400 question for the autonomous-mode rejection case — PUT currently returns 403, not 400, for this branch, and the spec says "identical 400 envelope". The circular import workaround for `WorkflowExecutor` inside `validate_for_update` must be preserved in any extracted helper.

Test coverage is the most notable gap. `WorkflowService.validate_for_update` has no direct tests today. The existing `test_update_workflow*` tests use fixture-level mocks for `validate_for_update`, so the new validate endpoint tests will follow the same pattern. The task explicitly requires tests in `tests/codemie/rest_api/routers/test_workflow.py` following `test_update_workflow*` patterns — these are well-understood and achievable with the existing `AsyncMock`/`ASGITransport` setup.

---

## 8. External References

None named by the task.
