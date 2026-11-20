DONE

## Commit

5e1d6faaf — "EPMCDME-14325: Add project-scoped activity events router (task 12)"

`git show --stat HEAD`:
```
 src/codemie/rest_api/main.py                                       |   2 +
 src/codemie/rest_api/routers/project_activity_events_router.py     |  76 +++++++++++
 tests/codemie/rest_api/routers/test_project_activity_events_router.py | 148 +++++++++++++++++++++
 3 files changed, 226 insertions(+)
```

## What was done

1. Read the sibling router `src/codemie/rest_api/routers/activity_events_router.py` and sibling test
   `tests/codemie/rest_api/routers/test_activity_events_router.py` for conventions.
2. Verified exact insertion points in `src/codemie/rest_api/main.py` (import at line 126, include_router at line 1024)
   and confirmed `project_admin_or_admin_or_maintainer_access` exists at
   `src/codemie/rest_api/security/authentication.py:343`.
3. TDD: wrote `tests/codemie/rest_api/routers/test_project_activity_events_router.py` first
   (class `TestListProjectActivityEvents`, 5 tests) — confirmed RED
   (`ModuleNotFoundError: No module named 'codemie.rest_api.routers.project_activity_events_router'`).
4. Created `src/codemie/rest_api/routers/project_activity_events_router.py`:
   - `router` with `prefix="/v1/projects/{project_name}/activity-events"`, `tags=["activity-events"]`,
     `dependencies=[Depends(authenticate)]`.
   - Single endpoint `list_project_activity_events` with `project_name: str` as a plain path parameter,
     query params `domain`, `event_type`, `actor_id`, `from_`/`to`, `sort_dir`, `limit`, `offset`, and
     `_: None = Depends(project_admin_or_admin_or_maintainer_access)`.
   - Calls `activity_event_service.list_project_events(project_name, domain=..., event_type=..., actor_id=...,
     from_dt=..., to_dt=..., sort_dir=..., limit=..., offset=...)`, wraps result in
     `PaginatedListResponse`/`PaginationData`, and wraps unexpected exceptions in `ExtendedHTTPException(500)`
     while re-raising `ExtendedHTTPException` unchanged.
   - No `get_filter_options` endpoint (per plan — project-scoped view has none).
5. Registered the router in `src/codemie/rest_api/main.py`: added
   `from codemie.rest_api.routers import project_activity_events_router` right after the
   `activity_events_router` import, and `app.include_router(project_activity_events_router.router)` right
   after `app.include_router(activity_events_router.router)`.
6. Confirmed GREEN: all 5 new tests pass.

## Verification commands run (all as specified)

1. `poetry run pytest tests/codemie/rest_api/routers/test_project_activity_events_router.py -v` →
   `5 passed in 0.41s`, EXIT=0. Log at
   `docs/superpowers/tasks/2026-09-17-project-scoped-activity-trail/test.local.log`.
2. `poetry run pytest tests/codemie/rest_api/routers/ -q` → `1484 passed, 7 skipped, 123 warnings` — no new
   failures; `test_activity_events_router.py` and all sibling router tests unaffected.
3. `poetry run python -c "from codemie.rest_api.main import app; print('ok')"` → printed `ok`; app imports
   and router registration is clean.
4. `poetry run ruff check` on the three touched/created files → `All checks passed!`.
   `poetry run ruff format --check` on the same files → `3 files already formatted` (no changes needed).

## Concerns

None. Scope was followed exactly: only `main.py`, the new router file, and the new test file were touched/
staged/committed. `activity_events_router.py`, `test_activity_events_router.py`,
`activity_event_service.py`, `authentication.py`, and model files were not modified.
