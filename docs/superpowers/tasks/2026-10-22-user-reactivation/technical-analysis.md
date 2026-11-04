# Technical Research

**Task**: user management deactivate reactivate admin
**Generated**: 2026-10-22
**Research path**: filesystem

---

## 1. Original Context

# Reactivate a deactivated user

## What is wrong
Platform admins can deactivate a user, but there is no way back. The only update route refuses is_active=true with 400, so a deactivated user stays locked out for good.

## What it should be
A dedicated endpoint, POST /v1/admin/users/{user_id}/reactivate, with the same guard as deactivation: authenticate, admin_access_only, and 400 when user management is disabled. It sets is_active=True, clears deleted_at, bumps update_date, and records a USER_REACTIVATED activity event ("user.reactivated") with the actor. It logs "user_reactivated: actor_user_id=..., target_user_id=..., domain=user_management", invalidates the auth cache after commit, and returns the user as CodeMieUserDetail, the same shape PUT returns. Memberships, roles and budgets were never touched by deactivation, so reactivation restores access as it was.

## Acceptance criteria
1. Reactivating a deactivated user returns 200 with the user detail, is_active true and deleted_at null, and the user can sign in again.
2. An unknown user id returns 404 "User not found".
3. Reactivating a user who is already active returns 409, and nothing is changed or logged.
4. A regular user (neither super admin nor maintainer) gets 403. Auditors are refused like other non-admins.
5. The activity event, the log line and the cache invalidation each happen exactly once, after a successful commit.
6. PUT with is_active=true still returns 400, with a message that names the reactivate endpoint.

## Definition of done
Repository, service and router tests cover every criterion above, and each one is mutation-checked. Router guard baselines are updated only for the new route. Ruff and the license check are clean. Commits are local and nothing is pushed.

## Out of scope
UI changes. Restoring MCP auth tokens removed on deactivation. Any change to the deactivate flow or to the user lookups.

