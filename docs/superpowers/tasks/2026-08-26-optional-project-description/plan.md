# Plan — EPMCDME-14336: Optional Project Description

Spec: `./spec.md`
Tech analysis: `./technical-analysis.md`

Ordering rationale: bottom-up. Change repository first (enables clear), then service (validation + resolver), then router (models + response fix). Tests are written before implementation for each task.

---

## Task 1 — Repository: allow clearing description

**Files:** `src/codemie/repository/application_repository.py`, `tests/codemie/repository/test_application_repository.py` (or nearest existing repo test file).

**Change:** add `clear_description: bool = False` parameter to `ApplicationRepository.update_project`. When true, unconditionally set `application.description = None`; otherwise keep the existing `if description is not None:` guard.

**Test-first: yes** — failing test `test_update_project_clear_description_sets_null` that calls `update_project(..., description=None, clear_description=True)` on a fixture app that already has a description, and asserts the persisted `description` is `None`. Add a second test proving `clear_description=False, description=None` leaves description unchanged.

---

## Task 2 — Service: create accepts None; whitespace normalized

**Files:** `src/codemie/service/project/project_service.py`, `tests/codemie/service/project/test_project_service.py`.

**Change:**
- `create_shared_project` signature: `description: str | None = None`.
- Rewrite `_validate_project_description` to: strip, return `None` when empty, keep 500-char rejection, no longer raise for empty.
- Pass the normalized value to the repository.

**Test-first: yes** — failing tests:
- `test_create_project_without_description_succeeds` (description omitted → stored `None`).
- `test_create_project_empty_description_stored_as_null` (`""` → `None`).
- `test_create_project_whitespace_description_stored_as_null` (`"   "` → `None`).
- Replace `test_empty_description_returns_400` (delete it — behavior inverted).
- Keep and verify `test_description_too_long_returns_400`.

---

## Task 3 — Service: update supports clear_description

**Files:** `src/codemie/service/project/project_service.py`, `tests/codemie/service/project/test_project_service_delete_update.py`.

**Change:** `ProjectService.update_project` gains `clear_description: bool = False`. Add `_resolve_updated_description` mirroring `_resolve_updated_display_name`: if `clear_description` → return `(None, clear=True)`; else if description provided → normalize whitespace to `None`; else no-op. Forward `clear_description` to repository.

**Test-first: yes** — failing tests:
- `test_update_project_clear_description_removes_value` (service called with `clear_description=True` → repository called with `description=None, clear_description=True`).
- `test_update_project_omitted_description_unchanged` (baseline).
- `test_update_project_whitespace_description_treated_as_clear`.

---

## Task 4 — Router: models + validators + response fix

**Files:** `src/codemie/rest_api/routers/projects.py`, `tests/codemie/rest_api/routers/test_projects_router.py`.

**Change:**
- `ProjectCreateRequest.description: Optional[str] = None`.
- `ProjectCreateResponse.description: Optional[str] = None`.
- `ProjectUpdateRequest`: add `clear_description: bool = False`. Extend `validate_non_empty` to count it as a provided field. Add a `model_validator(mode="after")` guard: `description` set AND `clear_description=True` → raise `ValueError` (yields 422).
- Route handler: pass `clear_description` through to `ProjectService.update_project`.
- Remove `project.description or ""` masking near line 1016 — return `project.description` (may be `None`).
- Simplify `project.description or payload.description` fallback near line 675 — both may be `None`; use `project.description` (post-persist truth).

**Test-first: yes** — failing tests:
- `test_create_project_without_description_returns_201_null` (router-level).
- `test_update_project_clear_description_returns_null` (PATCH with `clear_description=true`).
- `test_update_project_description_and_clear_mutually_exclusive` (PATCH with both → 422).
- `test_update_project_clear_description_alone_passes_non_empty_validator`.
- `test_project_response_null_description_not_coerced_to_empty` (assert `response.json()["description"] is None` when repo returns `None`).
- Update the two existing router tests that assert `""` in the response (`test_projects_router.py` lines ~1484, ~1524) to assert `None`.

---

## Task 5 — Documentation update

**Files:** any `docs/**` project-description guidance or `.ai-run/guides/**` references, if present. Otherwise skip.

**Change:** update prose that says "description is required" to "description is optional" and add a pointer to the FE contract section of `spec.md`.

**Test-first: no** — documentation only.

---

## Out of scope for this plan

- FE work (separate repo). The contract in `spec.md` is the handoff.
- Refactoring `ERRORS.DESC_REQUIRED` beyond removing its create-path usage. Keep the constant if still referenced elsewhere.
- Any changes to name / display_name / cost_center validation.

---

## Validation

After all tasks are green:
- Run `qa-gates` (lint + build + unit tests, project-configured).
- Regenerate no OpenAPI file — FastAPI serves it dynamically.
- Manually spot-check `/openapi.json` in a local run only if time permits (nice-to-have, not required).
