# EPMCDME-16895: IdP-managed users - service-account management API and identity guard

## Goal
A management API under `/v1/idp-sync/` that only one configured service account can call. It offers idempotent create/update, assign, unassign and deactivate of users and memberships, plus the reads a sync client needs. Every write to users and memberships goes through the platform. Scheduling stays in the customer-side job. The earlier in-repo sync (`enterprise/sync/coordinator.py`), which wrote to the database directly, was never shipped to the customer; there is no concurrent second writer. It stays in the branch and is reworked into a client of this API as the next task.

## Behavior

**Access (identity guard)**
- New setting `IDP_SYNC_SERVICE_ACCOUNT_USERNAME` (default empty, in `configs/config.py` next to `TEAMS_SERVICE_ACCOUNT_ID`, which is untouched). A caller is the management account only if `caller.username` equals the setting (the service account's `service-account-<client-id>` name, the same on every realm; the id differs per realm, so it is not used) and `user_type == service_account`. Unlike `teams_authentication_resolver.py:34-55`, which matches by id, it is fail-closed with no fallback.
- Management routes: setting empty -> 403 for everyone. Configured account with a valid IdP token -> allowed whether `IDP_MANAGED_ENABLED` is on or off. Any other caller, including platform admins and other service accounts -> 403.
- **Outside-the-prefix guard (mechanism).** One app-level dependency, passed as `FastAPI(lifespan=lifespan, dependencies=[...])` at `rest_api/main.py:1000` (no app-level dependencies exist there today). Because it is attached to the app, it covers every router, including any added later; it is not attached per router and is not a middleware. It depends on `authenticate`, so the caller is resolved once per request (FastAPI caches dependencies within a request) with no second authentication pass. `authenticate` itself is not changed.
  - The guard acts only when the caller is the configured account. It does not authenticate routes that are public today, so public routes stay public.
  - It takes the matched route's path template (`request.scope["route"].path`, which excludes the ingress `root_path` set by the Dockerfile `--root-path`). It answers 403 when that path is not under `/v1/idp-sync/`. A literal comparison of `request.url.path` is not used.
  - Management routes under the prefix are subject to the identity check above.
- Cross-reference: a route test (AC4) walks every route of the real app, so a router that bypasses the guard fails the test.

**Operations** (users by IdP id, projects by name; the service account is recorded as actor in every activity event)
- Implemented by one thin new service (about 4 methods: upsert_user, assign, unassign, deactivate). It reuses `UserDB`/`UserProject` repository writes (`user_repository`, `user_project_repository`, as the sync coordinator does at `coordinator.py:~290-370`), `ProjectAssignmentService` for membership, `application_repository.get_or_create` for projects, and `UserManagementService.deactivate_user_flow` for deactivation. New code only wires and applies the idempotency rules. Existing services and their switch locks (`require_no_idp_lock`, inline 403s in `UserAccessService` and `UserManagementService`) are not changed.
- Each call is all-or-nothing: one transaction, committed only when every step (including budget-provider sync) succeeded. On a budget-provider failure the call returns an error and nothing is persisted, so the job can retry the same call.
- Concurrency: idempotency does not rest on check-then-write. User insert runs in a savepoint; a unique-key conflict (id or email) is treated as "exists" and continues as an update. Membership insert treats a conflict as "already a member"; membership removal treats "not found" as done. The last-admin check on demotion and deactivation runs under a row lock on the admin rows (or equivalent serialization), so two parallel demotions cannot leave no admin.
- Upsert user: the request carries profile and a boolean `is_admin`, which is advisory input from the trusted account (the account can therefore mint admins). The server also treats `id == ADMIN_USER_ID` as admin. A new user is created without a personal project (with the switch off, one is created at first login). An existing user gets the differing profile fields and admin status updated. Demoting the last admin is rejected. A deactivated user is reactivated (`is_active=True`, `deleted_at=None`); previously revoked memberships are not restored.
- Assign: the project is created if missing, as on first login. Membership goes through `ProjectAssignmentService`, so budget membership and activity events are recorded. A repeat call is a no-op, and a differing project-admin flag is updated. A personal project is refused with 409 (the caller is allowed, this one change is refused for good, like a project creator).
- Unassign: budget membership is cleaned. Removing a non-member succeeds. A personal project is refused with 409. Unassigning the project creator is rejected with a defined error (409), because `ProjectAssignmentService._reject_if_creator` refuses it; this is the one exception to "unassign always succeeds".
- Deactivate: calls `deactivate_user_flow` (which commits and schedules MCP auth cleanup), then calls `invalidate_user_from_cache` after the commit. Refused for the last admin and for the service account itself. Cache invalidation is immediate on the serving replica; other replicas hold the in-process token cache until its TTL expires (about 30 s).
- Errors: unknown user -> not-found; malformed input -> validation error; both via `ExtendedHTTPException`.

**Reads**
- Get one user: active flag, profile, admin status, memberships with project-admin and is-personal flags.
- List users page by page (`page`/`per_page`), including deactivated users. Order is stable by `id` (not inherited from `date.desc()`). Two queries per page: the page, then a memberships fetch with the personal flag from `Application.project_type`.
- This is a new repository method. The existing getters (`get_by_id`, `_apply_filters`) keep hiding soft-deleted users.
- Target: a page of 100 with memberships in under 1 s at 60,000 users, about 5 memberships per user.

**Contract**
- Request and response models are documented as a contract for the customer job (field descriptions and examples in the models, including `is_admin` as advisory). The router-guard baselines (`openapi.json`, `route_precedence.json`) are regenerated by hand via `regenerate_baselines.py`.

## Acceptance criteria
- AC1 Empty setting: every management call returns 403.
- AC2 Configured account is accepted with the switch on and off.
- AC3 Other callers, including admins and other service accounts, get 403.
- AC4 The configured account gets 403 on every route of the real app outside `/v1/idp-sync/` (a test walks all routes and fails if any is not guarded). One test runs with a non-empty `root_path` and shows the account is neither refused on its own API nor admitted outside it. The Teams bot is unaffected. Public routes stay public.
- AC5 Upsert creates, updates and reactivates as specified, with no personal project and the last-admin demotion rejected; events record the service account as actor.
- AC6 Admin status is `is_admin` or `id == ADMIN_USER_ID`.
- AC7 Assign is idempotent, creates a missing project, updates the project-admin flag, records budget and activity events with the service account as actor, and refuses personal projects with 409. A budget-provider failure persists nothing.
- AC8 Unassign is idempotent for non-members, cleans the budget, refuses personal projects (409) and rejects the project creator (409).
- AC9 Deactivate runs through `deactivate_user_flow`, invalidates the auth cache after the commit (immediately on the serving replica, within the cache TTL on others), refuses the last admin and the service account itself, and records the service account as actor.
- AC10 Reads return deactivated users with the required fields in a stable id order.
- AC11 A load check on synthetic 60,000 users (about 5 memberships each) shows a page of 100 in under 1 s.
- AC12 Baselines are regenerated and the guard tests pass.
- AC13 Existing admin and switch-lock behavior is unchanged: the existing tests in `test_idp_lock_requests.py`, `test_user_management_router_idp_guard.py` and `test_idp_managed_service_guards.py` still pass.
- AC14 Unknown user -> not-found; malformed request -> validation error, both as `ExtendedHTTPException`.
- AC15 Retried or overlapping upsert and assign calls end as a no-op, never a server error; two parallel demotions never leave zero admins (tests with simulated conflicts).
- AC16 The request and response models are documented as a contract for the customer job (field descriptions, examples, `is_admin` advisory), checked in the OpenAPI baseline.

## Non-goals
- Removing or reworking the in-repo sync (`enterprise/sync/coordinator.py`, `Dockerfile.sync`): the next task turns it into a client of this API.
- The customer-side sync job.
- Budget clean-up beyond admin deactivation.
- Handling users that disappear from Keycloak.
- KB access sync.
- Legacy user mode.
- Changing platform-admin derivation, or editing Keycloak.
- Admin UI.
- Changes to `authenticate`, `TEAMS_SERVICE_ACCOUNT_ID`, or existing locks and services.
- Adding an index or migration, unless the load check fails.
- Rollout preconditions or protection against a second writer.
- Incremental (changed-since) sync, and a list of account names for rotation.

## Decisions
- App-level dependency on `authenticate`, matching the route template, over a middleware or per-router guard - mechanism for the outside-the-prefix guard (the prefix-guard decision itself stays) — user
- Path-prefix guard over changing authenticate - how to enforce route scoping — user
- Thin orchestrator service over adding bypass flags to existing services - how to get past the switch locks — user
- Client-sent `is_admin` plus server-side `ADMIN_USER_ID` over server-side role derivation - source of admin status — user
- New `IDP_SYNC_SERVICE_ACCOUNT_USERNAME` (default empty) and prefix `/v1/idp-sync/` over reusing the Teams setting - account identity — user
- Stable `id` ordering, two queries, load check, no index until measured over an up-front index or keeping `date DESC` - paging performance — user
- No rollout preconditions or second-writer protection over gating on in-repo sync removal - ordering with the in-repo sync, which was never shipped — user, answers AR-4
- Savepoint plus conflict-as-done and a row lock on the last-admin check over check-then-write - concurrency and retries
- All-or-nothing per call over partial success - budget-provider failure
- Unassigning the project creator is rejected (409) over silently succeeding - creator case
- Deactivation through `deactivate_user_flow` with cache invalidation after the commit over invalidating before it - ordering and replica scope
- New repository read method including deactivated users over changing the existing getters - read visibility
- Reactivation does not restore revoked memberships over restoring them - reactivation semantics
- No personal project on create over creating one - matches the switch behavior at `authentication_service.py:244`

## Open risks / assumptions
- The 1 s target at 60k users is unverified. There is no benchmark in the repo, and the check is planned.
- Keycloak admin-role derivation lives outside this repo, so the job must send `is_admin` correctly; the account can mint admins.
- Reactivation changes what the shared active-only getters return for that user.
- Starlette `root_path` behavior and how `user_type` is set for token callers are unverified; the plan must trace the latter and test it cannot be client-controlled.
- Other replicas keep a deactivated user's cached token up to the TTL (about 30 s).
- Isolation level, replica count and budget-provider latency are unverified; the row-lock approach depends on them.
- Single account user name only (no rotation list).
