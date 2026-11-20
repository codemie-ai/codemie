status: DONE
commit: 6429ffc2e
changed_paths:
- src/codemie/rest_api/models/activity_event.py
- src/codemie/rest_api/routers/project_activity_events_router.py
- src/codemie/service/activity/activity_event_service.py
- src/codemie/service/activity/activity_repository.py
- tests/codemie/rest_api/models/test_activity_event_models.py
- tests/codemie/rest_api/routers/test_project_activity_events_router.py
- tests/codemie/service/activity/test_activity_event_service.py
- tests/codemie/service/activity/test_activity_repository.py
test_command: poetry run pytest tests/codemie/rest_api/routers/test_project_activity_events_router.py tests/codemie/service/activity/ tests/codemie/rest_api/models/ -q
notes: Added get_distinct_event_types_by_project (repository, mirrors get_domain_mapping style), get_project_event_metadata (service, same ACTIVITY_EVENTS_ENABLED short-circuit as list_project_events), ActivityEventProjectMetadata model, and GET /v1/projects/{project_name}/activity-events/metadata router (placed before the "" route, using the same project_admin_or_admin_or_maintainer_access dependency and ExtendedHTTPException passthrough). The 403 router test required discovering that this repo's tests never mount the real main.py app for this router (main.py gates activity routers behind config.ENABLE_USER_MANAGEMENT, which is False in the test env) — followed the existing bare-FastAPI-app + local ExtendedHTTPException handler pattern from tests/codemie/rest_api/routers/test_schedulers.py instead. All 484 tests across the three affected test directories pass; ruff check/format clean.
