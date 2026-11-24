# Technical Research

**Task**: scheduler settings permissions ability
**Generated**: 2026-11-24
**Research path**: codegraph

---

## 1. Original Context

Implement the fix with regression tests for EPMCDME-17681 ("[G0.15] L14 part 1: the scheduler PATCH changes is_enabled on each Settings row"). Make sure admin and maintainers are still able to enable/disable schedulers. Ticket requirements, verbatim:

Problem: Schedulers (scheduled runs) are stored as a special kind of Settings row. The endpoint PATCH /v1/schedulers/{scheduler_id} switches a scheduler on or off by changing is_enabled. It does this for any Settings row with the given id and does no permission check. A caller can therefore change is_enabled on a row of a different user, and also on a row that is not a scheduler at all (for example another kind of integration).
Required change: The PATCH of is_enabled changes only a scheduler row on which the caller has write access. All other calls fail.
Acceptance Criteria:
1. Given a scheduler row of a different user on which the caller has no write access, when the caller sends a PATCH of is_enabled, then the request fails and the row is not changed.
2. Given a Settings row that is not a scheduler row, when a caller sends a PATCH of is_enabled with its id, then the request fails and the row is not changed.
3. Given a scheduler row, when its owner sends a PATCH of is_enabled, then the change works as today.
4. A regression test covers these cases, and the test passes in CI.
Out of scope: reading and deleting scheduler run history (handled later by ACS).
Live reproduction already done locally: a non-admin user PATCHed an admin-owned Scheduler row and an admin-owned Jira row, both returned HTTP 200 and changed is_enabled.
Code pointers: src/codemie/rest_api/routers/schedulers.py (patch_scheduler, ~line 82), src/codemie/service/settings/scheduler_settings_service.py (patch_is_enabled, ~line 708), and how existing settings update/delete endpoints decide write access (Ability / permission checks for user vs project settings, admin/maintainer handling) and existing tests for schedulers router/service.

---

## 2. Codebase Findings

### Existing Implementations

Purpose A: PATCH flips `is_enabled` on a scheduler row
- Route `patch_scheduler(scheduler_id, body)` - `src/codemie/rest_api/routers/schedulers.py:82-84`. Takes no `user`; calls `SchedulerSettingsService.patch_is_enabled(scheduler_id, body.isEnabled)`. Router-level deps are `require_core` and `authenticate` (`:40-44`), so the caller is authenticated but never passed on. Only caller of the service method.
- `SchedulerSettingsService.patch_is_enabled` - `src/codemie/service/settings/scheduler_settings_service.py:707-724`. `Settings.get_by_id(id)`; `raise_not_found(id, "Scheduler")` (404) when absent; no `credential_type` check, no owner/project check; rewrites or appends the `is_enabled` credential value, `flag_modified`, `setting.update()`, returns `_build_scheduler_item(setting)` (`:383`).
- Request model `PatchSchedulerRequest` - `src/codemie/rest_api/models/scheduler_run.py:102` (`isEnabled`).
- Readers of the flag: `_build_scheduler_item` (`:399`), list status filter `_apply_status_filter` (`:441`), cron engine `src/codemie/triggers/bindings/cron.py:1141`.

Purpose B: how the product decides write access on a Settings row (the existing way)
- `Ability(user).can(Action.WRITE, instance)` - `src/codemie/core/ability.py:224`. Instance must be an `Owned`; class name selects the `PERMISSIONS` table.
- Wrappers `UserSetting` / `ProjectSetting` (`src/codemie/rest_api/models/settings.py:432` / `:449`) wrap a Settings row; `SettingsService.get_setting_ability(credential_id, settings_type)` (`service/settings/settings.py:290`) loads by id and returns `ProjectSetting` if `settings_type == PROJECT`, else `UserSetting`. It raises `ValueError("Not found setting")` when absent and does NOT check `credential_type`.
- Rules (`ability.py:173-182`):
  - `UserSetting` WRITE: `[OWNED_BY]` only. `is_owned_by` = row `user_id` truthy and equals `user.id`. No ADMIN role: an admin or maintainer who is not the owner is denied.
  - `ProjectSetting` WRITE: `[OWNED_BY, MANAGED_BY, ADMIN]`. owner = `setting.user_id == user.id`; managed = `setting.project_name in user.admin_project_names`; `admin` role = `user.is_admin_or_maintainer` (`ability.py:271`).
