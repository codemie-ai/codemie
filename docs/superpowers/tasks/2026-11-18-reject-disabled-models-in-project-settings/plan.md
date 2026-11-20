# Reject Disabled Models in Project Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A project never gets a Disabled model as its default or as a newly added list entry (EPMCDME-17444).

**Architecture:** All changes live in `ProjectService` (`src/codemie/service/project/project_service.py`). They reuse `llm_service.assert_model_selectable` (D10) unchanged, so the shared "unchanged selection" exemption and `ModelNotSelectableError` text used by conversation, assistant and workflow flows are untouched. Project-specific wording goes in `ProjectService.ERRORS`.

**Tech Stack:** Python, FastAPI, SQLModel, pytest (MagicMock / `patch.object`, see `tests/codemie/service/project/test_project_service.py`).

**Spec:** requirements inline (Jira EPMCDME-17444); research in `technical-analysis.md` beside this file.

Commit per task using the repository's existing convention.

## Acceptance criteria

- AC1: a new project's default model is an enabled model; project is still stored with `allowed_models = None`.
- AC2: setting the default to a Disabled model via project settings is rejected (400) with a message naming the model and asking for an available one.
- AC3: newly adding a Disabled model to a project's list is rejected (400) with a clear message naming it.
- AC4: a stored list already holding a now-Disabled model: saves that do not add it succeed and the name stays stored.
- AC5: a stored default that is now Disabled: saving model settings without a new default is rejected asking for an available default.
- AC6: "all models" projects include a re-enabled model with no write; explicit-list projects do not gain it (verify only).
- AC7: behaviour for non-disabled models (including deprecated) is unchanged.

## Global Constraints

- Do not change `LLMService.assert_model_selectable` or the `ModelNotSelectableError` text (`src/codemie/core/exceptions.py:292`).
- Enabled state is read live; no catalog reload, no migration, no UI or router change.
- Provisioning (`service/provisioning_service.py:153`) shares `create_shared_project_in_session`, so it gets the new default rule with no separate change.
- Errors use `ExtendedHTTPException(code=400, ...)` with messages in `ProjectService.ERRORS` (`project_service.py:151-161`).
- negative-constraints pass: no task alters shared selectability text/exemption (Tasks 1-3 only call it); no task rewrites stored lists or converts old projects (Task 2 only compares, never writes); no UI/run-time task exists; Task 4 asserts no write for "all models".

## Review Focus

- Deprecated (not disabled) stored default or new default must keep today's behaviour: pass `allow_deprecated=True` in new checks.
- List entry given as base name while stored as deployment name (or vice versa) must not count as "newly added".
- Router names in the list or as default (resolved by `assert_model_selectable` via router options).
- Unknown model names must still fall through to the existing validators, not be rejected as disabled.
- Every web-available model disabled at project creation: must not surface as an unhandled `StopIteration`.

---

### Task 1: New-project default skips Disabled models (AC1, AC7)

**Files:**
- Modify: `src/codemie/service/project/project_service.py:151-161` (new ERRORS), `:264-269`
- Test: `tests/codemie/service/project/test_project_service.py` (near `:443-481`, reuse `_prepare` / `mock_llm_service`)

**Interfaces:**
- Produces: `ProjectService._is_disabled_model(name: str) -> bool` (classmethod; wraps `llm_service.assert_model_selectable(name, allow_deprecated=True)` and returns True on `ModelNotSelectableError`). Used by Tasks 2 and 3.
- Produces: `ERRORS.NO_AVAILABLE_DEFAULT_MODEL` = "No enabled web-available model exists to use as the project default".

- [ ] **Step 1: Failing tests** `test_create_shared_project_default_skips_disabled_model` (first web model disabled via `assert_model_selectable` side effect; assert `default_model` is the next one and `allowed_models is None`) and `test_create_shared_project_no_enabled_default_raises_400` (all disabled; assert `ExtendedHTTPException` code 400, not `StopIteration`).
- [ ] **Step 2: Run** `pytest tests/codemie/service/project/test_project_service.py -k "create_shared_project" -v`; expect the two new tests FAIL.
- [ ] **Step 3: Implement.** Add `_is_disabled_model`; at `:269` pick `next((m.deployment_name for m in models if m.forbidden_for_web is not True and not cls._is_disabled_model(m.deployment_name)), None)` and raise the 400 when None. Keep `include_all=True` at `:264` (existing assertion at `:457`) and leave `:265-268` as is.
- [ ] **Step 4: Run** the same command plus existing create tests; expect PASS.

