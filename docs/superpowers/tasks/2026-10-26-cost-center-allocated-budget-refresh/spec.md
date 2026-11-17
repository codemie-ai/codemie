# Spec: Cost Center Allocated Budget refresh on project membership change

**Ticket**: EPMCDME-16356 (sub-bug of EPMCDME-15623) · **Size**: M (16/36)
**Story**: docs/stories/2026-10-26-cost-center-allocated-budget-refresh/story.md

## Problem

The cost center Allocated Budget is summed live from active `ProjectMemberBudgetAssignment` rows
(`src/codemie/service/budget/cost_center_budget_summary.py`, via
`project_budget_repository.py:180`). Membership changes in
`src/codemie/service/project/project_assignment_service.py` only create the new member's row
(`_sync_project_budget_member_added`, copies a sibling's share) or soft-delete the removed member's row
(`_sync_project_budget_member_removed`). Remaining members' equal shares are never re-split, so the
sum drifts from what `rebalance_project_budget` would produce. Manual rebalance is the only fix.

## Approach

After a membership change that touches a project with budget allocations, re-split the equal
shares of that project's budgets in the database, using the existing
`ProjectBudgetService.recompute_equal_shares(session, project_name)` (`project_budget_service.py:1233`).
No new split logic, no new endpoint, no schema change.

- **DB-only, no provider push (story D2).** Existing members' provider-side limits are updated
  on the next provider sync or manual rebalance, as today. Only displayed/stored allocations change.
- **Single hook point.** The recompute is invoked from the shared helpers used by every path
  (single add/remove, bulk add/remove, CSV import via `assign_from_csv`), not per router, so no
  path is missed. Bulk and CSV recompute once per project per request, not once per user.
- **Sync/async bridge.** The membership service uses a sync `Session`; the recompute needs an
  `AsyncSession`. Reuse the in-repo bridge `_run_budget_provider_coro`
  (`project_assignment_service.py:69`). The recompute must see the committed membership change:
  it runs after the router commits, or otherwise in a way that observes the added/removed rows.
  The plan owns the exact placement.
- **Failure policy.** Recompute failure is logged (`budget_event=... component=...`) and
  swallowed, matching the fail-open convention on these paths; membership change still succeeds.
- **Overlap with `project_member_runtime_sync`.** None in trigger: `resync_project_member_allocations`
  runs only on the enforce-member-spend-limits toggle and itself calls `recompute_equal_shares`.
  The operation is idempotent, so no coordination is added.
- **Result is consistent with manual rebalance.** `recompute_equal_shares` and
  `_resync_member_allocations` both use `_equal_amounts_with_fixed_overrides`; FIXED-mode
  allocations are preserved.

## Acceptance criteria

1. Adding a user to a project with a budget updates equal shares so the cost center Allocated Budget
   matches a manual rebalance, without running one.
2. Removing a user does the same.
3. Bulk add, bulk remove and CSV import do the same.
4. A manual rebalance after a membership change leaves the cost center figures unchanged.
5. A project with no assigned budget: no allocation is created or changed, cost center unaffected.
6. A project with no membership change: figures identical to before.
7. FIXED-mode member allocations are not altered by the recompute.
8. A recompute failure does not fail the membership operation.

## Non-goals

- No provider (LiteLLM) push or full `rebalance_project_budget` on membership change.
- No change to rebalance behavior, budget assignment, or `recompute_equal_shares` split rules.
- No retroactive correction of existing wrong figures (story D3).
- No change to cost center page, Rollup Card, or `cost_center_budget_summary` read logic.
- No stored cost-center aggregate or caching layer.
- No change to `project_member_runtime_sync` triggers.

## Testing

Unit tests in `tests/codemie/service/project/test_project_assignment_service.py` for each path
(other members' shares change; no-budget project untouched; FIXED preserved; recompute failure
swallowed). One summary-level check that the rollup equals the post-rebalance figures.

## Risks

- `recompute_equal_shares` uses `budget.max_budget/soft_budget`; `_resync_member_allocations` uses
  effective values that depend on `get_enforce_member_spend_limits`. With enforcement off, results
  may differ from a manual rebalance; verify during planning (affects criterion 4).
- Transaction visibility between sync membership session and the async recompute session.
- The add path still seeds the new row from a sibling's share until recompute runs; a failed
  recompute leaves the old drift.
