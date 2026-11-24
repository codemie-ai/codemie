# EPMCDME-17681 Scheduler PATCH authorization Implementation Plan

**Goal:** `PATCH /v1/schedulers/{scheduler_id}` changes `is_enabled` only on a Scheduler-type Settings row the caller can write; everything else fails with the row untouched.

**Architecture:** The router passes the authenticated user into `SchedulerSettingsService.patch_is_enabled`, which enforces the check before any mutation. Write access reuses `Ability(user).can(Action.WRITE, UserSetting|ProjectSetting)` (`core/ability.py:173-182`), with an explicit admin/maintainer allowance because `UserSetting` WRITE is owner-only.

Commit per task using the repository's existing convention. No code comments in production changes. No feature docs.

## Acceptance criteria

- Non-owner without write access PATCHing a scheduler row: 403, row unchanged.
- PATCH with the id of a non-Scheduler Settings row: 404 "Scheduler not found", row unchanged.
- Owner PATCHing own scheduler row: 200, as today.
- Global admin and maintainer can PATCH any scheduler row (USER or PROJECT typed); project admin can PATCH PROJECT-typed rows of their project.
- Regression tests for all of the above pass.

## Decisions (recorded, not open)

- Non-scheduler row (`credential_type != SCHEDULER`) and missing id both raise `raise_not_found(id, "Scheduler")` (404), so existence is not revealed.
- Scheduler row without write access: `raise_forbidden("update")` (403, `routers/utils.py:68`), the helper `POST /settings/test` and `_check_permission` use. The 401 `raise_access_denied` is not used because 401 reads as unauthenticated.
- Wrapper is chosen from the row: `setting.setting_type or SettingType.USER` (fallback already used at `project_settings.py:244`). PROJECT uses `ProjectSetting` (owner, project admin, admin/maintainer); USER uses `UserSetting` (owner) plus `user.is_admin_or_maintainer`.
- Order: load, 404 checks, 403 check, then mutate. No write on denial.
- OpenAPI baseline: the new parameter is an unannotated `Depends(authenticate)` like `list_schedulers`, so no schema change is expected. If `tests/codemie/rest_api/router_guards/test_openapi_snapshot.py` fails, review the diff and regenerate with `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`; otherwise leave `baselines/openapi.json` alone.
- Negative constraints: out of scope scheduler-runs read/delete (`schedulers.py:133-`), list and `get_filter_options` scoping are not touched by any task; existence is not leaked for non-scheduler rows (Task 1); no write on denial (Task 1); admin/maintainer not blocked by the owner-only `UserSetting` rule (Task 1); no real-Postgres tests, no code comments, no docs (all tasks).

---

### Task 1: Enforce scheduler-type and write access in the service

**Files:**
- Modify: `src/codemie/service/settings/scheduler_settings_service.py:707-724`
- Modify: `tests/codemie/rest_api/routers/test_schedulers.py:216-245`

**Test-first:** yes — `patch_is_enabled` on a non-scheduler row raises 404 and never calls `update()`; on another user's USER scheduler row raises 403 and never calls `update()`; fails today because no check exists and the signature takes no user.

- [ ] Change the signature to `patch_is_enabled(scheduler_id: str, is_enabled: bool, user)`. After the `None` check, raise `raise_not_found(scheduler_id, "Scheduler")` when `setting.credential_type` is not `CredentialTypes.SCHEDULER` (compare tolerantly to enum or string value, as the list SQL does at `:605` with `.value.upper()`). Then call a new private static `_ensure_can_write(setting, user)` and leave the mutation block (`:715-724`) unchanged.
- [ ] New helper, lazy-importing `Ability`, `Action` (`codemie.core.ability`) and `UserSetting`, `ProjectSetting` (`rest_api.models.settings`) and `raise_forbidden`:

```python
@staticmethod
def _ensure_can_write(setting, user) -> None:
    if not user.is_admin_or_maintainer:
        wrapper = ProjectSetting if (setting.setting_type or SettingType.USER) == SettingType.PROJECT else UserSetting
        if not Ability(user).can(Action.WRITE, wrapper(setting)):
            raise_forbidden("update")
```

- [ ] Update the two existing tests (`:216`, `:238`) to pass a user. Give the row mock `credential_type = CredentialTypes.SCHEDULER`, `setting_type = SettingType.USER`, `user_id = "u1"`, and a caller `MagicMock(id="u1", is_admin_or_maintainer=False)`.
- [ ] Add service tests in the same file, using the same `patch("...scheduler_settings_service.Settings")` style and asserting `mock_setting.update.assert_not_called()` on every denial:
  - owner of USER scheduler row: item returned, `update` called once;
  - non-owner on USER row: `ExtendedHTTPException` with `code == 403`;
  - non-scheduler row (e.g. `CredentialTypes.JIRA`) as admin: code 404;
  - admin and maintainer (`is_admin_or_maintainer=True`) on another user's USER row and PROJECT row: allowed;
  - project admin (`admin_project_names=["p1"]`, row `setting_type=PROJECT`, `project_name="p1"`): allowed; plain project member non-owner: 403;
  - `setting_type=None` row falls back to USER (non-owner 403).

### Task 2: Pass the authenticated user from the router and cover the endpoint

**Files:**
- Modify: `src/codemie/rest_api/routers/schedulers.py:82-84`
- Modify: `tests/codemie/rest_api/routers/test_schedulers.py` (new router tests next to the existing client helpers, `:334-385`)

**Test-first:** yes — a router test asserting `patch_is_enabled` receives `user` as the authenticated caller; fails today because `patch_scheduler` takes no user.

- [ ] `patch_scheduler(scheduler_id: str, body: PatchSchedulerRequest, user=Depends(authenticate))` calls `SchedulerSettingsService.patch_is_enabled(scheduler_id, body.isEnabled, user)`, matching `list_schedulers` (`:66`).
- [ ] Add router tests with `FastAPI()` + `include_router(router)` + `dependency_overrides[authenticate]` returning a caller, and the `ExtendedHTTPException` handler pattern from `_make_delete_client` (`:369-385`):
  - 200 and the caller object is forwarded to the service (patch `SchedulerSettingsService.patch_is_enabled`);
  - end-to-end through the real service with `Settings` patched: non-owner gets 403, non-scheduler row gets 404, owner gets 200, and `update` is not called on 403/404.
- [ ] Run `tests/codemie/rest_api/router_guards/test_openapi_snapshot.py`; regenerate the baseline only if it fails, per the Decisions section.
