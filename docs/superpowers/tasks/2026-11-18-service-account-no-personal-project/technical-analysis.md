# Technical Research

**Task**: auth personal-project service-account user_type (EPMCDME-17053)
**Generated**: 2026-11-18
**Research path**: codegraph

---

## 1. Original Context

Implement backend fix so that personal project is not created for service accounts. Make sure regression tests cover service account creation, user type selection, and project creation for service accounts

Background from the requester's earlier investigation of EPMCDME-17053 (verify against the code, do not trust blindly): AuthenticationService.create_user_from_idp (src/codemie/service/user/authentication_service.py, ~line 242) calls `_ensure_projects_exist(idp_user.project_names + [db_user.email])` for every first-login user. `_ensure_projects_exist` calls application_repository.aget_or_create(session, name), which creates an Application with project_type='shared', created_by=None and no user_projects rows. For service_account/external users the later personal-project step (PersonalProjectService.ensure_personal_project_async, guarded by is_personal_project_excluded_account in user_type_validator.py) is skipped, so the bare email-named shared project is left behind with 0 users / 0 admins / empty created_by. Tests of create_user_from_idp mock out `_ensure_projects_exist`. Also look at: the user_type update path (UserManagementService._validate_user_type, user_type edit flow and its 'regular'/'external' wording), other callers that create email-named applications (ensure_project_exists, legacy JWT mode, sync paths, codemie-enterprise migration), and existing tests for these.

---

## 2. Codebase Findings

### Existing Implementations

**Purpose A: a first-login IDP user gets its Application records, but a service account must not end up with an email-named project.**
- `src/codemie/service/user/authentication_service.py:182-247` `create_user_from_idp`: creates `UserDB` with `user_type=idp_user.user_type` (:208), adds `user_projects` rows for `idp_user.project_names` (:229-231), KB rows, then at :242 calls `_ensure_projects_exist(idp_user.project_names + [db_user.email])` with no user_type check. Comment at :237-238 says personal-project creation is deferred until after commit.
- `authentication_service.py:250-270` `_ensure_projects_exist`: opens its own `get_async_session()`, loops `application_repository.aget_or_create(session, name)` (per-name failures logged and swallowed), commits.
- `src/codemie/repository/application_repository.py:988-1013` `aget_or_create`: case-insensitive lookup, else `acreate(session, name)`; `create` (:205-251) defaults `project_type="shared"`, `created_by=None`. Confirms the requester's description: bare shared project, no `user_projects` row.
- Callers of `create_user_from_idp`: `_create_first_login_user` (:429-452) <- `_create_or_recover_user` (:490) <- `authenticate_persistent_user` (:521-604). Requester's mechanism holds.
- Callers of `aget_or_create`: `authentication_service.py:263` and `:336`, `personal_project_service.py:295` (new personal project; then sets type personal/created_by), `enterprise/migration/coordinator.py:100`.

**Purpose B: a service/external account must not get a personal project (already implemented at six sites).**
- `src/codemie/rest_api/security/user_type_validator.py:35-52`: `SERVICE_ACCOUNT_USER_TYPE`, `VALID_USER_TYPES={'regular','external','service_account'}`, `PERSONAL_PROJECT_EXCLUDED_USER_TYPES={'external','service_account'}`, `is_personal_project_excluded(user_type)`, `is_personal_project_excluded_account(user_id, user_type)` (= excluded type OR registered provisioning client, which is stored as 'regular'), `is_service_account(user|UserDB)`.
- Guarded sites: `authentication_service.py:396` (`_finalize_authentication`, used by persistent, teams_sender, dev_header), `:803` (`authenticate_and_login`, local), `registration_service.py:215,252` (uses `is_personal_project_excluded(user_type)`), `personal_project_service.py:128` (`reconcile_personal_project_on_email_change`), `enterprise/migration/coordinator.py:102-104` (delegates, guarded in enterprise lib; not in this repo), analytics gate `cli_analytics_project_gate.py:233,249,309,356,413` and `cli_analytics_ingest_route.py:161`.
- Not guarded: `authentication_service.py:242` (the leak), see Purpose A.

