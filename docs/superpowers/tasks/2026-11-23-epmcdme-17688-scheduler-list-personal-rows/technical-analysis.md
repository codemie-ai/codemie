# Technical Research

**Task**: scheduler settings list owner filter
**Generated**: 2026-11-23
**Research path**: codegraph

---

## 1. Original Context

Implement the fix for https://jiraeu.epam.com/browse/EPMCDME-17688, make sure admin and maintainer are able to see the same things as on main.

Ticket EPMCDME-17688 body (fetched from Jira, verbatim):
Summary: [G0.16] L14 part 3: a scheduler list shows personal rows of other members
Problem: The scheduler list (GET /v1/schedulers), when called without the ownerType filter, also shows the personal schedulers of other members. A personal scheduler of one user is therefore visible to other users in a list.
Why it matters: ACS requires that a list shows only what the user could also open individually. A list that differs from the object rules cannot be kept as "compatible behavior", so it is repaired before the current behavior is recorded.
Required change: A scheduler list without ownerType shows only the personal scheduler rows of the caller, never those of other members.
Acceptance Criteria:
1. Given personal schedulers of several members, when a member requests the scheduler list without ownerType, then the list contains no personal scheduler row of a different member.
2. Given the same request, then the personal scheduler rows of the caller are still in the list.
3. A regression test covers these cases, and the test passes in CI.
Details: Parent Story EPMCDME-17676 (repairs of legacy defects before the ACS parity checks); Repo: codemie; Risk: Medium; Design cross-reference: part G0.16, defect L14 part 3.

Additional requirement from the user: admin and maintainer users must be able to see the same things as on main (i.e. no change in what admins/maintainers see in the list).

