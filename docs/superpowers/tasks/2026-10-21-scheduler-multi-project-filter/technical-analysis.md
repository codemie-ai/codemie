# Technical Research

**Task**: scheduler list filters project resource
**Generated**: 2026-10-21
**Research path**: codegraph

---

## 1. Original Context

feature_area: scheduler list filters project resource
run_dir: docs/superpowers/tasks/2026-10-21-scheduler-multi-project-filter

task_context (verbatim ticket EPMCDME-16235; this repository is the BACKEND `codemie` repo, so this run covers MR 1 (backend) only — the frontend `codemie-ui` MR is a separate repo and out of scope here):

Summary: Scheduler page: project selector has checkboxes but it's not possible to check several projects, only one checkbox can be checked. Expected - user can select several projects

Description: The project selector on the Scheduler page displays a checkbox for each project, which suggests that several projects can be selected at once. In practice only one checkbox can be checked at a time. Checking another project clears the previous selection. Users cannot view or manage scheduled items for several projects at once.
Affected areas: Scheduler page project selector; project selection state and the filtering of Scheduler content by selected projects.

Acceptance Criteria (ticket):
1. The user can check two or more projects in the Scheduler page project selector, and all of them stay checked.
2. The user can uncheck any selected project without affecting the other selected projects.
3. The Scheduler page content reflects all projects currently selected.
4. The selector's behavior matches its checkbox appearance (multi-select).

