# Technical Research

**Task**: project ownership permissions roles
**Generated**: 2026-10-14
**Research path**: filesystem

---

## 1. Original Context

Add ability to change the project ownership: 1. Add a new endpoint, something like PUT /project/{id}/ownership (check the actual field name in the DB model for ownership first). 2. The endpoint accepts a user id (the new owner). 3. Only maintainers, admins, and owners of the project can perform this request.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/core/models.py:384-459` — `Application` SQLModel (table name resolves to `applications`; `__tablename__` not shown in excerpt but indexes reference `ix_applications_*`). This is the project entity. The ownership field is `created_by: Optional[str]` (line 393, "User ID of creator (widened for non-UUID IDPs)"). There is no separate `owner_id` field — `created_by` is the only ownership column.
- `src/codemie/core/models.py:447-458` — `Application.is_owned_by(user)` returns `self.created_by is not None and self.created_by == user.id`; `is_managed_by(user)` returns `user.is_admin_or_maintainer or self.name in user.admin_project_names`; `is_shared_with(user)` covers broader read visibility.
- `src/codemie/core/ability.py` — `Ability` class with `Role` enum (`ANYONE`, `ADMIN`, `MANAGED_BY`, `OWNED_BY`, `SHARED_WITH`) and a `PERMISSIONS` registry. For `"Application"`, `Action.WRITE` is granted to `[Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN]` — i.e. exactly "owner, project admin/maintainer, or super admin", matching the task's required rule.
- `src/codemie/rest_api/routers/projects/_common.py:47-71` — `_authorize_project_access(request, project_name, user)` dependency: looks up the project by name, 404s if missing/soft-deleted, then calls `Ability(user).can(Action.WRITE, project)`, returning 404 (not 403) on failure "to avoid revealing project existence". This is the existing reusable write-authorization dependency and already encodes the owner/maintainer/admin rule the task asks for.
- `src/codemie/rest_api/routers/projects/management.py` — existing mutation endpoints: `PATCH /projects/{projectName}` (`update_project`), `PATCH /projects/{projectName}/allowed-models` (`update_allowed_models`), `DELETE /projects/{projectName}`. The delete endpoint uses `Depends(_authorize_project_access)` directly; `update_project`/`update_allowed_models` instead call `authenticate` and push the admin/maintainer check into the service layer (`project_service.update_project` → `_get_project_for_update`).
- `src/codemie/service/project/project_service.py:208-293` — `ProjectService.update_project(...)` pattern: opens a session, calls `_get_project_for_update` (404 if missing, then 403 via `user.is_admin_or_maintainer or user_project_repository.is_admin(...)`), applies field updates through `application_repository.update_project(...)`, records an `ActivityEventCreate` (domain `PROJECT_MANAGEMENT`), commits, and logs. Note this service-level check (`is_admin_or_maintainer` or project-admin) does **not** independently check `OWNED_BY` — it relies on project-admin status, whereas `_authorize_project_access`/`Ability` checks owner, maintainer, or admin directly from `created_by`.
- `src/codemie/repository/application_repository.py:722-749` — `update_project(session, application, *, name=None, display_name=None, description=None, clear_description=False, cost_center_id=None, chargeback_enabled=None, chargeback_attribution=None)` mutates fields directly on the `Application` instance and sets `update_date`. No parameter currently exists for `created_by`.
- `src/codemie/repository/application_repository.py:138-149` — `get_by_name(session, name)` is the standard project lookup by name (not by a separate numeric/UUID "id" — `Application.id` is the project name itself, per `Project.save()` setting `self.id = self.name`, visible at `core/models.py` save method).
- `src/codemie/repository/user_project_repository.py` — membership/role repository: `is_admin(session, user_id, project_name)`, `has_access(...)`, `get_by_user_and_project(...)`, `update_admin_status(...)`, `get_admin_projects(...)`. No existing method for ownership transfer.
- `src/codemie/repository/user_repository.py:49` — `UserRepository.get_by_id(session, user_id)` — existing lookup usable to validate the new-owner `user_id` exists.
- `src/codemie/service/activity/activity_models.py:46-49` — `ProjectManagementEvent` constants: `PROJECT_CREATED`, `PROJECT_UPDATED`, `PROJECT_DELETED`. No `PROJECT_OWNERSHIP_CHANGED`/similar event exists yet.
- `src/codemie/rest_api/models/project.py` — all current project request/response Pydantic models (`ProjectUpdateRequest`, `ProjectAssignmentRequest`, etc.) live here per convention; no ownership-transfer request/response model exists yet.

### Architecture and Layers Affected

- **Router layer**: `src/codemie/rest_api/routers/projects/management.py` (or a new submodule in the `projects` package) — would add the new endpoint; registered via `src/codemie/rest_api/routers/projects/__init__.py` which assembles `crud`, `spending`, `management`, `membership` submodules under prefix `/v1`.
- **Authorization helper layer**: `src/codemie/rest_api/routers/projects/_common.py` — `_authorize_project_access` already implements the owner/maintainer/admin WRITE rule via `Ability`.
- **Service layer**: `src/codemie/service/project/project_service.py` (`ProjectService`) — existing home for project mutation orchestration, activity logging, and cache invalidation (`invalidate_user_from_cache`).
- **Repository layer**: `src/codemie/repository/application_repository.py` (project/`Application` persistence) and `src/codemie/repository/user_repository.py` (to validate the new owner's user id exists) and `src/codemie/repository/user_project_repository.py` (project membership/role queries).
- **Model layer**: `src/codemie/core/models.py` (`Application` SQLModel — the `created_by` column already exists; no migration is evident as being required by current schema, since the column already exists).
- **REST schema layer**: `src/codemie/rest_api/models/project.py` — pydantic request/response models.
- **Activity/analytics layer**: `src/codemie/service/activity/activity_models.py` + `activity_event_repository` — existing pattern for recording project-management domain events.

### Integration Points

- `codemie.core.ability.Ability` / `Action` / `Role` — permission engine reused across many entities (`Assistant`, `Conversation`, `Guardrail`, `Application`, etc.).
- `codemie.rest_api.security.authentication.authenticate` — FastAPI dependency producing the `User` context (`is_admin`, `is_maintainer`, `is_admin_or_maintainer`, `admin_project_names`, `project_names`).
- `codemie.clients.postgres.get_session` — session-scoped DB access pattern used by every router/service touching project data.
- `invalidate_user_from_cache` (referenced in `project_service.py`) — user cache invalidation after project state changes.
- Router Contract Guard tests (`tests/codemie/rest_api/router_guards/`) rebuild the FastAPI app and diff `baselines/openapi.json` / `baselines/route_precedence.json` — any new route changes these contract snapshots and requires an intentional, reviewed regeneration (`poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`).

### Patterns and Conventions

- New request/response schemas belong in `src/codemie/rest_api/models/<feature>.py` (here, `src/codemie/rest_api/models/project.py`), imported by both router and service — not defined inline in the router module.
- Router functions validate/delegate; business logic (ownership checks beyond the FastAPI dependency, persistence, activity logging) lives in the service class (`ProjectService`), following `update_project`/`update_allowed_models`/`delete_project`.
- Project mutation endpoints consistently: open a `with get_session() as session:` block, re-fetch the project, apply changes via the repository, write an `ActivityEventCreate` row, `session.commit()`, and log a structured message (e.g. `f"project_updated: project={project.name!r}, by={user.id}, domain=project_management"`).
- 404 (not 403) is used by `_authorize_project_access` to avoid leaking project existence to unauthorized callers — note `ProjectService._get_project_for_update` instead raises 403 (`ACCESS_DENIED`) for authorized-but-insufficient-role callers; the two existing authorization styles in this codebase disagree on 403-vs-404 semantics, which is a convention decision the implementation will need to resolve consistently with whichever endpoint style is chosen.
- Project identification throughout the codebase is by **name** (`projectName` path parameter, `Application.id == Application.name`), not a numeric/opaque `{id}`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/api/rest-api-patterns.md` — router registration pattern (`routers/<feature>/` package with `__init__.py` building the parent router, submodules via bare `router = APIRouter()`), route-ordering caution for overlapping paths, and the Router Contract Guard warning.
- `.ai-run/guides/api/endpoint-conventions.md` — schemas live under `src/codemie/rest_api/models/<feature>.py`; router validates and delegates, service orchestrates; shared router helpers go in `src/codemie/rest_api/routers/utils.py` or a package-private `_common.py` (exactly the file hosting `_authorize_project_access`).
- `.ai-run/guides/testing/testing-api-patterns.md` — documents the Router Contract Guards (`test_openapi_matches_baseline`, `test_baseline_order_is_preserved`) that run under `make test` and must be regenerated deliberately after any intentional route/schema change.
- `.ai-run/guides/development/security-patterns.md`, `.ai-run/guides/architecture/service-layer-patterns.md`, `.ai-run/guides/data/repository-patterns.md` exist in the guide tree but were not read in depth for this pass; they are the natural next reads for implementation-time detail on auth/service/repository conventions.

