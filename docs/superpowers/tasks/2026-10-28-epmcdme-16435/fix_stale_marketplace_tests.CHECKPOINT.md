# CHECKPOINT: fix_stale_marketplace_tests

Tests only. No source, migration or config file touched. No new tests added (existing tests edited).

## Files changed
- tests/codemie/external/alembic/test_marketplace_migrations.py
- tests/codemie/service/marketplace_management/test_audit_persistence_roundtrip.py

## Per item
- A1 done: `PRE_SQUASH_REVISION = "e7b3a91c4d58"`; `SQUASHED_REVISION = "f3e2d1c0b9a8"` kept. This also fixes A2
  (`test_squashed_revision_chains_directly_from_the_pre_squash_head`) with no further edit.
- A3 done: `test_upgrade_then_downgrade_then_upgrade_is_clean` now asserts `head_revision == _script_head()` and runs
  `downgrade PRE_SQUASH_REVISION` instead of `downgrade -1`; the upgrade back to head and the reseed are unchanged.
- A4 done: `test_downgrade_restores_the_pre_squash_shape` asserts `_current_revision(engine) == _script_head()` after
  the upgrade; the `_seed_two_good_runs` reseed after it runs again.
- New helper `_script_head()` (plus imports `alembic.config.Config`, `alembic.script.ScriptDirectory`): builds the
  same `Config(alembic.ini)` + `script_location` the worker uses, calls `ScriptDirectory.get_current_head()`. It reads
  the version files only and does not run `env.py`, so it does not hit the isolation reason for the subprocess worker.
  No hard-coded head id.
- A5: no change needed; both cascade tests pass after A1-A4.
- B done: `_cleanup_payload(EntityKind.ASSISTANT, None x10, page=1, page_size=50)`. That covers bucket, reason, tier,
  verdict, mcp, category, cluster_id, q, rule and order_by, all None.

## Results
- `poetry run pytest tests/codemie/external/alembic/test_marketplace_migrations.py tests/codemie/service/marketplace_management/test_audit_persistence_roundtrip.py -q`
  -> 33 passed, 5 warnings (unrelated deprecation warnings), 42.91s.
- `ruff check` on both files: All checks passed.
- `ruff format --check` on both files: 2 files already formatted.
- The full suite was not run: the directive specified the two-file verify and nothing else changed.

## Not done
none. The downgrade chain from head to `e7b3a91c4d58` ran clean, so no later migration's downgrade failed.