Technical analysis comment on the ticket (from product-owner):
Delivery: one ticket, two merge requests. Backend (`codemie`) first, then frontend (`codemie-ui`).
Root cause:
- Frontend `SchedulerFilters.tsx`: `<ProjectSelector multiple>` but `projectId` is a single string and `handleApply` keeps only `projectIdArr?.[0]`. Resource filter has the same bug.
- Backend, `src/codemie/rest_api/routers/schedulers.py` `list_schedulers`: `project_id` and `resource_id` are `Optional[str]`. `SchedulerSettingsService._build_list_filters` (`src/codemie/service/settings/scheduler_settings_service.py`) filters with `s.project_name = :project_id` and `cv->>'value' = :resource_id`, so only one value is supported.
Decisions: D1 Resource filter fixed in this ticket too. D2 OR semantics: schedulers matching ANY selected project (and any selected resource). D3 Scheduler Run History page out of scope. D4 One ticket, two MRs.
MR 1 backend: `GET /v1/schedulers`: change `projectId` and `resourceId` to `list[str] | None = Query(None)` (repeated query params; precedent: `activity_events_router.py`). A single value must keep working (backward compatible). `_build_list_filters`: `s.project_name = ANY(:project_ids)` and `cv->>'value' = ANY(:resource_ids)`. Keep the existing non-admin restriction (`s.project_name = ANY(:project_names)`): selecting a project the user cannot access must not widen the result. Make sure the pagination count query uses the same conditions. Tests: `tests/codemie/rest_api/routers/test_schedulers.py`, `tests/codemie/service/settings/test_scheduler_settings_service*.py`; cover single value, several values, empty list, non-admin user selecting a project outside their access.
Refined AC: 1-3 as above; 4 also for Resource filter; 5 negative: non-admin never sees schedulers from projects they lack access to; 6 clearing all selections shows unfiltered list.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/rest_api/routers/schedulers.py:54-78` — `GET /v1/schedulers` handler `list_schedulers`. `project_id: Optional[str] = Query(None, alias="projectId")` (line 60), `resource_id: Optional[str] = Query(None, alias="resourceId")` (line 61). Forwards all params by keyword to `SchedulerSettingsService.list_schedulers`. `resource_type`, `status`, `last_run_status`, `owner_type`, `search` are also single `Optional[str]` (not in scope).
- `src/codemie/service/settings/scheduler_settings_service.py:517` — `list_schedulers(page, per_page, search, resource_type, project_id, resource_id, status, last_run_status, owner_type, user)`. Calls `_build_list_filters(project_id, resource_type, resource_id, search, status, last_run_status, owner_type, user=user)` (positional for the first six), builds `where = "WHERE " + " AND ".join(conditions)`, then runs a count query and a rows query with the same `where` and `params` (`count_sql.bindparams(**params)`, rows with `{**params, limit, offset}`). The count and rows queries already share the same conditions, so no separate count change is visible in this code.
- `scheduler_settings_service.py:466-514` — `_build_list_filters` (static). Current logic:
  - non-admin (`user is not None and not user.is_admin_or_maintainer`): empty `user.project_names` appends `"1=0"`; otherwise appends `s.project_name = ANY(:project_names)` with `params["project_names"] = list(...)`.
  - `if project_id:` appends `s.project_name = :project_id`.
  - `if resource_id:` appends `EXISTS (SELECT 1 FROM jsonb_array_elements(s.credential_values::jsonb) cv WHERE cv->>'key' = 'resource_id' AND cv->>'value' = :resource_id)`.
  - `resource_type`, `search`, `status`, `last_run_status`, `owner_type` handled separately; the project scope and `project_id` are independent ANDed conditions, so an explicit project outside the user's scope yields an empty intersection, not a widened result.
- `scheduler_settings_service.py:595-607` — `_fetch_all_scheduler_settings` / `get_filter_options` (line 661) feed the selector options; already user-scoped via `Settings.project_name.in_(project_names)`. Not a filter on the list endpoint.
- Callers of `_build_list_filters` and `list_schedulers`: only `list_schedulers` (service) calls `_build_list_filters`; only the router calls `SchedulerSettingsService.list_schedulers` (codegraph blast radius). `schedulers.py` also has `/scheduler-runs*` endpoints with their own `resourceId` (single) — decision D3 puts these out of scope.

### Architecture and Layers Affected
- Router layer: `src/codemie/rest_api/routers/schedulers.py` (prefix `/v1`, `dependencies=[Depends(authenticate)]`).
- Service layer: `SchedulerSettingsService` (extends `BaseSettingsService`), raw SQL via `sqlalchemy.text` against `codemie.settings s` with a `LEFT JOIN LATERAL` on `codemie.scheduler_runs`. No repository layer in this path.
- Response models: `src/codemie/rest_api/models/scheduler_run.py` (`SchedulersPaginatedResponse`) — unchanged by the filter shape.

### Integration Points
- Router -> `SchedulerSettingsService.list_schedulers` (keyword args) -> `_build_list_filters` -> `Settings.get_engine().connect()`.
- Frontend `codemie-ui` consumes `projectId` / `resourceId` query params (separate repo, out of scope).

### Patterns and Conventions
- Multi-value query precedent: `src/codemie/rest_api/routers/activity_events_router.py:55-59` uses `actor_id: list[str] | None = Query(None)`, `domain`, `event_type`, `entity_type` the same way (repeated params, no alias; those names are snake_case, whereas scheduler params use camelCase `alias=`).
- Array binding precedent in the same function: `s.project_name = ANY(:project_names)` with a Python list in params.
- Another multi-value convention exists in analytics (`AnalyticsFilterParams`, comma-separated string split in `projects_list`); the ticket directs the repeated-param form instead.
- Conditions are built as string fragments plus a `params` dict; empty values are skipped with truthiness (`if project_id:`), so an empty list is falsy and skipped.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists. Relevant by area: `api/rest-api-patterns.md`, `api/endpoint-conventions.md`, `architecture/service-layer-patterns.md`, `data/database-patterns.md`, `development/security-patterns.md`, `testing/testing-api-patterns.md`, `testing/testing-service-patterns.md`, `testing/testing-patterns.md`, `quality-gates.md`. Not opened in this run (codegraph and source were sufficient).

### Architectural Decisions
Only the ticket decisions D1-D4 (Section 1). No ADR or inline comment found on the list-filter semantics.

### Derived Conventions
Authorization for non-admins is enforced inside `_build_list_filters` by an always-present ANDed `project_names` condition, independent of client-supplied filters.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/rest_api/routers/test_schedulers.py` — `test_router_forwards_owner_type_to_service` (line 495) calls the router function directly with keyword args `project_id=None, resource_id=None, ...` and patches `codemie.rest_api.routers.schedulers.SchedulerSettingsService`; asserts forwarded kwargs. Also `test_build_list_filters_appends_setting_type_when_owner_type_provided` (line 518) and other scheduler/run repository tests.
- `tests/codemie/service/settings/test_scheduler_settings_service_class.py` — `_make_user` Mock helper (line 337, `is_admin_or_maintainer`, `project_names`); `test_build_list_filters_non_admin_adds_project_scope` (345), `..._admin_skips_project_scope` (362), `..._explicit_project_id_adds_alongside_scope` (378; asserts `":project_id"` in a condition and `params.get("project_id") == "proj-a"`), `..._empty_project_names_returns_no_rows_condition` (415), owner-type user-scoping tests (441, 460).
- `tests/codemie/service/settings/test_scheduler_settings_service.py` — second file matched by the `test_scheduler_settings_service*.py` glob; contents not inspected.

