# IdP-sync management API (EPMCDME-16895) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** A `/v1/idp-sync/` API callable only by one configured service account, with idempotent user/membership operations and reads for the customer sync job.

**Architecture:** App-level FastAPI dependency (on `authenticate`) enforces the identity rule; a thin new service `IdpSyncService` orchestrates existing repositories and `ProjectAssignmentService`/`deactivate_user_flow` without touching existing services or switch locks. One transaction per call.

**Tech Stack:** FastAPI, SQLModel, pytest.

**Spec:** `docs/superpowers/tasks/2026-11-10-rbac-api-updates/spec.md`

Commit per task using the repository's existing convention (`git log`; ticket EPMCDME-16895). No AI footer.

## Global Constraints

- Setting `IDP_SYNC_SERVICE_ACCOUNT_USERNAME` (default `""`; matched against `user.username`, not the id) in `configs/config.py` next to `TEAMS_SERVICE_ACCOUNT_ID`; prefix `/v1/idp-sync/`; single user name only.
- Caller is the account iff `caller.id == setting` and `user_type == service_account`; empty setting fails closed (403 for all).
- Do not change `authenticate`, `TEAMS_SERVICE_ACCOUNT_ID`, `require_no_idp_lock`, inline 403s, or existing services/getters.
- No index/migration unless the load check (Task 8) fails. No personal project on create. Reactivation does not restore memberships.
- Errors via `ExtendedHTTPException`; service account recorded as actor in every activity event; license header on new files.
- Guides: `.ai-run/guides/api/rest-api-patterns.md`, `architecture/layered-architecture.md`, `data/repository-patterns.md`, `development/error-handling.md`, `testing/testing-patterns.md`.

## Review Focus

- Public routes (health, config) must stay public under the app-level guard (Task 2 test).
- Token claim cannot set `user_type`/`id` (Task 1 trace test).
- Retried/overlapping upsert and assign end as no-op, never 500 (Tasks 4, 5).
- Budget-provider failure persists nothing (Task 5).
- Reactivated user does not get old memberships back (Task 4).

## Tasks

### Task 1: Trace caller identity source (risk: user_type for token callers)

**Files:** Test: `tests/codemie/rest_api/security/test_service_account_identity.py`; read `rest_api/security/user_providers/`, `authentication.py:100-169`.

**Interfaces:** Produces: written conclusion in the test module docstring on where `user_type` and `id` come from.

Test-first: yes — a token whose claims include `user_type=service_account` or a foreign `sub`-mismatching id yields a user with `user_type` from the DB row only (test fails if a claim can set it).

- [ ] Trace how `authenticate` builds `User.id` and `user_type` for IdP-token callers; write the test above using the provider with a crafted claims dict. If a claim can control `user_type`, stop and return `needs_input` to the caller (security finding) instead of continuing.

### Task 2: Config and identity guard

**Files:**
- Modify: `src/codemie/configs/config.py` (near line 240); `src/codemie/rest_api/main.py:~1000` (`FastAPI(lifespan=lifespan, dependencies=[Depends(enforce_idp_sync_identity)])`)
- Create: `src/codemie/rest_api/security/idp_sync_guard.py`
- Test: `tests/codemie/rest_api/security/test_idp_sync_guard.py`

**Interfaces:** Produces: `is_idp_sync_account(user: User) -> bool` (False when setting empty); `async def enforce_idp_sync_identity(request: Request, user: User = Depends(authenticate))`; `require_idp_sync_account` dependency for the new router (403 unless `is_idp_sync_account`).

Test-first: yes — configured account on `/v1/assistants`-like route gets 403; same account on a route under `/v1/idp-sync/` passes; empty setting makes `require_idp_sync_account` 403 for all; admin and other service accounts get 403 on management routes; Teams bot unaffected.

- [ ] Write failing tests (AC1-AC3 plus guard behavior, with switch on and off for AC2).
- [ ] Implement: guard uses `request.scope["route"].path` (not `request.url.path`); acts only for the configured account; does not authenticate public routes (find how public routes avoid `authenticate`; the app-level dependency must not require a token for them — verify with a test on the health route).
- [ ] Add a test with non-empty `root_path` (AC4): account not refused on its own API, not admitted outside it.

### Task 3: Route-walk test

**Files:** Test: `tests/codemie/rest_api/security/test_idp_sync_guard_routes.py`

Test-first: yes — walks every route of the real app (as `guarded_app.py` builds it); for each non-`/v1/idp-sync/` authenticated route the configured account gets 403; fails if any route lacks the app-level dependency (AC4).

- [ ] Write the test, run, confirm pass after Task 2; fix guard if any route escapes.

### Task 4: Repository reads (include deactivated, stable id order)

**Files:**
- Modify: `src/codemie/repository/user_repository.py` (new methods only; `get_by_id` and `_apply_filters` untouched)
- Test: `tests/codemie/repository/test_user_repository_idp_sync.py`

**Interfaces:** Produces: `get_any_by_id(session, user_id) -> UserDB | None` (includes soft-deleted); `list_page_including_deleted(session, page: int, per_page: int) -> tuple[list[UserDB], int]` ordered by `UserDB.id`; `fetch_memberships_with_personal(session, user_ids: list[str]) -> dict[str, list[MembershipRow]]` where `MembershipRow(project_name, is_project_admin, is_personal)`; personal flag from `Application.project_type`; one IN-list query.

