# Backend Mermaid Availability in Standalone — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the backend report Mermaid availability to the UI, warn admins at startup when standalone has no renderer, and classify rendering failures into three distinct, accurate error cases.

**Architecture:** Add one classmethod (`MermaidService.is_configured()`) as the single source of truth for "is a renderer reachable in principle"; split `draw_mermaid`'s catch-all exception into three typed exceptions; thread those through both diagram-generating API call sites with distinct messages; publish availability via the existing runtime-component mechanism in `customer_config.py`; extend the existing standalone startup hook with one combined warning.

**Tech Stack:** Python, FastAPI, Pydantic `BaseSettings`, pytest, `unittest.mock`.

**Requirements source:** Inline ticket text (EPMCDME-16477), no spec.md. Technical analysis: `docs/superpowers/tasks/2026-10-30-epmcdme-16477/technical-analysis.md`.

**Commit per task using the repository's existing convention.**

**Test commands:** run from the repository root as `poetry run pytest tests/<path> -v`.

## Acceptance criteria

- [ ] AC1 — Non-standalone: behavior unchanged, default renderer address kept, Mermaid reported available.
- [ ] AC2 — Standalone, no server address and no mermaid.ink: startup logs one warning naming the missing config and all four degraded features (chat diagrams, workflow graph preview, stored workflow diagram image, conversation export diagrams); startup continues.
- [ ] AC3 — Standalone, server address or mermaid.ink configured: no Mermaid warning at startup.
- [ ] AC4 — Standalone, `GET /config` requested: Mermaid reported available only if the feature flag is enabled AND a renderer is configured.
- [ ] AC5 — Standalone, flag enabled, reachable renderer, valid diagram: renders successfully.
- [ ] AC6 — Configured but unreachable renderer: error says renderer is unavailable, no syntax hint.
- [ ] AC7 — Reachable renderer rejects a diagram: error identifies an invalid diagram, asks to check syntax.
- [ ] AC8 — Rendering not configured, diagram requested via API: error says "not configured", distinct from AC6/AC7.
- [ ] AC9 — Rendering not configured, workflow create/update: save still succeeds.

## Global Constraints

- D1: `MERMAID_SERVER_URL` default (`http://localhost:8082`) stays; "not configured" is a real-value check, not a standalone-only rule.
- D2: `MERMAID_USE_MERMAID_INC=True` counts as configured even with a blank `MERMAID_SERVER_URL`.
- D3: New flag is a plain `config.py` boolean (`MERMAID_ENABLED`, default `True`), not a YAML `customer-config.yaml` component, not a `FEATURE_*` env override.
- D4: Outside standalone, the published `features:mermaid` component is always `enabled=True`, regardless of flag/dependency state. The flag only matters in standalone.
- D5: Availability is published via `customer_config.py#_get_runtime_config()`, exactly like `knowledgeBases`.
- D6: `MERMAID_ENABLED` controls only the published `features:mermaid` availability (UI). `draw_mermaid` does not check the flag, so the API still renders when a renderer is configured. This is intentional.
- Do not touch `export_utils.py`, the standalone Dockerfile, or bundle a renderer — all out of scope.

## Review Focus

- mermaid.ink opt-in with a blank `MERMAID_SERVER_URL` must still read as configured (D2) — an OR check, not AND.
- `MermaidService.is_configured()` is unconditional (not gated on `config.STANDALONE`): a non-standalone deployment that blanks `MERMAID_SERVER_URL` without enabling `MERMAID_USE_MERMAID_INC` must still get the "not configured" error from `draw_mermaid` itself — only the *published* `features:mermaid` flag is forced True outside standalone (D4), not the service's own guard.
- `draw_mermaid` must not consult `MERMAID_ENABLED` (D6) — flag off with a configured renderer still renders via the API.
- Non-200 classification: 5xx and 429 → `MermaidServiceUnavailableError` (AC6); other 4xx → `MermaidDiagramGenerationError` (AC7). Only `requests.exceptions.RequestException` becomes "unavailable"; other exceptions propagate unchanged.
- Narrowing `validate_workflow_and_draw`'s try/except (so `MermaidService` exceptions propagate) must not stop swallowing unrelated graph-build failures (e.g. LangGraph errors) — those must still return `None`, matching `test_create_workflow_diagram_no_diagram`.
- `update_workflow_schema` (the background-task helper feeding workflow create/update/rollback) must swallow every new exception type to `None` — AC9 breaks if any new exception type escapes this call site.
- The `features:mermaid` component is an AND of flag and dependency — flag on with no renderer, or renderer configured with flag off, must both resolve to `False` in standalone.

