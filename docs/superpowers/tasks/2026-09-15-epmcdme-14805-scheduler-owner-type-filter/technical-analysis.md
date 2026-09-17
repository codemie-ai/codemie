# Technical Research

**Task**: schedulers api query filter owner_type
**Generated**: 2026-09-15T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Add optional `ownerType` query parameter to GET /v1/schedulers endpoint. The parameter accepts "User" or "Project" values and filters schedulers by owner type. When absent, current behavior is preserved.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/routers/schedulers.py` — FastAPI router; `list_schedulers()` function (line 54–74) defines the `GET /v1/schedulers` endpoint. Currently accepts: `page`, `pageSize`, `search`, `resourceType`, `projectId`, `resourceId`, `status`, `lastRunStatus`. Delegates to `SchedulerSettingsService.list_schedulers()`.
- `src/codemie/service/settings/scheduler_settings_service.py` — `SchedulerSettingsService.list_schedulers()` (line 489–560) builds raw SQL via `_build_list_filters()` (line 444–486) and executes it against `codemie.settings` table. All filter conditions are appended to a `conditions` list and bound via `params` dict using SQLAlchemy `text()`.
- `src/codemie/rest_api/models/settings.py` — `Settings` SQLModel; `setting_type` column (line 260) of type `SettingType` enum. `SettingType` (line 244–247) has two values: `USER = "user"` and `PROJECT = "project"`. This is the database column that maps to the `ownerType` filter.
- `src/codemie/rest_api/models/scheduler_run.py` — Pydantic response/request models for the schedulers API; no `ownerType` field exists yet.

### Architecture and Layers Affected

- **API layer**: `src/codemie/rest_api/routers/schedulers.py` — add `owner_type: Optional[str] = Query(None, alias="ownerType")` parameter and pass it to the service.
- **Service layer**: `src/codemie/service/settings/scheduler_settings_service.py` — extend `_build_list_filters()` to accept and handle `owner_type`; extend `list_schedulers()` signature to forward `owner_type`.

### Integration Points

- `codemie.settings` PostgreSQL table — the raw SQL in `_build_list_filters` filters against `s.credential_type = 'SCHEDULER'`; the `owner_type` filter would add `s.setting_type = :owner_type` (after lowercasing the incoming "User"/"Project" to "user"/"project").
- `SettingType` enum in `settings.py` — `SettingType.USER.value == "user"`, `SettingType.PROJECT.value == "project"`. The incoming API values "User" / "Project" must be normalized to lowercase before comparison.

### Patterns and Conventions

- All existing query filters in `_build_list_filters` follow the same pattern: check the value, append a string condition to `conditions`, bind the value via `params`. Example from `project_id` filter: `conditions.append("s.project_name = :project_id"); params["project_id"] = project_id`.
- The `owner_type` filter is simpler — it is a direct column match (`s.setting_type = :owner_type`) rather than a JSONB subquery.
- API uses camelCase aliases (`alias="ownerType"`) on all multi-word query params — must follow the same convention.
- Optional parameters default to `None`; no filter applied when absent.

---

## 3. Documentation Findings

### Guides and Architecture Docs

Relevant guides available at `.ai-run/guides/`:
- `.ai-run/guides/api/rest-api-patterns.md` — FastAPI router patterns (P0 for API tasks).
- `.ai-run/guides/api/endpoint-conventions.md` — Route and response conventions.
- `.ai-run/guides/architecture/service-layer-patterns.md` — Service orchestration patterns.
- `.ai-run/guides/data/database-patterns.md` — SQLModel and session patterns.

### Architectural Decisions

- All filter logic is centralized in `_build_list_filters()` — new filters must be added there, not inlined in `list_schedulers()`.
- Raw SQL (`text()`) is used for the paginated list query to support lateral joins for `last_run`; filter extension follows the existing `conditions` / `params` accumulator pattern.

### Derived Conventions

- Incoming string filter values are compared case-insensitively where applicable (e.g., `lower(:resource_type)` for `resource_type`). For `owner_type`, the incoming "User"/"Project" values should be lowercased to match `setting_type` column values "user"/"project".
- No enum validation is done at the router layer for existing string filters (e.g., `status`, `resource_type`); invalid values silently return empty results. Same pattern can apply to `owner_type`, or a lightweight `Literal["User", "Project"]` annotation can be used for documentation clarity.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/routers/test_schedulers.py` — comprehensive test file covering: repository CRUD, service layer, router endpoints via `TestClient`, filter-options, and the `_build_list_filters` behavior indirectly through router tests.
- Tests for `list_schedulers` router function: `test_router_parses_comma_separated_status` and `test_router_passes_none_status_when_omitted` demonstrate the direct-call pattern used (calling the route function directly, not via HTTP client).
- No tests currently cover the `list_schedulers` router function directly for the other filter params (`resourceType`, `projectId`, `resourceId`, `search`, `lastRunStatus`).

