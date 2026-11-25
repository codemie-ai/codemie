# Technical Research

**Task**: security middleware exception error-handling
**Generated**: 2026-09-22T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

[Security] Global exception middleware exposes Python tracebacks in non-local 500 responses. The global handle_exceptions HTTP middleware in src/codemie/rest_api/main.py (lines ~1006-1027) returns up to 2000 chars of Python traceback in the HTTP 500 response body for all non-local environments (ENV != 'local'). The fix must: (1) return a generic HTTP 500 without any traceback or internal details, (2) log the full traceback server-side, (3) include the existing correlation/request identifier (set at main.py:996-1001 via X-Request-ID / codemie.request_id span) in the response body, (4) never disclose File paths, dependency versions, module names, class names. config.is_local is defined in src/codemie/configs/config.py:1038-1041. Acceptance criteria require automated tests covering local vs non-local config and regression coverage for the correlation between client-visible identifier and logged exception.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/main.py:1078–1099` — `handle_exceptions` middleware. The offending block:
  - Catches all unhandled `Exception` instances.
  - Calls `logger.error(e, exc_info=True)` — server-side logging is already in place.
  - For `config.is_local`, re-raises (no response, exception propagates to Starlette debug layer).
  - For all other environments: calls `traceback.format_exc()[-2000:]` and injects it into the `message` field of the JSON 500 body — this is the disclosure.
  - Does not include any request/correlation identifier in the response body.

- `src/codemie/rest_api/main.py:1055–1075` — `configure_logging` middleware.
  - Reads `X-Request-ID` header or generates `uuid.uuid4()`.
  - Stores it as `request.state.uuid`.
  - Calls `set_logging_info(uuid=uuid_str, ...)` to inject it into the logging context.
  - Sets `codemie.request_id` on the active OTel span.

- `src/codemie/configs/config.py:1065–1068` — `Config.is_local` property.
  - Returns `self.ENV == ENV_LOCAL`.
  - `ENV_LOCAL = "local"` (line 41); default `ENV: str = ENV_LOCAL` (line 52) — the default config value is local.

- `src/codemie/configs/logger.py` — exports `set_logging_info` and `logger` (imported at `main.py:46`).

### Architecture and Layers Affected

- **Middleware layer** (`src/codemie/rest_api/main.py`): single function `handle_exceptions`. This is the only change site for requirements 1–4.
- **Config layer** (`src/codemie/configs/config.py`): `Config.is_local` is read-only; no change needed there.
- **Middleware registration order**: in FastAPI/Starlette, `@app.middleware("http")` decorators are stacked in reverse registration order — the last registered is the outermost. `handle_exceptions` (line 1078) is registered after `configure_logging` (line 1055), so `handle_exceptions` is the outermost wrapper. When it catches an exception, `configure_logging` has already run and `request.state.uuid` is populated and available.

### Integration Points

- `configure_logging` sets `request.state.uuid` before any exception escapes the inner middleware chain — `handle_exceptions` can read `request.state.uuid` directly when building the safe response.
- `logger.error(e, exc_info=True)` already logs the full traceback via Python's logging system. The logging context includes `uuid` via `set_logging_info` called by `configure_logging`, so the server log entry and the client-visible `request_id` are already correlated through the logging framework.
- `traceback` stdlib module is imported at `main.py:17` and used only in `handle_exceptions`. If the traceback leak is removed, this import becomes unused.
- `ExtendedHTTPException` handler at line 1136 follows the safe pattern (log server-side, return structured body without internal detail) — this is the model the fix should follow.

### Patterns and Conventions

- All other exception handlers in `main.py` (lines 1102–1175) return `JSONResponse` with a `{"error": {"message": ..., "details": ..., "help": ...}}` body shape — no internal state in any field.
- The `ExtendedHTTPException` handler (line 1150–1157) is the closest pattern: checks severity, logs at `error` with `exc_info=True` for 5xx, returns a sanitised `JSONResponse`.
- Guide `.ai-run/guides/development/error-handling.md` states explicitly: "Log internal detail while returning safe client-facing messages."
- Guide `.ai-run/guides/development/logging-patterns.md` confirms: "Error with exception info when debugging requires stack context" — `logger.error(..., exc_info=True)` is already correct.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/development/error-handling.md` — directly covers this domain. Mandates "Log internal detail while returning safe client-facing messages." Cites `authentication.py:109` as a model of sanitised error return.
- `.ai-run/guides/development/logging-patterns.md` — covers logging severity and context. Confirms `error` + `exc_info=True` for server failures.
- `.ai-run/guides/testing/testing-patterns.md` — test location convention: mirror `src/` under `tests/codemie/`; test framework is pytest.
- `.ai-run/guides/testing/testing-api-patterns.md` — recommends testing error cases via `TestClient`; assert structured response fields, not raw exception text.

### Architectural Decisions

- The existing `configure_logging` middleware explicitly documents its purpose: set `request.state.uuid` from `X-Request-ID` for cross-system correlation (inline comment at `main.py:1066–1068`).
- The `ExtendedHTTPException` handler (line 1136) is evidence that the project already resolved "log 5xx detail server-side, return stable message to client" — `handle_exceptions` predates or missed that convention.

### Derived Conventions

