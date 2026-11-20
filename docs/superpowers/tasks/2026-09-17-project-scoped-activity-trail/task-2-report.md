DONE_WITH_CONCERNS

## Commit
- SHA: `cb43f1dd5`
- Message: `EPMCDME-14325: Add activity_events project/actor snapshot migration (task 2)`

## Revision id chosen
- `y2z3a4b5c6d7`
- Filename: `src/external/alembic/versions/y2z3a4b5c6d7_add_project_and_actor_snapshot_to_activity_events.py`

## Changed files
- `src/external/alembic/versions/y2z3a4b5c6d7_add_project_and_actor_snapshot_to_activity_events.py` (new)
- `tests/external/alembic/test_activity_events_project_actor_migration.py` (new)

No `__init__.py` was needed — `tests/external/` uses `pytest.ini`'s `--import-mode=importlib` with implicit namespace packages, confirmed by the existing sibling directory `tests/external/deployment_scripts/`, which also has no `__init__.py`.

## Test command and result
```
poetry run pytest tests/external/alembic/test_activity_events_project_actor_migration.py -v > /Users/Ilnur_Nigmatzyanov/codemie-dev/codemie/docs/superpowers/tasks/2026-09-17-project-scoped-activity-trail/test.local.log 2>&1; echo "EXIT=$?"; tail -25 ...
```
EXIT=0, all 3 tests passed:
- `test_upgrade_only_adds_three_nullable_columns_and_one_index`
- `test_upgrade_adds_expected_columns_and_index`
- `test_upgrade_never_touches_protected_columns`

RED was verified first: same command failed with `ModuleNotFoundError: No module named 'external.alembic.versions.y2z3a4b5c6d7_add_project_and_actor_snapshot_to_activity_events'` before the migration file existed (expected reason — module missing, not a typo).

## Test approach note
The task prompt's sample test patched `alembic.op.add_column` etc. globally via `unittest.mock.patch`. I instead followed this repo's existing convention (seen in `tests/codemie/migrations/test_d1c2b3a4e5f6_drop_interactive_features.py` and siblings): a minimal `_OpRecorder` stand-in, monkeypatched onto the migration module's own `op` attribute (`monkeypatch.setattr(module, "op", recorder)`), then asserting on recorded calls. This is more isolated (doesn't touch the real `alembic.op` global) and matches prior art in the codebase.

## Concern: the "verified sole head" fact was stale — I had to rebase
The task brief stated `s9t0u1v2w3x4` was confirmed to be the sole head with no children, to be used as `down_revision`. When I first wrote the migration with `down_revision = "s9t0u1v2w3x4"` and ran `alembic heads` from `src/external/alembic/` (`poetry run alembic -c alembic.ini heads`), it reported **two heads**: `c4d5e6f7a8b9` and my new `y2z3a4b5c6d7`. Investigation showed `s9t0u1v2w3x4` already has a child migration, `i1n2t3e4r5a6_add_interactive_features_to_assistants.py` (`down_revision = "s9t0u1v2w3x4"`), which chains forward through several more revisions eventually to `c4d5e6f7a8b9` — which was therefore the actual sole head on this branch at the time I ran the check (not `s9t0u1v2w3x4`).

I corrected this myself: changed `down_revision` to `c4d5e6f7a8b9` in the migration file, and reran `poetry run alembic -c alembic.ini heads`, which now reports a single head: `y2z3a4b5c6d7 (head)`. The unit test does not assert on `down_revision`, so this change did not require touching the test. Flagging this because another implementer (or a concurrent branch change) may have added `i1n2t3e4r5a6`/downstream migrations after the plan's fact-check was done — worth a sanity check before merge that no one else is also targeting `c4d5e6f7a8b9` as `down_revision` concurrently, which would reintroduce a fork.

## Other notes
- `alembic heads` ran successfully (with a working DB-independent config load) from `src/external/alembic/` using `poetry run alembic -c alembic.ini heads`; no DB connection issue encountered.
- Migration follows the `38255069bfab_create_activity_events_table.py` header/style convention (docstring, `Sequence`/`Union` typed revision vars, `sa`/`op` imports).
- No backfill, no FK, all three new columns nullable, matches Task 1's `ActivityEvent`/`ActivityEventCreate` fields exactly (`project_name`, `actor_email`, `actor_display_name`, `VARCHAR(255)`) and the `ix_activity_events_project_name_created_at` index on `(project_name, created_at)`.
