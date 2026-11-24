# Owner-only personal-to-project conversion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Only the owner of a personal (USER) integration can convert it via `PUT /v1/settings/project/{setting_id}`; everyone else, including Project Admins and platform admins, gets 403 and nothing is written.

**Architecture:** A router-level guard in `update_project_setting` reads the stored row's `setting_type` and `user_id` and runs before `SettingsService.update_settings` and before any project transfer. `SettingsService.update_settings` stays untouched (shared with `update_user_setting` and `upsert_project_setting`).

**Tech Stack:** FastAPI, SQLModel `Settings`, pytest with `unittest.mock`, httpx `AsyncClient`.

**Spec:** none (inline requirements, ticket EPMCDME-17686); research in `technical-analysis.md` next to this file.

Commit per task using the repository's existing convention.

## Acceptance criteria

- A non-owner (Project Admin or platform admin) PUT on another user's USER integration is rejected with 403; `update_settings` and `transfer_setting` are not called, including when the body changes `project_name`.
- The owner converting their own USER integration still returns 200 and calls `update_settings`.
- A regression test covers both non-owner variants and passes.
- Updating an already PROJECT-typed integration behaves as today for owner, Project Admin and platform admin.

## Global Constraints

- TDD: failing test first. No explanatory code comments. No new docs under `docs/`.
- Tests follow the neighbouring mock-based style in `tests/codemie/rest_api/routers/test_project_settings.py`; no real-Postgres tests.
- Validation per `.ai-run/guides/quality-gates.md`: `make ruff` plus the narrowest relevant tests.
- Out of scope: `update_user_setting` (PROJECT to USER), `delete_project_setting`, codemie-ui, SDK/CLI, enterprise package.

## Review Focus

- Non-owner combines conversion with a `project_name` change: still 403, no transfer.
- Stored `setting_type` is null (legacy): treated as USER, non-owner rejected.
- Stored `user_id` is null or empty: nobody is the owner, so rejected.
- PROJECT-typed row owned by someone else, caller is Project Admin or platform admin: still 200.
- A 403 from another check (`_check_permission`, LiteLLM admin check, budget guard) must not be mistaken for the guard: rejection tests assert the guard's own message and use a Git credential.

---

### Task 1: Owner-only guard on conversion

**Files:**
- Modify: `src/codemie/rest_api/routers/project_settings.py:147-169` (`update_project_setting`), plus one new private helper near `_validate_update_request` (:242)
- Test: `tests/codemie/rest_api/routers/test_project_settings.py` (add after `test_update_project_setting_logs_on_success`, :167-191)

**Interfaces:**
- Produces: `_ensure_personal_setting_owner(setting: Settings, user: User) -> None` raising `raise_forbidden("convert")` (403, existing helper; `details` reads "You do not have the necessary permissions to convert this entity.") when the stored type is USER or null and `not setting.user_id or setting.user_id != user.id`; no-op otherwise.

Test-first: yes — a non-owner platform admin or Project Admin PUT on another user's USER-typed setting currently returns 200 and calls `update_settings`; the test expects a 403 whose `details` contain "convert" and no `update_settings` or `transfer_setting` call.