**Purpose C: other places that create an Application named by the user.**
- `authentication_service.py:324-340` `ensure_project_exists(project_name)`: `aget_or_create` + commit, errors swallowed. Only caller: `rest_api/security/user_providers/legacy_jwt.py:56-58`, with `idp_workspace_name(user.project_names, user.username)` (`rest_api/security/user.py:73-75`): the **username**-named workspace, created only when the IDP claims list it; no user_type check there. This is IDP-claims mode (`ENABLE_USER_MANAGEMENT=False`), no DB user.
- `personal_project_service.py:229-315` `_create_personal_project`: converts an existing same-name shared project to personal only if no other user has access; otherwise `aget_or_create` + sets personal/created_by. This is how a regular user's bare email project from `_ensure_projects_exist` is later converted.
- `enterprise/migration/coordinator.py:98-104`: `get_or_create_application` and `ensure_personal_project` are thin adapters handed to `codemie_enterprise.migration.KeycloakMigrationCoordinator` (library outside the repo; the sequence of calls it makes is not visible here).
- `dev_header` user creation (`authentication_service.py:647-697`) creates a user with default `user_type` and no project creation.

**Purpose D: an admin chooses a user's type (user type selection).**
- `src/codemie/service/user/user_management_service.py:1239-1265` `_validate_user_type`: normalizes lowercase and checks `normalized not in VALID_USER_TYPES` (so `service_account` IS accepted), but the 400 `details` text says "user_type must be either 'regular' or 'external'" (:1262).
- Same stale wording: `user_management_service.py:1330` (docstring), `rest_api/routers/user_management_router.py:247` (docstring that feeds OpenAPI), `rest_api/models/user_management.py:256` comment, baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json:43001` (contains the router docstring text).
- Flow `update_user_fields` (:1300-1423): `_resolve_actor_admin_status`, `_validate_user_type`, `_validate_conditional_field_editability` (user_type editable by admin in local mode only, :1219-1224), `_build_updates_dict` (:1049-1050), `update_user`, commit, `invalidate_user_from_cache`. No personal-project or Application handling occurs when `user_type` changes (an existing personal project of a user switched to service_account/external is untouched; a user switched back gets one on the next `_finalize_authentication`).
- `UserDB.user_type` default `"regular"` (`rest_api/models/user_management.py:51`); `security.User.user_type` default `'regular'` (`rest_api/security/user.py:149`).
- IDP-side validation: `user_type_validator.validate_user_type` (:69-143) uses `_describe_valid_user_types()` (built from `VALID_USER_TYPES`), so its message is current.

**Other touched code**: `src/codemie/service/project/project_service.py` (shared-project creation; no service-account/user_type logic found by search, only personal-project guards at :145-147, :424, :739-751).

### Decisions Already in the Code

- Excluded types for personal projects are a single set `PERSONAL_PROJECT_EXCLUDED_USER_TYPES` + helper predicates (`user_type_validator.py:39-52`); convention: call the predicate at each creation site (six sites). Prior ticket: `docs/superpowers/tasks/2026-08-27-skip-private-project-creation/` (spec/analysis). Its code-review noted "all 6 sites"; `_ensure_projects_exist` was not one of them. Reason for omission: none found.
- Provisioning clients authenticate stored as 'regular' and are excluded via registry lookup (`user_type_validator.py:46-52`); `is_personal_project_excluded` (type only) vs `is_personal_project_excluded_account` (type + client id) are both in use: registration and analytics use the type-only form, authentication/reconcile use the account form.
- `_ensure_projects_exist` is done in `create_user_from_idp` "to avoid per-request overhead in the auth hot-path" (`authentication_service.py:240-241`); email-named project is ensured so that later personal-project conversion finds an existing record (`_create_personal_project` handles both existing and missing).
- `_ensure_projects_exist` uses its own session, separate from the user-creation session, and swallows per-project errors.
- `_validate_user_type` was changed to reuse `VALID_USER_TYPES` (test `test_user_type_update_local_mode_accepts_service_account`, `tests/.../test_user_management_service_field_editability.py:332`), but its message wasn't updated. The prior ticket's review (`2026-08-26-add-service-account-user-type/code-review-final.json`) flagged the stale wording.
- Data stored in the wrong direction for the task: `create_user_from_idp` has `idp_user.user_type` (raw IDP value, validated upstream) and `db_user.user_type` at the call site; provisioning-client status requires `idp_user.id` lookup.

### Task Statements Checked Against the Code

- "`create_user_from_idp` (~line 242) calls `_ensure_projects_exist(idp_user.project_names + [db_user.email])` for every first-login user": **holds** (`authentication_service.py:242`, no condition).
- "`aget_or_create` creates project_type='shared', created_by=None, no user_projects rows": **holds** (`application_repository.py:210-211`, :1003; `user_projects` rows are added only for `idp_user.project_names`, :229-231, and `aadd_project` for the personal one only in `_create_personal_project`).
- "later personal-project step guarded by `is_personal_project_excluded_account`, skipped for service_account/external": **holds** (`authentication_service.py:396`, :803; `personal_project_service.py:128`).
- "bare email project left with 0 users / 0 admins / empty created_by": **holds** for a service account whose `idp_user.project_names` does not include its email. If `project_names` includes the email, a `user_projects` row exists (:229-231) and `aget_or_create` is called with the same name twice in the list (idempotent).
- "Tests of `create_user_from_idp` mock out `_ensure_projects_exist`": **holds** (`tests/codemie/service/user/test_authentication_service.py:458-461, 537-540, 589-592`); `test_create_user_from_idp_service_account` (:566-607) only asserts `user_type == "service_account"` on the created `UserDB`; `test_create_user_from_idp_success` asserts `mock_ensure.assert_called_once_with(["project1","project2","idp@example.com"])` (:485), which uses `user_type="human"` (not a valid type).
- "user_type update path has 'regular'/'external' wording": **holds** (`user_management_service.py:1262`, :1330; router :247; model :256; openapi baseline :43001). The accepted set already includes `service_account`; wording is the only inconsistency. Test `test_user_type_invalid_value_rejected` (`test_user_management_service_field_editability.py:407-425`) asserts "regular" and "external" appear in `details`.
- "Other callers that create email-named applications (ensure_project_exists, legacy JWT, sync paths, enterprise migration)": `ensure_project_exists`/legacy JWT creates **username**-named workspaces, not email-named; sync path `sync_idp_user_profile` (:296-321) creates no Application (updates email/name/picture only); enterprise migration calls `get_or_create_application` and `ensure_personal_project` through adapters (coordinator.py:98-104); the library's call order **unverified** (codemie_enterprise not in repo; reading its migration coordinator would settle it).

### Consumers of Changed Contracts

- `AuthenticationService._ensure_projects_exist(project_names: list[str])`: single caller `authentication_service.py:242`; tests patch it by dotted path in three tests (`test_authentication_service.py:458, 537, 589`); `test_create_user_from_idp_success:485` asserts its exact argument list including the email.
- `create_user_from_idp(session, idp_user)`: callers `_create_first_login_user` (:448); race-recovery in `_create_or_recover_user` (:490+); tests `TestCreateUserFromIdp` (:417-607) and first-login tests of `authenticate_persistent_user` (:838, :1122, :1194, :1645; budget-sync tests use `user_type="service"`).
- Email-named Application records read downstream: `_has_personal_project_complete`, `_create_personal_project` (conversion path for regular users), `resolve_membership_default(projects, email)` (`authentication_service.py:158,405`), `User.effective_default_project`/`derive_idp_default_project` (`user.py:78-85, 214-224`), `personal_workspace_names` (`user.py:88-96`, budget attribution). Whether any of these reads the bare email project for a service account: not traced (they key on `user_projects` membership or names, not on Application existence).
- `_validate_user_type` / `VALID_USER_TYPES`: consumers `user_management_service.py:1258`, `user_type_validator.validate_user_type` (:69+), `_describe_valid_user_types` (:63-66), tests `test_user_type_validator.py:231-232` (set equality), field-editability tests, openapi baseline test (router docstring text in `tests/codemie/rest_api/router_guards/baselines/openapi.json`).
- `ensure_project_exists`: caller `legacy_jwt.py:58`; tests patch it (`test_legacy_jwt_default_project.py:42,107,128,154`, `test_attribution_matrix_end_to_end.py:124`, `test_hosted_identity_seam.py:123`) and unit tests `test_authentication_service.py:1336-1375`.

### Architecture and Layers Affected

- Service layer: `AuthenticationService` (`service/user/authentication_service.py`), `UserManagementService` (`service/user/user_management_service.py`), `PersonalProjectService`.
- Security/validation helper layer: `rest_api/security/user_type_validator.py`.
- Repository layer: `application_repository` (`aget_or_create`/`acreate`), `user_project_repository`.
- API layer text only: `rest_api/routers/user_management_router.py:247` (docstring flows into OpenAPI baseline).

### Integration Points

- IDP (Keycloak etc.) -> `idp_user` -> `authenticate_persistent_user` -> `create_user_from_idp`; enterprise IDP `validate_user_type` duplicate in `codemie_enterprise` (outside repo).
- `provisioning_clients_config` (`configs/provisioning_clients_config.py`) consulted by `_is_provisioning_client`.
- Budget bootstrap after commit: `_sync_budget_for_idp_projects` over `idp_user.project_names` (:273-293), `budget_service.schedule_default_budget_provisioning` (:587-590).
- `codemie_enterprise.migration.KeycloakMigrationCoordinator` via `enterprise/migration/coordinator.py`.

### Patterns and Conventions

- Static async methods on `AuthenticationService`, separate isolated sessions for post-commit side effects, errors logged not raised.
- Tests: pytest + `pytest.mark.asyncio`, `AsyncMock`/`patch` of module-level repositories by dotted path, Arrange/Act/Assert comments, `@pytest.mark.parametrize("user_type", ["external", "service_account"])` is the existing idiom for exclusion tests.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/architecture/service-layer-patterns.md`, `data/repository-patterns.md`, `testing/testing-patterns.md`, `testing/testing-service-patterns.md`, `development/security-patterns.md` exist and are the relevant guides (not opened in detail; code exploration was primary). No guide covers personal-project exclusion.