- Error responses use the `{"error": {"message": ..., "details": ..., "help": ...}}` shape consistently across all handlers. Adding a `request_id` field at the top level or inside `error` is a novel field — either position is defensible but the top level keeps it orthogonal to the error payload.
- Test files for individual exception handlers use a minimal `FastAPI()` app with `add_exception_handler` or `add_middleware`, import the handler/middleware directly, and exercise it with `TestClient` — see `tests/codemie/rest_api/test_mcp_auth_required_handler.py`.

---

## 4. Testing Landscape

### Existing Coverage

- No test file exists for `handle_exceptions` or `configure_logging` middleware. This is a coverage gap.
- `tests/codemie/rest_api/test_mcp_auth_required_handler.py` — tests the `mcp_auth_required_handler` exception handler using a minimal FastAPI app. Closest structural analogue for the new tests.
- `tests/codemie/rest_api/routers/conftest.py` — provides `_inject_request_uuid` autouse fixture to stub `request.state.uuid` for router tests that run without the uuid-injection middleware.

### Testing Framework and Patterns

- **Framework**: pytest (pyproject.toml:157), `pytest.ini` at repo root.
- **API test pattern**: minimal `FastAPI()` + `TestClient` + directly imported handler/middleware function. No full app startup.
- **Config mocking**: `unittest.mock.patch` or `monkeypatch.setattr` on `config.is_local` (a `@property` on the `Config` singleton).
- **Fixtures**: `conftest.py` autouse fixtures for state injection; existing tests use `from unittest.mock import AsyncMock, MagicMock, patch`.

### Coverage Gaps

- No test for `handle_exceptions` in any environment mode (local or non-local).
- No test asserting that `request_id` is present in the 500 response body.
- No test asserting that traceback text is absent from the non-local 500 response body.
- No test for the correlation between the client-visible identifier and the logged exception (i.e., that `logger.error` is called and the uuid used client-side matches the uuid in the log call).

---

## 5. Configuration and Environment

### Environment Variables

- `ENV` — controls `config.is_local`; value `"local"` activates the re-raise branch. Set in deployment manifests / `.env`. Default in `Config` is `"local"`.

### Configuration Files

- `src/codemie/configs/config.py` — `Config` class; `ENV_LOCAL = "local"` constant; `is_local` property at line 1065.
- No feature flag controls the traceback-disclosure behaviour; it is gated only on `config.is_local`.

### Feature Flags and Deployment Concerns

- No feature flag. The change is a direct code fix inside `handle_exceptions`.
- Removing the `traceback` import from `main.py` is a clean-up that follows from the fix — verify no other caller in that file uses `traceback` before removing the import.

---

## 6. Risk Indicators

- **Traceback disclosure is unconditional for non-local environments**: `traceback.format_exc()[-2000:]` is computed and placed in the `message` field of every unhandled 500 response. File paths, module names, class names, and dependency versions all appear in Python tracebacks.
- **`request.state.uuid` availability**: `configure_logging` runs inside `handle_exceptions` (inner middleware), so `request.state.uuid` is populated before any exception reaches the outer handler. However, if `configure_logging` itself raises before setting `request.state.uuid`, accessing `request.state.uuid` in `handle_exceptions` will raise `AttributeError`. The fix should use `getattr(request.state, "uuid", None)` defensively.
- **`traceback` import becomes unused**: `main.py:17` imports `traceback`. After the fix, no other usage exists in the file — the import should be removed to avoid a Ruff `F401` lint failure that would block CI.
- **No existing tests**: the middleware has zero test coverage. The acceptance criteria require new tests; there is no existing test to extend.
- **`config.is_local` is a property on a singleton**: patching it in tests requires `monkeypatch.setattr(config, "is_local", ...)` or patching `config.ENV` — both are straightforward but the test must target the right object (`codemie.configs.config` module-level `config` instance, not the class).
- Speculative: if any downstream client currently parses the `message` field to extract diagnostic info from the traceback text, removing it will be a silent breaking change for that client. No evidence of such a client was found in this repo.

---

## 7. Summary for Complexity Assessment

The change surface is a single function — `handle_exceptions` in `src/codemie/rest_api/main.py` (lines 1078–1099), roughly 20 lines. The fix is a straightforward substitution: remove the `traceback.format_exc()` call and its injection into the response body, and add `request_id` (read from `request.state.uuid`) to the response. The `logger.error(e, exc_info=True)` call that already provides server-side traceback logging requires no modification. A defensive `getattr(request.state, "uuid", None)` guards the edge case where `configure_logging` itself failed. The `traceback` import at line 17 must be removed to satisfy Ruff. The existing error-response shape (`{"error": {"message", "details", "help"}}`) is retained; `request_id` is added as a sibling or top-level field.

Test novelty is moderate: the project has no existing tests for either HTTP middleware function. The required tests must stand up a minimal FastAPI app with the middleware applied, mock `config.is_local` for both branches, and assert on (a) absence of traceback text in non-local 500 bodies, (b) presence and correctness of the `request_id` field, and (c) that `logger.error` was called with `exc_info=True` for correlation coverage. The `test_mcp_auth_required_handler.py` file provides a direct structural template. The config-mocking pattern is well-established across the test suite via `monkeypatch` or `unittest.mock.patch`.

Overall risk is low-to-medium. The code change is minimal and isolated; the main complexity is the test setup — building the middleware test harness from scratch and correctly patching the `config` singleton. No database, no external service, no new dependency. The `traceback` import removal is the only secondary file touch.

---

## 8. External References

None named by the task.