- [ ] **Step 1: Write the failing tests** in `test_project_settings.py`, in the file's existing style (patch `project_settings.Settings`, `SettingsService.get_setting_ability`, `SettingsService.update_settings`, `project_settings.SettingsTransferService`, `LocalIdp.authenticate`; the app has no exception handler, so rejections surface via `pytest.raises(ExtendedHTTPException)` as in `test_regular_user_cannot_create_project_litellm_when_personal_feature_exists`). Set the stored row explicitly: `mock_settings.get_by_id.return_value` with `setting_type`, `user_id="owner-a"`, `project_name="test_project"`. Every request uses `credential_type` `"Git"` with the same PAT payload as `test_update_project_setting_logs_on_success` (it passes the real `validate_git_request`); never LiteLLM. Every rejection test asserts `excinfo.value.code == status.HTTP_403_FORBIDDEN` AND `"convert" in excinfo.value.details`, so a 403 from `_check_permission`, the LiteLLM admin check or the budget guard cannot satisfy it, plus `update_settings.assert_not_called()` and `SettingsTransferService.transfer_setting.assert_not_called()`. Tests, unless stated patching `Ability` with `can` returning True:
  - `test_update_project_setting_rejects_non_owner_converting_personal_setting`: parametrize over caller = platform admin (`_admin_user()`, id `admin1`) and caller = Project Admin (`User(id="pa1", ...)` with `is_admin=False`, `project_names=["test_project", "second_project"]`, `admin_project_names=["test_project", "second_project"]`), crossed with body `project_name` = `"test_project"` and `"second_project"` (the project-change variant; the caller administers both, so `_check_permission` itself would pass and only the guard can reject).
  - `test_update_project_setting_rejects_non_owner_when_stored_type_is_null_or_owner_missing`: parametrize (`setting_type=None, user_id="owner-a"`) and (`setting_type=SettingType.USER, user_id=None`); caller `_admin_user()`.
  - `test_update_project_setting_rejects_real_project_admin_non_owner_past_ability_check`: do NOT patch `Ability` or `_check_permission`. Patch `SettingsService.get_setting_ability` to return a real `ProjectSetting(stored)` where `stored` is the same row object returned by the patched `Settings.get_by_id` (`setting_type=SettingType.USER`, `user_id="owner-a"`, `project_name="test_project"`); caller is the non-admin Project Admin above with body `project_name="test_project"`. Assert the same 403 plus `"convert" in details`, which also proves the real ability check let a non-owner Project Admin through (a 401 would mean it did not). No real Postgres.
  - `test_update_project_setting_allows_owner_to_convert_personal_setting`: stored `SettingType.USER`, `user_id="admin1"`, caller `_admin_user()`; 200 and `update_settings.assert_called_once()`.
  - `test_update_project_setting_unchanged_for_project_typed_setting_of_other_user`: stored `SettingType.PROJECT`, `user_id="owner-a"`, caller platform admin; 200 and `update_settings.assert_called_once()`.

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_project_settings.py -k "converting or owner or project_typed or real_project_admin" -v`
Expected: all rejection tests FAIL (`pytest.raises` finds no exception because the request returns 200 and calls `update_settings`); owner and PROJECT-typed tests PASS.

- [ ] **Step 3: Implement the guard.** In `update_project_setting`, fetch the stored row once right after the `Ability` check (`existing_setting = Settings.get_by_id(setting_id)`), call `_ensure_personal_setting_owner(existing_setting, user)` first, then pass `existing_setting` to `is_budget_integration_hidden_from` and drop the later duplicate `Settings.get_by_id` at :166. Add the helper (new symbol):

```python
def _ensure_personal_setting_owner(setting: Settings, user: User) -> None:
    stored_type = setting.setting_type or SettingType.USER
    if stored_type == SettingType.USER and (not setting.user_id or setting.user_id != user.id):
        raise_forbidden("convert")
```

Do not use `ProjectSetting.is_owned_by` (no non-empty check). No comments, no change to `SettingsService`.

- [ ] **Step 4: Run the two router test files**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_project_settings.py tests/codemie/rest_api/routers/test_project_settings_project_change.py -v`
Expected: all PASS. Existing tests stub the stored row with `Mock`/`MagicMock`, whose `setting_type` is neither USER nor falsy, so they should pass untouched. If one goes red, set `setting_type=SettingType.PROJECT` (and `user_id` where relevant) on its stored row only; change nothing else in it.

- [ ] **Step 5: Quality gates**

Run: `make ruff`, then `poetry run pytest tests/codemie/rest_api/routers/test_contract_baseline.py tests/codemie/service/settings/test_settings_service.py -q`
Expected: clean ruff, tests PASS (the response set of the route is unchanged in the OpenAPI baseline: 403 is raised via the existing `ExtendedHTTPException` path; if the baseline test fails on it, report instead of editing the baseline blindly).

- [ ] **Step 6: Commit** using the repository's existing convention (ticket EPMCDME-17686).

---

## Constraint check (negative constraints)

- "Everyone else including Project Admins and platform admins is rejected": guard keys on stored `user_id` only, ahead of the ability result (Task 1, tests with both variants, including one with the real `Ability`).
- "nothing written, no project transfer": guard runs before `update_settings` and `transfer_setting` (Task 1, assertions on both mocks and project_name-change variant).
- "Updating an already PROJECT-typed integration behaves as today": guard is a no-op for PROJECT rows (Task 1, last test).
- "Do not change SettingsService.update_settings", "no comments", "no docs", "no real-Postgres tests": honored, no task touches them.