Known pointers: deactivation route DELETE /{user_id} in src/codemie/rest_api/routers/user_management_router.py:257; UserManagementService.deactivate_user in src/codemie/service/user/user_management_service.py:269 (activity event, log line, invalidate_user_from_cache after commit); user_repository.soft_delete in src/codemie/repository/user_repository.py:155; UserManagementEvent in src/codemie/service/activity/activity_models.py:58 (event_type plain VARCHAR, no migration); PUT is_active=true refusal at user_management_service.py:1141; OpenAPI baseline test tests/codemie/rest_api/router_guards/test_openapi_snapshot.py with baselines/openapi.json and route_precedence.json. Existing commit 54ed80b2a on the branch is settled. Also report existing test files/fixtures for the repository, service and router layers of user management, and how auditors are handled by admin_access_only.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/rest_api/routers/user_management_router.py:256-267` `deactivate_user` (DELETE `/{user_id}`): `Depends(authenticate)`, `Depends(admin_access_only)`, `if not config.ENABLE_USER_MANAGEMENT: raise ExtendedHTTPException(code=400, message=_USER_MGMT_NOT_ENABLED)`, then `user_management_service.deactivate_user_flow(user_id, user.id)`. The PUT route (just above) passes `is_active` through to `update_user_fields`.
- `src/codemie/service/user/user_management_service.py`:
  - `deactivate_user(session, user_id, actor_user_id)` (static, ~l.269): `user_repository.get_by_id` (404 `_USER_NOT_FOUND` if none), last-admin guard, `user_repository.soft_delete`, `activity_event_repository.insert(ActivityEventCreate(domain=ActivityDomain.USER_MANAGEMENT, event_type=UserManagementEvent.USER_DEACTIVATED, entity_type=ActivityEntityType.USER, entity_id=user_id, actor_id=actor_user_id), session)`, `logger.info("user_deactivated: actor_user_id=..., target_user_id=..., domain=user_management")`, returns refreshed user. No commit inside.
  - `_handle_deactivation_flow` (~l.1060): calls `deactivate_user`, `session.commit()`, `invalidate_user_from_cache(user_id)`, `_schedule_mcp_auth_cleanup_after_commit`, then resolves actor (`user_repository.get_by_id`, `.is_admin`) and returns `get_user_with_relationships(session, user_id, actor_user_id, actor_is_admin)` (CodeMieUserDetail).
  - `update_user_fields` (~l.1141-1146): `if is_active is not None: if is_active: raise ExtendedHTTPException(code=400, message="Cannot reactivate user. Reactivation is not supported.")`. Only existing test on this string: `tests/codemie/service/user/test_user_management_service_field_editability.py:858` asserts `"Cannot reactivate user" in exc_info.value.message`. The docstring at ~l.1120 also says "Deactivation only (False allowed, True raises error)".
  - `update_user_fields` tail (~l.1195-1210): commit, `invalidate_user_from_cache`, actor admin status via `is_admin_or_maintainer(actor)`, `get_user_with_relationships(...)`. This is the "same shape PUT returns".
  - `deactivate_user_flow` (~l.1214): `with get_session() as session` (imported from `codemie.clients.postgres` inside the function), calls `deactivate_user`, commit, invalidate cache, MCP cleanup; returns `{"message": ...}`.
  - `get_user_with_relationships` builds `CodeMieUserDetail` including `is_active`, `deleted_at`, `update_date` (~l.157-230).
- `src/codemie/repository/user_repository.py`: `get_by_id` (l.56, no active filter), `get_active_by_id` (l.95, filters `is_active` and `deleted_at IS NULL`), `update(session, user_id, **fields)` (generic setattr, sets update_date, flush+refresh), `soft_delete` (l.155: sets `is_active=False`, `deleted_at=now`, `update_date=now`, `session.add`, `flush`, returns bool). No reactivate/restore method exists. No "reactivat" symbol in `src` for users.
- `src/codemie/service/activity/activity_models.py:58` `UserManagementEvent` (plain string constants: USER_CREATED, USER_UPDATED, USER_DEACTIVATED="user.deactivated", USER_LOGIN, ...). `ActivityEvent.event_type` is `VARCHAR(128)`; domain `VARCHAR(64)`. No USER_REACTIVATED constant yet. `tests/codemie/service/activity/test_activity_models.py` exists.
- `src/codemie/service/user/authentication_service.py:73` `invalidate_user_from_cache(user_id)` drops all `_auth_token_cache` entries for the user. Sign-in/token paths reject `not is_active or deleted_at is not None` (lines ~122, 561, 623), so clearing both fields is what permits sign-in.
- Guard: `src/codemie/rest_api/security/authentication.py:180` `admin_access_only` returns 403 unless `request.state.user.is_admin_or_maintainer`. Despite the name it admits maintainers. It logs `access_denied_admin: actor_user_id=..., domain=user_management`.

### Auditor handling
`admin_access_only` checks only `is_admin_or_maintainer` (`permissions.is_admin_or_maintainer`: `is_admin` or `is_maintainer`, non-bool values treated as False). `is_auditor` is not consulted, so a pure auditor gets 403 with `ACCESS_DENIED_MESSAGE`. Auditor read access is granted only by the separate dependency `admin_or_maintainer_or_auditor_access` (authentication.py:241), used on GET list, GET detail projects and KB reads. `tests/codemie/rest_api/routers/test_user_management_router_auditor.py` has an `auditor_user` fixture and a route-dependency inspection test class (imports both guards). Criterion 4 is satisfied by simply using `admin_access_only`.

### Architecture and Layers Affected
Router (`user_management_router.py`) -> service (`UserManagementService`, singleton `user_management_service`) -> repository (`user_repository`, `activity_event_repository`) -> SQLModel `UserDB`. Cross-cutting: auth cache (`authentication_service`), activity models, OpenAPI/route baselines in `tests/codemie/rest_api/router_guards/`.

### Integration Points
- `activity_event_repository.insert` (same session, so transactional with the commit).
- `invalidate_user_from_cache` (in-memory cache).
- `_schedule_mcp_auth_cleanup_after_commit` / `enqueue_mcp_auth_cleanup` is the deactivation-side MCP hook; task says restoring MCP tokens is out of scope.
- `ExtendedHTTPException(code, message)` for 400/404/409 (409 is not used in this module's visible code paths; check other services for precedent).

### Patterns and Conventions
- Static service methods; one "inner" method taking a `session` (does DB work, event, log, no commit) plus a "flow" method owning `get_session()`, commit, cache invalidation. Event insert and log line sit inside the inner method, before commit (deactivation logs before commit). Criterion 5 asks for event/log/cache "after a successful commit", which differs from the deactivation ordering for event and log (they precede commit there). This is a design decision for spec.
- Constants `_USER_NOT_FOUND`, `_USER_MGMT_NOT_ENABLED` are module-level.
- Log format `name: key=value, ..., domain=user_management`.
- Router functions are plain sync `def`, called directly in router tests with a `User` object and patched `config`/`user_management_service`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists (api/, architecture/, data/, development/, testing/, standards/, quality-gates.md, ...). Relevant per AGENTS.md: `api/rest-api-patterns.md`, `api/endpoint-conventions.md`, `architecture/service-layer-patterns.md`, `data/repository-patterns.md`, `development/error-handling.md`, `development/logging-patterns.md`, `quality-gates.md`, `testing/testing-patterns.md`, `testing/testing-api-patterns.md`, `testing/testing-service-patterns.md`, `testing/local-verification.md`. I did not read their bodies; the spec stage should load P0 guides (AGENTS.md rule).

### Architectural Decisions
Inline comment in `_schedule_mcp_auth_cleanup_after_commit` records an approved deviation (no shared user-lifecycle hook registry). The router_guards module docstrings state baselines are never auto-rewritten and are regenerated manually, only for intended contract changes.

### Derived Conventions
See Section 2 Patterns. Commit 54ed80b2a (settled) made user-facing lookups use the active lookup, so a deactivated user is 404 on assignment, access, password and profile flows; `get_by_id` (unfiltered) remains the admin-side lookup and is what reactivation needs.

---

## 4. Testing Landscape

### Existing Coverage
Repository (`tests/codemie/repository/`):
- `test_user_repository_crud.py`: fixtures `user_repository`, `db_session` (MagicMock(spec=Session)), `async_db_session`, `sample_user` (active `UserDB`), `sample_inactive_user` (`is_active=False`, `deleted_at` set). Classes `TestUserRepositoryGetById`, `GetActiveById`, `Update`, `SoftDelete` (l.368; `test_soft_delete_success`, `test_soft_delete_user_not_found`, mocks `get_by_id` via mocker). Natural home for a reactivate repository test.
- `test_user_repository.py`, `test_user_repository_list_with_projects.py` (deactivated-user list/lookups, from 54ed80b2a), `_projects.py`, `_sort.py`.

Service (`tests/codemie/service/user/`):
- `test_user_management_service.py`: module-level functions (not classes), decorators `@patch("codemie.service.user.user_management_service.activity_event_repository")` and `@patch("...user_repository")`, `session = MagicMock()`, helper `_mock_user(id)`. `# deactivate_user` section at ~l.149: `test_deactivate_user_emits_user_deactivated_event` (asserts `mock_activity.insert.call_args[0][0]` domain, event_type, entity_type, entity_id, actor_id), `test_deactivate_user_no_event_when_user_not_found`. Best template for reactivate.
- `test_user_management_service_field_editability.py:858` covers the PUT is_active=true 400 (asserts "Cannot reactivate user" substring; will need updating since criterion 6 changes the message, unless the new message keeps that substring).
- `test_user_management_service_super_admin.py` also touches deactivate.
- `test_authentication_service.py` covers cache/is_active rejection.

