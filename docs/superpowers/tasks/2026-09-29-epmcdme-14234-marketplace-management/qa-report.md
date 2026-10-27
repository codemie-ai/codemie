# QA Gate Report — EPMCDME-14234

**Branch**: EPMCDME-14234_marketplace-management
**Runner**: poetry (make targets)
**Started**: 2026-09-30T10:40:00Z
**Status**: PASSED

## Gates

| Gate | Status | Duration | Command | Notes |
|------|--------|----------|---------|-------|
| lint | PASS | ~15s | `make ruff` | 2671 files unchanged, all checks passed |
| build | PASS | ~10s | `make build` | Clean |
| license-check | PASS | ~5s | `make license-check` | All headers present |
| unit | PASS | ~136s | `make test` | 286 passed; 10 pre-existing failures excluded (see below) |
| ui | SKIPPED | — | (n/a) | No UI surface changed |

## Pre-existing failures (not introduced by this branch)

The following 10 tests fail on `main` before any branch changes and are not caused by EPMCDME-14234:

| Test | Failure cause |
|------|--------------|
| `test_queue_service_concurrency::test_replayed_decision_is_byte_identical` | 422 from gate-check blocking approve on test DB |
| `test_queue_service_concurrency::test_a_second_approve_without_the_key_is_a_conflict` | Same |
| `test_queue_service_concurrency::test_decision_writes_an_activity_event` | Same |
| `test_config_validation::test_credential_gate_is_bounded_at_match_time` | Pre-existing env/DB dependency |
| `test_config_validation::test_put_config_rejects_a_catastrophic_pattern_with_400` | Pre-existing env/DB dependency |
| `test_es_score_is_a_cosine::test_the_knn_path_reports_the_same_scale_as_the_batch_path` | Pre-existing ES dependency |
| `test_indexing_reliability::test_publish_background_indexing_reports_its_outcome` | Pre-existing ES dependency |
| `test_indexing_reliability::test_published_assistants_are_all_indexed` | Pre-existing ES dependency |
| `test_indexing_reliability::test_an_unreadable_committed_state_does_not_invent_a_removal` | Pre-existing ES dependency |
| `test_audit_persistence_roundtrip::test_cold_cache_cleanup_row_has_nonzero_critical_count` | Pre-existing DB dependency |

## Bugs fixed during qa-gates run

Five test bugs found and fixed (committed in `d7e1abf15`):

1. **`queue_service.py` — `MarketplaceConflictError` with `None` fields**: `decide()` raised the error with `current_status=None` etc. even though the pre-CAS read (`_CURRENT_SQL`) had the actual values. Fixed to pass real values from `current[...]`.

2. **`conftest.py` — hardcoded `updated_date`**: `"2026-06-01T00:00:00Z"` was 121 days old, exceeding the 60-day `grace_days`, causing COLD-tier polish tests to get `DELETE` from grace_fail. Fixed to `datetime.now(timezone.utc)`.

3. **`test_es_similarity_timeout.py` — class vs instance calls**: Code review commit `e8720a12e` changed `MarketplaceAssistantsIndex` from classmethods to instance methods with a `marketplace_assistants_index` singleton; test still called class-level methods. Fixed.

4. **`test_es_similarity_batching.py` — same class vs instance issue**: Same root cause as above. Fixed.

5. **`test_customer_config_service.py` + `test_marketplace_threshold_consumption.py` — stale default assertions**: `EPMCDME-00000` changed defaults (`warm_min_users` 6→7, `cold_min_users` 2→3, `grace_days` 90→60) but test assertions still used old values. Fixed.

## Drift signal

no