### Architectural Decisions
- Prior tasks in-repo: `docs/superpowers/tasks/2026-08-27-skip-private-project-creation/` (spec/analysis of the six-site exclusion, states regular and unknown types keep unconditional creation) and `docs/superpowers/tasks/2026-08-26-add-service-account-user-type/` (adds `service_account`; code-review-final.json flags the `_validate_user_type` hardcoded wording; code-review-deferred.md).
- `docs/superpowers/tasks/2026-11-18-service-account-no-personal-project/state.local.json` is this run's state.

### Derived Conventions
- Exclusion predicate called at the call site, not inside the creation function (except `reconcile_...`, which receives `user_type`).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/user/test_authentication_service.py`: `TestCreateUserFromIdp` (:417-607; `_ensure_projects_exist` always mocked), `ensure_project_exists` tests (:1336-1375), `_finalize_authentication` exclusion tests (:1499-1599 and :1920+, parametrized external/service_account), email-change reconcile with excluded type (:991-1071), first-login/race tests of `authenticate_persistent_user` (:838+, :1645+). No test exercises `_ensure_projects_exist` itself.
- `tests/codemie/service/project/test_personal_project_service.py`: `ensure_personal_project_async` create/skip/failure tests (:59+), reconcile skip for excluded types (:1078-1113).
- `tests/codemie/rest_api/security/test_user_type_validator.py` (31 matches; set contents :231, parametrize :277), `test_provisioning_access.py:126`.
- `tests/codemie/service/user/test_user_management_service_field_editability.py`: `test_user_type_update_local_mode_accepts_service_account` (:332-397), `test_user_type_invalid_value_rejected` (:407-425, asserts 'regular' and 'external' in details).
- `tests/codemie/service/user/test_registration_service.py`: user_type used 10 times (registration skip behavior not re-verified).
- Project creation: `tests/codemie/service/project/test_project_service.py`; its `service_account` fixture (:334-337) is a plain `User(id="svc-1")` with the default `user_type='regular'` (not a real service_account type), used by `TestCreateSharedProjectInSession` (e.g. `test_creates_project_without_creator_membership`, :361).

### Testing Framework and Patterns
- pytest, pytest-asyncio; mock-based (no real Postgres in this area, per neighbouring tests); async session context mocked via `_make_async_session_cm`.

### Coverage Gaps
- No test that `create_user_from_idp` for service_account/external avoids creating the email-named Application (always patched), and none for regular still creating it beyond the argument list at :485.
- No direct test of `_ensure_projects_exist` (session, per-name failure swallowing).
- No test of what `_validate_user_type` 400 message says for `service_account`-aware wording beyond the current 'regular'/'external' assertions.
- No test for user_type change on the update path and its effect on existing projects.
- Service-account-specific project creation tests do not exist; the fixture named `service_account` is a regular-type user.

---

## 5. Configuration and Environment

### Environment Variables
- `IDP_PROVIDER`, `ENABLE_USER_MANAGEMENT`, `ADMIN_USER_ID`, `ADMIN_ROLE_NAME`, `USER_PROJECT_LIMIT` (read in `create_user_from_idp`); `TEAMS_SERVICE_ACCOUNT_ID` (`configs/config.py:302`, default `codemie-teams-bot`); `EXTERNAL_USER_TYPE` (`config`, used by `User.is_external_user`).

### Configuration Files
- Provisioning clients registry (`configs/provisioning_clients_config.py`) governs `is_service_account` / `is_personal_project_excluded_account`.

### Feature Flags and Deployment Concerns
- `ENABLE_USER_MANAGEMENT` selects persistent vs legacy JWT provider; user_type edit only in local IDP mode. No migration/flag specific to this area found. Existing leftover bare email projects in deployed DBs: not found in code (no cleanup job located).

---

## 6. Risk Indicators

- `authentication_service.py:242` keeps an existing mechanism (`_ensure_projects_exist` creating the email-named shared project) in place for all types; its consumers/tests are listed in "Consumers of Changed Contracts"; three tests patch it by name and one asserts its exact argument list.
- `create_user_from_idp` tests use non-standard `user_type="human"`; `is_personal_project_excluded*` treats it as not excluded.
- Speculative: two predicates exist (`is_personal_project_excluded` type-only vs `is_personal_project_excluded_account` with provisioning-client lookup); the one chosen determines whether a 'regular'-stored provisioning client is covered at the `create_user_from_idp` site.
- Speculative: leftover bare email-named shared projects for already-created service accounts exist in deployed data; no cleanup path found in code.
- User_type update path does not touch projects; changing a user to/from an excluded type leaves existing personal projects as they are (`update_user_fields` has no project hook).
- Stale 'regular'/'external' wording is duplicated in five places, including the OpenAPI baseline file; a docstring edit at `user_management_router.py:247` alters the generated baseline text.
- Enterprise migration (`codemie_enterprise`) call order and any pre-creation of email-named applications are unverified (outside repo).
- Legacy JWT `ensure_project_exists` creates username-named workspaces without a user_type check; applicability to service accounts unverified.
- Mocking of `_ensure_projects_exist` hides the behaviour in existing tests; the service-account project-creation fixture in `test_project_service.py` is a regular-type user.

---

## 7. Summary for Complexity Assessment

The issue reproduces in code: `create_user_from_idp` (`authentication_service.py:242`) unconditionally passes the user's email to `_ensure_projects_exist` -> `aget_or_create`, which creates a shared, ownerless Application, while every other personal-project creation site (six, listed above) already consults `is_personal_project_excluded*`. The surface is small: one service method, its helper, and the existing predicate in `user_type_validator.py`. The user_type edit path already accepts `service_account` through `VALID_USER_TYPES`; only its error text, docstrings, a model comment and an OpenAPI baseline entry still say 'regular'/'external'.

Technical novelty is low: the exclusion predicate, parametrized `external`/`service_account` tests and mock style are all established. Layers: service and security-helper layers plus (text only) router docstring/OpenAPI baseline; repository and DB unchanged by anything found.

Test posture: exclusion logic is well covered at the other sites, but `create_user_from_idp` tests patch out `_ensure_projects_exist`, so the faulty path has no assertion, and one existing test asserts the exact argument list that includes the email. Key risks: choice of predicate (type-only vs account) at the call site, unverified enterprise migration behaviour, leftover data for existing service accounts, and the user_type-update message baseline.

---

## 8. External References

None named by the task.
