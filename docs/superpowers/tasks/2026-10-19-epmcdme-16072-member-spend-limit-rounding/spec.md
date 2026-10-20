# EPMCDME-16072 — Equal member spend-limit split for small budgets

## Intent

When "Enforce member spend limits" is on, a project budget is split equally across members. Small budgets or large member counts give members inconsistent and even negative limits. In the ticket screenshot, most members show $0.02 and one shows -$0.29. Admins cannot trust the numbers, and LiteLLM can be handed a negative cap.

Cause: `ProjectBudgetService._allocate_equal` and `_equal_amounts_with_fixed_overrides` (`src/codemie/service/budget/project_budget_service.py`) round each share half-up to the cent. They then add `budget − share × n` to the last member by `user_id`. When half-up rounds up, the total overshoots and the last member absorbs a negative remainder.

All EQUAL members share **one** LiteLLM budget (the shared child budget). Every member sync overwrites it, so the cap that applies is whichever member synced last. Any rule that gives EQUAL members different values cannot be enforced as shown.

## Goals

- **G1.** Every EQUAL member gets the same non-negative limit: `floor_to_cent((budget − Σ fixed overrides) / equal_member_count)`. (AC1, AC3, AC4)
- **G2.** The sum of all member limits never exceeds the project budget. Leftover cents (fewer than one cent per EQUAL member) stay unallocated. (AC2)
- **G3.** Per-member soft limit uses the same floor rule and is capped at the per-member max (`soft = min(floor(soft_share), max_share)`).
- **G4.** The rule is identical for every budget size and member count. No special casing. (AC4)
- **G5.** When enforcement is on and EQUAL members end up at $0.00 because the budget is too small to give each at least one cent, the save still succeeds (no rejection), and the condition is highlighted explicitly:
  - **Backend:** a WARNING log at allocation time. Project budget and group category responses carry `zero_limit_member_count: int`, the number of active EQUAL members whose effective limit is $0.00 while enforcement is on. It is 0 when enforcement is off, because members then get the project max.
  - **UI (codemie-ui):** the project budget section shows an `InfoWarning` (WARNING type) for each budget category whose `zero_limit_member_count > 0`. Example text: "Platform budget is too small to split across members: 70 members have a $0.00 limit and cannot spend. Increase the budget or turn off Enforce member spend limits."
- **G5a.** `zero_limit_member_count` source is my call: count active EQUAL allocation rows with stored `allocated_max_budget <= 0` while enforcement is on (FIXED rows excluded). It needs no extra child-budget lookups in the list and group endpoints. On rows saved before the fix it can be off by a few members until the next save, toggle-on, or rebalance; new data is always exact.
- **G5b.** **Turning enforcement on recalculates shares.** `resync_project_member_allocations` (`src/codemie/enterprise/litellm/project_member_runtime_sync.py`), when `enforce_limit=True`, first recomputes EQUAL shares per project budget with the same helper as rebalance (`_equal_amounts_with_fixed_overrides` on the budget's `max_budget`/`soft_budget`), persists changed amounts (`update_allocation`), then pushes to LiteLLM as today. This heals pre-fix projects on the ticket's own repro path (enable the toggle, then save) without a migration. If the FIXED overrides exceed the budget (`ValidationException`), log a warning and keep the stored amounts. That path must never fail the toggle, matching today's behavior. Turning enforcement off is unchanged.
- **G6.** Regression tests cover small budgets and large member counts (AC5): $1/70, the screenshot pattern $0.99/65, $0.30/70 (zero share), 1000 members, sub-cent amounts from group percentages, soft ≤ max, sum ≤ budget, fixed overrides plus equal remainder. Callsite seam tests at create and resync assert the value seeded into the shared child budget, per `.ai-run/guides/testing/testing-patterns.md`.

## Non-goals

These are my calls, open for review:

- **No data migration.** Existing bad rows heal on the next budget save, rebalance, override change, or enforcement toggle-on (G5b). Projects that already have enforcement on and are never saved again keep their old rows; this is an accepted risk.
- **No auto-rebalance on member add/remove.** `ProjectAssignmentService._get_member_added_allocation_amounts` copies an existing EQUAL share. Once shares are uniform it copies the right value, but the total can exceed the budget by one share until the next rebalance. This drift is pre-existing and not a rounding bug.
- **No UI change for the visible gap** between the budget and the allocated total (e.g. $0.70 allocated of $1.00). It is the honest result of uniform shares.
- **No project-level soft ≤ hard validation** for group categories. This fix only guarantees per-member soft ≤ max.
- **No warning in the budget modal before save.** The warning appears in the budget section after save, from backend data, so the UI never re-implements the split math.

## Anti-goals

- Members with different limits under EQUAL mode, even by one cent.
- Rejecting saves because the budget is small (the team lead is not sure about blocking).
- Computing shares in the UI.
- Counting FIXED $0 overrides in the warning: a 0 override is a deliberate value (EPMCDME-15764).

## Constraints

- Decimal math with `ROUND_DOWN` to `_CENT`, persisted as float as today. Inputs can carry sub-cent precision (group category amount = `total × pct / 100`).
- Keep the existing deterministic member ordering and the FIXED-override validation messages.
- The shared child budget gets the uniform share. `allocation_rows[0]` and `shared_sample` become correct automatically because all EQUAL rows are equal.
- Repos and branches: `codemie` on `EPMCDME-16072_member-spend-limit-rounding`; `codemie-ui` on a new `EPMCDME-16072_member-spend-limit-rounding` branch from `origin/main`.

## Decided

- Floor plus unallocated leftover rule (a largest-remainder split is rejected because the provider budget is shared).
- $0.00 shares are allowed. A warning is shown in the backend log, the API flag and a UI banner.

- Recalculate shares on enforcement toggle-on (G5b).
- Every risky or major resolution in this spec (floor rule and unallocated leftover cents, $0.00 shares allowed with warning, no migration, recalculation on toggle-on, row-based warning count, the non-goals) goes into the MR descriptions for both repos.

## Left to the builder

- The exact helper factoring (e.g. one shared `_equal_share` used by both helpers).
- Where `zero_limit_member_count` is computed in the router builders (single-budget and group category responses).
- Exact banner placement within `ProjectBudgetsSection` and the TypeScript type updates.
- Updating the existing lifecycle test that asserts the old last-member remainder.