### Testing Framework and Patterns
pytest; unit tests assert on the `(conditions, params)` tuple returned by `_build_list_filters` and on kwargs forwarded by the router function; `unittest.mock.Mock`/`patch`. No DB-backed execution of the SQL in the files inspected.

### Coverage Gaps
- No test found exercising multi-value `project_id`/`resource_id`, nor a router-level test through FastAPI query parsing (the router tests call the function directly, so `Query` parsing of repeated params is not exercised).
- No test of the executed SQL (`ANY(...)` against real Postgres); everything inspected checks string fragments and params only.
- The existing test at line 378 asserts the current parameter name/shape (`:project_id`, `params["project_id"] == "proj-a"`); it is coupled to the current implementation.

---

## 5. Configuration and Environment

### Environment Variables
None found related to this endpoint.

### Configuration Files
None for this path. `customer_config_declarations.py` `SCHEDULERS` (`features:schedulersView`) only toggles Schedulers navigation visibility in the UI.

### Feature Flags and Deployment Concerns
No flag gates the list endpoint. `get_filter_options` has an in-class cache (`_filter_options_cache`, TTL 60 s), unrelated to list filtering.

---

## 6. Risk Indicators

- Speculative: the `project_id`/`resource_id` parameter names and types will change in the router, service `list_schedulers` and `_build_list_filters`; existing tests at `test_scheduler_settings_service_class.py:378` and router test calls passing `project_id=None` may need updating depending on the chosen names.
- `_build_list_filters` takes the first six arguments positionally and `list_schedulers` passes them positionally; renaming or retyping arguments without matching order changes would silently mis-bind. Only one production caller was found.
- Single-value backward compatibility: FastAPI parses a single `?projectId=x` into `["x"]` for `list[str]` with `Query`, so old clients keep working; any internal caller passing a bare `str` to the service would be treated as an iterable of characters if the service is not defensive. Codegraph shows the router as the only caller.
- Authorization: the `project_names` scope condition is independent and ANDed, so the non-admin guarantee should hold unchanged as long as that block is not touched; the empty-`project_names` `1=0` branch must stay. This is the security-relevant behavior (AC 5) with an existing test only for the single-id case.
- Empty list vs. absent: `Query(None)` yields `None` when absent; an explicit empty value cannot be sent as a repeated param, but a service-level `[]` is falsy and is skipped (equals unfiltered, matching AC 6).
- Bound-parameter style: `ANY(:name)` with Python lists is already used in the same function (`:project_names`); no new binding pattern is needed.
- `cv->>'value' = :resource_id` sits inside a `jsonb_array_elements` EXISTS subquery; the `ANY` form stays inside that subquery. Condition-shape tests are string-based, so SQL validity is not covered by existing tests.
- Frontend `codemie-ui` still sends a single value until its MR lands; ordering (backend first) is compatible.
- `/scheduler-runs*` endpoints keep a single `resourceId`; out of scope per D3 but share naming, a possible source of confusion.

---

## 7. Summary for Complexity Assessment

The change sits in two layers of one endpoint: the router signature in `src/codemie/rest_api/routers/schedulers.py` (two query params) and `SchedulerSettingsService.list_schedulers` / `_build_list_filters` in `src/codemie/service/settings/scheduler_settings_service.py` (two filter blocks). The count and rows queries already share one `conditions`/`params` pair. The only production caller of the service method is the router, and `_build_list_filters` has one production caller. Two source files and two existing test files are involved.

There is little technical novelty. The repeated-query-param form has a precedent in `activity_events_router.py`, and the `= ANY(:list)` binding is already used in the same function for the non-admin project scope. The non-admin restriction is a separate ANDed condition, so it is structurally independent of the client-supplied filter.

Test posture is moderate. Unit tests assert on the returned `(conditions, params)` and on router kwargs forwarding; no test runs the SQL or goes through FastAPI query parsing, and no multi-value case exists. One existing test (line 378) is coupled to the current param name. Main risks are positional argument binding between service and filter builder, keeping the non-admin scope intact (AC 5), and the lack of SQL-level test coverage.

---

## 8. External References

None named by the task. (`SchedulerFilters.tsx` is in the separate `codemie-ui` repo, out of scope and not read.)
