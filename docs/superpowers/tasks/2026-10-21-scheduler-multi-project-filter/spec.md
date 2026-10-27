# Spec: Scheduler list accepts several projects and resources (EPMCDME-16235, backend MR 1)

## Problem

The Scheduler page project selector looks multi-select, but `GET /v1/schedulers` takes one
`projectId` and one `resourceId` (`src/codemie/rest_api/routers/schedulers.py:60-61`), and
`SchedulerSettingsService._build_list_filters` compares them with `=`
(`src/codemie/service/settings/scheduler_settings_service.py:481-495`). The backend cannot
express "schedulers of any of these projects", so the frontend (separate `codemie-ui` MR, out
of scope here) has no way to send more than one.

## Decisions already settled (ticket)

- The Resource filter gets the same change as the Project filter.
- OR semantics: a scheduler matches if it belongs to ANY selected project and matches ANY selected resource. The two filters (and all other filters) stay ANDed with each other.
- One ticket, two MRs; backend first. This spec covers the backend only.

## Behavior

1. `GET /v1/schedulers` accepts `projectId` and `resourceId` as repeated query params
   (`?projectId=a&projectId=b`). Type is `list[str] | None = Query(None, alias=...)`, the form
   used by `activity_events_router.py:55-59`. A single `?projectId=a` still works unchanged.
2. `SchedulerSettingsService.list_schedulers` and `_build_list_filters` take the lists
   (renamed `project_ids`, `resource_ids`; position in the argument list unchanged).
3. Project condition: `s.project_name = ANY(:project_ids)`. Resource condition: the existing
   `EXISTS (... jsonb_array_elements ... cv->>'key' = 'resource_id' AND cv->>'value' = ANY(:resource_ids))`
   - `ANY` stays inside the subquery.
4. Absent, `None`, empty list, or only blank strings = no filter for that dimension. Blank
   entries are dropped so `?projectId=` keeps today's "unfiltered" behavior instead of
   matching nothing.
5. The non-admin scope block (`s.project_name = ANY(:project_names)`, and `1=0` when the user has no
   projects) is not modified. It is an independent ANDed condition, so a non-admin who selects
   a project outside their access gets the intersection (empty for that project), never a widened result.
6. The count query and rows query keep sharing the single `conditions`/`params` pair in
   `list_schedulers`; nothing to change there beyond the renamed params.

## Acceptance criteria

1. Two or more `projectId` values return schedulers of all those projects.
2. Dropping one value from the request leaves the remaining values applied.
3. The result contains schedulers belonging to any selected project (OR).
4. Criteria 1-3 hold for `resourceId` (any selected resource).
5. A non-admin never receives schedulers from projects outside their access, whatever `projectId` values are sent, including a mix of accessible and inaccessible projects.
6. No `projectId`/`resourceId` (or an empty/blank value) returns the unfiltered list.
7. A single `projectId` / `resourceId` behaves exactly as before.
8. Pagination count reflects the same filtered set as the rows.

## Tests

- Service (`tests/codemie/service/settings/test_scheduler_settings_service_class.py`): update the
  test at line 378 (it asserts `:project_id`) to the new `:project_ids` / list param; add cases
  for single value, several values, empty list, blank values, resource ids (single and
  several), and non-admin with a project outside their access (scope condition and
  `project_names` param still present alongside the filter).
- Router (`tests/codemie/rest_api/routers/test_schedulers.py`): update existing direct-call
  tests to the renamed kwargs, and add a test that repeated query params reach the service
  as lists (use FastAPI `TestClient` so `Query` parsing is exercised; the existing tests call the
  function directly and would not catch it).

## Non-goals

- Frontend changes (`codemie-ui` `SchedulerFilters.tsx`, `ProjectSelector`) - separate MR.
- Scheduler Run History endpoints (`/scheduler-runs*`) keep a single `resourceId` (D3).
- Making `resourceType`, `status`, `lastRunStatus`, `ownerType` or `search` multi-valued.
- Comma-separated value format (analytics style); repeated params only.
- Changes to the non-admin scope logic, `get_filter_options`, response models, or pagination.
- Running the SQL against a real Postgres in new tests (existing tests are string/param-level).

## Risks

- Unit tests do not execute the SQL; `ANY(:list)` with a Python list is already used in the same function, which limits the risk.
- Service args are bound positionally between `list_schedulers` and `_build_list_filters`; keep the order and update both call sites together.
