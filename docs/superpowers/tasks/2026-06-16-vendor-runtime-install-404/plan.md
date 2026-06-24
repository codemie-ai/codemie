# Vendor Runtime Import — Correct HTTP Status on All-Failure Batch

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Return the correct HTTP status code (404/409/500) from `import_vendor_entities` when every item in the summary has an error and none have `aiRunId`.

**Architecture:** Single guard block added after `summary = service.import_entities(...)` in the router. No service-layer changes. Tests cover all-404, all-409, all-500, mixed success+error, and empty-summary edge cases.

**Tech Stack:** Python, FastAPI, pytest

---

## File Map

| File | Change |
|---|---|
| `src/codemie/rest_api/routers/vendor.py` | Replace `return {"summary": summary}` at line 405 with all-errors guard + return |
| `tests/codemie/rest_api/routers/test_vendor_router.py` | Append 5 new tests for `import_vendor_entities` |

---

### Task 1: Write failing tests and implement the all-errors guard

**Test-first: yes — `test_import_vendor_entities_all_404_raises_404` fails because the router currently returns 200 for all-error batches**

**Files:**
- Modify: `tests/codemie/rest_api/routers/test_vendor_router.py`
- Modify: `src/codemie/rest_api/routers/vendor.py:402-405`

---

- [ ] **Step 1a: Add `import_vendor_entities` to the existing import on line 20 of `test_vendor_router.py`**

Replace:
```python
from codemie.rest_api.routers.vendor import unimport_vendor_entity
```
With:
```python
from codemie.rest_api.routers.vendor import import_vendor_entities, unimport_vendor_entity
```

- [ ] **Step 1b: Append the 5 new tests and helper at the end of `test_vendor_router.py`**

