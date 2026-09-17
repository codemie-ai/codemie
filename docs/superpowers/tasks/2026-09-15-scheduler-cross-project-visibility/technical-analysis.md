# Technical Research

**Task**: schedulers authorization project filtering visibility
**Generated**: 2026-09-15T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

When calling GET /v1/schedulers?page=0&pageSize=10&resourceType=Assistant, the response includes schedulers belonging to other projects and users that the authenticated user is not part of. This is a data leakage/authorization bug — users can see schedulers they should not have access to. The fix needs to scope scheduler listing to only projects/resources the current user is authorized to access.

---

## 2. Codebase Findings

### Existing Implementations

- `/src/codemie/rest_api/routers/schedulers.py` — FastAPI router for `/v1/schedulers`. Router-level `dependencies=[Depends(authenticate)]` (line 40–43) authenticates requests but does not surface the `User` object into the `list_schedulers` handler. `get_scheduler_filter_options` (line 50) correctly injects `user=Depends(authenticate)` and forwards it to the service — making the bug an inconsistency between two sibling endpoints.
- `/src/codemie/service/settings/scheduler_settings_service.py` — `SchedulerSettingsService.list_schedulers()` (line 493–565). Accepts `page`, `per_page`, `search`, `resource_type`, `project_id`, `resource_id`, `status`, `last_run_status`, `owner_type` but no `user` parameter. `_build_list_filters()` (line 444–490) builds raw SQL conditions; the only mandatory condition is `credential_type = 'SCHEDULER'`. All other filters are optional and client-supplied.
- `/src/codemie/rest_api/models/settings.py` — `Settings` SQLModel (the physical storage for schedulers; rows have `credential_type = 'SCHEDULER'`). Ownership fields: `user_id: Optional[str]` (indexed), `project_name: str` (indexed, GIN trigram), `setting_type: SettingType` (`USER` or `PROJECT`). The `Scheduler` Pydantic model (line 219) describes JSONB `credential_values` content only — not a DB table.
- `/src/codemie/repository/scheduler_run_repository.py` — repository for scheduler run records only; not involved in the listing bug.
- `/src/codemie/rest_api/models/scheduler_run.py` — Pydantic response model for scheduler runs.

### Architecture and Layers Affected

| Layer | Component | Change Required |
|---|---|---|
| API / Router | `schedulers.py` — `list_schedulers` | Add `user: User = Depends(authenticate)` parameter; pass `user` to service |
| Service | `SchedulerSettingsService.list_schedulers` | Accept `user` param; derive accessible project names; pass to `_build_list_filters` |
| Service | `SchedulerSettingsService._build_list_filters` | Add mandatory SQL condition scoping to `user.project_names` / `user.id` (skip for admins) |
| Service (secondary) | `SchedulerSettingsService.get_filter_options` / `_fetch_all_scheduler_settings` | Same user-scoping fix needed; also: the `_filter_options_cache` is a shared global dict with no per-user key segmentation — it leaks project names across users |

### Integration Points

- `codemie.rest_api.security.authentication.authenticate` — the central FastAPI dependency; resolves `User` from JWT/bearer token; already used correctly in `get_scheduler_filter_options`
- `codemie.rest_api.security.user.User` — carries `user.id`, `user.project_names`, `user.admin_project_names`, `user.is_admin_or_maintainer`; no changes needed to this class
- `codemie.settings` PostgreSQL table — `user_id` and `project_name` columns already exist and are indexed; no schema migration needed

### Patterns and Conventions

The established pattern for list endpoints (from `user_settings.py` lines 145–151 and `analytics.py`):

```python
# In the router endpoint
user: User = Depends(authenticate)

# In the service / repository
if user.is_admin_or_maintainer:
    # no project filter — admins see all
else:
    accessible_projects = set(user.project_names or []) | set(user.admin_project_names or [])
    # apply: s.project_name = ANY(:project_names)
    # for USER-type settings also apply: s.user_id = :user_id
```

`_find_schedule_by_resource_id` in the same service class already demonstrates this correctly: it passes `user_id` and `project_name` as required `SearchFields`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `/Users/yevhen_slyva/codemie-dev/codemie/.ai-run/guides/development/security-patterns.md` — mandates use of the central `authenticate` dependency for all authenticated routes; prohibits inline bearer parsing; requires role-based helpers for authorization checks.
- `/Users/yevhen_slyva/codemie-dev/codemie/.ai-run/guides/data/repository-patterns.md` — repositories own all data access; services must not bypass them.
- `/Users/yevhen_slyva/codemie-dev/codemie/.ai-run/guides/api/rest-api-patterns.md` — depend on `authenticate` and authorization helpers; never duplicate role checks in endpoint code.

### Architectural Decisions

- Authentication is centralized through `authenticate`; authorization (project membership) is enforced by passing the resolved `User` to the service layer and filtering by `user.project_names`.
- Schedulers are stored as `Settings` rows (`credential_type='SCHEDULER'`) rather than a dedicated table. Project and user ownership are captured via `project_name` and `user_id` plain-string columns.

