# Cost Center Allocated Budget Refresh Implementation Plan

> For agentic workers: use superpowers:subagent-driven-development or superpowers:executing-plans, task by task.

**Goal:** Re-split equal member shares (DB only) after project membership changes so the cost center Allocated Budget matches a manual rebalance.

**Spec:** `docs/superpowers/tasks/2026-10-26-cost-center-allocated-budget-refresh/spec.md`

Commit per task using the repository's existing convention.

## Decisions on the spec's open risks

1. **Raw vs effective budget values (criterion 4): no divergence.** `rebalance_project_budget` passes `eff_max=budget.max_budget`, `eff_soft=budget.soft_budget` (`project_budget_service.py:1368`) into the same `_equal_amounts_with_fixed_overrides` that `recompute_equal_shares` (:1251) feeds with the raw budget values. `enforce_limit` only changes the `effective_max_budget` pushed to the provider (:1222), not stored amounts. No change to either method; task 2 pins this with a test.
2. **Commit visibility:** the recompute needs its own `AsyncSession` (`get_async_session`), which cannot see the router's uncommitted sync transaction. So the recompute runs in the routers **after** `session.commit()`, through one service method (single implementation, one call per router path). Per-project, once per request, so bulk and CSV do not recompute per user.

## Task 1: Service method `refresh_member_shares`

**Files:** Modify `src/codemie/service/project/project_assignment_service.py` (next to `_run_budget_provider_coro`, :69); Test `tests/codemie/service/project/test_project_assignment_service.py`.

**Interfaces:** Produces `ProjectAssignmentService.refresh_member_shares(project_name: str) -> None` (static, sync).

Test-first: yes — with `project_budget_service.recompute_equal_shares` patched to raise, `refresh_member_shares("p")` returns without raising and logs `budget_event=member_share_refresh_failed component=project_assignment_service`; with it patched to succeed it is awaited once with `project_name="p"`.

- [ ] Write the tests, see them fail, then implement: inner coroutine opens `get_async_session()` (already imported, :30), calls `project_budget_service.recompute_equal_shares(session, project_name)` (lazy import, as in `project_member_runtime_sync.py:276`), commits explicitly unless the context manager already does. Run it via `_run_budget_provider_coro`; wrap in `try/except Exception` and log a warning (fail-open, criterion 8).

## Task 2: Call it from every membership router path

**Files:** Modify `src/codemie/rest_api/routers/projects/membership.py` at the post-commit points of assign (:61), remove (:111), bulk assign (:139), CSV import (:216), bulk remove (:254); Test `tests/codemie/rest_api/routers/test_projects_router.py`.

**Interfaces:** Consumes `refresh_member_shares` from Task 1. Not added to `update_user_project_role` (:87): role change does not alter membership.

Test-first: yes — for each of the five routes, with `refresh_member_shares` patched, a successful call invokes it once with the project name after `session.commit()` (assert call order); when the service raises (404/422) it is not called.

- [ ] Write the tests, see them fail, add one `project_assignment_service.refresh_member_shares(project_name)` line after each commit (inside or after the `with`, after commit). CSV delegates to bulk assign in the service, so only the router needs the hook.

## Task 3: Behavior tests against real recompute logic

**Files:** Test `tests/codemie/service/budget/test_project_budget_allocation_rounding.py` (existing `recompute_equal_shares` tests), `tests/codemie/service/budget/test_cost_center_budget_summary.py`.

Test-first: no — characterization of existing `recompute_equal_shares`; the tests should pass once written, so no failing step exists.

- [ ] Add tests: (a) after a member is removed from a 3-member EQUAL budget (or an added row seeded from a sibling), `recompute_equal_shares` yields shares equal to what `_equal_amounts_with_fixed_overrides(allocs, budget.max_budget, budget.soft_budget)` returns, which is the manual-rebalance input (criteria 1, 2, 4); (b) FIXED allocation rows are unchanged (criterion 7); (c) a project with no member allocations performs no update (criterion 5); (d) the cost center summary's `allocated_member_budget_total` equals the sum of the recomputed rows (criterion 6 and rollup check).
