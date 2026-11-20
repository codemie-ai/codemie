DONE

## Summary

Resumed task after a transient stream stall. Tests for all 4 sites were already written on disk (uncommitted); verified RED, implemented the 4 production changes, verified GREEN, committed.

## Tests (RED confirmed before implementation)

- `tests/codemie/service/project/test_project_service.py`
  - `TestProjectServiceCreateSharedProject::test_create_shared_project_emits_project_name_and_actor_snapshot`
  - `TestProjectServiceUpdateProjectActivityEvent::test_update_project_emits_project_name_and_actor_snapshot`
  - `TestProjectServiceDeleteProjectActivityEvent::test_delete_project_emits_project_name_with_no_actor_snapshot`
- `tests/codemie/service/project/test_personal_project_service.py`
  - `TestPersonalProjectService::test_ensure_personal_project_emits_no_project_name_with_actor_email`

4 failed / 70 passed pre-implementation.

## Production changes

- `src/codemie/service/project/project_service.py`
  - `create_shared_project` (~line 160): added `project_name=validated_name, actor_email=user.email, actor_display_name=user.name` to `ActivityEventCreate(...)`.
  - `update_project` (~line 243): added `project_name=project.name, actor_email=user.email, actor_display_name=user.name`.
  - `delete_project` (~line 543): added `project_name=project_name, actor_email=None, actor_display_name=None` (no `User` object available at this layer; signature and router caller unchanged).
- `src/codemie/service/project/personal_project_service.py`
  - `ensure_personal_project_async` (~line 84): added `project_name=None, actor_email=user_email, actor_display_name=None` (personal projects unconditionally exclude `project_name` per AC9).

## Verification

```
poetry run pytest tests/codemie/service/project/test_project_service.py tests/codemie/service/project/test_personal_project_service.py -v
```
Result: `74 passed, 2 warnings` (EXIT=0). Full log at `docs/superpowers/tasks/2026-09-17-project-scoped-activity-trail/test.local.log`.

## Commit

`13da43263` — "EPMCDME-14325: Emit project_name/actor snapshot on project domain events (task 3)"

`git show --stat HEAD` confirms exactly 4 files changed:
- `src/codemie/service/project/project_service.py`
- `src/codemie/service/project/personal_project_service.py`
- `tests/codemie/service/project/test_project_service.py`
- `tests/codemie/service/project/test_personal_project_service.py`

`tests/external/alembic/test_activity_events_project_actor_migration.py` was left untouched/uncommitted (pre-existing modification from a different task), as instructed.
