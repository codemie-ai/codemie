status: DONE
commit: e39a847b0
changed_paths:
- src/codemie/rest_api/models/activity_event.py
- src/codemie/rest_api/routers/project_activity_events_router.py
- src/codemie/service/activity/activity_event_service.py
- src/codemie/service/activity/activity_repository.py
- tests/codemie/rest_api/models/test_activity_event_models.py
- tests/codemie/rest_api/routers/test_activity_events_router.py
- tests/codemie/rest_api/routers/test_project_activity_events_router.py
- tests/codemie/service/activity/test_activity_event_service.py
test_command: poetry run pytest tests/codemie/rest_api/routers/test_project_activity_events_router.py tests/codemie/service/activity/ tests/codemie/rest_api/models/ tests/codemie/rest_api/routers/test_activity_events_router.py -q
notes: Added label/category/entity_label to ActivityEventListItem, new ProjectActivityEventsResponse envelope, and the ACTIVITY_EVENTS_ENABLED short-circuit in list_project_events (returns [], 0, False without calling find_by_project). Deviation from the named-files list: ActivityEventRow (in activity_repository.py) never got an entity_label field in any prior task despite ActivityEvent gaining one in task 2, so entity_label passthrough was structurally impossible without adding it there (3-line additive change: one dataclass field + two constructor sites). Also fixed tests/codemie/rest_api/routers/test_activity_events_router.py's _item() helper, which broke because label/category are now required fields on the shared ActivityEventListItem model used by the untouched global router.