### Testing Framework and Patterns

- **Framework**: pytest with `unittest.mock.patch` and `MagicMock`.
- **Router tests**: Two patterns used — (a) direct function call to the route handler with keyword arguments; (b) `TestClient` with `app.dependency_overrides` for auth bypass.
- **Service tests**: `patch` on `SchedulerSettingsService` class methods.
- **SQL tests**: Capture raw SQL string via `fake_execute` to assert clause presence.

### Coverage Gaps

- No existing test for `_build_list_filters` receiving an `owner_type` argument — new tests needed.
- No test for the router passing `ownerType` query param through to the service.
- No test for `list_schedulers` service forwarding `owner_type` to `_build_list_filters`.

---

## 5. Configuration and Environment

### Environment Variables

No new environment variables required. The filter operates against existing database columns.

### Configuration Files

No configuration file changes required.

### Feature Flags and Deployment Concerns

No feature flags required. The parameter is optional with backward-compatible default (`None`). No migration is needed — `setting_type` column already exists in `codemie.settings`.

---

## 6. Risk Indicators

- `_build_list_filters` uses raw SQL strings — the `owner_type` condition must be carefully constructed to avoid SQL injection; bind via `params` dict as done for all other filters (never string-interpolated).
- Incoming API values "User"/"Project" are capitalized; the `setting_type` column stores lowercase "user"/"project" — case normalization (`lower()` or `.casefold()`) is required before binding.
- No existing test directly validates `_build_list_filters` SQL output for the `owner_type` condition — the SQL assertion pattern (capturing raw SQL string) used in `test_repository_list_runs_multi_status_builds_in_clause` should be replicated for this filter.
- `list_schedulers` in `scheduler_settings_service.py` has a long signature — adding `owner_type` extends it further; no architectural concern but reviewers should verify the param is forwarded consistently through all three layers (router → `list_schedulers()` → `_build_list_filters()`).

---

## 7. Summary for Complexity Assessment

The task touches two layers: the API layer (`routers/schedulers.py`) and the Service layer (`scheduler_settings_service.py`). File change surface is minimal — exactly two production files require modification: the router function `list_schedulers` gains one new `Query` parameter, and the service gains one new argument flowing through `list_schedulers()` → `_build_list_filters()`. No model changes, no migration, no new dependencies.

The change follows a well-established pattern already used for six other query filters in `_build_list_filters`. The only non-trivial detail is case normalization: the API surface uses capitalized values ("User"/"Project") while the database stores lowercase ("user"/"project"), requiring a `.lower()` call on the incoming value before binding. This is a shallow pattern that appears once in the conditions append.

Test coverage for the affected code is present but does not yet cover `ownerType` specifically. Three new test cases are needed: (1) router passes `ownerType` to the service, (2) `_build_list_filters` appends the correct SQL condition when `owner_type` is provided, and (3) the condition is absent when `owner_type` is `None`. The testing pattern is well-established in the existing test file and requires no new fixtures. Overall complexity is low.
