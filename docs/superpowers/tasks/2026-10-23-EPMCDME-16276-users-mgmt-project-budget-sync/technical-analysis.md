# Technical Research

**Task**: project-membership user-management budget litellm
**Generated**: 2026-10-23
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-16276 — fix: removing a user from a project via Users management (DELETE /v1/admin/users/{user_id}/projects/{project} → UserAccessService.revoke_project_access) does not run the same cleanup as Projects management (DELETE /v1/projects/{project}/assignment/{user_id} → ProjectAssignmentService.remove_user_from_project): project member budget allocation (project_member_budget_assignments) and the LiteLLM per-member customer stay active, no activity event, no creator guard. Result (verified locally with real codemie-claude + LLM proxy + LiteLLM): a user unassigned via Users management keeps spending on the project and its CLI budget (spend metric tagged with the project; LiteLLM customer codemie:project:<p>:category:cli:user:<uid> keeps getting charged). Removal via Projects management correctly falls back to the personal budget. Also: adding a project via Users management (POST /v1/admin/users/{user_id}/projects) creates no budget allocation. Goal: make the Users management add/remove paths run the same membership+budget sync as Projects management (ideally one shared service method), with tests; optionally a defensive membership check in the LLM proxy (src/codemie/enterprise/litellm/proxy_router.py, budget_resolution_service) before charging a project budget.

---

## 2. Codebase Findings

### Existing Implementations

**Projects management path (correct behaviour)** — `src/codemie/service/project/project_assignment_service.py`
- `assign_user_to_project(session, project, user_id, project_name, is_project_admin, actor, action)` (L358): personal-project guard (takes `Application`), UUID format check, user exists, 409 if already assigned, `user_project_repository.add_project`, then `_sync_project_budget_member_added(session, project_name, user_id, actor.id)` (L417), then `activity_event_repository.insert(... UserManagementEvent.USER_PROJECT_ASSIGNED, attributes={project_name, is_project_admin})`. It does not commit; the router commits.
- `remove_user_from_project(session, project, user_id, project_name, actor, action)` (L749): `_reject_if_personal_project`, `_reject_if_creator` (400 "Cannot remove the project creator from the project"), UUID check, user exists, `user_project_repository.remove_project` (404 if nothing removed), `_sync_project_budget_member_removed` (L797), activity event `USER_PROJECT_REMOVED`. No commit.
- `_sync_project_budget_member_added` (L209): for each active `ProjectBudgetAssignment` of the project, creates a `ProjectMemberBudgetAssignment` (EQUAL mode, amounts from `_get_member_added_allocation_amounts`, `sync_status=PENDING`) unless one already exists. It makes no provider call; the provider is synced lazily at runtime.
- `_sync_project_budget_member_removed` (L268): for each active PMBA, captures terminal spend (`_capture_terminal_member_spend`), calls `get_active_provider().delete_member_allocation(allocation=...)` through `_run_budget_provider_coro` (a sync-to-async bridge), soft-deletes (`deleted_at=now`), pops `_resolution_cache[(project, category, user_id)]`, then flushes. Provider failures are logged and do not raise.
- Bulk paths call the same helpers: bulk assign (L616), bulk remove (L728).
- Other callers of the helpers: `authentication_service._sync_budget_for_idp_projects` (L273–290, IDP bootstrap, `actor_id=None`), `project_service` project delete (L685).

**Users management path (the bug)** — `src/codemie/service/user/user_access_service.py`
- `grant_project_access(user_id, project_name, is_project_admin, actor)` (L89): opens its own `get_session()`, checks the user exists, runs its own `_reject_if_personal_project(session, project_name, actor, user_id, action)` (L341; looks up the project by name, and a missing project passes), returns 409 if the membership exists, calls `application_repository.get_or_create` (auto-creates the project), `add_project`, `session.commit()`, `_invalidate_auth_cache(user_id)`. It writes **no PMBA and no activity event**.
- `revoke_project_access(user_id, project_name, actor)` (L198): user exists, personal guard, `remove_project` (404 `NO_PROJECT_ACCESS`), commit, invalidate auth cache. It has **no creator guard, no budget sync, no `_resolution_cache` pop and no activity event**.
- `update_user_project_access` (L114) only changes the admin flag, so it has no budget impact.

**Routers**
- `src/codemie/rest_api/routers/user_management_router.py`: `add_project_access` POST `/{user_id}/projects` (L315, `admin_access_only`, gated by `config.ENABLE_USER_MANAGEMENT`), `remove_project_access` DELETE `/{user_id}/projects/{project_name}` (L353).
- `src/codemie/rest_api/routers/projects/membership.py`: POST/DELETE `/projects/{projectName}/assignment[/{userId}]` (L41, L92). These use `Depends(_authorize_project_access)` to get the `Application`, open `get_session()`, call the service, then `session.commit()`.