- Callers deciding write access today:
  - `PUT /settings/user/{id}` - `routers/user_settings.py:204-239`: `get_setting_ability(..., USER)`, `Ability.can(WRITE)`, else `raise_access_denied("write")` (HTTP 401).
  - `DELETE /settings/user/{id}` - `user_settings.py:267-284`: same with `Action.DELETE`.
  - `PUT /settings/project/{id}` - `routers/project_settings.py:147-161`: `get_setting_ability(..., PROJECT)`, `Ability.can(WRITE)`, then `_ensure_personal_setting_owner` (`:243`: a USER-typed row needs `user_id == user.id` even on the project route, so admins are refused) and `is_budget_integration_hidden_from`.
  - `DELETE /settings/project/{id}` - `project_settings.py:213-218`: no Ability; `_check_permission(user, project_name)` (`:298`): admin or maintainer pass, else must be `is_application_admin(project_name)`, else 403.
  - `POST /settings/test` - `user_settings.py:326-329`: picks `ProjectSetting` vs `UserSetting` from the stored `setting.setting_type or USER` and checks `Action.READ`; denial is `raise_forbidden` (403).
- Error helpers `routers/utils.py:58` `raise_access_denied` (401), `:68` `raise_forbidden` (403), `:96` `raise_not_found` (404).

Purpose C: what kinds of scheduler rows exist (who owns them)
- USER rows: `SchedulerSettingsService._create_new_schedule` (`scheduler_settings_service.py:260-266`, hard-coded `SettingType.USER`, `user_id` = datasource owner, alias prefix `Schedule_`); `POST /settings/user` with `credential_type=SCHEDULER` (`user_settings.py:214`).
- PROJECT rows: `POST /settings/project` with `credential_type=SCHEDULER` (`project_settings.py:99-103`), gated by `_check_permission` (admin/maintainer or project admin). They store the creator's `user_id`.
- `Settings.setting_type` is nullable, model default USER (`models/settings.py`); `setting.setting_type or SettingType.USER` is the existing fallback (`project_settings.py:244`, `user_settings.py:326`).
- Scheduler rows are identified by `Settings.credential_type == CredentialTypes.SCHEDULER` (list SQL `s.credential_type = 'SCHEDULER'`, `:471`, `:605`).

Purpose D: what the list endpoint shows a caller (sibling, same file)
- `_build_list_filters` / `_apply_owner_filter` (`:454-518`): non-admin callers are scoped to `project_name IN project_names`; admin/maintainer unscoped. Without `ownerType` the list shows USER rows of any member and PROJECT rows. Prior task `docs/superpowers/tasks/2026-11-23-epmcdme-17688-scheduler-list-personal-rows/` (same branch family) changes the list-side owner filter; its plan lists "`PATCH /schedulers/{id}` ownership" as out of scope.

Other touched code
- `tests/codemie/rest_api/router_guards/baselines/openapi.json:83302` holds `PATCH /v1/schedulers/{scheduler_id}` (operationId `patch_scheduler_v1_schedulers__scheduler_id__patch`).

### Decisions Already in the Code
- Convention for write checks on Settings rows: `SettingsService.get_setting_ability` + `Ability(user).can(Action.WRITE, ...)`, picking `UserSetting` or `ProjectSetting` (`user_settings.py:233-239`, `project_settings.py:152-158`). None found that wraps a scheduler specifically.
- Wrapper choice by route, not by the row: `get_setting_ability` takes `settings_type` from the caller (the route), not from `setting.setting_type`; only `test_setting` derives it from the row (`user_settings.py:326`).
- `UserSetting` has no admin override; `ProjectSetting` does (`ability.py:173-182`). Reason: none found.
- Denial status is inconsistent: ability denials use 401 (`raise_access_denied`) on update/delete routes; `raise_forbidden` 403 on test route and `_check_permission`; missing row 404 (`raise_not_found`).
- The service layer receives no user today; the router passes `user=Depends(authenticate)` into the service for `list_schedulers` and `get_filter_options` (`schedulers.py:50-52,66-68`), router tests assert `kwargs["user"] is caller` (`tests/.../test_schedulers.py:518`).