### Derived Conventions

- Endpoints that expose list operations over multi-tenant data must inject `user=Depends(authenticate)` and forward the user into the service layer — not just rely on router-level authentication.
- The `is_admin_or_maintainer` check is the global-bypass condition: admins see all records.

---

## 4. Testing Landscape

### Existing Coverage

- `/tests/codemie/rest_api/routers/test_schedulers.py` — router-level tests exist. Auth is bypassed via `app.dependency_overrides[auth_module.authenticate] = lambda: None`, injecting `None` as the user. No test verifies that listing is scoped to the authenticated user's projects.
- `test_build_list_filters_appends_setting_type_when_owner_type_provided` — tests the `owner_type` → `setting_type` filter path only.
- `test_router_forwards_owner_type_to_service` — calls `list_schedulers()` with no user argument, confirming there is currently no user injection.

### Testing Framework and Patterns

- pytest with `unittest.mock` (MagicMock, patch)
- Auth bypass: `app.dependency_overrides[authenticate] = lambda: None`
- No fixtures or factories for `Settings`/`Scheduler` rows; tests build model instances inline with local helpers

### Coverage Gaps

- No test that `GET /v1/schedulers` scopes results to the authenticated user's accessible projects
- No test that a user from project A cannot see schedulers belonging to project B
- No test that omitting `projectId` does not return all schedulers across all tenants
- No test for admin bypass (admins should see all)
- `_filter_options_cache` per-user key segmentation is completely untested

---

## 5. Configuration and Environment

### Environment Variables

- No scheduler-specific environment variables found in `.env` or `.env.example`.
- `ENABLE_USER_MANAGEMENT` flag (used by `authenticate`) controls whether the legacy or persistent user provider is used — relevant to how `user.project_names` is populated.

### Configuration Files

- `/src/codemie/service/customer_config_declarations.py` — `SCHEDULERS = SettingDeclaration(component_id="features:schedulersView", ...)` gates the schedulers feature for customers. Not directly involved in the auth bug.

### Feature Flags and Deployment Concerns

- No feature flag gates the authorization fix itself.
- The `_filter_options_cache` in `SchedulerSettingsService` is a module-level shared dict — fixing it requires either keying by user identity or invalidating it per request. This is a secondary concern but is a real data-leakage vector.

---

## 6. Risk Indicators

- **No authorization test coverage** for `GET /v1/schedulers` — cross-project data leakage is completely untested; regression risk is high without new tests.
- **`get_scheduler_filter_options`** already injects `user=Depends(authenticate)` but the downstream `_fetch_all_scheduler_settings()` call is not user-scoped — this endpoint may have a parallel data-leakage issue.
- **`_filter_options_cache`** is a module-level shared dict keyed by `(resource_type, project_id)` only. A user in project A can receive cached filter options that include resource names from project B. This is a secondary data-leakage vector that must be addressed alongside the main fix.
- **No DB migration needed** — `user_id` and `project_name` columns already exist on `codemie.settings`; this reduces risk significantly.
- **Router-level `dependencies=[Depends(authenticate)]`** is a misleading pattern — it runs authentication but the resolved `User` is silently discarded by `list_schedulers`, making the bug non-obvious from a code-review standpoint. Comment or inline documentation should be added to make the injection explicit.
- **Auth mock `lambda: None`** in existing tests means tests pass even with `None` user. New tests need a real mock `User` object with populated `project_names` to be meaningful.
- **`_build_list_filters` raw SQL** — the fix must use parameterized values (`:project_names` with `ANY`) to avoid SQL injection when constructing the array condition.

---

## 7. Summary for Complexity Assessment

The bug is confined to two tightly coupled components: the `list_schedulers` router handler in `/src/codemie/rest_api/routers/schedulers.py` and `SchedulerSettingsService._build_list_filters` in `/src/codemie/service/settings/scheduler_settings_service.py`. No schema migration is required because `user_id` and `project_name` columns already exist on the `codemie.settings` table. The fix pattern is well-established and used in at least three other list endpoints in the same codebase (`user_settings.py`, `analytics.py`, `settings_index_service.py`). The estimated file change surface is 2–3 source files and 1 test file.

The task follows an existing, documented pattern rather than introducing a novel approach. The `User` dependency, the `project_names` property, and the `is_admin_or_maintainer` bypass are all already present and tested elsewhere. The secondary concern — the `_filter_options_cache` leaking cross-project filter options — adds minor complexity: the cache must be re-keyed to include user identity or removed, but this is a straightforward dict-key change.

Test coverage for the affected area is a significant gap: no test currently verifies multi-tenant isolation for the scheduler listing endpoint. New tests must mock a `User` object with specific `project_names` and assert that results are filtered accordingly. The auth override pattern (`lambda: None`) must be replaced or supplemented with a mock user for the new test cases. Overall complexity is low-to-medium: the code change is small and pattern-following, but the security sensitivity (data leakage) and the need for meaningful new authorization tests elevate the care required.
