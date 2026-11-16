DONE

## Task 10: Admin endpoints for LLM model status management

### What was implemented

1. `src/codemie/core/models.py`
   - Added import: `from codemie.configs.llm_config import ModelStatus`
   - Added three request models right after `LLMBulkRetirementRequest`:
     - `LLMModelStatusUpdateRequest(ConfiguredModel)` — `status: ModelStatus`
     - `LLMModelStatusBulkUpdateItem(ConfiguredModel)` — `base_name: str`, `status: ModelStatus`
     - `LLMModelStatusBulkUpdateRequest(ConfiguredModel)` — `updates: list[LLMModelStatusBulkUpdateItem] = Field(..., min_length=1, max_length=100)`
   - No other symbols in the file were touched.

2. `src/codemie/rest_api/routers/admin.py`
   - New imports: `get_session` (from `codemie.clients.postgres`), `LLMModelStatusRepository`, `LLMModelStatusBulkUpdateRequest`/`LLMModelStatusUpdateRequest` (added to the existing `codemie.core.models` import block), and module-level `llm_service` (from `codemie.service.llm_service.llm_service`).
   - Added three endpoints after `retire_llm_models_bulk`, mirroring its `Depends(admin_access_only)` + logging pattern:
     - `GET /admin/llm/status` → `get_llm_model_status`: calls `llm_service.get_all_llm_model_info()` (include_all semantics — the full catalog), cross-references `LLMModelStatusRepository.get_all(session)` by `base_name`, returns `BaseResponseWithData(data=[{base_name, status, updated_by_name, updated_at}, ...])`, defaulting `status="enabled"`/`updated_by_name=None`/`updated_at=None` when no persisted row exists.
     - `PUT /admin/llm/{base_name}/status` → `update_llm_model_status`: calls `LLMModelStatusRepository.upsert(session, base_name, request.status.value, admin.id, admin.name)`, returns `BaseResponseWithData(data={"base_name": base_name, "status": request.status.value})`.
     - `POST /admin/llm/status/bulk` → `bulk_update_llm_model_status`: loops `request.updates`, calling `upsert` per item inside its own try/except (mirroring `retire_models_bulk`'s per-item isolation), returns `BaseResponseWithData(data={"total", "succeeded", "failed", "results": [{"base_name", "status", "success", "error"}, ...]})`.
   - `retire_llm_model`, `retire_llm_models_bulk`, and everything else in the file were left untouched.

3. `tests/codemie/rest_api/routers/test_admin_router.py`
   - Extended (not replaced) the existing file, following its direct-function-call test style.
   - `TestLLMModelStatusRoutesAreAdminOnly.test_llm_status_routes_use_admin_access_only` — route-introspection test (same technique as the pre-existing `TestApplicationsSearchOpenToAuditor`) confirming all three new routes carry `Depends(admin_access_only)` in their route-level `dependencies=[...]`. This is how "non-admin gets 403" is verified given that calling the endpoint function directly bypasses FastAPI's dependency injection.
   - `test_get_llm_model_status_defaults_to_enabled_when_no_persisted_row` — mocks `llm_service.get_all_llm_model_info` and `LLMModelStatusRepository.get_all` (and `get_session`), asserts `m1` picks up the persisted "deprecated"/"alice" override and `m2` defaults to "enabled"/`None`.
   - `test_update_llm_model_status_persists_via_repository_upsert` — mocks `LLMModelStatusRepository.upsert` and `get_session`, asserts the returned `data` and the exact positional args passed to `upsert` (`base_name`, `"deprecated"`, `admin.id`, `admin.name`).
   - `test_llm_model_status_update_request_rejects_invalid_status` — asserts `LLMModelStatusUpdateRequest(status="not-a-real-status")` raises `pydantic.ValidationError` (the 422-equivalent check, per the ruling in the task brief, since FastAPI-level parsing doesn't run when calling the function directly).
   - `test_bulk_update_llm_model_status_reports_partial_success` — mocks `LLMModelStatusRepository.upsert` with a `side_effect` that succeeds for `m1` and raises `ValueError` for `m2`; asserts `data["results"]` has one `success: True` and one `success: False` entry, independent of each other, and `total`/`succeeded`/`failed` are consistent.
   - Note: initial version of the bulk-partial-success test mocked `get_session`'s context manager with a bare `MagicMock()` for `__exit__`, which (being truthy) silently swallowed the `ValueError` raised inside the `with` block — surfaced as a real RED failure (`2 == 1` instead of `1 == 1`) before being fixed by setting `__exit__=MagicMock(return_value=False)`. Confirms the per-item try/except in the implementation is correctly scoped around the `with get_session()` block, not swallowed by it.

### TDD flow

- RED (before implementation): `ImportError: cannot import name 'LLMModelStatusBulkUpdateRequest' from 'codemie.core.models'` — confirmed the test suite fails for the right reason (feature missing).
- Implemented the three models + three endpoints.
- GREEN: all 15 tests in the file pass (12 pre-existing + this task's tests: 1 route-introspection class with 1 test + 4 function tests = 5 new tests, plus the pre-existing `TestApplicationsSearchOpenToAuditor` class's 2 tests were already counted in the pre-existing 12... exact count confirmed by pytest output below).

### Commit

- SHA: `03bbe50c4a66ab308cce5f997860fb934dde9435`
- Message: `EPMCDME-15080: Add admin endpoints for LLM model status management`
- `git show --stat HEAD` confirms exactly 3 files changed: `src/codemie/core/models.py` (+14), `src/codemie/rest_api/routers/admin.py` (+127), `tests/codemie/rest_api/routers/test_admin_router.py` (+115).

### Test command run (exact)

```
poetry run pytest tests/codemie/rest_api/routers/test_admin_router.py -o addopts="--import-mode=importlib" > /Users/Aliaksei_Hurynovich/Developer/_codemie/codemie/docs/superpowers/tasks/2026-09-21-status-persistence-api/.test.log 2>&1; echo "EXIT=$?"; tail -50 /Users/Aliaksei_Hurynovich/Developer/_codemie/codemie/docs/superpowers/tasks/2026-09-21-status-persistence-api/.test.log
```

EXIT=0, final result: `15 passed, 2 warnings in 13.98s`. No pre-existing test in the file broke.

Sanity import check: `PYTHONPATH=src poetry run python -c "import codemie.core.models; import codemie.rest_api.routers.admin; print('OK')"` — printed `OK`, no traceback (plain `poetry run python -c "import codemie.core.models"` alone fails with `ModuleNotFoundError: No module named 'codemie'` in this repo regardless of my changes, because `src` is not on `sys.path` outside of pytest's rootdir-based path insertion — this is pre-existing repo behavior, not a regression; adding `PYTHONPATH=src` resolves it and the import is clean).

### Files changed

- `src/codemie/core/models.py`
- `src/codemie/rest_api/routers/admin.py`
- `tests/codemie/rest_api/routers/test_admin_router.py`

### Self-review notes

- Only staged/committed the three files above. Did not touch `config/customer/customer-config.yaml`, `.codemie-repos/`, or `.ai-run/sdlc-factory/doctor.json`, all of which were already dirty before this task and are excluded per instructions.
- Did not modify `LLMRetirementPair`/`LLMRetirementRequest`/`LLMBulkRetirementRequest` in `core/models.py`, nor `retire_llm_model`/`retire_llm_models_bulk` in `admin.py`, nor any pre-existing test in `test_admin_router.py` — ownership boundaries respected.
- `llm_service` is imported at module level in `admin.py` (unlike `reload_llm_models`, which imports it lazily inside the function body to avoid an import-time dependency). Module-level import was verified safe via the sanity-check script above (no circular import surfaced) and via the full test suite passing on collection.
- The `GET /admin/llm/status` endpoint opens one `get_session()` for the `LLMModelStatusRepository.get_all` call, matching the spec's requirement to cross-reference persisted rows for `updated_by_name`/`updated_at` (fields not carried on `LLMModel`).
- `PUT` and each iteration of `POST .../bulk` each open their own `get_session()` context around the single `upsert` call, keeping per-item isolation (a DB failure on one bulk item does not affect the others, and does not hold a shared session/transaction across items).
