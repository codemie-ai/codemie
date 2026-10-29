# Fix: 6 stale marketplace tests (introduced by 08bc2d852, EPMCDME-14234)

Tests only. Do NOT change any source, migration or config file. Two files, six tests; they fail on origin/main too.
Root cause (verified by scout, read-only): two later tickets changed the alembic chain under the tests.
EPMCDME-14145 inserted `e7b3a91c4d58` between `c9d8e7f6a5b4` and the squashed revision `f3e2d1c0b9a8`;
EPMCDME-13429 stacked `c6f15537a1b2` -> `c7f15537b2c3` -> `9a36991134b3` (current head) on top of it.
Chain: c9d8e7f6a5b4 -> e7b3a91c4d58 -> f3e2d1c0b9a8 -> c6f15537a1b2 -> c7f15537b2c3 -> 9a36991134b3.

## A. tests/codemie/external/alembic/test_marketplace_migrations.py (5 tests)
1. Line ~78: `PRE_SQUASH_REVISION` must be `"e7b3a91c4d58"` (the squashed revision's real parent).
   Keep `SQUASHED_REVISION = "f3e2d1c0b9a8"` for the squash-boundary checks.
2. `test_squashed_revision_chains_directly_from_the_pre_squash_head` (~line 612): follows from 1.
3. `test_upgrade_then_downgrade_then_upgrade_is_clean` (~lines 345-348): the `head_revision == SQUASHED_REVISION`
   assertion must compare against the real head (read it from alembic's `ScriptDirectory`
   `get_current_head()` using the same config the file already uses, no hard-coded id), and the `downgrade -1`
   step must go to `PRE_SQUASH_REVISION` (downgrade to that explicit revision, then upgrade back to head).
4. `test_downgrade_restores_the_pre_squash_shape` (~line 420): same head-agnostic assertion.
   The reseed call after it (`_seed_two_good_runs`, ~line 425) must run again.
5. `test_verdict_fk_prevents_orphans` and `test_retention_is_scoped_to_one_entity_kind` failed only as a cascade
   (the downgrade/upgrade cycle wiped the module-scoped seed because the earlier tests aborted before reseeding).
   They should pass once 1-4 are fixed; change them only if they still fail after that.
Keep the diff minimal; keep the existing test intent and names. If a later migration's downgrade itself fails when
the cycle reaches it, STOP and report in the CHECKPOINT, do not modify migrations.

## B. tests/codemie/service/marketplace_management/test_audit_persistence_roundtrip.py (1 test)
`test_cold_cache_cleanup_row_has_nonzero_critical_count` (line ~367) calls `mm._cleanup_payload` with 11 arguments.
The source signature (src/codemie/rest_api/routers/marketplace_management.py:~882) is
`(kind, bucket, reason, tier, verdict, mcp, category, cluster_id, q, rule, order_by, page, page_size)`.
Pass `rule` and `order_by` as `None` and `page=1, page_size=50` (keywords for the last two are clearer).

## Verify
Run both files: `poetry run pytest tests/codemie/external/alembic/test_marketplace_migrations.py tests/codemie/service/marketplace_management/test_audit_persistence_roundtrip.py -q`
(alembic tests use a throwaway `mm_mig_test_*` database on the local docker-compose Postgres: do not stop or touch the
compose services or volumes). Then `ruff check` and `ruff format --check` on the two changed files.
No git operations. Write `fix_stale_marketplace_tests.CHECKPOINT.md` next to this file: files changed, the exact test
results, and anything you could not fix.