```python
def _make_user():
    return MagicMock(spec=User)


@patch("codemie.rest_api.routers.vendor.get_service_or_404")
def test_import_vendor_entities_all_404_raises_404(mock_get_service):
    """All items failed with 404 → router must raise HTTP 404."""
    mock_service = MagicMock()
    mock_service.import_entities.return_value = [
        {
            "runtimeId": "r1",
            "endpointName": "nonexistent-ep",
            "error": {"statusCode": "404", "message": "Runtime endpoint not found"},
        }
    ]
    mock_get_service.return_value = mock_service

    with pytest.raises(ExtendedHTTPException) as exc_info:
        import_vendor_entities(
            origin=Vendor.AWS,
            entity=Entities.AWS_AGENTCORE_RUNTIMES,
            body=[{"setting_id": "s1", "id": "r1", "agentcoreRuntimeEndpointName": "nonexistent-ep", "configuration_json": "{}"}],
            user=_make_user(),
        )

    assert exc_info.value.code == 404


@patch("codemie.rest_api.routers.vendor.get_service_or_404")
def test_import_vendor_entities_all_409_raises_409(mock_get_service):
    """All items failed with 409 → router must raise HTTP 409."""
    mock_service = MagicMock()
    mock_service.import_entities.return_value = [
        {
            "runtimeId": "r1",
            "endpointName": "ep1",
            "error": {"statusCode": "409", "message": "Endpoint not in READY status"},
        }
    ]
    mock_get_service.return_value = mock_service

    with pytest.raises(ExtendedHTTPException) as exc_info:
        import_vendor_entities(
            origin=Vendor.AWS,
            entity=Entities.AWS_AGENTCORE_RUNTIMES,
            body=[{"setting_id": "s1", "id": "r1", "agentcoreRuntimeEndpointName": "ep1", "configuration_json": "{}"}],
            user=_make_user(),
        )

    assert exc_info.value.code == 409


@patch("codemie.rest_api.routers.vendor.get_service_or_404")
def test_import_vendor_entities_all_500_raises_500(mock_get_service):
    """All items failed with 500 → router must raise HTTP 500."""
    mock_service = MagicMock()
    mock_service.import_entities.return_value = [
        {
            "runtimeId": "r1",
            "endpointName": "ep1",
            "error": {"statusCode": "500", "message": "Unexpected AWS error"},
        }
    ]
    mock_get_service.return_value = mock_service

    with pytest.raises(ExtendedHTTPException) as exc_info:
        import_vendor_entities(
            origin=Vendor.AWS,
            entity=Entities.AWS_AGENTCORE_RUNTIMES,
            body=[{"setting_id": "s1", "id": "r1", "agentcoreRuntimeEndpointName": "ep1", "configuration_json": "{}"}],
            user=_make_user(),
        )

    assert exc_info.value.code == 500


@patch("codemie.rest_api.routers.vendor.get_service_or_404")
def test_import_vendor_entities_partial_success_returns_200(mock_get_service):
    """One success + one error → HTTP 200 with full summary (batch partial success)."""
    mock_service = MagicMock()
    mock_service.import_entities.return_value = [
        {"runtimeId": "r1", "endpointName": "ep1", "aiRunId": "uuid-1"},
        {"runtimeId": "r2", "endpointName": "ep2", "error": {"statusCode": "404", "message": "not found"}},
    ]
    mock_get_service.return_value = mock_service

    result = import_vendor_entities(
        origin=Vendor.AWS,
        entity=Entities.AWS_AGENTCORE_RUNTIMES,
        body=[
            {"setting_id": "s1", "id": "r1", "agentcoreRuntimeEndpointName": "ep1", "configuration_json": "{}"},
            {"setting_id": "s1", "id": "r2", "agentcoreRuntimeEndpointName": "ep2", "configuration_json": "{}"},
        ],
        user=_make_user(),
    )

    assert "summary" in result
    assert len(result["summary"]) == 2


@patch("codemie.rest_api.routers.vendor.get_service_or_404")
def test_import_vendor_entities_empty_summary_raises_500(mock_get_service):
    """Empty summary (no items at all) → HTTP 500 safe fallback."""
    mock_service = MagicMock()
    mock_service.import_entities.return_value = []
    mock_get_service.return_value = mock_service

    with pytest.raises(ExtendedHTTPException) as exc_info:
        import_vendor_entities(
            origin=Vendor.AWS,
            entity=Entities.AWS_AGENTCORE_RUNTIMES,
            body=[{"setting_id": "s1", "id": "r1", "agentcoreRuntimeEndpointName": "ep1", "configuration_json": "{}"}],
            user=_make_user(),
        )

    assert exc_info.value.code == 500
```

- [ ] **Step 2: Run the first test to confirm it fails (RED)**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_vendor_router.py::test_import_vendor_entities_all_404_raises_404 -v
```

Expected: **FAILED** — function returns `{"summary": [...]}` instead of raising `ExtendedHTTPException`.

- [ ] **Step 3: Replace lines 403–405 in `src/codemie/rest_api/routers/vendor.py`**

Replace:
```python
    service = get_service_or_404(origin, entity)
    summary = service.import_entities(user=user, import_payload=result)

    return {"summary": summary}
```

With:
```python
    service = get_service_or_404(origin, entity)
    summary = service.import_entities(user=user, import_payload=result)

    successes = [r for r in summary if "aiRunId" in r]
    if not successes:
        first_error = summary[0].get("error", {}) if summary else {}
        try:
            code = int(first_error.get("statusCode", 500))
        except (TypeError, ValueError):
            code = 500
        raise ExtendedHTTPException(
            code=code,
            message=first_error.get("message", "Import failed"),
            details=f"All {len(summary)} item(s) failed to import.",
            help="Check the error details for each item.",
        )

    return {"summary": summary}
```

- [ ] **Step 4: Run all 5 new tests (GREEN)**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_vendor_router.py -k "import_vendor" -v
```

Expected: all 5 tests **PASSED**.

- [ ] **Step 5: Run the full test module to confirm no regressions**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_vendor_router.py -v
```

Expected: all tests **PASSED**.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/rest_api/routers/vendor.py \
        tests/codemie/rest_api/routers/test_vendor_router.py
git commit -m "EPMCDME-12240: Return correct HTTP status when all vendor import items fail"
```
