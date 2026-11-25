# EPMCDME-14713: Traceback Disclosure Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Python traceback disclosure from the global `handle_exceptions` middleware and add a correlation `request_id` to every non-local HTTP 500 response.

**Architecture:** Single function edit in `src/codemie/rest_api/main.py` — remove `traceback.format_exc()` injection, read `request.state.uuid` set by the inner `configure_logging` middleware, and emit a safe generic 500 body. New test file exercises local vs non-local branches and logger correlation via a minimal FastAPI + TestClient harness (same pattern as `test_mcp_auth_required_handler.py`).

**Tech Stack:** Python, FastAPI, Starlette middleware, pytest, `unittest.mock.patch`

**Spec:** Requirements inline in task directory; see `technical-analysis.md` alongside this plan.

## Acceptance criteria

- Unhandled exceptions return HTTP 500 in all environments with no traceback text in the body.
- Response body contains no `"File "`, filesystem path, module name, class name, or raw exception text.
- Response body contains `request_id` matching the `X-Request-ID` / `request.state.uuid` value.
- Server logs contain the full traceback and the same `request_id` (`logger.error` with `exc_info=True`).
- Response shape `{"error": {"message", "details", "help"}, "request_id": ...}` is preserved for clients.
- Automated tests cover non-local (safe body) and local (re-raise) branches, plus logger correlation.

## Global Constraints

- Do not add new dependencies.
- `traceback` import at `main.py:17` must be removed after the fix (Ruff F401 gate).
- Commit per task using the repository's existing convention.

---

### Task 1: Fix `handle_exceptions` and add tests

**Test-first: yes — `test_non_local_hides_traceback` asserts no traceback text in the 500 body when `config.is_local` is False**

**Files:**
- Modify: `src/codemie/rest_api/main.py:17` (remove `traceback` import)
- Modify: `src/codemie/rest_api/main.py:1078-1099` (rewrite non-local response block)
- Create: `tests/codemie/rest_api/test_handle_exceptions.py`

**Interfaces:**
- Consumes: `handle_exceptions` async function imported from `codemie.rest_api.main`; `config` singleton from same module (patched in tests)
- Produces: nothing consumed by later tasks

- [ ] **Step 1: Write the failing tests**

Create `tests/codemie/rest_api/test_handle_exceptions.py`.

Middleware order note: Starlette applies middleware in reverse registration order — the last `@app.middleware("http")` registered is outermost. Register `inject_uuid` first (inner) and `handle_exceptions` second (outer) so that `request.state.uuid` is always set before the exception handler fires, matching the real app's `configure_logging` → `handle_exceptions` ordering.

```python
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codemie.rest_api.main import handle_exceptions

_STUB_UUID = "test-uuid-deadbeef"


def _build_app() -> FastAPI:
    app = FastAPI()

    # Registered first → inner middleware (runs after handle_exceptions wraps it)
    @app.middleware("http")
    async def inject_uuid(request, call_next):
        request.state.uuid = _STUB_UUID
        return await call_next(request)

    # Registered second → outermost middleware (catches exceptions)
    app.middleware("http")(handle_exceptions)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret: /srv/app/codemie/secret.py line 42")

    return app


_app = _build_app()
_client = TestClient(_app, raise_server_exceptions=False)


def test_non_local_hides_traceback():
    with patch("codemie.rest_api.main.config") as mock_cfg:
        mock_cfg.is_local = False
        response = _client.get("/boom")

    assert response.status_code == 500
    body = response.text
    # None of these must appear in any deployed-environment 500 response
    assert "Traceback" not in body
    assert "File " not in body
    assert "RuntimeError" not in body
    assert "secret" not in body
    assert "/srv" not in body


def test_non_local_includes_request_id():
    with patch("codemie.rest_api.main.config") as mock_cfg:
        mock_cfg.is_local = False
        response = _client.get("/boom")

    data = response.json()
    assert data.get("request_id") == _STUB_UUID


def test_non_local_preserves_error_shape():
    with patch("codemie.rest_api.main.config") as mock_cfg:
        mock_cfg.is_local = False
        response = _client.get("/boom")

    data = response.json()
    assert "error" in data
    assert "message" in data["error"]
    assert "details" in data["error"]
    assert "help" in data["error"]


def test_logger_called_with_exc_info_and_uuid_correlates():
    with patch("codemie.rest_api.main.config") as mock_cfg, \
         patch("codemie.rest_api.main.logger") as mock_log:
        mock_cfg.is_local = False
        response = _client.get("/boom")

    # logger.error must be called with exc_info=True so server logs contain the traceback
    mock_log.error.assert_called_once()
    _args, kwargs = mock_log.error.call_args
    assert kwargs.get("exc_info") is True

    # The client-visible request_id must match the uuid already in the logging context
    assert response.json().get("request_id") == _STUB_UUID


def test_local_reraises():
    with patch("codemie.rest_api.main.config") as mock_cfg:
        mock_cfg.is_local = True
        local_client = TestClient(_app, raise_server_exceptions=True)
        with pytest.raises(RuntimeError):
            local_client.get("/boom")
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/codemie/rest_api/test_handle_exceptions.py -v
```

Expected failures:
- `test_non_local_hides_traceback` — body currently contains `"Traceback"` and `"File "`.
- `test_non_local_includes_request_id` — response has no `request_id` key.
- `test_non_local_preserves_error_shape` — `message` field contains traceback text, not a clean string.

- [ ] **Step 3: Implement the fix in `main.py`**

At `main.py:17`, remove:
```python
import traceback
```

At `main.py:1078-1099`, replace the entire `handle_exceptions` function body with:

```python
@app.middleware("http")
async def handle_exceptions(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as e:
        logger.error(e, exc_info=True)

        if config.is_local:
            raise e

        request_id = getattr(request.state, "uuid", None)

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "message": "Internal Server Error.",
                    "details": "An unexpected error occurred while processing the request.",
                    "help": "Please try again later or contact support if the problem persists.",
                },
                "request_id": request_id,
            },
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/codemie/rest_api/test_handle_exceptions.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Run linter to confirm no F401**

```
make ruff
```

Expected: no `F401` (unused import) or other new lint failures.

---

## Negative-constraint pass

| Negative constraint | Task that honours it |
|---|---|
| No `"File "` in response body | Task 1 — `traceback.format_exc()` removed; asserted by `test_non_local_hides_traceback` |
| No filesystem path in response | Same — no exception attribute interpolated into any response field |
| No dependency version / source line in response | Same — only a static string in `message` |
| No internal module or class name in response | Same |
| No raw exception message in response | `str(e)` is never inserted; `message` is a static literal |
| `is_local` re-raise branch kept (local debug preserved) | Task 1 step 3 explicitly keeps `if config.is_local: raise e` |

No task violates any negative constraint. No negative constraint is left unaddressed.
