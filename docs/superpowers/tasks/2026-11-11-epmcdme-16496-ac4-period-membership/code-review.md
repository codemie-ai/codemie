---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 12c6accd3ad5f6a9a2ad39ee3c07dee94f775d10
---

# Code review — 2026-11-11-epmcdme-16496-ac4-period-membership

**Decision:** approve (check round, confidence final low · check medium)  
**Reviewed head:** `12c6accd` · **Rounds:** final → check

**Closed:** 6 fixed · 0 moot · 1 decided · 1 wont-fix

Full-profile final round (blind, edge-case, acceptance, verification-gap lenses + standards audit;
edge-case lens file never materialized, confidence low) returned request-changes with 8 blocking
findings (CR-001..CR-008). Review-fix round fixed CR-003..CR-008 (ops-script batching, zero-value
scoping on the lines_daily reset, license header, a deduped `_live_resumed` SQL probe, two new
live-Postgres tests for the UTC-midnight preset and the underived-resumed arm) and ran the CR-001
production-scale benchmark. Check round verified the fix-up: 7/8 resolved; CR-001 (decision-kind)
stayed open pending an explicit human sign-off on the benchmark result, which evidence alone does
not satisfy. User reviewed the benchmark (1.9M-row sweep of a 0–2% resumed cohort; the latency
budget holds through 1%, crosses only past 2%; the real observed resumed fraction is ~0.001%) and
signed off on shipping without the `session_dims_active_shared_cov` INCLUDE-list migration fallback
— CR-001 disposed wont-fix. CR-002 (no ticket filed yet for the deferred Cursor cross-tool
hook-coverage gap) decided: left unticketed this round by user direction. 0 deferred.

## Open — 0

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and
Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| Lifecycle-hooks-only session excluded across all six endpoints | pass | pre-existing `has_activity` gate unaffected; live test `test_lifecycle_hooks_only_session_excluded_from_every_endpoint` |
| Resumed session with real activity on another day, housekeeping-only in the queried window, excluded whether `started_at` is inside or outside the window | pass | `reader.py` arm 6 / arm 4; live test `test_resumed_session_housekeeping_only_day_excluded_when_started_inside_or_outside_window` |
| Session whose only record in a window is a zero-delta lines-of-code point excluded (raw point or `lines_daily` row) | pass | `value <> 0` check, `_LINES_DAILY_NONZERO`; live tests `test_whole_day_zero_lines_only_session_excluded_*` / `test_exit_time_only_session_excluded_*` |
| Slash-command `user_prompt` row or its paired `INTERACTION` span excluded when no real activity exists | pass | `command_name` filter; unit + live `test_exit_time_only_session_excluded_from_every_endpoint` |
| Hour-based presets crossing UTC midnight require an in-window raw record for the partial day, not just a daily rollup row | pass | pre-existing AND-join in `_active_in_window`, now pinned by a live seeded-row test (CR-006) |
| Session totals agree across overview, sessions, users, repositories, cost, and activity-by-weekday for the same filter | pass | `test_cross_endpoint_session_totals_agree_for_the_same_filter` asserts all six surfaces agree |
| Genuinely active session admitted regardless of resumed/started-in-window status | pass | control sessions admitted across every live scenario in `test_live_session_period_membership.py` |
| Genuinely active, non-resumed, started-in-window session keeps its unconditional sub-day-precise admission | pass | arm 1 unchanged; `test_sessions_cte_decides_the_window_by_session_start` |
| Non-goal: no DB migration/new column unless the pre-merge benchmark shows the index-only arm regresses unacceptably | pass | no migration file in diff; benchmarked this round, see CR-001 |
| Non-goal: no per-tool special-casing in the log/span or resumed fix | pass | fix expressed in shared vocabulary; Cursor generalization noted as unconfirmed, not special-cased |
| Non-goal: `resumed`'s own stored, value-blind definition unchanged | pass | `session_activity.py` `DERIVE_SQL` resumed clause untouched |
| Non-goal: ticket and owner filed for the deferred CLI-hook-coverage (e.g. Cursor) follow-up | fail | left unticketed this round by user direction; spec.md's Open Risks still states none exists, see CR-002 |
| Production-scale benchmark of the resumed-population arm recorded before merge | pass | run and recorded in `cli-analytics-storage.md`; human sign-off closes CR-001 |
| Doc update to `cli-analytics-storage.md`'s Window Semantics section | pass | Window Semantics and Stored-activity-facts sections rewritten for the six-arm rule |

Standards: commit-format pass · code-quality pass · security pass
