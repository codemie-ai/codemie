# Technical Research

**Task**: settings integration project ownership ability
**Generated**: 2026-11-20
**Research path**: codegraph

---

## 1. Original Context

Ticket EPMCDME-17686, repo codemie.

Summary: [G0.13] L4: a conversion moves a personal integration of a different user into a project

Problem: An integration (stored connection settings with credentials) can be personal, so only its owner uses it, or it can belong to a project, so every member of the project can use it. Updating a personal integration through the project settings endpoint (PUT /v1/settings/project/{setting_id}) converts it into a project integration. This conversion does not check that the caller owns the personal integration. A Project Admin of the integration's project, or a platform admin, can convert the personal integration of a different user. The secret stays in the integration, and from then on every member of the project can use it.

Required change: Only the owner of a personal integration can convert it into a project integration. Everyone else, including Project Admins and platform admins, is rejected.

Acceptance Criteria:
1. Given a personal integration of user A, when a different user (also a Project Admin or a platform admin) converts it into a project integration, then the request is rejected and the integration stays personal.
2. Given a personal integration of user A, when user A converts it, then the conversion works as today.
3. A regression test covers the first case, and the test passes in CI.

Details: Parent story EPMCDME-17676. Risk: High (moves one user's secret into a project others can read and use).

Already established locally (reproduced on the running stack, treat as facts to verify in code): src/codemie/rest_api/routers/project_settings.py update_project_setting only checks Ability(user).can(WRITE, ProjectSetting(setting)) (owner, or admin of setting.project_name; superadmin passes too), then SettingsService.update_settings (src/codemie/service/settings/settings.py ~line 525) sets setting_type = PROJECT unconditionally. Observed: Project Admin and superadmin non-owners got 200 and USER->PROJECT; plain member got 401; owner got 200. The UI (codemie-ui) never triggers this path; API-only.

feature_area: settings integration project ownership ability
run_dir: /Users/bohdan_maliar/Projects/codemie-dev/codemie/docs/superpowers/tasks/2026-11-20-epmcdme-17686

---

## 2. Codebase Findings

### Existing Implementations

Purpose A: "Only a setting's owner may act on a personal integration; project integrations are managed by owner/project admin/admin."
- `src/codemie/core/ability.py:173-182` PERMISSIONS table. `UserSetting`: READ/WRITE/DELETE = [OWNED_BY]. `ProjectSetting`: READ = [SHARED_WITH, OWNED_BY, MANAGED_BY, ADMIN]; WRITE and DELETE = [OWNED_BY, MANAGED_BY, ADMIN].
- `src/codemie/core/ability.py:224-244` `Ability.can`; `admin()` at :271 = `user.is_admin_or_maintainer`.
- `src/codemie/rest_api/models/settings.py:432-446` `UserSetting`: owned/managed = `bool(user_id) and user_id == user.id`. Lines 449-460 `ProjectSetting`: owned = `user_id == user.id`; managed = `project_name in user.admin_project_names`; shared = `project_name in user.project_names`.
- `src/codemie/service/settings/settings.py:289-303` `SettingsService.get_setting_ability(credential_id, settings_type)`: loads the setting, masks secrets, then picks the wrapper from the **caller-supplied `settings_type` argument**, not from the stored `setting.setting_type`. `ProjectSetting` is chosen whenever the router passes `SettingType.PROJECT`. Raises `ValueError("Not found setting")` if absent.
- Dependents of `get_setting_ability`: `routers/project_settings.py:152`, `routers/user_settings.py:233`, `routers/user_settings.py:278`.

Purpose B: "Update a project integration through the project endpoint" (the path in the ticket).
- `src/codemie/rest_api/routers/project_settings.py:147-203` `update_project_setting` (PUT /v1/settings/project/{setting_id}). Order: `get_setting_ability(PROJECT)` -> `Ability(user).can(WRITE)` else `raise_access_denied("write")` (401) -> `is_budget_integration_hidden_from` (403) -> `_validate_update_request` (:242-264) -> `project_changed` -> `_check_permission(user, request.project_name)` if the project changed -> `SettingsService.update_settings(..., settings_type=PROJECT, user_id=user.id)` inside `transactional_session()` when the project changes, then `SettingsTransferService.transfer_setting`.
- `src/codemie/service/settings/settings.py:463-542` `update_settings`. Lines 525-528: `if settings_type == PROJECT: user_setting.setting_type = PROJECT else USER`. This is unconditional on the stored type and the caller. `user_id` param is used only for alias uniqueness, OAuth and the LiteLLM cache; the stored `user_setting.user_id` is not changed.
- `Settings.check_alias_unique` (`models/settings.py:339-357`): with `PROJECT` it checks alias uniqueness project-wide, without `user_id`.
- Other callers of `update_settings`:
  - `routers/user_settings.py:249` `update_user_setting`. It passes no `settings_type`, so the stored type is forced to USER. Its guard is `UserSetting` (owner only). A PROJECT setting whose `user_id` is the caller is also converted to USER by it (reverse direction).
  - `service/settings/settings.py:367` `upsert_project_setting`. It passes PROJECT, with no `user_id`, and only for rows already found with `setting_type=PROJECT` by alias.
- Who reads PROJECT-type settings (consumers of the converted row): `SettingsService.get_settings` with `Settings.get_by_project_names` (`settings.py:275`); `ProjectSettingsHandler` (`service/settings/settings_handler.py:220-232`, last in the `build_settings_handlers` chain, strips `USER_ID` from the search); `AssistantSettingsHandler` (:148-168, matches `setting_type == PROJECT`); `routers/user_settings.py:148-155` listing for admins/members; `service/oauth/authorization.py` and `service/iac_export/exporters/integration.py` use `ProjectSetting`/`UserSetting`.

Purpose C: "Same-purpose siblings that already guard personal vs project settings."
- `routers/user_settings.py:306-331` `test_setting`: derives the ability from the **stored** `setting.setting_type` (`ProjectSetting if PROJECT else UserSetting`). This is the only place that picks the wrapper from stored data.
- `routers/project_settings.py:159` / `SettingsService.is_budget_integration_hidden_from` (`settings.py:305-313`): a post-ability guard on the stored `setting_type` and alias prefix, raising 403 via `raise_forbidden`.
- `routers/settings.py:95` `POST /settings/transfer` (admin/maintainer only, `admin_or_maintainer_access_only`): bulk-moves USER and/or PROJECT rows between projects in `SettingsTransferService.transfer` (`settings_transfer_service.py:405`). It preserves `row.setting_type`; no type change found (grep of `setting_type` in that file shows only filters and copies).
- `SettingsTransferService.transfer_setting` (`settings_transfer_service.py:379-403`): single-setting move; it is called after `update_settings` in both routers.

Other touched code: `src/codemie/rest_api/routers/utils.py:58-75` `raise_access_denied` (401) and `raise_forbidden` (403), both with the same body shape.

### Decisions Already in the Code

- Ability wrapper is chosen by the endpoint, not by the stored row (`settings.py:303`). Alternative: the stored type, as in `user_settings.py:326`. Reason: none found. Result: a USER row addressed through the project endpoint gets `ProjectSetting` rules.
- `update_settings` treats `settings_type` as "the type to store", not "the type that must already exist" (`settings.py:525-528`). Reason: none found. The two routers rely on that to flip the type in either direction.
- Convention for denial status: `raise_access_denied` -> 401 for ability failure (`project_settings.py:158`); `raise_forbidden` -> 403 for extra guards after the ability check (`project_settings.py:160`, :196 in the pattern of `_validate_update_request` using 403 with `_ACCESS_DENIED_MESSAGE`). Both are used in the same function.
- Convention for router-level extra guards: check inline in the router before `update_settings` (budget guard `:159`, LiteLLM admin guard `:247`, `_check_permission` `:169`). The service layer holds no caller identity beyond a `user_id` string.
- Ownership data: `Settings.user_id` is indexed, nullable (`models/settings.py:286`). Project settings created by the system have `user_id="system"` (`settings.py:338`, :376). `setting_type` is `Optional` with default USER (`models/settings.py:296`); the transfer service treats a null as USER (`settings_transfer_service.py:88`). `UserSetting` ownership requires a non-empty `user_id`.

### Task Statements Checked Against the Code

- "update_project_setting only checks Ability(user).can(WRITE, ProjectSetting(setting))": holds (`project_settings.py:152-158`), plus the budget guard at :159 and per-type validation at :164; none of them looks at the stored `setting_type` or at ownership.
- "(owner, or admin of setting.project_name; superadmin passes too)": holds (`ability.py:178-182`, `models/settings.py:449-460`, `ability.py:271`).
- "SettingsService.update_settings (~line 525) sets setting_type = PROJECT unconditionally": holds (`settings.py:525-526`).
- "Plain member got 401": holds. WRITE does not include SHARED_WITH, so `can` is false and `raise_access_denied` returns 401.
- "Owner got 200": holds for owner of a USER row (OWNED_BY via `ProjectSetting.is_owned_by`).
- "The UI never triggers this path; API-only": unverified (codemie-ui is outside this repository; the openapi baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json` lists the route).
- Not stated in the ticket: the non-owner path also reaches `_check_permission` and `transfer_setting` when the request changes `project_name`, so a personal integration of user A can be moved to another project in the same call.
- Not stated in the ticket: the owner of a USER row whose `project_name` differs from any project they administer also passes the ability check (OWNED_BY), as the ticket expects for AC 2.

### Consumers of Changed Contracts

The contract is "PUT /v1/settings/project/{id} on a row with stored type USER".
- `routers/project_settings.py:147` (only route); `tests/codemie/rest_api/routers/test_project_settings.py:167` and `test_project_settings_project_change.py` (5 tests) exercise it.
- `SettingsService.update_settings` is shared with `user_settings.py:249` and `settings.py:367`; the router guard would not change them. `upsert_project_setting` passes PROJECT only for rows already PROJECT-typed by lookup.
- Integration clients that update a USER-typed row via the project endpoint as owner (AC 2) are the legitimate consumers; no other in-repo caller of this route found (searched `src` and `tests` for `settings/project`, `update_project_setting`).
- Provisioning or enterprise callers of `update_settings`: searched `src` for `.update_settings(` and found only the three above. The enterprise package is mounted outside this repo (not searched).

### Architecture and Layers Affected

- REST router: `routers/project_settings.py` (`update_project_setting`).
- Service: `service/settings/settings.py` (`update_settings`, `get_setting_ability`).
- Model/permission: `models/settings.py` (`ProjectSetting`, `UserSetting`, `Settings`), `core/ability.py` (PERMISSIONS table).
- Persistence: `Settings` SQLModel row (`setting_type`, `user_id`, `project_name`).

### Integration Points

- `Ability` (236 callers codebase-wide; PERMISSIONS table is shared).
- `SettingsTransferService.transfer_setting` after `update_settings` (:185).
- `transactional_session` (`clients/postgres`) wraps update and transfer only when `project_changed`.
- LiteLLM cache clear in `_clear_litellm_user_credentials_cache_if_needed` (`settings.py:538`) uses the new `setting_type`.

### Patterns and Conventions

- `Owned` subclasses with `is_owned_by/is_managed_by/is_shared_with` plus PERMISSIONS dict (`ability.py`).
- Routers do `try: ... except ExtendedHTTPException: raise` so a guard raised inside the `try` passes through unchanged (`project_settings.py:186-187`).
- Ruff-formatted, no inline explanatory comments wanted by the user (memory note); line length per `.ai-run/guides/standards/code-quality.md`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/development/security-patterns.md`, `.ai-run/guides/api/endpoint-conventions.md`, `.ai-run/guides/api/rest-api-patterns.md`, `.ai-run/guides/development/error-handling.md`, `.ai-run/guides/testing/testing-api-patterns.md` exist and are relevant by topic; they were not read in full by this analysis. No guide covers personal-to-project conversion specifically.

### Architectural Decisions
- None found for personal vs project integration conversion (no ADR; no inline comment explaining `settings.py:525-528`).

### Derived Conventions
- Authorization lives in the router (Ability plus inline guards); the service trusts the router.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/rest_api/routers/test_project_settings.py:167` `test_update_project_setting_logs_on_success` (200 path), ms_teams update tests (:310, :347).
- `tests/codemie/rest_api/routers/test_project_settings_project_change.py` (5 tests): project change path, `_check_permission` ordering, transfer call order.
- `tests/codemie/service/settings/test_settings_service.py` (72 test/helper markers): `update_settings` behaviour with USER and PROJECT rows built as `Settings(..., setting_type=SettingType.USER|PROJECT)`.
- `tests/codemie/rest_api/routers/test_user_settings.py`, `test_user_settings_crud.py`: sibling user endpoint.
- `tests/codemie/service/settings/test_settings_handler.py`: handler chain.
- `tests/enterprise/litellm/test_credentials.py`: calls `update_settings`/`get_setting_ability`.
- `tests/codemie/rest_api/routers/test_contract_baseline.py`, `router_guards/baselines/openapi.json`: contract baselines include this route.
- No test found for a non-owner converting a USER row, nor for an owner converting one.

### Testing Framework and Patterns
- pytest with `@pytest.mark.anyio`, `anyio_backend` fixture returning `'asyncio'`; a FastAPI app with `router` included and `httpx.AsyncClient(ASGITransport(app))` (`test_project_settings.py:21-31`).
- Heavy `unittest.mock.patch` of `codemie.rest_api.routers.project_settings.Settings`, `...Ability`, `SettingsService.get_setting_ability`, `SettingsService.update_settings`, and `LocalIdp.authenticate` (returns `User`). `project_change` tests add `extended_http_exception_handler` to the app and helpers `_admin_user()` (is_admin True) and `_regular_user()` (project-a).
- Most existing tests patch `Ability` so `can` returns True, and `Settings` as a `MagicMock`, so `Settings.get_by_id(...).setting_type` and `.user_id` are `Mock`/`MagicMock` attributes in those tests unless set.
- Memory note: tests match the area style (mock-based), no real-Postgres tests where the area has none.

### Coverage Gaps
- No test of ownership on conversion in either direction; no test where `Ability` is real for this router.

---

## 5. Configuration and Environment

### Environment Variables
- None specific to this behaviour found.

### Configuration Files
- `customer_config` feature `personalLiteLLMIntegrations` (`user_settings.py:63`) governs personal LiteLLM only; not on this path.

### Feature Flags and Deployment Concerns
- None found for this path. Endpoint is in the OpenAPI baseline (`tests/codemie/rest_api/router_guards/baselines/openapi.json`); a changed response set for the route would show there.

---

## 6. Risk Indicators

- Existing mechanism kept in place: the ability wrapper for the project endpoint is chosen from the endpoint argument, not the stored type (`settings.py:303`); `get_setting_ability` has three callers (see Existing Implementations).
- `ProjectSetting` ownership is `user_id == user.id` with no non-empty check, unlike `UserSetting` (`models/settings.py:454` vs :437). A row with null `user_id` and a caller with a null/empty id would compare equal only if both are None; system-created rows use `"system"`.
- Stored `setting_type` can be null (legacy; `Optional`), treated as USER in `settings_transfer_service.py:88` but `update_settings` and ability code do not handle it explicitly.
- Same function mutates the type in both directions: `user_settings.py:249` converts PROJECT to USER for any row whose `user_id` is the caller (owner of a project integration who is no longer admin). It is the reverse of this ticket and not in its scope.
- Project-change path: a non-owner can also combine conversion with a `project_name` change (`_check_permission` on the target and `transfer_setting`), so any guard placed after `update_settings` is too late: the DB write is inside the call.
- Existing router tests patch `Settings`, `Ability` and `get_setting_ability` with `MagicMock`, so attribute reads on the stored row (`setting_type`, `user_id`) yield Mock objects there; a new guard reading them can change the outcome of existing tests (`test_project_settings.py:167`, `test_project_settings_project_change.py` x5). Speculative: these tests may need the stored row set explicitly.
- Status code convention is mixed: 401 via `raise_access_denied` versus 403 via `raise_forbidden`; the ticket says "rejected" without a code.
- `update_settings` signature is shared with `update_user_setting` and `upsert_project_setting`; Speculative: a guard in the service would affect both; a guard in the router affects only this endpoint.
- Platform admin and Project Admin reach this path through different Ability roles (ADMIN, MANAGED_BY); both must be covered by the rule, and `Ability.can` returns True on any of them.
- Settings with `setting_type=USER` and `user_id` of a different user also reach `update_project_setting` for ability check as `ProjectSetting`, so `READ`/`DELETE` through the `/settings/project/{id}` DELETE route have the same shape (not examined in detail: `delete_project_setting` at `project_settings.py:206`).
- Guide gap: no guide documents personal vs project integration rules.

---

## 7. Summary for Complexity Assessment

The vulnerable path is a single route, `update_project_setting` in `routers/project_settings.py`, which authorizes with a `ProjectSetting` wrapper chosen by the endpoint and then calls the shared `SettingsService.update_settings`, which writes `setting_type = PROJECT` without looking at the stored type or the caller. Layers touched: REST router, settings service, and possibly the `Owned` models/ability table. Related reads of a converted row are in the settings handler chain, project listings and assistant integration lookup, all of which already treat PROJECT rows as usable by every project member.

Technical novelty is low: the router already has the pattern of inline extra guards (budget, LiteLLM, project transfer) and error helpers for 401/403, and `test_setting` shows an existing example of selecting the ability wrapper from the stored `setting_type`. The existing service has 3 callers of `update_settings` with different semantics; the type flip also happens in the reverse direction in `update_user_setting`.

Test posture: five router tests and a large service test file exist and are mock-based; none covers ownership on conversion. Existing router tests mock `Settings` and `Ability`, so a new test for AC 1 and AC 2 in that area style needs the stored row's `setting_type` and `user_id` set. Key risks: the project-change variant of the same request, the 401/403 choice, legacy null `setting_type`, and the unchanged reverse-direction path in `update_user_setting`.

---

## 8. External References

None named by the task
