# Service accounts get no email-named project (EPMCDME-17053, backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** First login of a service_account / external / provisioning-client user no longer leaves an ownerless email-named Application behind; regression tests cover creation, user-type selection and project creation for service accounts.

**Architecture:** `create_user_from_idp` stops adding `db_user.email` to the projects ensured when the existing predicate `is_personal_project_excluded_account` says the account is excluded. Everything else is test coverage and stale wording.

**Tech Stack:** Python, FastAPI, SQLModel, pytest + pytest-asyncio (mock-based, as neighbouring tests).

Commit per task using the repository's existing convention. No explanatory code comments (repo preference). Tests mock-based, no real Postgres.

## Acceptance criteria

- [ ] `create_user_from_idp` does not request an email-named project for user_type `service_account`, `external`, or a registered provisioning client (stored as `regular`).
- [ ] `create_user_from_idp` still requests the email-named project for a regular user; the later personal-project step is unchanged.
- [ ] `_ensure_projects_exist` is directly tested (creates each name, swallows per-name failure, commits).
- [ ] Admin user_type update accepts `service_account`, `regular`, `external` (normalised to lowercase), and still rejects other values with 400.
- [ ] The 400 text and the docstrings/comments at `user_management_service.py:1262`, `:1330`, `user_management_router.py:247`, `models/user_management.py:256` name all three types; the OpenAPI baseline matches.
- [ ] `create_shared_project_in_session` tests run with a real `user_type="service_account"` actor.

**Spec:** inline requirements; research in `technical-analysis.md` (same directory).

## Global Constraints

- Reuse `is_personal_project_excluded_account(user_id, user_type)` from `src/codemie/rest_api/security/user_type_validator.py:46`; do not add a new predicate.
- Regular-user behaviour at the call site is unchanged.
- Out of scope: cleanup of existing bare email-named projects in deployed data (follow-up note below), UI, legacy JWT `ensure_project_exists`, enterprise migration, personal-project behaviour for regular users.

## Review Focus

- Provisioning client stored as `regular`: must be excluded via the registry, not only via type (Task 1 test).
- Service account whose `project_names` already contains its email: `user_projects` row and explicit project still created (Task 1 test).
- Mixed-case / padded `Service_Account ` input to the admin edit: persisted as `service_account` (Task 2 test).
- Router docstring edit changes OpenAPI text: baseline must be regenerated, not hand-edited (Task 2).

---

### Task 1: Skip the email-named project for excluded accounts

**Files:**
- Modify: `src/codemie/service/user/authentication_service.py:240-242`
- Test: `tests/codemie/service/user/test_authentication_service.py` (`TestCreateUserFromIdp`, :417-607; add `TestEnsureProjectsExist` next to it)

**Interfaces:**
- Consumes: `is_personal_project_excluded_account(user_id: str, user_type: str) -> bool` (import inside the method, as the other call sites in this file do, :393/:744).
- Produces: `create_user_from_idp` calls `_ensure_projects_exist(idp_user.project_names)` for excluded accounts and `_ensure_projects_exist(idp_user.project_names + [db_user.email])` otherwise. `_ensure_projects_exist(project_names: list[str]) -> None` is unchanged.

- [ ] **Step 1: Write failing tests** in `TestCreateUserFromIdp`, reusing the structure of `test_create_user_from_idp_success` (:421-485: patched `user_repository`, `user_project_repository`, `user_kb_repository`, `config`, and `_ensure_projects_exist` as `AsyncMock`):
  - `test_create_user_from_idp_skips_email_project_for_excluded_type`, parametrized `user_type` in `["service_account", "external"]`, `project_names=["project1"]`: `mock_ensure.assert_called_once_with(["project1"])`.
  - `test_create_user_from_idp_skips_email_project_for_provisioning_client`: `user_type="regular"`, patch `codemie.rest_api.security.user_type_validator.provisioning_clients_config` so `find_client` returns a truthy object; assert `["project1"]` only.
  - `test_create_user_from_idp_ensures_email_project_for_regular`: `user_type="regular"`, assert `["project1", "<email>"]`.
  - `test_create_user_from_idp_service_account_email_in_project_names`: `project_names=[email]`; assert `mock_ensure` called with `[email]` and `aadd_project` still called once for it.
- [ ] **Step 2: Write `TestEnsureProjectsExist`** (patch `codemie.clients.postgres.get_async_session` with the async context manager style used elsewhere in this file, and `codemie.service.user.authentication_service.application_repository`): 
  - `test_creates_each_name_and_commits`: `aget_or_create` awaited per name, `session.commit` awaited once.
  - `test_per_name_failure_is_swallowed`: first `aget_or_create` raises, second still called, commit still awaited.