### Architectural Decisions

- Inline docstring at `_authorize_project_access` (`_common.py:52-55`) records the deliberate decision to return 404 instead of 403 for project write-authorization failures, to avoid revealing project existence to unauthorized users — a decision any new ownership-transfer endpoint must either follow or explicitly deviate from.
- No ADR or guide text specifically addresses "ownership transfer" — this is a new capability on top of the existing owner/maintainer/admin model.

### Derived Conventions

- Endpoint path and HTTP verb conventions observed: all existing project mutation endpoints use `/v1/projects/{projectName}...` (plural `projects`, `projectName` param, not `{id}`), e.g. `PATCH /projects/{projectName}`, `PATCH /projects/{projectName}/allowed-models`, `DELETE /projects/{projectName}`. The ticket's suggested path `PUT /project/{id}/ownership` differs from this convention on three points (singular `project`, `{id}` vs `{projectName}`, and verb/suffix style); this is a design decision for the spec, not something resolved by code research.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/routers/test_projects_router.py` — unit tests for `/v1/projects` visibility, creation, assignment, delete, and update endpoints; imports `update_project`, `delete_project`, `_authorize_project_access`, `_raise_project_not_found` directly from the router modules, and uses `regular_user` / `super_admin_user` `User` fixtures with `patch.object(config, 'ENABLE_USER_MANAGEMENT', True)`.
- `tests/codemie/rest_api/routers/test_projects_router_auditor.py` — auditor-role-specific router tests.
- `tests/codemie/service/project/test_project_service.py`, `test_project_service_delete_update.py`, `test_project_service_chargeback.py` — service-layer tests for `ProjectService` mutation methods.
- `tests/codemie/repository/test_user_project_repository_*.py` (basic, extended, bulk_filter, visibility, default, story18, rename_cascade) — extensive repository-layer coverage of role/membership logic (`is_admin`, `has_access`, admin status updates).
- `tests/codemie/core/test_project_validator.py` — validation logic tests.
- No test file or test name references "ownership transfer", "change owner", or similar — grep across `src/codemie` and `tests` for `ownership|change_owner|transfer_owner` only matched unrelated generic "ownership" comments in OAuth/token/budget files and `Ability`'s doc comments, confirming no prior implementation or test scaffold.

