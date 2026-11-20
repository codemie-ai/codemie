status: DONE
commit: 2405bb4a8
changed_paths:
- src/codemie/service/activity/activity_repository.py
- src/codemie/service/activity/activity_event_service.py
- src/codemie/rest_api/routers/project_activity_events_router.py
- tests/codemie/service/activity/test_activity_repository.py
- tests/codemie/service/activity/test_activity_event_service.py
- tests/codemie/rest_api/routers/test_project_activity_events_router.py
test_command: poetry run pytest tests/codemie/rest_api/routers/test_project_activity_events_router.py tests/codemie/service/activity/test_activity_event_service.py tests/codemie/service/activity/test_activity_repository.py -q > docs/superpowers/tasks/2026-09-22-activity-trail-taxonomy-metadata/test.local-7.log 2>&1; echo "EXIT=$?"; tail -40 docs/superpowers/tasks/2026-09-22-activity-trail-taxonomy-metadata/test.local-7.log
notes: Added `category: list[str] | None = None` to find_by_project (resolves via CATEGORY_EVENT_TYPES into an `event_type IN (...)` condition applied to both count_stmt and data_stmt, never touching `domain`), threaded through list_project_events, and exposed as a `Query(None)` param on the router, mirroring domain/event_type wiring exactly. The plan's specific claim that filtering category=["Budget"] should include `project.member_spend_enforcement.updated` did not hold: task 1's real EVENT_TAXONOMY places `project.member_spend_enforcement.updated` under "Settings", not "Budget" (see src/codemie/service/activity/activity_taxonomy.py lines 31-34), so the repository test instead asserts the mechanism against the real taxonomy — "budget.created" is included when filtering category=["Budget"] and "project.member_spend_enforcement.updated" is correctly excluded. All 40 tests across the three touched test files pass (RED confirmed before each implementation change, GREEN confirmed after).