**Note:** `_make_external_request` (mermaid.ink) currently sends no timeout, so an unreachable mermaid.ink hangs instead of reporting unavailable. Task 1 adds `timeout=config.MERMAID_SERVER_TIMEOUT` there as one-line hardening.

---

### Task 1: `MermaidService.is_configured()` + `MERMAID_ENABLED` flag + typed exceptions

**Files:**
- Modify: `src/codemie/configs/config.py:83-85` — add `MERMAID_ENABLED: bool = True` alongside the existing Mermaid settings.
- Modify: `src/codemie/service/file_service/mermaid_service.py` — add `is_configured()`, new exception classes, rewrite `draw_mermaid`'s exception handling, classify non-200 in `_process_response`, add timeout to `_make_external_request`.
- Test: `tests/codemie/service/file_service/test_mermaid_service.py`

**Interfaces:**
- Produces: `MermaidService.is_configured() -> bool` — `bool(config.MERMAID_SERVER_URL) or config.MERMAID_USE_MERMAID_INC`. Used by Tasks 2 and 3.
- Produces: `MermaidNotConfiguredError(RuntimeError)`, `MermaidServiceUnavailableError(RuntimeError)` in `mermaid_service.py` (both subclass `RuntimeError` so the existing `except Exception`/`pytest.raises(RuntimeError)` call sites keep working untouched until Tasks 4/5 give them specific handling). `MermaidDiagramGenerationError` already exists and keeps its meaning (reachable renderer rejected the diagram, 4xx → AC7).
- Produces: `MermaidInvalidResponseError(MermaidDiagramGenerationError)` — replaces the bare `ValueError` `_process_response` raises for an unexpected content type, so it is never confused with workflow-config `ValueError`s and is handled wherever `MermaidDiagramGenerationError` is.
- Consumes: nothing new.

**Test-first: yes — `draw_mermaid` must raise `MermaidNotConfiguredError` without attempting a request when neither `MERMAID_SERVER_URL` nor `MERMAID_USE_MERMAID_INC` is set, `MermaidServiceUnavailableError` on a connection error or a 5xx/429 response, and `MermaidDiagramGenerationError` on a 4xx; today these collapse into a bare `RuntimeError`/one generation error.**

- [ ] **Step 1: Write failing tests**

```python
def test_is_configured_true_when_server_url_set(): ...   # MERMAID_SERVER_URL set, INC False -> True
def test_is_configured_true_when_mermaid_inc_enabled(): ...  # MERMAID_SERVER_URL="", INC True -> True
def test_is_configured_false_when_neither_set(): ...     # MERMAID_SERVER_URL="", INC False -> False
def test_mermaid_enabled_defaults_true(): ...            # Config().MERMAID_ENABLED is True
def test_draw_mermaid_raises_not_configured_without_request(self, mock_internal, mock_config):
    mock_config.MERMAID_SERVER_URL = ""
    mock_config.MERMAID_USE_MERMAID_INC = False
    with pytest.raises(MermaidNotConfiguredError):
        MermaidService.draw_mermaid("graph TD; A-->B;")
    mock_internal.assert_not_called()

def test_draw_mermaid_connection_error_raises_service_unavailable(self, mock_internal, mock_config):
    mock_config.MERMAID_SERVER_URL = "http://localhost:8082"
    mock_config.MERMAID_USE_MERMAID_INC = False
    mock_internal.side_effect = requests.exceptions.ConnectionError("refused")
    with pytest.raises(MermaidServiceUnavailableError):
        MermaidService.draw_mermaid("graph TD; A-->B;")
```

Also add:
- `test_draw_mermaid_5xx_raises_service_unavailable` (parametrize 500/502/503 and 429) → `MermaidServiceUnavailableError`.
- `test_draw_mermaid_4xx_raises_generation_error` (e.g. 400) → `MermaidDiagramGenerationError`, not `MermaidServiceUnavailableError`.
- `test_draw_mermaid_bad_content_type_raises_invalid_response` → `MermaidInvalidResponseError`.
- `test_draw_mermaid_unexpected_exception_propagates` — `mock_internal.side_effect = KeyError("x")` → `pytest.raises(KeyError)` (not converted to unavailable).
- `test_external_request_passes_timeout` — `requests.get` called with `timeout=config.MERMAID_SERVER_TIMEOUT`.

Update `test_draw_mermaid_non_200_status` and its external-request twin to use a 4xx status and assert `pytest.raises(MermaidDiagramGenerationError)`.