### Task Statements Checked Against the Code
- "does no permission check ... for any Settings row with the given id": holds (`scheduler_settings_service.py:711-724`, `schedulers.py:83`).
- "also on a row that is not a scheduler": holds; no `credential_type` condition in `patch_is_enabled`, and `get_setting_ability` does not check it either.
- "only a scheduler row on which the caller has write access": the existing write-access rule is `Ability` over `UserSetting`/`ProjectSetting` (Purpose B). Which wrapper applies to a scheduler row is not decided in the code; scheduler rows exist as both USER and PROJECT.
- "admin and maintainers are still able to enable/disable schedulers": contradicted for the `UserSetting` rule: `Action.WRITE` on `UserSetting` is owner-only (`ability.py:175`), so an admin/maintainer on another member's USER row would be denied by the existing rule. Holds for `ProjectSetting` rows (ADMIN and MANAGED_BY roles, `ability.py:180`). Today admin/maintainer can PATCH every row (no check).
- "when its owner sends a PATCH ... works as today": holds; owner satisfies both wrappers (`UserSetting.is_owned_by`, `ProjectSetting.is_owned_by`).
- "a non-admin user PATCHed an admin-owned Scheduler row and an admin-owned Jira row, both HTTP 200": consistent with the code.
- "Out of scope: reading and deleting scheduler run history": consistent; `GET/DELETE /scheduler-runs*` (`schedulers.py:133-`) take no user and do no scoping; untouched by PATCH.
- `local` ENV forces `is_admin=True` for every user (`rest_api/security/user.py` `resolve_is_admin`), so local live reproduction by a "non-admin" depends on that setting (see 17688 analysis).

### Consumers of Changed Contracts
Contract: `patch_is_enabled(scheduler_id, is_enabled)` signature and its behaviour (no user, any row, 404 only when absent).
- `patch_scheduler` router (`schedulers.py:84`): only production caller.
- Tests: `tests/codemie/rest_api/routers/test_schedulers.py:216` (`test_patch_is_enabled_calls_flag_modified`) and `:238` (`..._raises_not_found`) call it with two positional args and patch `scheduler_settings_service.Settings` with a `MagicMock` row, so the mock row has no `credential_type`/`user_id` set to real values; they would see any added argument or check.
- No other Python caller found (`grep patch_is_enabled` over `src/` and `tests/`).
- HTTP consumers: the UI schedulers view (outside repo; unverified). Response model `SchedulerListItem` unchanged.
- OpenAPI baseline: `tests/codemie/rest_api/router_guards/baselines/openapi.json:83302`; a new `user` dependency or declared responses may alter it.
- Same-row writers outside the PATCH: `create_or_update_schedule`/`delete_schedule` filter by `user_id` (`scheduler_settings_service.py:127,128`); `Settings.update()` is shared.

### Architecture and Layers Affected
- Router `rest_api/routers/schedulers.py` -> service `service/settings/scheduler_settings_service.py` (static methods) -> `Settings` model (`rest_api/models/settings.py`) + `core/ability.py`.

### Integration Points
- `Ability`/`Action` (`core/ability.py`), `UserSetting`/`ProjectSetting`, `User` (`id`, `project_names`, `admin_project_names`, `is_admin_or_maintainer`), `authenticate` (`rest_api/security/authentication.py`), `ExtendedHTTPException`.

### Patterns and Conventions
- Routers inject `user: User = Depends(authenticate)` and pass it down; service methods are `@staticmethod` on `SchedulerSettingsService`; lazy imports inside methods for `raise_not_found`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/development/security-patterns.md`, `.ai-run/guides/api/rest-api-patterns.md`, `.ai-run/guides/architecture/service-layer-patterns.md`, `.ai-run/guides/testing/testing-api-patterns.md` and `testing-service-patterns.md` exist for these areas (not re-read for this analysis; conventions here derived from code). No scheduler-specific guide.
- Prior task docs: `docs/superpowers/tasks/2026-09-09-epmcdme-10682-schedulers/spec.md:69` (PATCH spec: auth, `isEnabled`, 200/404; no ownership rule) and `2026-11-23-epmcdme-17688-.../technical-analysis.md`.

### Architectural Decisions
- None found for scheduler write authorization.

### Derived Conventions
- Ability check then `raise_access_denied`/`raise_forbidden`; see Decisions.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/rest_api/routers/test_schedulers.py`: PATCH covered only at service level with a MagicMock row (`:216`, `:238`); no router-level PATCH test. Router tests use `FastAPI()` + `app.include_router(router)` + `app.dependency_overrides[authenticate]` (`:340-344`, `:555-559`), or call the route function directly with `user=caller` (`:518`); `ExtendedHTTPException` handler test client at `:369-384`.
- `tests/codemie/service/settings/test_scheduler_settings_service_class.py`, `..._stagger.py`, `test_scheduler_settings_service.py`: other service methods; no `patch_is_enabled`.
- Ability coverage: `tests/codemie/rest_api/routers/test_project_settings.py:309` (project admin non-owner past ability check); `tests/codemie/service/settings/test_settings_handler.py`.

