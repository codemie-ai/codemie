# EPMCDME-17688 Scheduler list hides other members' personal rows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `GET /v1/schedulers` without `ownerType` returns no personal (USER) scheduler row of another member for non-admin/maintainer callers, while still returning the caller's own personal rows and all project rows in the caller's projects.

**Architecture:** Service-layer only. `SchedulerSettingsService._apply_owner_filter` (`src/codemie/service/settings/scheduler_settings_service.py:454-462`) gains a branch for the empty `owner_type` case. The router, params and response model do not change.

**Tech Stack:** Python, raw SQL fragments with named binds, pytest with mocks.

**Spec:** none (inline requirements; technical analysis in `technical-analysis.md` beside this file).

## Global Constraints

- Commit per task using the repository's existing convention.
- No code comments in the implementation or tests.
- Admin/maintainer (`user.is_admin_or_maintainer`) get the same conditions as on main: nothing new is added for them. The branch at `scheduler_settings_service.py:471` stays unchanged.
- `ownerType=User` and `ownerType=Project` behaviour, and the `user=None` path, stay unchanged.
- Tests are mock-based and inspect the `(conditions, params)` tuple. There are no real-Postgres tests.
- Out of scope, do not touch: `get_filter_options` / `_fetch_all_scheduler_settings`, scheduler-runs list/stats, `PATCH /schedulers/{id}` ownership.

## Acceptance criteria

- A non-admin/maintainer member listing without `ownerType` gets no personal row of another member.
- The same request still returns the caller's own personal rows and PROJECT rows of the caller's projects.
- A regression test covers these cases and passes.

## Review Focus

- PROJECT rows store their creator's `user_id`, so a bare `s.user_id = :caller_id` would hide project rows. The condition must keep `setting_type = 'PROJECT'` rows.
- Legacy rows with NULL `setting_type` count as personal (caller-only): `NULL = 'PROJECT'` is not true, so only the `user_id` branch can match them.
- A caller with an empty `project_names` still gets `1=0` and no owner condition is needed.

---

### Task 1: Restrict the no-ownerType list to project rows plus the caller's own rows

**Files:**
- Modify: `src/codemie/service/settings/scheduler_settings_service.py:455-457`
- Test: `tests/codemie/service/settings/test_scheduler_settings_service_class.py:537-551`

**Interfaces:**
- Produces: when `owner_type` is falsy, `user is not None` and `not user.is_admin_or_maintainer`, `_apply_owner_filter` appends the condition `(s.setting_type = 'PROJECT' OR s.user_id = :caller_id)` and sets `params["caller_id"] = str(user.id)`. In every other case with a falsy `owner_type` it adds nothing.

Test-first: yes — a non-admin `_build_list_filters(owner_type=None)` must contain the PROJECT-or-caller condition and `caller_id == user.id`; today it contains no `s.user_id` condition, so the test fails.

- [ ] **Step 1: Update and add tests** in `test_scheduler_settings_service_class.py`, using `_make_user_with_id` and the existing call style. Replace `test_build_list_filters_no_owner_type_does_not_add_caller_id` (`:537`) with these tests:
  - `test_build_list_filters_no_owner_type_restricts_non_admin_to_project_or_own_rows`: non-admin `user-42`. Assert `"(s.setting_type = 'PROJECT' OR s.user_id = :caller_id)" in conditions`, `params["caller_id"] == "user-42"` and `"owner_type" not in params`.
  - `test_build_list_filters_no_owner_type_adds_no_owner_condition_for_admin_or_maintainer`: parametrize on `is_admin` (the `_make_user` helper makes `is_admin_or_maintainer` follow it; check how it builds the mock and set `is_admin_or_maintainer=True` explicitly for a maintainer variant if needed). Assert no condition contains `s.user_id` or `s.setting_type`, and `"caller_id" not in params`.
  - `test_build_list_filters_no_owner_type_without_user_adds_no_owner_condition`: `user=None`; assert the same.
  - Leave the existing `owner_type="User"` and `owner_type="Project"` tests untouched; they are the regression guard for those paths.
- [ ] **Step 2: Run to verify failure.** Run `poetry run pytest tests/codemie/service/settings/test_scheduler_settings_service_class.py -k build_list_filters -v`. Expected: the non-admin restriction test FAILS and the admin/maintainer and `user=None` tests PASS.
- [ ] **Step 3: Implement** in `_apply_owner_filter`: replace the early `return` on a falsy `owner_type` with a branch. When `user is not None and not user.is_admin_or_maintainer`, append the condition above and set `params["caller_id"]`. Then `return`. Leave the rest of the method unchanged.
- [ ] **Step 4: Run to verify pass.** Run the same pytest command, then the whole file and `tests/codemie/rest_api/routers/test_schedulers.py`. Expected: all PASS. `test_build_list_filters_omits_setting_type_when_owner_type_is_none` (`test_schedulers.py:566`) calls with no user and still holds, so leave it unchanged.
