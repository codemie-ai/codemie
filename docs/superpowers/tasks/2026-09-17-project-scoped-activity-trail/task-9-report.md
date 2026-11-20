DONE

## Commit
- SHA: 782360a1876ebf3e71a23ff7b12d0dfd81ae7238
- Subject: `EPMCDME-14325: Add list_project_events service method (task 9)`

## Changed files (git show --stat HEAD)
```
 src/codemie/service/activity/activity_event_service.py    | 43 ++++++++++++++
 tests/codemie/service/activity/test_activity_event_service.py | 68 ++++++++++++++++++++++
 2 files changed, 111 insertions(+)
```

## What was done
- Added `ActivityEventService.list_project_events(project_name, *, domain=None, event_type=None, actor_id=None, from_dt=None, to_dt=None, sort_dir="desc", limit=50, offset=0) -> tuple[list[ActivityEventListItem], int]` to `src/codemie/service/activity/activity_event_service.py`, mirroring `list_events` but delegating to `activity_event_repository.find_by_project(project_name, domain=..., event_type=..., actor_id=..., from_dt=..., to_dt=..., sort_dir=..., limit=..., offset=..., session=session)` and mapping rows to `ActivityEventListItem` with the additional `project_name=r.project_name` field.
- `list_events` and `get_filter_options` were left untouched.
- Added `class TestListProjectEvents` to `tests/codemie/service/activity/test_activity_event_service.py` with 3 tests mirroring `TestListEvents`'s structure:
  - `test_returns_paginated_items_and_total`
  - `test_delegates_project_name_and_filters_to_repository` (asserts `call_args.args[0] == "acme"` since `project_name` is passed positionally, matching the repository's positional-only signature, plus all filter kwargs)
  - `test_returns_empty_list_when_no_events`

## TDD verification
- RED: ran `pytest -k TestListProjectEvents -v` before implementing; all 3 new tests failed with `AttributeError: 'ActivityEventService' object has no attribute 'list_project_events'` — expected failure reason.
- GREEN: implemented the method, re-ran full file.

## Test commands run
1. `poetry run pytest tests/codemie/service/activity/test_activity_event_service.py -v > .../test.local.log 2>&1; echo "EXIT=$?"` → `EXIT=0`, 9 passed (3 `TestListEvents`, 3 `TestListProjectEvents`, 3 `TestGetFilterOptions`).
2. `poetry run pytest tests/codemie/service/activity/ -q` → 34 passed.
3. `poetry run ruff check src/codemie/service/activity/activity_event_service.py tests/codemie/service/activity/test_activity_event_service.py` → All checks passed.
4. `poetry run ruff format --check src/codemie/service/activity/activity_event_service.py tests/codemie/service/activity/test_activity_event_service.py` → 2 files already formatted (no changes needed).

## Concerns
None. Only the two intended files were modified; `activity_repository.py`, the models, and the migration were not touched.
