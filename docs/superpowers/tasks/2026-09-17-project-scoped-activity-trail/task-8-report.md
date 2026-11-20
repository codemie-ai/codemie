DONE

## Commit

b092321039e38be0e18c9d54124dca8cb783850e
"EPMCDME-14325: Add find_by_project repository method with ACTIVITY_EVENTS_ENABLED gating (task 8)"

## Changed files (confirmed via `git show --stat HEAD`)

- src/codemie/service/activity/activity_repository.py (85 insertions)
- tests/codemie/service/activity/test_activity_repository.py (94 insertions)

## What was done

1. Added `project_name: str | None = None` to `ActivityEventRow` — placed at
   the end of the dataclass with a default so `find_all`'s existing
   `ActivityEventRow(...)` construction (which does not pass `project_name`)
   remains untouched and unbroken. `find_all` itself was not modified.
2. Added abstract method `find_by_project` to `ActivityEventRepository`.
3. Implemented `find_by_project` on `SQLActivityEventRepository`:
   - Returns `([], 0)` immediately if `config.ACTIVITY_EVENTS_ENABLED` is
     falsy, before touching the session.
   - `conditions` starts with the mandatory `ActivityEvent.project_name ==
     project_name`, then optionally adds `domain`, `event_type`, `actor_id`,
     `from_dt`, `to_dt` filters (no `entity_type`/`entity_id`, per spec).
   - Reuses the same `conditions` list for both the count query and the
     data query (AC11 reuse pattern).
   - Orders by `(created_at, id)` tuple, both descending or both ascending
     depending on `sort_dir`, for a deterministic tie-break.
   - Data select uses `func.coalesce(ActivityEvent.actor_email,
     UserDB.email)` and `func.coalesce(ActivityEvent.actor_display_name,
     UserDB.name)` labeled `actor_email`/`actor_name`, outer-joined to
     `UserDB` — preferring the write-time snapshot, falling back to the
     live join for pre-migration rows.
   - Builds `ActivityEventRow` per result row, including
     `project_name=event.project_name`.
4. Did not touch `activity_models.py`, migrations, or any event-emitting
   service files — out of scope per instructions.

## Test-first (TDD) evidence

Added `class TestFindByProject` to
`tests/codemie/service/activity/test_activity_repository.py` with 4 tests:
- `test_find_by_project_returns_enriched_rows_and_count`
- `test_find_by_project_filters_are_passed_to_query`
- `test_find_by_project_returns_empty_when_activity_events_disabled`
- `test_find_by_project_prefers_snapshot_actor_fields_over_join`

Ran the new tests before implementing `find_by_project` / the dataclass
field: all 4 failed with `AttributeError: 'SQLActivityEventRepository'
object has no attribute 'find_by_project'` (RED, expected reason — feature
missing). After implementing, re-ran: all 4 passed (GREEN), along with the
existing 15 tests in the file (19 total, no regressions).

## Verification commands run

1. `poetry run pytest tests/codemie/service/activity/test_activity_repository.py -v > .../test.local.log 2>&1; echo "EXIT=$?"`
   → `EXIT=0`, 19 passed.
2. `poetry run pytest tests/codemie/service/activity/ -q`
   → 31 passed, no regressions elsewhere in the activity test directory.
3. `poetry run ruff check src/codemie/service/activity/activity_repository.py tests/codemie/service/activity/test_activity_repository.py`
   → "All checks passed!"
4. `poetry run ruff format --check src/codemie/service/activity/activity_repository.py tests/codemie/service/activity/test_activity_repository.py`
   → "2 files already formatted"
5. `git show --stat HEAD` → confirms exactly the two intended files changed.

## Concerns

None. One design note worth flagging to the plan owner: `ActivityEventRow.project_name`
was placed at the end of the dataclass with a default value (`= None`)
rather than "after entity_id" as loosely suggested in the task text,
because Python dataclasses require all fields after the first
defaulted field to also have defaults, and `find_all`'s existing
untouched construction call does not pass `project_name` — placing it
mid-list without a default would have made `find_all` raise `TypeError`
at runtime. This preserves `find_all`'s exact behavior (AC10) while
satisfying the requirement to add the field.