Router (`tests/codemie/rest_api/routers/`):
- `test_user_management_router_crud.py`: fixtures `admin_user`, `maintainer_user`, `regular_user`, `mock_user_detail`; `TestDeactivateUser` (l.396) with `test_deactivate_user_success` and `test_deactivate_user_disabled_management` (patches `config` and `user_management_service`, calls router function directly).
- `test_user_management_router_authorization.py`: `test_other_endpoints_remain_super_admin_only` (l.~306) holds a dict of route path -> methods that must all depend on `admin_access_only`, and iterates `router.routes`; a new route not in that dict may fail or be unchecked depending on the remainder of the loop (not read past l.330). `/v1/admin/users/{user_id}` is listed as `["PUT","DELETE"]`.
- `test_user_management_router_auditor.py`: route dependency inspection and auditor fixture (use for criterion 4 auditor 403).
- Others: `_story18`, `_default_project`, `_pagination`, `_sort`.

Router guards (`tests/codemie/rest_api/router_guards/`): `test_openapi_snapshot.py` (marked `@pytest.mark.enterprise`, needs real `codemie_enterprise`; compares `app.openapi()` of the guarded app with `baselines/openapi.json` as exact text), `test_route_precedence.py`/`test_route_overlap.py` with `baselines/route_precedence.json` (overlapping route pairs). `regenerate_baselines.py` run via `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines` writes BOTH baselines from the current tree. `baselines/openapi.json` currently contains `is_active: true`-ish example at authorization test only; no `/admin/users` entries were found in `route_precedence.json` (grep returned none), so the precedence file may not change, but this was not verified by running the test.