**Runtime budget resolution (why spend continues)**
- `src/codemie/repository/project_budget_repository.py:380` `get_project_budget_context` joins `project_budget_assignments` with `project_member_budget_assignments` (`pmba_deleted_at IS NULL`) and `budgets`. It **never consults `user_projects`**. A leftover PMBA therefore resolves to PROJECT scope.
- `src/codemie/service/budget/budget_resolution_service.py`: `resolve` / `resolve_sync` cache the result in `_resolution_cache` (TTLCache keyed `(project_name, category, user_id)`; `BUDGET_RESOLUTION_CACHE_TTL=60`, `MAX_SIZE=50000` in `configs/config.py` L322–323).
- `src/codemie/enterprise/litellm/proxy_router.py`: `_validate_project_header` (~L420) only checks that the project is present and non-empty (`project_validator.require_valid_project`). `_extract_request_info` takes the project from the `X-CodeMie-Project` header, then `user.default_project`, then `user.username`. `_resolve_project_budget_runtime` (L926) calls `ensure_project_member_runtime_ready` and then `budget_resolution_service.resolve`, then `dispatch_runtime`. **The proxy never checks membership.**
- `src/codemie/enterprise/litellm/project_member_runtime_sync.py:89` `ensure_project_member_runtime_ready` resolves the budget and lazily syncs the PMBA to LiteLLM. It raises `RuntimeError` if the scope is PROJECT but no allocation is found.
- `src/codemie/enterprise/litellm/budget_provider_adapter.py:1266` `delete_member_allocation` calls `delete_project_member_budget_assignment`. The customer alias prefix is `codemie:project:` (L57).

### Architecture and Layers Affected
- API: `user_management_router.py` (admin users) and `projects/membership.py`.
- Service: `UserAccessService`, `ProjectAssignmentService` (holds the budget-sync helpers), `BudgetResolutionService`.
- Repository: `user_project_repository`, `application_repository`, `project_budget_repository` (the context query), `activity_event_repository`.
- Enterprise/provider: LiteLLM proxy router, `project_member_runtime_sync`, `budget_provider_adapter`.

### Integration Points
- `user_access_service` → `user_project_repository`, `application_repository`, `authentication_service.invalidate_user_from_cache`. It does not currently import `project_assignment_service` or anything budget-related.
- `project_assignment_service` → `budget_models`, `budget_resolution_service._resolution_cache`, `provider_registry.get_active_provider`, `activity_repository`, `project_spend_tracking_repository`.
- `authentication_service` and `project_service` already import `ProjectAssignmentService` and call its private `_sync_*` helpers directly. This sets a precedent for cross-service use.
- External: the LiteLLM admin API through the active budget provider (customer and member budget deletion).

### Patterns and Conventions
- Static-method services with module singletons (`user_access_service`, `project_assignment_service = ProjectAssignmentService()`).
- `ProjectAssignmentService` methods take a caller-owned `session` and never commit. `UserAccessService` methods own their `get_session()` and commit internally.
- Errors use `ExtendedHTTPException(code, message, details, help)`.
- Logging is structured `key=value`, with `budget_event=...` tokens and no PII.
- Activity events use `ActivityEventCreate(domain=USER_MANAGEMENT, event_type=UserManagementEvent.USER_PROJECT_ASSIGNED|REMOVED, entity_type=USER, entity_id=user_id, actor_id=actor.id, attributes={...})` (enum in `service/activity/activity_models.py` L64–65).
- The two services each have their own `_reject_if_personal_project` with different signatures. UAS logs through `_log_project_authorization_failure`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/architecture/service-layer-patterns.md` and `layered-architecture.md` (API→Service→Repository).
- `.ai-run/guides/testing/testing-service-patterns.md` ("Test service orchestration with repositories/providers mocked or controlled unless … integration-level") and `testing-api-patterns.md`.

### Architectural Decisions
- The module docstring of `project_assignment_service.py` says it moved assignment logic from the router to the service layer ("Code Review MEDIUM #5: Layering violation fix").
- The resolution cache comment says "TTL=60s matches the staleness tolerance for admin budget changes".
- The `get_project_budget_context` docstring states it returns None only when no active assignment plus member allocation exists. Membership is not part of that contract.

### Derived Conventions
- Budget sync on membership changes is done by calling the `ProjectAssignmentService._sync_project_budget_member_added/_removed(session, ...)` helpers inside the same transaction as the `user_projects` mutation.
- Callers outside PAS (IDP bootstrap, project delete) call the private helpers directly.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/project/test_project_assignment_service.py`: single and bulk assign/remove, `TestRejectIfCreator`, `TestGetMemberAddedAllocationAmounts`, `TestSyncProjectBudgetMemberAdded` (L711), `TestCaptureTerminalMemberSpend`, `TestSyncProjectBudgetMemberRemoved` (L1046; patches `get_active_provider`, `_run_budget_provider_coro`, `_capture_terminal_member_spend`).
- `tests/codemie/service/user/test_user_access_service.py`: grant (L116+: success, user not found, personal blocked, 409) and revoke (L376+: success, user not found, personal blocked, no access). These patch `codemie.clients.postgres.get_session` and the module-level `user_repository`, `user_project_repository` and `application_repository`.
- `tests/codemie/service/user/test_user_access_service_personal_constraints.py`: personal-project guard logging.
- `tests/codemie/rest_api/routers/test_user_management_router_crud.py`: router tests that mock `user_access_service` (L523+).
- `tests/codemie/rest_api/routers/test_projects_router.py`: projects membership routes.
- `tests/codemie/service/user/test_authentication_service.py` L1268+: IDP bootstrap budget sync.

