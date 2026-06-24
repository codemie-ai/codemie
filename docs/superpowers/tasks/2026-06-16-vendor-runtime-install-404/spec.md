# Spec: Vendor Runtime Import — Correct HTTP Status on All-Failure Batch

## Problem

`POST /vendors/{origin}/{entity}` (`import_vendor_entities`) always returns HTTP 200, even when every item in the batch fails. When `agentcoreRuntimeEndpointName` does not exist the service returns a summary item with an `"error"` dict (no `"aiRunId"`), but the router wraps it in `{"summary": [...]}` with HTTP 200. SDK consumers that deserialize each summary item as `VendorRuntimeInstallSummary` — which requires `aiRunId` — receive a `ValidationError`.

## Goal

Return the appropriate HTTP status code (404, 409, 500, …) when **all** summary items are errors. Keep HTTP 200 for partial-success batches (at least one item has `"aiRunId"`).

## Scope

One file changed: `src/codemie/rest_api/routers/vendor.py` — the `import_vendor_entities` handler.  
One file extended: `tests/codemie/rest_api/routers/test_vendor_router.py` — new tests for the all-error path.

No changes to the service layer or data models.

## Behaviour

| Batch result | HTTP status |
|---|---|
| At least one item has `"aiRunId"` | 200 (unchanged) |
| All items have `"error"`, first error `statusCode` is `"404"` | 404 |
| All items have `"error"`, first error `statusCode` is `"409"` | 409 |
| All items have `"error"`, first error `statusCode` is `"500"` | 500 |
| All items have `"error"`, `statusCode` missing or unparseable | 500 (safe fallback) |

## Implementation

In `import_vendor_entities`, after `summary = service.import_entities(...)`:

```python
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

## Acceptance Criteria

1. `POST /vendors/aws/agentcore-runtimes` with a nonexistent `agentcoreRuntimeEndpointName` returns HTTP 404.
2. All-409 batch returns HTTP 409.
3. A batch where at least one item succeeds (has `"aiRunId"`) returns HTTP 200 with the full summary.
4. Empty summary (edge case) returns HTTP 500.
5. All existing vendor router tests continue to pass.