### Testing Framework and Patterns

- pytest with fixtures and `unittest.mock.patch`/`MagicMock`/`AsyncMock`; `@patch.object(config, ...)` used to toggle `ENABLE_USER_MANAGEMENT`/`ENV` for tests.
- Router Contract Guards (`tests/codemie/rest_api/router_guards/`) run under `make test` and compare OpenAPI/route-precedence baselines in a subprocess against committed JSON snapshots.

### Coverage Gaps

- No existing tests for an ownership-transfer endpoint, service method, or repository method — this is wholly new functionality.
- No existing test exercises the exact "owner OR maintainer OR admin" tri-state permission combination outside of `Ability`'s generic `PERMISSIONS` table tests (if any) — most project-service-level tests check `is_admin_or_maintainer or is_project_admin`, which is a narrower rule than the `Ability`-based owner/maintainer/admin WRITE check used by `_authorize_project_access`.

---

## 5. Configuration and Environment

### Environment Variables

- `ENABLE_USER_MANAGEMENT` (via `codemie.configs.config`) — gates all project-management endpoints; `_ensure_user_management_enabled()` in `_common.py` raises 400 if disabled. Any new ownership endpoint would sit behind this same existing flag per the established pattern.

### Configuration Files

- No feature-area-specific config file beyond the general app config module (`codemie.configs.config`) was found; project-management behavior is controlled through the `ENABLE_USER_MANAGEMENT` flag and `customer_config.is_feature_enabled(...)` checks (e.g. `"projectChargeback"` in `project_service.py`), not a dedicated ownership config.