### Testing Framework and Patterns
pytest (`pytest.ini`), `unittest.mock.patch` plus `pytest-mock` `mocker`; mock sessions (no real DB) for repository tests; direct calls of router handlers with `User` objects. Enterprise marker on OpenAPI snapshot.

### Coverage Gaps
No tests exist for a reactivate path (none exists in source). No test pins the full PUT 400 message other than the substring check.

---

## 5. Configuration and Environment

### Environment Variables
`ENABLE_USER_MANAGEMENT` (`src/codemie/configs/config.py:248`, default False): master switch, checked in router with 400 `_USER_MGMT_NOT_ENABLED`. `IDP_PROVIDER` also related to user management (config.py:261).

### Configuration Files
`config.py` only. No feature flag specific to reactivation.

### Feature Flags and Deployment Concerns
No migration needed: `UserDB.is_active` and `deleted_at` exist; `activity_events.event_type` is VARCHAR(128). Makefile targets: `license-check` (`poetry run python scripts/license_headers/check_license_headers.py --check --quiet $(FILE)`), `license`, `license-fix`, `ruff`, and `verify` (ruff license gitleaks test). New test files need the Apache license header (copy the header seen in router_guards files, "Copyright 2026 EPAM Systems, Inc.").

---

## 6. Risk Indicators

- Ordering mismatch: criterion 5 requires event, log and cache invalidation "after a successful commit", whereas `deactivate_user` inserts the event and logs before commit (only cache invalidation is after). Speculative: reactivation either needs a different structure from deactivation or the criterion is read as "only if commit succeeds".
- Speculative: Criterion 3 (409, nothing changed or logged) needs an already-active check before any write. An "active" definition should consider both `is_active` and `deleted_at` (a half-state, `is_active=True` with `deleted_at` set, is possible in data; auth rejects it). Which counts as "already active" is undecided.
- Existing 400 message "Cannot reactivate user. Reactivation is not supported." is asserted by substring in `test_user_management_service_field_editability.py:858`; changing the message for criterion 6 interacts with that test. Also the `update_user_fields` docstring is stale after the change.
- `baselines/openapi.json` is 1.9 MB and the snapshot test is enterprise-marked; it may be skipped locally without `codemie_enterprise`. Regeneration rewrites both baselines from the current tree and could introduce unrelated diffs if the tree differs from baseline origin; "update only for the new route" requires reviewing the diff by hand.
- `test_other_endpoints_remain_super_admin_only` enumerates routes explicitly; adding a route may need a new entry (verify against the rest of the loop at l.330+).
- Mutation-checking each criterion: tests mostly use MagicMock sessions and patched repositories, so assertions must be specific (call counts, `assert_called_once`, not-called on 404/409) to kill mutants.
- 409 is not an established code in this service layer (not seen in the files read); check `ExtendedHTTPException` handler supports it.
- `get_session` imported inside the flow function (local import), so service flow tests must patch `codemie.clients.postgres.get_session`.
- Existing commit 54ed80b2a is settled; changes must not alter deactivate flow or lookups.
- Working tree has an unrelated modification to `.codemie/codemie-cli.config.json`; avoid staging it.

---

## 7. Summary for Complexity Assessment

The change touches three layers (router, service, repository) plus the activity event constants, and test baselines. The pattern to copy is directly adjacent: the DELETE `/{user_id}` route, `UserManagementService.deactivate_user` / `deactivate_user_flow` / `_handle_deactivation_flow`, and `user_repository.soft_delete`. The guard (`authenticate`, `admin_access_only`, `ENABLE_USER_MANAGEMENT` 400) is reused unchanged and already refuses auditors and regular users. No migration, config or new dependency is involved. Source change surface is small: one router function, one or two service methods, one repository method, one event constant, one message edit in `update_user_fields`.

Technical novelty is low. The non-trivial points are the commit-then-side-effects ordering required by criterion 5 (differs from deactivation), the 409 already-active semantics, and the baseline regeneration for `openapi.json` (large, enterprise-gated) with the requirement to touch only the new route.

Test posture is good for the patterns (repository crud, service module-level patched tests, router direct-call tests with admin/maintainer/regular/auditor fixtures) but nothing exists for reactivation. The deliverable includes repository, service, router tests for each criterion with mutation checks, plus updating one existing assertion (field_editability l.858) and possibly the authorization route-map test. Overall small to medium effort.

---

## 8. External References

None named by the task
