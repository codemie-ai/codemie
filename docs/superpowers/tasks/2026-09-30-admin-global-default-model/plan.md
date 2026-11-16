# Admin-selected global default model — implementation plan

Spec: `spec.md` · Research: `technical-analysis.md` · Initial size: L (26/36).

Commit per task using the repository's existing convention. Backend work is in `codemie`; Tasks 6–7 are in sibling `../codemie-ui`, whose write access must be authorized before execution. Focused tests belong with each task; the SDLC validation stage owns full quality gates and browser checks.

## Task 1 — Persist one admin global choice

**Files:** New SQLModel and repository beside `src/codemie/rest_api/models/llm_model_status.py` and `src/codemie/repository/llm_model_status_repository.py`; new Alembic revision under `src/external/alembic/versions/`.

**Test-first: yes —** a repository test fails until set/replace/read/clear retain one row with editor identity and update time, including concurrent first writes.

Create a single-key global-choice record containing model `base_name`, editor ID/name, and timestamps. Add atomic replace/clear operations and a migration chained to the current Alembic head. Keep read failures visible to the service and persist no derived effective-default flags.

## Task 2 — Resolve and validate the effective global model

**Files:** `src/codemie/service/llm_service/llm_service.py:429-529`, new choice repository, `tests/codemie/service/test_llm_service.py`.

**Test-first: yes —** service cases fail for admin override, clear, configured router-first fallback, unavailable/disabled/deprecated choice, restored choice, database outage, and unchanged `code` precedence.

Add one resolver over a supplied catalog: saved selectable choice → selectable configured global → existing safe selectable fallback. Match auto-router and concrete-model selectability, reject unknown names explicitly at set time, and preserve the last successfully read choice during transient DB failures. Route `default_llm_model`, category lookup, and category map through this resolver without changing `code` declarations or mutating catalog source objects.

## Task 3 — Project defaults into each accessible catalog

**Files:** `src/codemie/service/llm_service/llm_service.py:183-231,606-735`, `src/codemie/configs/llm_config.py:217-250`, `tests/codemie/service/test_llm_service.py`.

**Test-first: yes —** catalog cases fail until exactly one accessible item carries `global`, old global flags disappear after set/clear/reload, auto-router options carry the selected flag, `code` labels survive, and a restricted user gets an accessible fallback.

Build fresh response projections for plain models and LiteLLM auto-router options from the same resolved choice. Filter by the user's permitted and selectable entries before assigning global metadata; avoid leaking global router options into restricted catalogs. Recompute `default` alongside `default_for_categories` on each read so cached YAML/proxy objects stay untouched. Preserve unrelated category labels and existing no-model behavior.

## Task 4 — Add authorized admin read, set, and clear API

**Files:** `src/codemie/rest_api/routers/admin.py:420-545`, a narrow request/response model beside `src/codemie/rest_api/models/llm_model_status.py`, `tests/codemie/rest_api/routers/test_admin_router.py`.

**Test-first: yes —** route cases fail until admin read/set/replace/clear work, non-admin writes fail, invalid selections return an actionable error without changing the saved row, and reads distinguish saved from effective choice.

Add `/v1/admin/llm/default` GET, PUT, and DELETE using existing admin authentication. Return saved base name, editor/time, effective base name, source, and temporary-fallback state. Delegate validation and resolution to the service and persistence to the repository. Keep model-status endpoints independent.

## Task 5 — Align public catalog and default endpoints

**Files:** `src/codemie/rest_api/routers/llm_models.py:35-135`, `tests/codemie/rest_api/routers/test_llm_models.py`.

**Test-first: yes —** endpoint cases fail until list, all-category defaults, and single-category default agree for the same user; a restricted user never receives an inaccessible admin choice; auto-router defaults work; and no-model behavior is preserved.

Pass authenticated user scope and `include_all` visibility into the service projection/resolver for all three endpoints. Return the same effective global choice in catalog flags and default responses. Keep category-specific `code` behavior configured and prior fallback semantics for other categories.

## Task 6 — Add UI default state and mutations

**Files:** `../codemie-ui/src/store/adminModels.ts:18-70`, `../codemie-ui/src/types/entity/adminModel.ts:17-34`, `../codemie-ui/src/store/__tests__/adminModels.test.ts`.

**Test-first: yes —** store cases fail until it reads saved/effective metadata, sets and clears via admin API, refreshes both default state and model list after success, and leaves state unchanged on rejected writes.

Type the admin default response and expose fetch/set/clear actions through the existing Valtio store and shared API client. Preserve backend validation details for the page to display. Treat auto-router entries as candidates when selectable.

## Task 7 — Add global-default controls to Model management

**Files:** `../codemie-ui/src/pages/settings/administration/ModelsManagementPage.tsx:68-175`, `../codemie-ui/src/pages/settings/administration/components/ModelActions.tsx:24-90`, their focused component tests.

**Test-first: yes —** page/action cases fail until saved and effective values are distinguishable during fallback, set and clear appear only where valid, actions refresh labels, and validation errors are shown without a success state.

Show the saved choice and current effective default near the table. Add “Set as global default” on selectable rows and “Clear global default” for a saved choice, including when temporarily unavailable. Keep status actions and existing `code` labels intact. Help → Available models should update through the corrected catalog API without a separate editing control.

## Negative constraints checked

- Tasks 2, 3, 5, and 7 preserve configuration-owned `code`; no task adds category editing or CLI behavior.
- Tasks 1–5 implement a single global choice, with no per-user or per-project persisted override.
- Tasks 2 and 4 reuse existing availability and status rules rather than replacing status management.
- No task duplicates full quality gates, code review, commits, or browser verification stages.