### Feature Flags and Deployment Concerns

- `customer_config.is_feature_enabled("projectChargeback")` is an example of a customer-config feature gate used for other project fields; no existing flag gates ownership behavior.
- Router Contract Guard baselines (`tests/codemie/rest_api/router_guards/baselines/openapi.json`, `route_precedence.json`) must be regenerated as a reviewed, intentional step whenever a new route is added — this is a mechanical but mandatory step for any new endpoint in this codebase.

---

## 6. Risk Indicators

- Speculative: The ticket's suggested path `PUT /project/{id}/ownership` conflicts with the established `/v1/projects/{projectName}` convention (plural resource, name-based identifier); using `{id}` where the codebase otherwise uses `projectName` risks ambiguity about whether "id" means the project name or some other identifier, since `Application.id` is in fact set equal to the project name.
- Speculative: Two different authorization styles coexist for project mutation — `_authorize_project_access` (404-on-deny, via `Ability`'s owner/maintainer/admin WRITE check) versus `ProjectService._get_project_for_update` (403-on-deny, via `is_admin_or_maintainer or is_project_admin`, which omits the OWNED_BY/creator check). The task's rule ("maintainers, admins, and owners") matches `Ability`'s existing WRITE permission set exactly, but whichever code path is reused/extended must be chosen deliberately since the two differ in both response code and in whether plain ownership (`created_by`) counts.
- Speculative: Transferring ownership raises a question current code does not answer: whether the new owner must already be a project member/admin (per `user_project_repository`), and whether `UserRepository.get_by_id` validation of the target user id is required before persisting — no existing validator does this specifically for ownership.
- Adding a new route changes the Router Contract Guard baselines (`tests/codemie/rest_api/router_guards/baselines/openapi.json`, `route_precedence.json`); the baseline regeneration step is mandatory and easy to miss.
- No activity-event constant exists yet for an ownership-change event (`ProjectManagementEvent` currently only has `PROJECT_CREATED/UPDATED/DELETED`), so emitting an auditable event for this sensitive action is new work, not a reuse of an existing constant.
- No test coverage exists anywhere for ownership transfer; this is a greenfield feature at every layer (router, service, repository, schema).

---

## 7. Summary for Complexity Assessment

This task touches the full project-management stack in a well-established area: router (`src/codemie/rest_api/routers/projects/management.py` or a new submodule), REST schema (`src/codemie/rest_api/models/project.py`), service (`src/codemie/service/project/project_service.py`), and repository (`src/codemie/repository/application_repository.py`, plus possibly `user_repository.py` to validate the new owner and `user_project_repository.py` for membership/role context). The DB field for ownership already exists — `Application.created_by` — so no schema migration is evident as needed; this is additive logic on an existing column, not a new table or column.

Technical novelty is moderate: the permission rule the task wants ("maintainers, admins, and owners") is not new to the codebase — it is exactly `Ability`'s existing WRITE permission set (`Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN`) already implemented and used by `_authorize_project_access`. The main judgment calls are (a) reconciling two existing, inconsistent authorization styles for project mutations (404-via-Ability vs. 403-via-service-check that omits ownership), (b) deciding whether the new owner needs prior membership/validation, and (c) the endpoint path/verb diverging from the codebase's established `/v1/projects/{projectName}` convention. None of these are deep architectural risks, but each is a concrete decision point for the spec.

Test coverage posture is a gap: no test anywhere exercises ownership transfer, so full new test coverage (router, service, possibly repository) is needed. The project's Router Contract Guard adds a mechanical but mandatory step (baseline regeneration) whenever a new route is introduced. Overall this reads as a small-to-moderate, well-trodden-pattern feature with no missing infrastructure, bounded mostly by a few design decisions rather than unknown territory.

---

## 8. External References

None named by the task. The task's instruction to "check the actual field name in the DB model for ownership first" refers to an internal lookup, not an external source — this was resolved in Section 2: the field is `Application.created_by` in `src/codemie/core/models.py:393`.
