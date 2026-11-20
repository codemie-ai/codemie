DONE

## Commit

`ffea3d3e9` — "EPMCDME-14325: Add cross-cutting regression tests for project-scoped activity trail (task 13)"

Changed files (`git show --stat HEAD`):
- `tests/codemie/service/activity/test_activity_repository.py` (110 insertions, 0 deletions)

## What was done

Read `tests/codemie/service/activity/test_activity_repository.py` and
`tests/codemie/rest_api/routers/test_activity_events_router.py` in full first to match existing
conventions. Confirmed there is no real-DB/SQLite session fixture anywhere in
`tests/codemie/service/activity/` — all existing tests in this file use `MagicMock()` sessions — so
the plan's suggested "`session_with_events` fixture pattern" does not exist and was not introduced.
All three new tests follow the same `MagicMock()`-session convention as the existing tests.

Added 4 tests total (the plan's Test 1 was split into two symmetric tests, one per class, per the
plan's own "or a sibling test right after it" allowance):

1. `TestFindAll::test_find_all_query_has_no_project_name_filter_or_secondary_sort` (AC10) — calls
   `find_all` with filters set, captures the data `Select` statement passed to
   `session.execute` (`call_args_list[1].args[0]`), and asserts via `str(stmt)` that the `WHERE`
   clause does not reference `project_name` and the `ORDER BY` clause is the single column
   `activity_events.created_at DESC` (no secondary sort key).

2. `TestFindByProject::test_find_by_project_query_has_project_name_filter_and_secondary_sort`
   (AC10 counterpart) — same statement-introspection technique on `find_by_project`, asserting the
   `WHERE` clause contains an exact-equality condition
   `activity_events.project_name = :project_name_1` and the `ORDER BY` clause is
   `activity_events.created_at DESC, activity_events.id DESC`.

3. `TestFindByProject::test_find_by_project_excludes_personal_project_rows_from_other_project_results`
   (AC9) — asserts the captured `WHERE` clause uses exact equality on `project_name` (never
   `IS NOT NULL` or an `IN (...)` list), which structurally guarantees that a personal-project row
   stored with `project_name=None` (per `personal_project_service.py:90`) can never satisfy
   `project_name = <some project>` in SQL and therefore cannot leak into another project's results.
   Also asserts the returned rows all have a non-`None` `project_name`.

4. `TestFindByProject::test_find_by_project_paginated_calls_use_consistent_tie_break_ordering`
   (AC12) — calls `find_by_project` twice with `offset=0` and `offset=1` (fresh mock session each
   time) and asserts the captured `ORDER BY` clause (`created_at DESC, id DESC`) is byte-identical
   across both calls, proving the tie-break ordering is stable and not offset-dependent (which is
   what prevents gaps/repeats when paginating over rows sharing a `created_at`).

Approach used real SQLAlchemy `Select` statement introspection (`str(stmt)`, splitting on
`WHERE`/`ORDER BY`/`LIMIT`) rather than testing mock behavior — this is a true regression test
against the actual generated query text, since `session.execute.call_args_list[1].args[0]` captures
the real `Select` object built by the repository (the `MagicMock` session doesn't touch the
statement's internal structure).

Left `tests/codemie/rest_api/routers/test_activity_events_router.py` untouched: it fully mocks
`activity_event_service` at the router boundary and has no visibility into repository-level SQL, so
there was nothing meaningful to add there beyond what the 4 repository-level tests above already
cover for AC9/AC10/AC12.

## Regression found?

No regression found in `find_by_project` or `find_all`. All new tests passed on first run against
the existing (already-committed) implementation — Task 8's `id` secondary sort key was already
correctly present, and `find_all` was confirmed to have no `project_name` filter and no secondary
sort key. `activity_repository.py` was NOT modified.

## Test commands run and results

1. `poetry run pytest tests/codemie/service/activity/test_activity_repository.py -v` →
   `23 passed in 0.36s`, `EXIT=0`. Log written to
   `docs/superpowers/tasks/2026-09-17-project-scoped-activity-trail/test.local.log`.

2. `poetry run pytest tests/codemie/service/activity/ tests/codemie/rest_api/routers/test_activity_events_router.py -q`
   → `46 passed in 0.48s`. No new failures anywhere in the wider regression check.

3. `poetry run ruff check tests/codemie/service/activity/test_activity_repository.py` →
   "All checks passed!"
   `poetry run ruff format --check tests/codemie/service/activity/test_activity_repository.py` →
   "1 file already formatted" (no reformatting needed).

## Concerns

- Two pre-existing changes were present in the working tree before this task started and were
  deliberately left untouched/unstaged, per instructions to stage only the intended file:
  - `tests/external/alembic/test_activity_events_project_actor_migration.py` (modified, not by this
    task)
  - `docs/codemie/analytics/codemie-analytics-....json` (untracked, unrelated analytics artifact)
- The statement-introspection tests rely on `str(Select)` textual rendering and on
  `session.execute.call_args_list[1]` being the data query (index 1, after the count query at index
  0). This matches the existing call pattern used throughout the file (`side_effect=[count_result,
  data_result]`) and is stable as long as `find_all`/`find_by_project` keep issuing exactly one count
  query followed by one data query — consistent with all other tests in this file.
- This is the final task (13 of 13) in the EPMCDME-14325 plan. With this commit, the plan is
  complete.
