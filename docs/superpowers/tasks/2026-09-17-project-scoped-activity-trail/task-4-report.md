DONE

## Commit
5d43be9cc - "EPMCDME-14325: Emit project_name/actor snapshot on project membership events (task 4)"

## Changed files
- `src/codemie/service/project/project_assignment_service.py`
- `tests/codemie/service/project/test_project_assignment_service.py`

`git show --stat HEAD` confirms exactly these 2 files changed.

## What was done
Added `project_name=project_name, actor_email=actor.email, actor_display_name=actor.name` kwargs to all 5 `ActivityEventCreate(...)` construction sites in `project_assignment_service.py`, leaving the existing `attributes={...}` dicts unchanged:
1. `assign_user_to_project` (~line 419)
2. `update_user_project_role` (~line 500)
3. `bulk_assign_users_to_project` (per-user loop, ~line 622)
4. `bulk_remove_users_from_project` (per-record loop, ~line 733)
5. `remove_user_from_project` (~line 802)

Test-first (superpowers:test-driven-development):
- Added one new test per method asserting `event.project_name == project_name == event.attributes["project_name"]`, `event.actor_email == actor.email`, `event.actor_display_name == actor.name`, and that `attributes` is otherwise unchanged.
- For `bulk_assign_users_to_project` and `bulk_remove_users_from_project`, extended the existing activity-repo-patched tests (`test_bulk_assign_upsert_existing`, `test_bulk_remove_success`) rather than adding new ones, per instructions.

RED confirmed first: 5 failures, each due to `event.project_name` being `None` (kwargs not yet accepted by production code) — expected reason, not a typo/error.

While turning GREEN, discovered and fixed an unrelated pre-existing pitfall in several tests: `MagicMock(name="...")` does NOT set the `.name` attribute (it configures the mock's internal display name for repr, per unittest.mock's reserved `name` kwarg). This caused `actor.name` to remain an auto-generated `MagicMock`, which failed pydantic's strict `Optional[str]` validation on `actor_display_name` for all pre-existing tests that hit the new code path with a bare/inline actor mock. Fixed by assigning `actor.name = "..."` as a separate statement after construction, for every affected test (both new and pre-existing): `test_assign_user_success`, `test_update_role_success`, `test_bulk_assign_success_new_users`, and the actor construction inside my new/extended tests.

## Test command and result
```
poetry run pytest tests/codemie/service/project/test_project_assignment_service.py -v > /Users/Ilnur_Nigmatzyanov/codemie-dev/codemie/docs/superpowers/tasks/2026-09-17-project-scoped-activity-trail/test.local.log 2>&1; echo "EXIT=$?"; tail -30 /Users/Ilnur_Nigmatzyanov/codemie-dev/codemie/docs/superpowers/tasks/2026-09-17-project-scoped-activity-trail/test.local.log
```
Result: `EXIT=0`, `44 passed, 2 warnings in 9.13s` (full pre-existing suite in this file, no regressions).

## Concerns
None. Did not touch `tests/external/alembic/...`, `project_service.py`, or `personal_project_service.py`. Did not touch `activity_models.py` (fields already present from task 1).