- [ ] **Step 2: Run tests, confirm they fail** (new classes/`is_configured`/classification don't exist yet).

Run: `poetry run pytest tests/codemie/service/file_service/test_mermaid_service.py -v`

- [ ] **Step 3: Implement in `mermaid_service.py`**

```python
class MermaidNotConfiguredError(RuntimeError):
    """No Mermaid renderer is configured (no server URL, mermaid.ink not opted in)."""


class MermaidServiceUnavailableError(RuntimeError):
    """A Mermaid renderer is configured but could not be reached."""


class MermaidInvalidResponseError(MermaidDiagramGenerationError):
    """The renderer answered 200 with an unexpected content type."""
```

Add `is_configured()` as a `@classmethod` per the Interfaces block. In `draw_mermaid`: raise `MermaidNotConfiguredError` immediately when `not cls.is_configured()`, before any request; do not check `MERMAID_ENABLED` (D6). Keep the `_make_internal_request`/`_make_external_request` dispatch in a `try` that catches only `requests.exceptions.RequestException` and re-raises as `MermaidServiceUnavailableError`; remove the broad `except Exception` so other exceptions (including the typed ones from `_process_response`) propagate. In `_process_response`: status `>= 500` or `== 429` → `MermaidServiceUnavailableError`; other non-200 → `MermaidDiagramGenerationError`; bad content type → `MermaidInvalidResponseError` instead of `ValueError`. In `_make_external_request`, pass `timeout=config.MERMAID_SERVER_TIMEOUT`.

- [ ] **Step 4: Run tests, confirm they pass.**

---

### Task 2: Publish `features:mermaid` in `customer_config.py`

**Files:**
- Modify: `src/codemie/configs/customer_config.py:30-49` (`CONFIG_IDS`) — add `"mermaid": "features:mermaid"`.
- Modify: `src/codemie/configs/customer_config.py` end of `_get_runtime_config()` (before `return runtime_config`, ~line 301) — append the new `Component`.
- Test: `tests/codemie/configs/test_customer_config.py`

**Interfaces:**
- Consumes: `MermaidService.is_configured()` from Task 1 (lazy import, same style as `ElasticSearchClient`: `from codemie.service.file_service.mermaid_service import MermaidService  # noqa: PLC0415`).
- Produces: `Component(id="features:mermaid", ...)` resolvable through the existing `get_runtime_components()`/`resolve_components()` path — Task 3/5 need nothing from this task, it's UI-publishing only.

**Test-first: yes — the `features:mermaid` runtime component does not exist yet; a test requesting it today gets `KeyError`/no matching component, not a boolean.**

- [ ] **Step 1: Write failing tests** in `test_customer_config.py`, following the existing `knowledgeBases`-component test shape (set `MERMAID_ENABLED` explicitly in every test; never rely on its default):

```python
def test_mermaid_available_outside_standalone_regardless_of_flag_or_config(): ...
    # STANDALONE=False, MERMAID_ENABLED=False, MERMAID_SERVER_URL="" -> enabled True (D4)
def test_mermaid_unavailable_in_standalone_without_renderer(): ...
    # STANDALONE=True, MERMAID_ENABLED=True, MERMAID_SERVER_URL="", INC=False -> enabled False
def test_mermaid_unavailable_in_standalone_when_flag_off(): ...
    # STANDALONE=True, MERMAID_ENABLED=False, MERMAID_SERVER_URL set -> enabled False
def test_mermaid_available_in_standalone_when_flag_on_and_configured(): ...
    # STANDALONE=True, MERMAID_ENABLED=True, MERMAID_SERVER_URL set -> enabled True
```

- [ ] **Step 2: Run tests, confirm they fail** (`CONFIG_IDS["mermaid"]` missing / component absent).

Run: `poetry run pytest tests/codemie/configs/test_customer_config.py -v`

- [ ] **Step 3: Implement** the `CONFIG_IDS` entry and append:

```python
runtime_config.append(
    Component(
        id=CONFIG_IDS["mermaid"],
        settings=ComponentSetting(
            enabled=True if not config.STANDALONE else (config.MERMAID_ENABLED and MermaidService.is_configured())
        ),
    )
)
```

- [ ] **Step 4: Run tests, confirm they pass.**

---

### Task 3: Standalone startup warning

**Files:**
- Modify: `src/codemie/rest_api/main.py:516-520` (`_run_standalone_startup`)
- Test: `tests/codemie/rest_api/test_standalone_startup.py`

**Interfaces:**
- Consumes: `MermaidService.is_configured()` from Task 1 (lazy import inside the function, matching `_check_sharepoint_pkce_redis`'s style).

**Test-first: yes — `_run_standalone_startup()` with `STANDALONE=True` and no renderer configured currently logs only `"Standalone mode enabled"`; a test asserting a `logger.warning` call naming the degraded features fails today.**

- [ ] **Step 1: Write failing tests**

```python
def test_warns_when_mermaid_not_configured():
    with patch.object(main.config, "STANDALONE", True), \
         patch.object(main.config, "MERMAID_SERVER_URL", ""), \
         patch.object(main.config, "MERMAID_USE_MERMAID_INC", False), \
         patch.object(main, "logger") as log:
        main._run_standalone_startup()
    log.warning.assert_called_once()
    msg = log.warning.call_args[0][0]
    for feature in ("chat diagrams", "workflow graph preview", "stored workflow diagram image", "conversation export diagrams"):
        assert feature in msg

def test_no_warning_when_mermaid_configured():
    with patch.object(main.config, "STANDALONE", True), \
         patch.object(main.config, "MERMAID_SERVER_URL", "http://mermaid:8082"), \
         patch.object(main, "logger") as log:
        main._run_standalone_startup()
    log.warning.assert_not_called()
```

- [ ] **Step 2: Run tests, confirm they fail.**

Run: `poetry run pytest tests/codemie/rest_api/test_standalone_startup.py -v`

- [ ] **Step 3: Implement** — after the existing `logger.info("Standalone mode enabled")` line, lazily import `MermaidService` and, when `not MermaidService.is_configured()`, log one `logger.warning(...)` whose text names the missing config (`MERMAID_SERVER_URL` / `MERMAID_USE_MERMAID_INC`) and lists all four degraded features verbatim as in Step 1's assertions. Never raise — match the existing `_check_sharepoint_pkce_redis` non-fatal shape.

- [ ] **Step 4: Run tests (including the two pre-existing `test_gate_*` tests), confirm all pass.**

---

### Task 4: Distinct error mapping for `POST /files/diagram/mermaid`

**Files:**
- Modify: `src/codemie/rest_api/routers/files.py:413-435` (`generate_diagram_or_raise`)
- Test: `tests/codemie/rest_api/routers/test_files.py`

**Interfaces:**
- Consumes: `MermaidNotConfiguredError`, `MermaidServiceUnavailableError`, `MermaidDiagramGenerationError` from Task 1.

**Test-first: yes — today every exception from `MermaidService.draw_mermaid` becomes the same 400 "Mermaid diagram configuration error"; a test asserting a 503 "not configured" or 503 "unavailable" response fails against current code.**

- [ ] **Step 1: Write failing tests**, alongside the existing `test_create_mermaid_diagram_syntax_error`/`test_create_mermaid_diagram_service_unavailable`:

```python
def test_create_mermaid_diagram_not_configured(mocker, auth_headers):
    mocker.patch(
        "codemie.service.file_service.mermaid_service.MermaidService.draw_mermaid",
        side_effect=MermaidNotConfiguredError("Mermaid rendering is not configured"),
    )
    response = client.post("/v1/files/diagram/mermaid", json={"code": "graph TD; A-->B;"}, headers=auth_headers)
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    body = response.json()["error"]
    assert "not configured" in body["message"].lower()
    assert "syntax" not in body["help"].lower()

def test_create_mermaid_diagram_renderer_unavailable(mocker, auth_headers):
    mocker.patch(
        "codemie.service.file_service.mermaid_service.MermaidService.draw_mermaid",
        side_effect=MermaidServiceUnavailableError("connection refused"),
    )
    response = client.post("/v1/files/diagram/mermaid", json={"code": "graph TD; A-->B;"}, headers=auth_headers)
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    body = response.json()["error"]
    assert "unavailable" in body["message"].lower()
    assert "syntax" not in body["help"].lower()
```

- [ ] **Step 2: Run tests, confirm they fail** (both currently 400 "Mermaid diagram configuration error").

Run: `poetry run pytest tests/codemie/rest_api/routers/test_files.py -v`

- [ ] **Step 3: Implement** — in `generate_diagram_or_raise`, add `except MermaidNotConfiguredError` and `except MermaidServiceUnavailableError` clauses before the existing catch-all `except Exception`, each raising `ExtendedHTTPException(code=503, message=..., details=str(e), help=...)` with help text that does **not** mention syntax. Leave the existing catch-all (400, "Mermaid diagram configuration error", "Please check your Mermaid syntax and try again.") as the handler for `MermaidDiagramGenerationError` (incl. `MermaidInvalidResponseError`)/anything else — this is what satisfies AC7, and it's already exercised by `test_create_mermaid_diagram_syntax_error`. Leave the dead `if diagram is None` 503 branch as-is (still exercised by `test_create_mermaid_diagram_service_unavailable`'s explicit mock).

- [ ] **Step 4: Run the full `test_files.py` Mermaid section, confirm all pass (new and pre-existing).**

---

### Task 5: Distinct error mapping for the workflow diagram preview, and AC9 safety for workflow save

**Files:**
- Modify: `src/codemie/workflows/workflow.py:296-323` (`validate_workflow_and_draw`)
- Modify: `src/codemie/rest_api/routers/workflow.py:789-816` (`create_workflow_diagram`) and `:861-863` (`update_workflow_schema`)
- Test: `tests/codemie/rest_api/routers/test_workflow.py`

**Interfaces:**
- Consumes: `MermaidNotConfiguredError`, `MermaidServiceUnavailableError`, `MermaidDiagramGenerationError` (incl. `MermaidInvalidResponseError`) from Task 1; `WorkflowExecutor.validate_workflow_and_draw(...) -> bytes | None` keeps its existing signature and still returns `None` on a graph-build failure, but now lets Mermaid-specific exceptions propagate instead of swallowing them too.

**Test-first: yes — `/workflows/diagram` today maps every Mermaid failure to the same 503 "Mermaid is not available." (or 400 generic WORKFLOW_CONFIGURATION_ERROR with empty help), and `update_workflow_schema` has no test proving it survives a raised Mermaid exception; these assertions fail against current code.**

- [ ] **Step 1: Write failing tests**

```python
@patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw")
async def test_create_workflow_diagram_not_configured(mock_draw, create_workflow_request, request_header):
    mock_draw.side_effect = MermaidNotConfiguredError("not configured")
    ...
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "not configured" in response.json()["error"]["message"].lower()

@patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw")
async def test_create_workflow_diagram_renderer_unavailable(mock_draw, create_workflow_request, request_header):
    mock_draw.side_effect = MermaidServiceUnavailableError("unreachable")
    ...
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "unavailable" in response.json()["error"]["message"].lower()

@pytest.mark.parametrize("exc", [MermaidDiagramGenerationError("400"), MermaidInvalidResponseError("text/html")])
@patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw")
async def test_create_workflow_diagram_invalid_diagram(mock_draw, exc, create_workflow_request, request_header):
    mock_draw.side_effect = exc
    ...
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()["error"]
    assert "invalid diagram" in body["message"].lower()
    assert "syntax" in body["help"].lower()

# Direct unit test for the AC9 safety net, no HTTP layer involved:
@patch("codemie.rest_api.routers.workflow.workflow_service.save_workflow_schema")
@patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw")
def test_update_workflow_schema_swallows_not_configured(mock_draw, mock_save, ...):
    mock_draw.side_effect = MermaidNotConfiguredError("not configured")
    update_workflow_schema(workflow_config, user)  # must not raise
    mock_save.assert_called_once_with(workflow_config, None)
```

Confirm `test_create_workflow_diagram_no_diagram` and `test_create_workflow_diagram_invalid_config` (pre-existing, unaffected by this task) still describe the `None`/generic-`Exception` paths correctly.

- [ ] **Step 2: Run tests, confirm the new ones fail.**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_workflow.py -v`

- [ ] **Step 3: Implement**
  - In `validate_workflow_and_draw`: narrow the existing `try/except Exception: return None` to wrap only `workflow.get_graph()` / edge filtering / `graph.draw_mermaid(...)` (unrelated LangGraph failures keep returning `None`). Call `MermaidService.draw_mermaid(...)` *outside* that `try`, so its exceptions propagate to the caller.
  - In `create_workflow_diagram`: add `except MermaidNotConfiguredError` / `except MermaidServiceUnavailableError` clauses before the existing catch-all, mirroring Task 4's messages/help text (503, no syntax hint); and `except MermaidDiagramGenerationError` (covers `MermaidInvalidResponseError`) returning 400 with an "invalid diagram" message and help "check your Mermaid syntax", mirroring Task 4's AC7 text. All three before the catch-all so they never fall into WORKFLOW_CONFIGURATION_ERROR.
  - In `update_workflow_schema`: wrap the `validate_workflow_and_draw(...)` call in `try/except Exception as e: logger.error(...); workflow_schema = None`, then call `workflow_service.save_workflow_schema(workflow_config, workflow_schema)` unconditionally — this is the AC9 safety net now that `validate_workflow_and_draw` no longer swallows Mermaid-specific errors itself.

- [ ] **Step 4: Run the full Mermaid/diagram sections of `test_workflow.py`, confirm all pass (new and pre-existing).**
