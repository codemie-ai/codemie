---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 92a2911c6a2357c617b0b3c6ff2c0e950ac94502
---

# Code review — 2026-11-05-epmcdme-15517

**Decision:** approve (final round, confidence low: no spec, so acceptance was not audited)  
**Reviewed head:** `92a2911c6a` · **Rounds:** final · **Profile:** balanced

Edge-case lens and standards ran; no findings. The commit after the reviewed head (`da02e32277`) changes only the router module docstring (the D1 note). Local artifacts: `docs/superpowers/reviews/2026-11-05-epmcdme-15517/` (verdict JSON, brief, frozen diff; not committed).

## Open — 0

Standards: commit-format pass · code-quality pass · security pass

## Acceptance addendum

The review above ran without a spec. This addendum audits the approved contract (the user's direction recorded in `implementation-evidence.md` and the independent review) against the final diff. It adds no new lens and reuses the edge-case and standards results. **Reviewed revision:** production code at `da02e32277` (identical to the reviewed `92a2911c6a` except the router docstring); the branch head at the time of writing carries only task documents on top.

| Approved requirement | Status | Trace |
|---|---|---|
| Sessions without `cost_daily` stay visible in Sessions and counted by Activity; `include_costless` kept | pass | `get_session_page`, `get_session_cost_facts`, `get_session_start_times` not in the diff; live asserts `sum(activity.by_hour) == 4` including the cost-less session; `test_live_session_page.py` and the existing cost-less tests pass |
| Overview, Cost `total_sessions` and Users count the same authorized, filtered universe | pass | `get_cost_kpis` counts `sel`; `get_users` is `sel LEFT JOIN`; handler drops the priced-only filters; live: Overview = Cost = Activity = 4, Users 3 + 1 |
| Window, reported activity timestamps, viewer scope, project/user/repo/branch filters, deny-all preserved | pass | `_sessions_cte` and `_activity_cte` untouched; live: a personal cost-less session outside the scope is in no count; unknown id and `deny_all` select nothing. Project/repo/branch filters are not separately exercised: they are conditions inside the untouched CTE |
| Denominators from the intended metric; spend-only semantics, cost and token sums intact; no row multiplication; subagent totals-only preserved | pass | `spend_sessions` and `avg_cost_per_session` unchanged (live: cost / 3 spend sessions); Users average duration over listed sessions; multi-cost-row session counted once; `test_live_session_subagent_cost.py` passes |
| Missing cost rows are not zero-dollar rows; neither hides an eligible session | pass | live: a `$0` cost row, a two-row session and a cost-less session are all counted |
| Sibling consistency | pass | found in review: `get_class_lists("all")` skipped cost-less sessions; fixed and live-asserted. Distinctions kept on purpose: `get_users_daily_activity` (cost days) and Cost `cost_by_user` |
| No schema, UI, CLI or API-model change | pass | diff is 5 files: reader, handler, router docstring, two test files |

Limitations of the evidence: turns, tool, file and duration rollup rows of the sum test are seeded directly into the derived tables (synthetic), after a real refresh from `log_events` for the priced cost rows; Overview and Users were served by calling the router functions directly with a stub filter, not through HTTP ingestion, auth or `FilterParams`. The assessment (S, 14/36) is unchanged: only documents changed since.

## Reconciliation addendum (rebase onto main with EPMCDME-16496)

The review above is of the pre-rebase diff (`reviewed_head` above). After `main` took EPMCDME-16496, which makes Overview, Cost `total_sessions`, Users, the class lists and the Users average duration count cost-less sessions, this branch's production delta shrank to: `get_repositories` reading `include_costless=True` (review finding f-0c5f2ed47eb8), the Users average skipping a NULL `duration_ms`, and the router D1 docstring. The Users developer fix asked for in review is already upstream (`_DEVELOPER = _session_developer(_LINES_EMAIL)`) and is kept as a regression test only. The rest is tests adapted to `main`'s in-period activity window (a lifecycle-only start is not a session). This reduced delta has had the scoped checks of the pull request but not a new lens review.
