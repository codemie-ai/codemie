# EPMCDME-17316 Implementation Plan

**Goal:** A non-personal application that differs from the user's stored email only by case is converted to that user's personal project (membership under the real name, one `project.created` event), like an exact-name one.

**Approach:** Relax `_is_reusable` in `personal_project_service.py`. Spec: `docs/superpowers/tasks/2026-11-16-epmcdme-17316-mixed-case-project-created-event/spec.md`.

Commit per task using the repository's existing convention (prefix `EPMCDME-17316:`). Run tests with `poetry run pytest <path> -n0 -p no:cacheprovider`; lint with `poetry run ruff check` / `ruff format` on the touched files only.

---

### Task 1: Relax the case-variant guard and update the tests

**Files:**
- Modify: `src/codemie/service/project/personal_project_service.py:197-237` (`_is_reusable`, its docstring, the `ValueError` message and `_create_personal_project` Raises text)
- Modify: `tests/codemie/service/project/test_personal_project_service.py:1198-1218` and add cases in `class TestCaseInsensitivePersonalProject` (starts `:1136`)

**Test-first: yes — `ensure_personal_project_async` called with a shared, `created_by=None`, case-variant application that has no members must return True, convert it to personal with `created_by=user`, add one membership under the real name and emit one `project.created` event whose `entity_id` is the real name; today `_is_reusable` is False, `_create_personal_project` raises `ValueError` and the call returns False.**

- [ ] **Step 1: Write the failing tests** (mocked-repository pattern of the class; patch `codemie.clients.postgres.get_async_session` with `_make_async_session_cm(AsyncMock())`, `...personal_project_service.activity_event_repository` (`async_insert = AsyncMock()`), `user_project_repository`, `application_repository`; see `:1299-1343` for a template).
  - Change `test_create_personal_project_rejects_foreign_case_variant` (`:1198-1218`): drop the `not-personal` parameter and keep only `owned-by-another-user`.
  - New `test_create_personal_project_converts_non_personal_case_variant`: parametrize `project_type="shared", created_by=None` and `project_type="shared", created_by=USER_ID`; `aget_by_project_name` returns `[]` (and a variant with only this user's row); assert the application becomes `PERSONAL` with `created_by == USER_ID`, the return value is `REAL`, and `aadd_project` is called once with `REAL`.
  - New `test_ensure_converts_case_variant_and_emits_one_event_with_real_name`: drive `ensure_personal_project_async(USER_ID, EMAIL)` with a shared case-variant `REAL`; assert returns True, one `async_insert` call, `entity_id == REAL`. Then call it again with `aget_by_user_and_project` returning a membership (the same, now-personal application object); assert no further `async_insert` call.
  - New `test_create_personal_project_refuses_case_variant_with_other_members`: shared case-variant, `aget_by_project_name` returns a row for another user; `ValueError` raised, `aadd_project` and `session.add` not called. Via `ensure_personal_project_async` the result is False and `async_insert` is not called.
  - New `test_create_personal_project_exact_name_lowercase_unchanged`: lowercase email + identically named shared application with no members is still converted (regression).

- [ ] **Step 2: Run, expect failure:** `poetry run pytest tests/codemie/service/project/test_personal_project_service.py -k "CaseInsensitive" -n0 -p no:cacheprovider` — the conversion and emit tests fail on `ValueError` / False.

- [ ] **Step 3: Implement.** In `_is_reusable` keep the exact-name `return True` and replace the final line so it returns False only for another user's personal project, i.e. `not (application.project_type == PERSONAL and application.created_by != user_id)`. Update the docstring to say a case variant is converted unless it is another user's personal project; the other-members check at `:242-257` stays untouched. Reword the `logger.warning` and `ValueError` text at `:233-237` and the `Raises:` entry at `:226` to "another user's personal project". Do not touch the emitter (`:81-95`), `_find_application`, or `_ensure_projects_exist`.

- [ ] **Step 4: Run, expect pass:** the command from Step 2, then the whole file `poetry run pytest tests/codemie/service/project/test_personal_project_service.py -n0 -p no:cacheprovider`, then `poetry run ruff check` and `poetry run ruff format --check` on the two touched files.

---

## Negative-constraint pass

- Normalising emails / renaming applications (non-goal): no task touches `EmailStr`, IdP claims or names. Honored by Task 1 (only the guard changes).
- Changing the activity-events read API, `find_all` or any index (non-goal; read-path filter dropped): no task touches it. Honored.
- Changing the emitter or `project_service.py:271-280` (non-goal): Task 1 explicitly leaves them. Honored.
- Strict event writes (non-goal): not used. Honored.
- No data migration, no cleanup script, no re-emission: none planned. Honored.
- Still refuse another user's personal case-variant and any project with other members (spec AC4, AC5): covered by the kept `owned-by-another-user` case and the new other-members test.
- `_ensure_projects_exist` unchanged, pre-existing application pick order unchanged: not touched.