Test-first: yes — create with first web model Disabled currently stores that model as default.

### Task 2: Reject newly added Disabled list entries; keep stored ones (AC3, AC4)

**Files:**
- Modify: `project_service.py:151-161` (ERRORS), `:933-969` (`update_allowed_models`)
- Test: `test_project_service.py` (near `:1011-1074`)

**Interfaces:**
- Consumes: `ProjectService._is_disabled_model` (Task 1).
- Produces: `ERRORS.ALLOWED_MODEL_DISABLED` = "Model '{model}' is disabled and cannot be added to allowed_models; choose an available model".
- Produces: `ProjectService._assert_new_models_not_disabled(validated_models: list[str] | None, stored_models: list[str] | None) -> None`, raising `ExtendedHTTPException(400)`.

- [ ] **Step 1: Failing tests** `test_update_allowed_models_rejects_newly_added_disabled_model` (stored `[A]`, request `[A, B]`, B disabled; assert 400 and message contains `B`), `test_update_allowed_models_keeps_stored_disabled_name` (stored `[A, B]`, B disabled, request `[A, B, C]` with enabled default; assert persisted list contains B), `test_update_allowed_models_alias_form_not_treated_as_new` (stored deployment name, request base name of same model, disabled; succeeds).
- [ ] **Step 2: Run** `pytest ... -k update_allowed_models -v`; first test FAILS.
- [ ] **Step 3: Implement** the helper: for each name in `validated_models` not in the stored set (compare after normalising via `model_aliases`, `model_availability_service.py:42-52`), raise if `_is_disabled_model(name)`. Call it in `update_allowed_models` right after the authorization check (`:946`), before default validation. `None` request list is a no-op.
- [ ] **Step 4: Run** same command; PASS.

Test-first: yes — a newly added Disabled name is currently persisted.

### Task 3: Disabled default rejected, including stored (AC2, AC5, AC7)

**Files:**
- Modify: `project_service.py:151-161` (ERRORS), `:953-959`
- Test: `test_project_service.py` (near `:1011-1074`)

**Interfaces:**
- Produces: `ERRORS.DEFAULT_MODEL_DISABLED` = "default_model '{model}' is disabled; choose an available default model".

- [ ] **Step 1: Failing tests** `test_update_allowed_models_rejects_new_disabled_default` (400, message contains model name and "available"), `test_update_allowed_models_rejects_stored_disabled_default_without_new_default` (stored default disabled, `default_model=None`; 400), `test_update_allowed_models_stored_deprecated_default_still_saved` (AC7), `test_update_allowed_models_enabled_default_unchanged_behaviour`.
- [ ] **Step 2: Run** `-k update_allowed_models`; stored-disabled test FAILS.
- [ ] **Step 3: Implement.** Replace the `assert_model_selectable(..., previous_model_name=project.default_model)` call at `:955` with `assert_model_selectable(effective_default_model, allow_deprecated=effective_default_model == project.default_model)` (no `previous_model_name`, so a disabled stored default is no longer exempt). On `ModelNotSelectableError`, raise `ExtendedHTTPException(code=400, message=ERRORS.DEFAULT_MODEL_DISABLED.format(model=...), details=str(exc))` when the model is disabled, otherwise keep the existing "Model not selectable" mapping (use `_is_disabled_model`).
- [ ] **Step 4: Run** same command plus `tests/codemie/rest_api/routers/test_projects_router.py`; PASS.

Test-first: yes — a stored Disabled default is currently kept on save.

### Task 4: Verify "all models" vs explicit list on re-enable (AC6)

**Files:**
- Test: `tests/codemie/service/test_project_model_availability.py` (existing module; follow its fixtures)

- [ ] **Step 1: Add** `test_all_models_project_includes_reenabled_model_without_write` (project `allowed_models=None`; model status flips Disabled to Enabled; `get_allowed_chat_models` includes it; no repository write called) and `test_explicit_list_project_does_not_gain_reenabled_model`.
- [ ] **Step 2: Run** `pytest tests/codemie/service/test_project_model_availability.py -v`; expect PASS immediately (characterization of current behaviour). If it fails, stop and report; do not change production code.

Test-first: no — AC6 is already current behaviour; the tests pin it.