### Testing Framework and Patterns
- pytest (`pytest.ini`) with `unittest.mock` `@patch` decorators on module-level singletons and `MagicMock` sessions (`session.exec.return_value.all.return_value = [...]`).
- Router tests use the FastAPI TestClient with the service mocked.

### Coverage Gaps
- None of the UAS grant/revoke tests check budget sync, activity events, the creator guard or resolution-cache eviction.
- No test checks that both removal paths produce the same side effects.
- No proxy/resolution test covers a user with a PMBA but no `user_projects` row.

---

## 5. Configuration and Environment

### Environment Variables
- `ENABLE_USER_MANAGEMENT` (default False) gates every admin users route.
- `USER_PROJECT_LIMIT` (3).
- `BUDGET_RESOLUTION_CACHE_TTL` (60s) and `BUDGET_RESOLUTION_CACHE_MAX_SIZE` (50000).
- `config.ENV`: `Environment.LOCAL` skips the UUID format check in PAS.

### Configuration Files
- `src/codemie/configs/config.py`
- `litellm_config.yaml` (local LiteLLM)

### Feature Flags and Deployment Concerns
- The budget provider is selected through `provider_registry.get_active_provider()`. With no provider, the helpers still soft-delete PMBAs.
- The resolution cache is per process. Popping it in one worker does not evict other workers, so a removed member can still resolve to PROJECT scope for up to 60s.

---

## 6. Risk Indicators

- **Transaction-model mismatch**: UAS methods own their session and commit, while the PAS helpers expect a caller session and flush only. Speculative: the delegation needs a shared, session-taking method that the UAS wrapper commits.
- **Behavioural divergence when delegating**: UAS `grant` auto-creates missing projects (`application_repository.get_or_create`) and treats a missing project as non-personal. PAS `assign` requires an existing `Application` and validates the UUID format. The two paths also return different 404/409 messages, and router tests assert UAS messages.
- **Creator guard is new for the Users mgmt DELETE path**: it would change the API response (400) for an admin who removes a project creator. Existing revoke tests may need updating.
- **Auth-cache asymmetry**: UAS calls `_invalidate_auth_cache`, but `ProjectAssignmentService.remove_user_from_project` and the `membership.py` router do not. A shared method needs to decide where cache invalidation lives.
- **Provider call inside the request**: `_sync_project_budget_member_removed` runs async LiteLLM calls synchronously through `_run_budget_provider_coro`. Failures are logged and swallowed, so the DB soft-delete still happens.
- **Defensive proxy check**: `get_project_budget_context` and `budget_resolution_service.resolve` do not check `user_projects`, and `_validate_project_header` does not check membership. Speculative: a membership check there would add a DB query per request on a hot path, would interact with the 60s TTL cache, and must keep background consumers and the `user.username` personal-project fallback working.
- **Existing stale data**: PMBAs already orphaned by past Users-mgmt removals stay in the DB. A code fix does not clean them up. Speculative: a backfill or one-off cleanup may be wanted.
- The private helpers (`_sync_*`) are already called from three other modules. Changing their signatures affects `authentication_service` and `project_service`.

---

## 7. Summary for Complexity Assessment

The change touches the API layer only marginally: `user_management_router.py` add/remove routes, and possibly `projects/membership.py` if a shared method changes the call shape. The work sits in the service layer. `UserAccessService.grant_project_access` and `revoke_project_access` must gain the side effects that `ProjectAssignmentService.assign_user_to_project` and `remove_user_from_project` already have: PMBA create or soft-delete, LiteLLM member-allocation deletion, `_resolution_cache` eviction, activity events, and the creator guard on removal. All of that logic already exists as reusable static helpers that three other modules call, so the work is wiring and consolidation, not new budget logic. The core surface is about 2 service files and 2 test files. The optional defensive check would add `proxy_router.py`, `budget_resolution_service.py` and `project_budget_repository.py`.

Technical novelty is low to moderate. The main design friction is reconciling the two transaction models (UAS commits internally, PAS relies on the caller to commit) and the behavioural differences: project auto-create on grant, missing-project handling in the personal guard, error messages, UUID validation, and auth-cache invalidation, which only UAS does. The optional proxy membership check is the riskiest part. It sits on the per-request hot path, interacts with a 60s per-process TTL cache, and has to keep background-consumer and personal-project fallbacks working.

Test posture is good for the PAS helpers (dedicated `TestSyncProjectBudgetMemberAdded/Removed` classes with clear mock patterns) and for UAS validation branches. However, the UAS tests cover no budget or activity side effects, and nothing proves the two paths are equivalent. Key risks are API-visible behaviour changes on the admin endpoints (a new 400 creator guard), orphaned PMBAs already in existing data, and cross-worker cache staleness after removal.

---

## 8. External References

None named by the task.
