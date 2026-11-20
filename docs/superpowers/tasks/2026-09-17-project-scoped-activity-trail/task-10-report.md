DONE

## Commit

- SHA: `fc756d41ce6d42b9251f737255a345a38c39ecf3`
- Subject: `EPMCDME-14325: Add project_name to ActivityEventListItem (task 10)`

### Changed files (`git show --stat HEAD`)
```
src/codemie/rest_api/models/activity_event.py               |  1 +
tests/codemie/rest_api/models/test_activity_event_models.py | 11 +++++++++++
2 files changed, 12 insertions(+)
```

## What was done

- Added `project_name: str | None = None` to `ActivityEventListItem` in
  `src/codemie/rest_api/models/activity_event.py`, placed right after
  `entity_id` and before `actor_id`. No other change to this file
  (`DomainFilterEntry` / `ActivityEventFilterOptions` untouched).
- Followed TDD: added `test_project_name_accepts_value` and extended
  `test_optional_fields_default_to_none` with a `project_name is None`
  assertion in `tests/codemie/rest_api/models/test_activity_event_models.py`,
  reusing the existing `TestActivityEventListItem` class and its
  conventions (no new imports/header needed since the file already
  existed).
- Confirmed RED first: running the tests before the model change failed
  with `AttributeError: 'ActivityEventListItem' object has no attribute
  'project_name'` for both the new test and the extended
  `test_optional_fields_default_to_none` assertion — the expected failure
  reason (field genuinely absent from the model, not a typo).
- Added the field, reran — all 5 tests in the file passed (GREEN).

## Test commands run

1. `poetry run pytest tests/codemie/rest_api/models/test_activity_event_models.py -v`
   → `EXIT=0`, 5 passed. Log saved to
   `docs/superpowers/tasks/2026-09-17-project-scoped-activity-trail/test.local.log`.
2. `grep -rn "ActivityEventListItem(" src tests` → only three construction
   sites: `src/codemie/service/activity/activity_event_service.py:56`
   (keyword-based), `tests/codemie/rest_api/routers/test_activity_events_router.py:31`
   (keyword-based), and the model/test file itself. None use positional
   args or a strict allowlist, so the new optional field is backward
   compatible.
3. `poetry run pytest tests/codemie/rest_api/ tests/codemie/service/activity/ -q`
   → `2363 passed, 10 skipped` — no regressions.
4. `poetry run ruff check src/codemie/rest_api/models/activity_event.py tests/codemie/rest_api/models/test_activity_event_models.py`
   → All checks passed.
5. `poetry run ruff format --check src/codemie/rest_api/models/activity_event.py tests/codemie/rest_api/models/test_activity_event_models.py`
   → 2 files already formatted (no changes needed).

## Concerns

None. This is a minimal, backward-compatible additive change; no
consumers needed updates. Task 9 (`ActivityEventService.list_project_events`)
can now be built on top of this field plus the earlier
`find_by_project` repository method.
