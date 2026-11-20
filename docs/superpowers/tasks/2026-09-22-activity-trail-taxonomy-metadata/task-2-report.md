status: DONE
commit: c82f29f94
changed_paths:
- src/codemie/service/activity/activity_models.py
- src/external/alembic/versions/a1b2c3d4e5f6_add_entity_label_to_activity_events.py
- tests/codemie/service/activity/test_activity_models.py
- tests/external/alembic/test_activity_events_entity_label_migration.py
test_command: poetry run pytest tests/codemie/service/activity/ tests/codemie/rest_api/models/test_activity_event_models.py tests/codemie/rest_api/routers/test_activity_events_router.py tests/codemie/rest_api/routers/test_project_activity_events_router.py tests/codemie/service/budget/test_budget_service_activity.py tests/external/alembic/
notes: Added nullable entity_label VARCHAR(255) to ActivityEvent (SQLModel) and ActivityEventCreate, extended entity_pair_must_be_complete validator to require entity_type when entity_label is set, and added migration a1b2c3d4e5f6 (down_revision y2z3a4b5c6d7, confirmed current head) adding the column with no backfill. TDD: wrote failing tests first (RED confirmed for missing field/column), then implemented; full activity-related suite (134 tests) passes GREEN.
