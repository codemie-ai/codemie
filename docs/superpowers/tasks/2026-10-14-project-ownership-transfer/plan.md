# Project Ownership Transfer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an endpoint that lets a project's owner, maintainers, project admins, or super admins reassign ownership to any existing platform user by updating `Application.created_by`.

**Architecture:** New `PUT /v1/projects/{projectName}/ownership` route in the existing `management.py` router submodule, reusing `_authorize_project_access` (Ability-based, 404-on-deny) for authorization, a new `ProjectService.update_project_ownership` orchestration method, and a `created_by` parameter added to the existing `ApplicationRepository.update_project`.

**Tech Stack:** FastAPI, SQLModel/Postgres, pytest.

**Spec:** none (inline requirements below) — see `## Acceptance criteria`.

## Global Constraints

- Project identification is by name (`projectName` path param), not a numeric id.
- No DB migration — `Application.created_by` already exists.
- Authorization must be the existing `Ability` WRITE set (owner, maintainer, project admin, super admin) via `_authorize_project_access`, not `_get_project_for_update`'s narrower 403 check.
- New owner needs no prior project membership — only must exist as a platform user.
- Commit per task using the repository's existing convention.

---

## Acceptance criteria

- [ ] `PUT /v1/projects/{projectName}/ownership` exists, accepts `{"user_id": "<new-owner-id>"}`, and on success sets `Application.created_by` to that id.
- [ ] Only the current owner, project maintainers, project admins, or super admins can call it; anyone else gets a 404 (matching `_authorize_project_access`'s existing deny behavior).
- [ ] The new owner may be any existing platform user with no prior project membership required.
- [ ] A non-existent target user id returns a 404/400, not a silent success.
- [ ] Router Contract Guard baselines are regenerated and committed alongside the new route.
- [ ] Router- and service-level tests cover: success (incl. `created_by` actually changed), unauthorized caller, target user not found.

---

### Task 1: Request/response schemas

**Files:**
- Modify: `src/codemie/rest_api/models/project.py:205` (add near `ProjectUpdateRequest`)
- Test: `tests/codemie/rest_api/routers/test_projects_router.py` (new test class, see Task 3)

**Interfaces:**
- Produces: `ProjectOwnershipUpdateRequest(user_id: str)`, `ProjectOwnershipUpdateResponse(name: str, created_by: str, message: str)`.

Test-first: no — pure data classes, exercised via Task 3's router tests.

- [ ] **Step 1:** Add:
```python
class ProjectOwnershipUpdateRequest(BaseModel):
    user_id: str


class ProjectOwnershipUpdateResponse(BaseModel):
    name: str
    created_by: str
    message: str
```
- [ ] **Step 2:** Commit.

---

### Task 2: Repository — allow updating `created_by`

**Files:**
- Modify: `src/codemie/repository/application_repository.py:722-753` (`update_project`)
- Test: `tests/codemie/repository/test_application_repository*.py` (add a focused test near existing `update_project` coverage, or create `test_application_repository_ownership.py` if no existing `update_project` test file is found)

**Interfaces:**
- Produces: `update_project(..., created_by: str | None = None)` — sets `application.created_by = created_by` only when not `None`.

Test-first: yes — a failing test asserting `update_project(session, app, created_by="new-user")` persists `app.created_by == "new-user"` while leaving it untouched when the param is omitted.

- [ ] **Step 1:** Write the failing test described above.
- [ ] **Step 2:** Run it, confirm it fails (`TypeError: unexpected keyword argument 'created_by'`).
- [ ] **Step 3:** Add `created_by: Optional[str] = None` to the signature and `if created_by is not None: application.created_by = created_by` to the body (`application_repository.py:734`, `:744`).
- [ ] **Step 4:** Run the test, confirm it passes.
- [ ] **Step 5:** Commit.

---

### Task 3: Service — `ProjectService.update_project_ownership`

**Files:**
- Modify: `src/codemie/service/project/project_service.py` (new classmethod near `update_project`, lines 208-281)
- Modify: `src/codemie/service/activity/activity_models.py:46-49` (`ProjectManagementEvent`) — add `PROJECT_OWNERSHIP_CHANGED = "project.ownership_changed"`
- Test: `tests/codemie/service/project/test_project_service_ownership.py` (new file)

**Interfaces:**
- Consumes: `application_repository.get_by_name`, `application_repository.update_project(..., created_by=...)` (Task 2), `UserRepository.get_by_id` (`src/codemie/repository/user_repository.py:49`), `invalidate_user_from_cache`, `ActivityEventCreate`.
- Produces: `ProjectService.update_project_ownership(user: User, project_name: str, new_owner_id: str) -> Application` — raises `ExtendedHTTPException(code=404, ...)` if the project is missing/deleted or if `new_owner_id` does not resolve via `UserRepository.get_by_id`; otherwise updates `created_by`, logs a `PROJECT_OWNERSHIP_CHANGED` activity event, invalidates cache for the old and new owner, and returns the refreshed `Application`. Performs no additional permission check — the router's `_authorize_project_access` dependency is the sole gate.

Test-first: yes — failing tests for: (a) success updates and persists `created_by`; (b) nonexistent `new_owner_id` raises 404.

- [ ] **Step 1:** Write both failing tests against the not-yet-existing `update_project_ownership`.
- [ ] **Step 2:** Run them, confirm both fail (`AttributeError`).
- [ ] **Step 3:** Add `PROJECT_OWNERSHIP_CHANGED` to `ProjectManagementEvent`.
- [ ] **Step 4:** Implement `update_project_ownership` following `update_project`'s session/activity-logging pattern (`project_service.py:225-281`): open `with get_session() as session:`, re-fetch via `application_repository.get_by_name`, 404 if missing/deleted, look up `new_owner_id` via `UserRepository.get_by_id` and 404 if `None`, call `application_repository.update_project(session, project, created_by=new_owner_id)`, insert the activity event (`entity_type=ActivityEntityType.PROJECT`, `entity_id=project.name`, `actor_id=user.id`), `session.commit()`, `session.refresh(project)`, call `invalidate_user_from_cache` for both `project.created_by` (pre-update) and `new_owner_id`, log, return `project`.
- [ ] **Step 5:** Run the tests, confirm they pass.
- [ ] **Step 6:** Commit.

---

### Task 4: Router — `PUT /v1/projects/{projectName}/ownership`

**Files:**
- Modify: `src/codemie/rest_api/routers/projects/management.py` (new handler, alongside `delete_project` at lines 114-140)
- Test: `tests/codemie/rest_api/routers/test_projects_router.py` (new `TestProjectOwnershipEndpoint` class)

**Interfaces:**
- Consumes: `_authorize_project_access` (`_common.py:47-71`), `project_service.update_project_ownership` (Task 3), `ProjectOwnershipUpdateRequest`/`Response` (Task 1).
- Produces: route `PUT /v1/projects/{projectName}/ownership`.

Test-first: yes — failing tests for: success (200, `created_by` in response matches new owner, service called with the new owner id), unauthorized caller (404, matching `_authorize_project_access`'s existing deny behavior — same pattern as `test_delete_project_propagates_403_from_service`/`TestCheckProjectAccess`), target user not found (404 propagated from the service).

- [ ] **Step 1:** Write the three failing tests, mocking `project_service.update_project_ownership` where appropriate and reusing the existing `regular_user`/`super_admin_user` fixtures and `_authorize_project_access` override pattern already used in this test file's `TestDeleteProjectEndpoint`/`TestCheckProjectAccess` classes.
- [ ] **Step 2:** Run them, confirm all fail (404 route-not-found).
- [ ] **Step 3:** Implement:
```python
@router.put("/projects/{projectName}/ownership", response_model=ProjectOwnershipUpdateResponse)
def update_project_ownership(
    payload: ProjectOwnershipUpdateRequest,
    request: Request,
    project_name: str = Path(alias="projectName"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    project = project_service.update_project_ownership(
        user=request.state.user,
        project_name=authorized_project.name,
        new_owner_id=payload.user_id,
    )
    return ProjectOwnershipUpdateResponse(
        name=project.name,
        created_by=project.created_by,
        message=f"Ownership of '{project.name}' transferred to '{project.created_by}'",
    )
```
Import `ProjectOwnershipUpdateRequest`/`Response` from `codemie.rest_api.models.project`.
- [ ] **Step 4:** Run the tests, confirm they pass.
- [ ] **Step 5:** Commit.

---

### Task 5: Regenerate Router Contract Guard baselines

**Files:**
- Modify: `tests/codemie/rest_api/router_guards/baselines/openapi.json`
- Modify: `tests/codemie/rest_api/router_guards/baselines/route_precedence.json`

**Interfaces:**
- Consumes: the route added in Task 4.

Test-first: no — this task makes the existing (already-passing-before-Task-4, now-failing-after-it) Router Contract Guard tests pass again; there is no new test to write.

- [ ] **Step 1:** Run `make test` (or the guard tests directly) and confirm `test_openapi_matches_baseline` / `test_baseline_order_is_preserved` fail after Task 4's new route.
- [ ] **Step 2:** Run `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`.
- [ ] **Step 3:** Re-run the guard tests, confirm they pass, and review the baseline diff to confirm it only reflects the new `/v1/projects/{projectName}/ownership` route.
- [ ] **Step 4:** Commit the regenerated baseline files.

---

## Self-review notes

- **Coverage:** Req 1 → Task 4 (route/convention); Req 2 → Task 1 (`user_id` in body); Req 3 → Task 3 (`UserRepository.get_by_id` validation, no membership check added anywhere); Req 4 → Task 4 (`_authorize_project_access`, not `_get_project_for_update`); Req 5 → Tasks 2+3 (`created_by` update); Req 6 → Task 5; Req 7 → Tasks 1-3 follow cited existing patterns; Req 8 → no migration task present; Req 9 → Tasks 2-4 test-first steps.
- **Negative constraints:** no membership check added for the new owner (Task 3 explicitly omits one); authorization uses only `_authorize_project_access`/Ability, never `_get_project_for_update`'s 403 path; no migration task exists; no whole-suite quality-gate, verification, or code-review task is included — those remain the calling flow's responsibility.