Already established (verify, don't trust blindly): code is src/codemie/service/settings/scheduler_settings_service.py — _build_list_filters (~line 465) and _apply_owner_filter (~line 455); router src/codemie/rest_api/routers/schedulers.py list_schedulers. With ownerType empty, _apply_owner_filter returns early and no setting_type/user_id condition is added; non-admin callers are only scoped by project_name = ANY(project_names); admins/maintainers (user.is_admin_or_maintainer) skip project scoping entirely. Reproduced live: a member sees other members' personal (setting_type='USER') scheduler rows. Please also research: what values setting_type takes for scheduler rows and who creates PROJECT-type ones, how is_admin_or_maintainer is defined, whether other callers of _build_list_filters/_apply_owner_filter/list_schedulers exist (e.g. scheduler-runs list/stats, filter-options, _fetch_all_scheduler_settings) that share the same gap, and the existing test files/style for the scheduler list (tests/ area) so the plan can follow neighbours.

feature_area: scheduler settings list owner filter

---

## 2. Codebase Findings

### Existing Implementations

Purpose A: a caller lists schedulers (GET /v1/schedulers)
- Route `list_schedulers` — `src/codemie/rest_api/routers/schedulers.py:55-79`. Passes `owner_type` (alias `ownerType`) and `user=Depends(authenticate)` straight to the service; no logic. Only caller of the service method.
- `SchedulerSettingsService.list_schedulers` — `src/codemie/service/settings/scheduler_settings_service.py:518`. Builds raw SQL over `codemie.settings s` LEFT JOIN LATERAL last run `lr`; count and rows use the same `where` from `_build_list_filters`; then loads `Settings` by id and maps with `_build_scheduler_item`. Only caller of `_build_list_filters` in `src/` (line 538).
- `_build_list_filters` — `:465-515`. Always `s.credential_type = 'SCHEDULER'`. If `user is not None and not user.is_admin_or_maintainer`: `1=0` when `project_names` empty, else `s.project_name = ANY(:project_names)`. Admin/maintainer: no project condition. Then project_ids, resource_type, resource_ids, search, status, last_run_status filters; last step calls `_apply_owner_filter` (line 513).
- `_apply_owner_filter` — `:454-462`. `owner_type` falsy: returns, no condition. Otherwise `s.setting_type = :owner_type` (value upper-cased; no whitelist) and, only when `owner_type.upper() == "USER"` and `user is not None`, `s.user_id = :caller_id` (`str(user.id)`). This applies to admins too (no bypass; test at `tests/.../test_scheduler_settings_service_class.py:501`).
- Resulting visibility today (rows in `s`): 
  - `ownerType=User`: caller's own personal rows (any role).
  - `ownerType=Project`: PROJECT rows, project-scoped for non-admins.
  - no `ownerType`: non-admin sees ALL rows (USER of every member + PROJECT) in own projects; admin/maintainer sees ALL rows of all projects. This is the defect.

Purpose B: who creates scheduler rows and with which `setting_type`
- `SettingType` enum values `user` / `project` (`src/codemie/rest_api/models/settings.py:266`); column `setting_type` defaults `SettingType.USER` (`:296`), Optional, DB enum `('USER','PROJECT')` nullable (`alembic/versions/074d06e75b25_create_settings.py:102`). Raw SQL compares with `'USER'`/`'PROJECT'`.
- USER rows: `SchedulerSettingsService._create_new_schedule` (`scheduler_settings_service.py:260-268`, hard-coded `SettingType.USER`, `user_id` = datasource owner, alias prefix `Schedule_`); `POST /v1/settings/user` with `credential_type=SCHEDULER` (`routers/user_settings.py:171,214`, `settings_type=USER`).
- PROJECT rows: `POST /v1/settings/project` with `credential_type=SCHEDULER` (`routers/project_settings.py:100-123`, `SettingsService.create_setting(user_id=user.id, ..., settings_type=SettingType.PROJECT)`), gated by `_check_permission` (`:291`): admin/maintainer or project admin of `project_name`. Such rows store the creator's `user_id`, so `user_id` does not distinguish personal from project rows.
- Both go through `validate_scheduler_request` (`service/settings/settings_request_validator.py:241`).

Purpose C: who is "admin or maintainer"
- `User.is_admin_or_maintainer` property (`src/codemie/rest_api/security/user.py:195`) -> `permissions.is_admin_or_maintainer(user)` (`rest_api/security/permissions.py:20`): `is_admin or is_maintainer`, each only if a real `bool`.
- `User.resolve_is_admin` (`user.py:160`): `ENV=local` forces `is_admin=True` for everyone; with `ENABLE_USER_MANAGEMENT=False` admin comes from `ADMIN_USER_ID` / `ADMIN_ROLE_NAME`; `is_maintainer` forces `is_admin=True`. Auditors are NOT included (`is_admin_maintainer_or_auditor` is a separate function; not used by scheduler code).
- Other users of the same predicate in this area: `_fetch_all_scheduler_settings` (`:603`), `get_filter_options` cache key (`:670`).

Other touched code
- `SchedulerSettingsService._fetch_all_scheduler_settings` — `:597-608`. Used only by `get_filter_options` (`:676`). Scope: non-admin `project_name IN project_names`; no `setting_type`/`user_id` condition.
- `get_filter_options` — `:663-702`; route `GET /v1/schedulers/filter-options` (`routers/schedulers.py:50`). Output is unique resources and projects built from every scheduler row in scope, 60 s cache keyed `"admin"` or `frozenset(project_names)`.
- `GET /v1/scheduler-runs` and `/scheduler-runs/stats` (`routers/schedulers.py:133,159`) and `SchedulerRunService.list_runs/get_stats` (`service/scheduler_run_service.py:39,42`): take no `user`, apply no project or owner scoping at all (they never call `_build_list_filters`).
- `PATCH /v1/schedulers/{id}` (`routers/schedulers.py:82`, `patch_is_enabled` `:705`): no user/permission check, loads by id.
- Engine-level readers that must see all rows: `src/codemie/triggers/bindings/cron.py:1141` (`Settings.get_all_by_fields` on SCHEDULER), `get_scheduler_settings_for_datasources`, `delete_schedule` (filtered by `user_id`).

### Decisions Already in the Code
- Admin/maintainer is exempt from project scoping in list and filter-options (`scheduler_settings_service.py:471,603,670`); alternative none; reason none found.
- `ownerType=User` is scoped to caller's own `user_id` even for admins (EPMCDME-10682 fix, tests at `test_scheduler_settings_service_class.py:473-516`); reason recorded in test comments only.
- `ownerType` is passed through as free text, upper-cased, no whitelist (`:459`); an unknown value is compared with the PG enum column in raw SQL.
- Convention: list filters are raw SQL fragments appended to `conditions` plus bind `params`; owner logic lives in `_apply_owner_filter`; tests assert on those strings/params (see Section 4).
- Rows are USER or PROJECT; `setting_type` and `user_id` are nullable columns (model default USER).

### Task Statements Checked Against the Code
- "With ownerType empty, `_apply_owner_filter` returns early, no setting_type/user_id condition": holds (`:456-457`).
- "Non-admins only scoped by project_name = ANY(project_names)": holds (`:471-477`; `1=0` when no projects).
- "Admins/maintainers skip project scoping entirely": holds (`:471`).
- "Member sees other members' personal rows (reproduced live)": consistent with the code; no code path excludes them.
- "Admin/maintainer must see the same as main": keeps in place the admin/maintainer unrestricted path in `_build_list_filters:471`. Admin currently sees every personal row of every member when `ownerType` is absent. Required change text ("shows only the personal rows of the caller") read literally would also cover admins; the user's added requirement says they stay as on main. Consumers of that path: `list_schedulers` only.
- "Acceptance: list shows only what the user could open individually": no individual open (GET by id) endpoint exists for schedulers; `PATCH /schedulers/{id}` has no permission check. Unverified against any object rule in this repo.
- "ACS design cross-reference G0.16 / L14 part 3": unverified (outside this repository).

### Consumers of Changed Contracts
Contract: the row set returned by `_build_list_filters` for non-`ownerType` calls (and `_apply_owner_filter`).
- `list_schedulers` (`scheduler_settings_service.py:538`): the only runtime consumer; count and rows share one `where`, so pagination `total` follows the filter.
- Tests calling `_build_list_filters` directly: `tests/codemie/service/settings/test_scheduler_settings_service_class.py:345-551`, `tests/codemie/rest_api/routers/test_schedulers.py:549-580`.
- Same gap, separate code (not consumers of `_build_list_filters`): `get_filter_options` -> `_fetch_all_scheduler_settings` (non-admin sees resources/projects drawn from other members' personal rows); `scheduler-runs` list/stats (no scoping). Both outside the ticket's stated scope.
- No external API contract change: query params and response model unchanged; `tests/codemie/rest_api/router_guards/baselines/openapi.json` lists `/v1/schedulers` (8 matches) and would not change for a service-only edit.

### Architecture and Layers Affected
- REST router (`rest_api/routers/schedulers.py`) -> service (`service/settings/scheduler_settings_service.py`, raw SQL via `Settings.get_engine()`) -> `codemie.settings` / `codemie.scheduler_runs` tables. Change surface is the service layer's filter builder.

### Integration Points
- `Settings` model/engine, `User` (`project_names`, `id`, `is_admin_or_maintainer`), `authenticate` dependency. No external services.

### Patterns and Conventions
- Static methods on `SchedulerSettingsService`; string SQL conditions with named binds; `str(user.id)` for caller binding.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/data/database-patterns.md`, `.ai-run/guides/architecture/service-layer-patterns.md`, `.ai-run/guides/testing/testing-service-patterns.md`, `.ai-run/guides/testing/testing-api-patterns.md` exist; not scheduler-specific. No guide covers the scheduler list.

### Architectural Decisions
- None found for scheduler list visibility. Test comments reference EPMCDME-10682 (ownerType=User caller scoping) and EPMCDME-14805 (ownerType filter).

### Derived Conventions
- Owner semantics are expressed in `_apply_owner_filter`; role bypass for project scope is `user.is_admin_or_maintainer`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/settings/test_scheduler_settings_service_class.py`: `_build_list_filters` user scoping (`:345-475`), `ownerType` cases (`:482-551`), `get_filter_options` scoping (`:440`). Helpers `_make_user(is_admin, project_names)` (`:337`), `_make_user_with_id(user_id, is_admin, project_names)` (`:476`), `_filters(**kw)` (`:378`).
- `tests/codemie/rest_api/routers/test_schedulers.py`: router forwards `ownerType` (`:495`), repeated params (`:526`), `_build_list_filters` with `user` omitted (`:549-580`).
- `tests/codemie/service/settings/test_scheduler_settings_service_stagger.py`, `test_scheduler_settings_service.py`: other service paths.
- Existing assertion that contradicts the ticket: `test_build_list_filters_no_owner_type_does_not_add_caller_id` (`test_scheduler_settings_service_class.py:537-551`) asserts no `s.user_id` condition and no `caller_id` param for a non-admin with `owner_type=None`. `test_build_list_filters_omits_setting_type_when_owner_type_is_none` (`test_schedulers.py:566`) asserts no `setting_type` condition/`owner_type` param with `user` omitted (`user=None`).
- Nothing executes the list SQL (no test over `list_schedulers` itself, no Postgres-backed test in this area).

### Testing Framework and Patterns
- pytest; `unittest.mock.Mock/patch`; plain functions plus `@pytest.mark.parametrize`; tests inspect the `(conditions, params)` tuple (string `in`/`any(... in c)`), mock users with `.id`, `.project_names`, `.is_admin_or_maintainer`.

### Coverage Gaps
- No test for the combined behavior "member, no ownerType" at SQL-result level; no test of maintainer vs admin vs member row sets with `ownerType` absent; `get_filter_options` has no personal-row test.

---

## 5. Configuration and Environment

### Environment Variables
- `ENV` (`local` makes every `User` admin; relevant to live repro), `ENABLE_USER_MANAGEMENT`, `ADMIN_USER_ID`, `ADMIN_ROLE_NAME` (`security/user.py:160-178`).

### Configuration Files
- None specific to scheduler list.

### Feature Flags and Deployment Concerns
- None found. `_filter_options_cache` is process-local, 60 s TTL.

---

## 6. Risk Indicators

- Existing mechanism kept in place: admin/maintainer unrestricted path (`_build_list_filters:471`) used only by `list_schedulers`; see Section 2 (statement "Admin/maintainer must see the same as main").
- Literal ticket text ("only caller's personal rows") vs. user requirement (admin unchanged) point different ways for admins; the user's instruction is the later one.
- `user_id` of a PROJECT row is its creator (`project_settings.py:123`); a bare `s.user_id = :caller_id` condition therefore does not separate personal from project rows. Speculative: the condition must key on `setting_type` as well.
- `setting_type` is nullable in the DB (model default USER); Speculative: legacy NULL rows could be classified differently by `= 'USER'` vs `<> 'PROJECT'` forms.
- `user=None` path: `_build_list_filters` is called without a user in `test_schedulers.py:549-580`; `_apply_owner_filter` already tolerates `user is None`.
- Related gaps outside the ticket's stated scope: `get_filter_options`/`_fetch_all_scheduler_settings` (exposes resource and project names from others' personal rows to non-admins; cache keyed per project set, shared across members) and `scheduler-runs` list/stats (no scoping). Not named in the ticket.
- Count and rows share one `where`, so a filter change moves `pagination.total` consistently; no test executes the SQL.
- Two existing tests assert the current no-`ownerType` behavior (Section 4) and will need to change with any fix.
- Unknown `ownerType` values are not validated (`:459`); pre-existing.

---

## 7. Summary for Complexity Assessment

The defect is confined to the service layer: `_apply_owner_filter` returns early when `ownerType` is absent, and `_build_list_filters` scopes non-admins only by project. `list_schedulers` is the only caller of both, and the REST contract (params, response model, OpenAPI baseline) is unaffected. One file in `src/` carries the behavior; the router needs no change. Admins and maintainers go through the `is_admin_or_maintainer` branch that skips project scoping, and today they see every row with no `ownerType`; the user wants that unchanged.

Rows are `USER` (personal, created by datasource scheduling and `/v1/settings/user`) or `PROJECT` (created via `/v1/settings/project` by admin/maintainer/project admin, with the creator's `user_id`). Test coverage is mock-based, asserting condition strings and params; there is no SQL-executing test, and one test in each of two files asserts today's behavior for `owner_type=None`.

Key risks: role-dependent behavior (admin unchanged vs ticket wording), `user_id` not identifying personal rows, nullable `setting_type`, and sibling code paths with the same gap (filter-options, scheduler-runs) that the ticket does not name.

---

## 8. External References

- Jira EPMCDME-17688: https://jiraeu.epam.com/browse/EPMCDME-17688 — resolved through the task text (body quoted in Section 1); not opened separately. Parent Story EPMCDME-17676 and the ACS design (G0.16, L14 part 3): not provided, unverified.