Test-first: yes — deactivated user appears in the page and in `get_any_by_id`; page order stable by id across pages; memberships carry `is_personal` and `is_project_admin`.

- [ ] Write failing tests, implement, run.

### Task 5: Request/response models (contract)

**Files:** Create `src/codemie/rest_api/models/idp_sync.py`; Test `tests/codemie/rest_api/models/test_idp_sync_models.py`

**Interfaces:** Produces: `IdpUserUpsertRequest(id, email, username, name, is_admin: bool)`, `IdpAssignRequest(project_name, is_project_admin: bool = False)`, `IdpUserResponse(id, is_active, profile fields, is_admin, memberships: list[IdpMembership])`, `IdpUserPage(items, total, page, per_page)`. Every field carries `Field(description=..., examples=...)`; `is_admin` described as advisory.

Test-first: yes — malformed id/empty project name raises validation error; every field has a description (AC14, AC16).

- [ ] Write tests, implement models.

### Task 6: IdpSyncService - upsert, assign, unassign, deactivate

**Files:**
- Create: `src/codemie/service/user/idp_sync_service.py`
- Test: `tests/codemie/service/user/test_idp_sync_service.py`

**Interfaces:** Consumes Task 4 methods, `user_repository`, `user_project_repository`, `application_repository.get_or_create`, `ProjectAssignmentService` (`assign_user_to_project`, `update_user_project_role`, `remove_user_from_project`), `UserManagementService.deactivate_user_flow`, `invalidate_user_from_cache`. Produces: `upsert_user(session, actor, req) -> IdpUserResponse`, `assign(session, actor, user_id, req) -> IdpUserResponse`, `unassign(session, actor, user_id, project_name) -> None`, `deactivate(session, actor, user_id) -> None`, `get_user(...)`, `list_users(...)`.

Test-first: yes — see per-behavior failing tests below.

- [ ] Upsert tests: creates without personal project; updates differing profile/admin; admin if `is_admin` or `id == ADMIN_USER_ID` (AC6); demoting last admin rejected, with the `count_active_admins` check under a row lock (`SELECT ... FOR UPDATE` on admin rows); soft-deleted user reactivated (`is_active=True`, `deleted_at=None`), old `UserProject` rows not restored; activity events have `actor` = service account. Implement with the insert inside a savepoint; `IntegrityError` (id or email) continues as update. Test simulates the conflict (AC15).
- [ ] Assign tests: missing project created via `get_or_create`; repeat is no-op (409 from `ProjectAssignmentService` treated as done); differing admin flag updated via `update_user_project_role`; personal project rejected; budget sync and activity event invoked with service account as actor; a budget-provider exception triggers rollback and re-raises so nothing persists (AC7). The service never commits partially; commit once at the end.
- [ ] Unassign tests: non-member (404) treated as done; budget cleaned; personal project rejected; project creator rejected with 409 `ExtendedHTTPException` (AC8).
- [ ] Deactivate tests: unknown user -> not-found; service account itself refused; last admin refused; goes through `deactivate_user_flow`; `invalidate_user_from_cache` called only after the flow returns (order asserted with mocks) (AC9). First confirm no switch lock on `deactivate_user_flow`'s path.
- [ ] Parallel-demotion test: two sessions demoting the last two admins - lock call asserted and the second sees count 1 and is rejected (AC15). Trace and record the DB isolation level used in tests/config in the module docstring; do not assume it.
- [ ] Reads tests: unknown user -> not-found; list includes deactivated, id order (AC10, AC14).

### Task 7: Router, registration, baselines

**Files:**
- Create: `src/codemie/rest_api/routers/idp_sync.py` (prefix `/v1/idp-sync`, router-level `Depends(require_idp_sync_account)`)
- Modify: `src/codemie/rest_api/main.py:~1030+` (include router)
- Modify: `tests/codemie/rest_api/router_guards/baselines/openapi.json`, `route_precedence.json` (via `regenerate_baselines.py` by hand)
- Test: `tests/codemie/rest_api/routers/test_idp_sync_router.py`

**Interfaces:** Routes: `PUT /users/{user_id}` (upsert), `GET /users/{user_id}`, `GET /users?page&per_page` (per_page up to 100), `PUT /users/{user_id}/projects/{project_name}`, `DELETE /users/{user_id}/projects/{project_name}`, `POST /users/{user_id}/deactivate`. Router commits once per call; rollback on error.

Test-first: yes — with the switch on and off, the configured account gets 200 on each route and others 403; empty setting 403; AC12/AC16 baseline tests fail until regenerated.

- [ ] Write failing router tests, implement thin handlers, regenerate baselines, run `tests/codemie/rest_api/router_guards/`.
- [ ] Run existing `test_idp_lock_requests.py`, `test_user_management_router_idp_guard.py`, `test_idp_managed_service_guards.py` unchanged and passing (AC13).

### Task 8: Load check at 60,000 users

**Files:** Test: `tests/performance/test_idp_sync_list_load.py` (marked so it is excluded from the default run; follow existing pytest marker conventions)

Test-first: no — measurement test: seed 60,000 users with about 5 memberships each in a real PostgreSQL test database, assert a page of 100 via `list_users` completes in under 1 s (AC11). If it fails, report the plan to add an index/migration as a follow-up decision (return to caller); do not add one speculatively.

- [ ] Write and run the check; record the measured time in the test docstring.
