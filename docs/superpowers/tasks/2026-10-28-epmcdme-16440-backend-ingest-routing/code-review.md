---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 3c9b7fea3d7cb84ef2b911756c48f03d861eb697
---

# Code review — 2026-10-28-epmcdme-16440-backend-ingest-routing

**Decision:** approve (check round, confidence final low · check medium)  
**Reviewed head:** `3c9b7fe` · **Rounds:** final → check

**Closed:** 10 fixed · 0 moot · 0 decided

Targeted check of CR-001..CR-010 at head 06c4744f1a04501f352a511ab877f9c1fc4913cb: all resolved against current source (CR-001 and the implementer's invalid claim confirmed unreachable; CR-007 residual accepted). Live-PG run was not re-executed by this verifier (confidence medium). Non-blocking: the lock now spans app-DB lookups/provisioning, and a legacy-anchored (unbound) session still resolves the candidate destination first.

## Open — 0

## Status addendum (2026-10-28, after the 16441 refresh; the generated text above is kept as written)

- The header `reviewed_head` (`3c9b7fea3`) is the pinned head of the code review, not the final code. History: automated review at `ed7c35766` (request-changes), fix-up `06c4744f1`, automated check approve, then manual corrections `92be6924a` and `3c9b7fea3` after the independent review IR-001..IR-004 (the records of both rounds are consolidated in the section "Fix-up record" below; the two separate report files were removed from the delivery branch).
- Later commits: `fd874c3c1` (lint, license headers, boot-flag test) and `260e89f8f` (per-test ids in the live range module) came from the QA gates; `07f5bb27c` adds `EvidenceGate.user_hash` for EPMCDME-16441 compatibility. Independent reviews by the user: the delta to `fd874c3c1`, then `260e89f8f` (isolation delta), then the delta `875c3b730..47ff1ebe0` (approved: `EvidenceGate.user_hash` = None conforms to the !4652 head `e53fde4e4` `IngestGate` (`str | None`), and `record_gate_discard` omits the hash safely; the conflict test and the 46-pass log were checked). Production code is unchanged since `07f5bb27c`.
- This is compatibility preparation, not combined integration proof. Pending integration steps, none of them run: reuse of the shared `_user_hash`/HMAC helper and the `record_gate_discard` wiring in `ingestor.py`; one version of the duplicated range-test fix (both branches rewrote `test_reader_live_session_range.py`); then targeted seam checks once the two changes are combined.
- The "partial" rows below were graded at the first full round and carried forward. In the final code (`07f5bb27c`) the code paths named by their notes exist and have tests: boundary before the stale check (`ingestor.py` `check_boundary`, `_raise_if_stale`; tests `test_a_stale_policy_loses_to_the_session_boundary...` and `test_a_session_boundary_violation_wins_over_a_stale_policy`), the hook-row `repo_remote` fallback and the route-note and default-unavailable counters (`test_the_hook_rows_repo_remote_routes_when_the_header_carries_none`, `test_every_route_note_is_counted`). These rows were not re-scored by a reviewer, so they stay labelled partial (carried forward), not final coverage.
- Cohesion (`code-quality`): `ingestor.py` and the router were split (`session_binding.py`, `cli_analytics_ingest_route.py`); the router remains over 500 lines, mostly code that was there before.
- Status: implementation done; QA `PASSED_WITH_ACCEPTED_BASELINE_FAILURES`; not MR-ready, not merge-ready.

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| AC4 Registered repository routes when no link; link beats registration; registration beats default | partial (final round) | carried forward from prior verdict; CR-008 hook repo_remote fallback now implemented |
| AC7 Registered project inaccessible/deleted/ambiguous: default with explicit route_note | partial (final round) | carried forward; route_note counters now added (CR-009) |
| AC13 Default failure: broken default retryable 503; no projects reasoned discard | partial (final round) | carried forward; default_project_unavailable counter now added (CR-009) |
| Design 4 order: session boundary before stale check | partial (final round) | carried forward; stale 409 now raised after check_boundary (CR-006) |
| AC1-3, AC5-6, AC8-12, AC14-25 and Design 10 docs / non-goals | pass | carried forward from prior verdict |

Standards: commit-format pass · code-quality partial (final round) · security pass

## Fix-up record (consolidated from the removed `code-review-fixup.md` and `code-review-manual-fixup.md`)

### Round 1 — CR-001..CR-010 (reviewed head `ed7c35766`, fix-up `06c4744f1`)

| id | verdict | action |
|---|---|---|
| CR-001 | invalid as stated (unreachable) | no production change; pinned by `test_a_declared_session_with_session_less_rows_is_a_boundary_discard_not_a_bypass` |
| CR-002 | valid | fixed: `same_user` (trimmed, lower-cased, false on empty); `check_owner` discards `session_owner_conflict` on a different or missing sender |
| CR-003 | valid | fixed: destination chosen under the session lock via `resolve_session`; a bound session validates only the bound destination; Personal on a session anchored elsewhere = `session_project_conflict` without provisioning |
| CR-004 | valid | fixed: evidence without a sender email is a final discard `session_owner_conflict` before the gate |
| CR-005 | valid (cohesion) | fixed: binding code moved to `postgres/session_binding.py` (`ingestor.py` 737 to 596 lines) |
| CR-006 | valid | fixed: stale 409 raised in the ingestor after `check_boundary`, before gate/lock/binding |
| CR-007 | valid (cohesion), partly | route class and helpers moved to `cli_analytics_ingest_route.py`; the 5-line policy endpoint stays; router still over 500 lines, mostly older code |
| CR-008 | valid | fixed: earliest hook row `repo_remote` as fallback, the header wins |
| CR-009 | valid | fixed: `DESTINATION_EVENTS` counter (`route_note:<note>`, `default_project_unavailable:discard|retry`) |
| CR-010 | valid | fixed: live PG cases for personal on/off, stale-intact, bound destination, disabled bound project, concurrent first binding, missing sender, email case; router and gate tests |

Verified, no defect: `session_dims` readers tolerate binding-only rows (`started_at` NULL) and legacy rows (`owner_email` NULL); `personal_ingest_enabled` stays strict (`True`/`"true"`) because it reads a raw extra of a privacy gate; the header spelling `X-CodeMie-Session-ID` vs the doc is case-insensitive.

CR-001 evidence: a row without a session id carries `''` (`otlp.py` `_session_key`) and `check_boundary` discards it as `evidence_session_conflict`; a request without rows has `record_count == 0` and `_store` returns before any transaction, so nothing is stored without a binding.

RED honesty (round 1): against the reviewed head, the live cases for bound destination, disabled bound project, Personal on a shared bound session, missing sender and email case failed on their own assertions (behavioural RED). The stale-intact and concurrent-resolver cases failed only because the new API did not exist (API-absence RED). The two real-resolver personal on/off cases passed on old code (coverage, no RED). Gate, router and ingestor unit tests have no old-code run.

### Round 2 — IR-001..IR-004 (manual round after the independent review of `06c4744f1`; heads `92be6924a`, `3c9b7fea3`)

- **IR-001 (P1), fixed, accepted:** the known owner is a set: `session_dims.jwt_email` plus every `hook_events.user_email` of the session (`BINDINGS_SQL` `legacy_emails`); under the lock a sender matching none is `session_owner_conflict`, a missing sender with a known owner is a conflict, an empty known set (unavailable identity) is adopted as before.
- **IR-002 (P1), fixed, accepted:** bound rows count their server `updated_at` in the `_idle` retention predicate (`session_purge.py`); client times still pass through `_past`.
- **IR-003 (P2), fixed after a reopen:** a body carries exactly one of `remote` (raw, normalized) or `key` (the returned identity, taken exactly); `register`/`unregister` never inspect existing rows to choose a reading; `GET`/`PUT` return `{key, project_name}`; the response field `remote` was renamed `key` (unreleased endpoint).
- **IR-004 (P2), fixed after a reopen:** `RepositoryRegistrationRepository.lock_project` (`SELECT ... FOR UPDATE` on the application row); registration, rename and delete lock the project row, so a stale authorization cannot insert after a delete or rename. No FK or migration.

Tests (real exit codes): live PG `test_live_session_binding.py` 45 passed; targeted lifecycle/registration/router/purge/owner set 98 passed; project service, router and model tests with the live module 446 passed; `test_live_registration_lifecycle.py` 5 passed (barrier tests). RED: 7 of the 9 new live cases of IR-001/IR-002 failed on their own assertions on old code (2 are regression guards that passed); IR-003 tests have only API-level RED; for IR-004 the lock was removed once and the first live case failed on its assertion, after which the run hung on the held lock and was killed, so only that case's RED was observed, and the file was restored. The wide sweep's remaining failures were `test_dirty.py` calendar edges (18), `test_settings.py` (2) and `test_cli_analytics_jobs.py` (1), which also failed on a clean checkout of `06c4744f1`; the live dispatch-gate case `...last_calendar_day...` (`OSError: [Errno 22]`) was not reproduced on a baseline.

Residual risks: the known-owner check trusts client-claimed OTLP hook emails (log/usage tables are not consulted); a rollup recompute can extend an old bound session's retention once; the project row lock waits behind a slow registration (one insert); orphan registrations from earlier builds are not cleaned (unreleased feature, no migration); the first-bind email lookup scans the session's hook rows once and is not load-measured.
