DONE

## Commit
e7b9b2d5c — "EPMCDME-14325: Add project_name/actor snapshot columns to ActivityEvent (task 1)"

## Changed files
- src/codemie/service/activity/activity_models.py
- tests/codemie/service/activity/test_activity_models.py

## Implementation summary
- Added `project_name`, `actor_email`, `actor_display_name` (all `Optional[str]`, `VARCHAR(255)`, nullable) to `ActivityEvent`, placed after `attributes`.
- Added `Index("ix_activity_events_project_name_created_at", "project_name", "created_at")` to `ActivityEvent.__table_args__`.
- Added the same three optional fields (defaulting to `None`) to `ActivityEventCreate`, alongside `attributes`.
- Added import of `ActivityDomain` and `ProjectManagementEvent` to the test file (needed for the new test per the task's Step 1 spec).
- Added two new tests: `test_activity_event_create_accepts_project_and_actor_snapshot_fields` and `test_activity_event_create_defaults_project_and_actor_snapshot_to_none`.

## Test-first (RED/GREEN) evidence

RED (before implementation):
Command: `poetry run pytest tests/codemie/service/activity/test_activity_models.py -v`
Result: EXIT=1 — 2 failed, 4 passed. Failure: `AttributeError: 'ActivityEventCreate' object has no attribute 'project_name'` (expected — feature not yet implemented).

GREEN (after implementation):
Command:
```
poetry run pytest tests/codemie/service/activity/test_activity_models.py -v > /Users/Ilnur_Nigmatzyanov/codemie-dev/codemie/docs/superpowers/tasks/2026-09-17-project-scoped-activity-trail/test.local.log 2>&1; echo "EXIT=$?"; tail -25 .../test.local.log
```
Result: EXIT=0 — 6 passed in 0.17s (all 4 pre-existing tests + 2 new tests).

## Verification against task scope
- `git show --stat HEAD` confirms exactly the two declared files were changed: `src/codemie/service/activity/activity_models.py` and `tests/codemie/service/activity/test_activity_models.py`.
- No other files touched. No backfill logic added. No change to `attributes` JSON shape. `actor_id`'s existing FK/null-on-delete behavior untouched.

## Concerns / notes
- None. Task fully implemented per spec, test-first with visible RED then GREEN, single commit, scoped strictly to the two declared files.