- [ ] **Step 3: Run** `poetry run pytest tests/codemie/service/user/test_authentication_service.py -k "CreateUserFromIdp or EnsureProjectsExist" -v`. Expected: the three skip tests FAIL (email still passed); the rest pass.
- [ ] **Step 4: Implement** in `create_user_from_idp`: build `projects_to_ensure = list(idp_user.project_names)`, append `db_user.email` only when `not is_personal_project_excluded_account(db_user.id, db_user.user_type)`, pass it to `_ensure_projects_exist`. Keep the existing `test_create_user_from_idp_success` assertion (user_type `human` is not excluded) and the three `_ensure_projects_exist` patches as they are.
- [ ] **Step 5: Run** the same command plus the whole file. Expected: PASS.

Test-first: yes — `create_user_from_idp` with `user_type="service_account"` must call `_ensure_projects_exist(["project1"])` without the email; currently it passes the email and the test fails.

### Task 2: User-type selection accepts and describes all three types

**Files:**
- Modify: `src/codemie/service/user/user_management_service.py:1262` (message) and `:1330` (docstring); `src/codemie/rest_api/routers/user_management_router.py:247` (docstring); `src/codemie/rest_api/models/user_management.py:256` (comment, drop it or name all three)
- Modify (regenerated): `tests/codemie/rest_api/router_guards/baselines/openapi.json` (text near :43001)
- Test: `tests/codemie/service/user/test_user_management_service_field_editability.py` (:332-425)

**Interfaces:**
- Consumes: `_describe_valid_user_types() -> str` from `codemie.rest_api.security.user_type_validator` (sorted list rendered as `'external', 'regular', or 'service_account'`); `VALID_USER_TYPES` already imported in `user_management_service.py`.
- Produces: `_validate_user_type` 400 `details` = `f"user_type must be one of {_describe_valid_user_types()}"`; message `"Invalid user_type"` unchanged.

- [ ] **Step 1: Write failing/regression tests** in the field-editability file:
  - Extend `test_user_type_invalid_value_rejected` (:407-425) to also assert `"service_account" in exc_info.value.details` (this fails first).
  - `test_validate_user_type_normalises_and_accepts_all_valid_types`: parametrize `("Service_Account ", "service_account")`, `("REGULAR", "regular")`, `("external", "external")`, `(None, None)` against `UserManagementService._validate_user_type`.
  - `test_user_type_update_local_mode_accepts_regular_and_external`: parametrize over `regular` and `external`, modelled on `test_user_type_update_local_mode_accepts_service_account` (:332-397) and assert `mock_repo.update.call_args[1]["user_type"]` equals the value.
  - In the existing service_account test keep the persisted-value assertion (:396-397) as the persistence check.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/service/user/test_user_management_service_field_editability.py -v`. Expected: only the extended invalid-value test FAILS.
- [ ] **Step 3: Implement** the message change and replace the stale `'regular' or 'external'` wording in the four listed places with all three types.
- [ ] **Step 4: Regenerate the baseline** with `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`; review the diff is limited to the router docstring text (and nothing else), then run `poetry run pytest tests/codemie/rest_api/router_guards -v`. Expected: PASS.
- [ ] **Step 5: Run** the field-editability file again. Expected: PASS.

Test-first: yes — invalid-value rejection must name `service_account` in `details`; currently the text says only `'regular' or 'external'`.

### Task 3: Real service-account actor in project-creation tests

**Files:**
- Modify: `tests/codemie/service/project/test_project_service.py:334-337` (the `service_account` fixture)

**Interfaces:**
- Consumes: `security.User(..., user_type=...)` (default `'regular'`).
- Produces: fixture `service_account` returns `User(id="svc-1", username="svc", email="svc@example.com", is_admin=False, user_type="service_account")`.

- [ ] **Step 1: Set `user_type="service_account"`** on the fixture; add one assertion in `test_creates_project_without_creator_membership` (:361) that `service_account.user_type == "service_account"` is the actor passed to `create_shared_project_in_session`, so the fixture cannot silently regress to a regular user.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/service/project/test_project_service.py -v`. Expected: PASS (project creation must not depend on actor type; if a test fails, the failure is a real finding to report, not to paper over).

Test-first: no — test-only fixture correction; existing `TestCreateSharedProjectInSession` tests are the verification.

## Follow-up note (not in this plan)

Service accounts created before this fix already have a bare email-named shared Application (0 users, no creator) in deployed data. No cleanup/migration is planned here; track as a separate ticket (candidate: one-off script deleting shared applications with `created_by IS NULL`, no `user_projects` rows, whose name equals the email of a service_account/external user).

## Negative-constraint pass

- No real-Postgres tests, no code comments, no feature docs: Tasks 1-3 are mock-based and add no docs.
- Regular users' personal-project behaviour unchanged: Task 1 keeps the email for non-excluded accounts and asserts it.
- No migration/cleanup of existing data: only the follow-up note.
- No UI, legacy JWT, or enterprise changes: no task touches them.