### Testing Framework and Patterns
- pytest, `unittest.mock` (`patch`, `MagicMock`), mock-based, no real database in this area; `from __future__ import annotations`; module-level helper factories (`_make_scheduler_setting`, `:264`).

### Coverage Gaps
- No test for PATCH by non-owner, by admin/maintainer, by project admin, or on a non-SCHEDULER row.

---

## 5. Configuration and Environment

### Environment Variables
- `ENV=local` makes every user admin (`resolve_is_admin`); `ENABLE_USER_MANAGEMENT`, `ADMIN_USER_ID`, `ADMIN_ROLE_NAME` govern admin derivation when user management is off.

### Configuration Files
- None specific. `tests/codemie/rest_api/router_guards/baselines/openapi.json` is a recorded contract.

### Feature Flags and Deployment Concerns
- None found for PATCH. Router is registered unconditionally (`main.py` include_router of schedulers router, per earlier analysis `2026-09-15-feature-flag-schedulers-view`).

---

## 6. Risk Indicators

- Existing mechanism kept in place by a task statement: "admin and maintainers still able" vs `UserSetting` WRITE owner-only (`ability.py:175`) - see Task Statements; the only existing rule giving admin/maintainer write is `ProjectSetting` (`ability.py:180`). Speculative: reusing `UserSetting` for USER rows would deny admins on other members' USER rows.
- Scheduler rows are USER and PROJECT typed, and `get_setting_ability` takes the type from the caller, not the row (`settings.py:290`); `setting_type` is nullable (fallback USER).
- Denial status codes are inconsistent across existing routes (401 vs 403); the ticket says only "the request fails". Not-a-scheduler and not-found today differ from forbidden (404).
- Row-level status ordering: a 404 vs 403 for a foreign row leaks existence; the repo has both patterns.
- Existing service tests patch `Settings` with a bare `MagicMock` row (`test_schedulers.py:216,238`) and will be affected by any added check or signature change.
- OpenAPI baseline (`router_guards/baselines/openapi.json:83302`) may need regeneration if router signature/responses change.
- Sibling gaps with the same shape but out of this ticket: `get_filter_options`/`_fetch_all_scheduler_settings` and `/scheduler-runs*` (no user scoping).
- Related in-flight change on list owner filter (EPMCDME-17688) touches the same service file; merge adjacency only.
- `ENV=local` makes everyone admin: live checks of a non-admin need a non-local env or a flag override.

---

## 7. Summary for Complexity Assessment

The defect sits in two small units: `patch_scheduler` (`routers/schedulers.py:82`, no user) and `SchedulerSettingsService.patch_is_enabled` (`scheduler_settings_service.py:707`, loads by id with no `credential_type` or access check). Layers: router, service static method, `Settings` model, `core/ability.py`. The repo already has an established write-access mechanism (`Ability.can(Action.WRITE, UserSetting|ProjectSetting)`, used by the user/project settings update routes), so the change surface is two production files plus tests; no schema, config or migration facts were found as constraints.

The non-trivial part is the interaction between that mechanism and the ticket's admin/maintainer requirement: `UserSetting` WRITE is owner-only while `ProjectSetting` WRITE includes project admins and global admin/maintainers, and scheduler rows exist with both `setting_type` values (nullable, USER fallback). Existing denial codes differ between routes (401/403). These are facts for the spec to resolve; no option is weighed here.

Test posture: PATCH has only two service-level tests with MagicMock rows and no router-level test; neighbours are mock-based (FastAPI `TestClient` with `dependency_overrides`, or direct route calls). Ability rules are well covered in settings router tests. Risk factors: admin parity versus `UserSetting` rule, status-code choice, existing tests affected by a signature change, and the OpenAPI baseline.

---

## 8. External References

None named by the task (Jira ticket EPMCDME-17681 text was supplied verbatim; no file or URL path was given).
