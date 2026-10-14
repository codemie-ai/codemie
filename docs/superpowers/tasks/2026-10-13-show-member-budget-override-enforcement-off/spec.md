# EPMCDME-15764 — Show member budget override when enforcement is off

## Problem

With the project setting "Enforce member spend limits" off, the project-budget API reports
`budget.max_budget` as every member's `allocated_max_budget` (rule from EPMCDME-12959). A saved
member override (`allocation_mode == "fixed"`) is stored, reports success and is marked
"Overridden" in the UI, but its amount is never shown.

## Behaviour

The per-member displayed max on the admin project-budget read path becomes:

| Enforcement | Member `allocation_mode` | `allocated_max_budget` returned |
|---|---|---|
| on | any | persisted `allocated_max_budget` (unchanged) |
| off | `fixed` | persisted `allocated_max_budget` |
| off | anything else | `budget.max_budget` (unchanged) |

The branch is on `allocation_mode`, never on the amount: an override of `0` is returned as `0`.

`allocated_member_budget_total` is the sum of the per-member values returned in the same
response, so with enforcement off it is `Σ fixed overrides + n_equal × budget.max_budget`.

The rule applies at all three sites that inline it today in
`src/codemie/rest_api/routers/project_budget_router.py`: the total (79–82) and the per-member
value (109) in `_build_project_budget_response`, and `list_project_budget_members` (348–355).
The three must agree; the rule is expressed once and reused rather than edited in three places.
The inline comment at 80–81, which says every member holds the full budget when enforcement is
off, is made false by this change and must be corrected or removed.

`allocated_soft_budget`, `allocation_mode` and every other response field keep coming straight
from the row. Response models and the OpenAPI baseline do not change.

## Acceptance criteria

1. Enforcement off, member with a fixed override: `GET /v1/admin/project-budgets/{id}/members`
   and every response built by `_build_project_budget_response` return the stored override as
   that member's `allocated_max_budget`, including an override of `0`.
2. Enforcement off, member without an override: `allocated_max_budget == budget.max_budget`.
3. Enforcement on: persisted `allocated_max_budget` for every member, fixed or equal.
4. `allocated_member_budget_total` equals the sum of the per-member `allocated_max_budget`
   values in the same response, including for a budget mixing fixed and equal members.
5. Provider sync and runtime enforcement are unchanged: no diff in
   `src/codemie/enterprise/litellm/` or `src/codemie/service/budget/`.
6. Regression tests in `tests/codemie/rest_api/routers/test_project_budget_router.py` cover 1–4
   for both the builder and the members endpoint: enforcement off with a fixed row, with a
   zero-amount fixed row, and with mixed fixed and equal rows asserting the total. Existing
   enforcement tests in that file pass unmodified.

## Non-goals

- No change to what LiteLLM enforces, to `project_member_runtime_sync.py`, to the budget
  provider adapter, or to any write/sync path that uses the enforcement rule. While enforcement
  is off the displayed override is a label, not an enforced cap.
- No change to the override, clear-override or rebalance write behaviour or validation.
- No change to analytics limits (`member_spend_service._limit_value`), the project-budget-groups
  total (`_build_project_budget_group_response`) or the project list/detail total; they keep
  using persisted amounts.
- No change to `allocated_soft_budget` handling.
- No response model, schema, migration or setting changes.
- No UI work; making allocation rows clickable is in `codemie-ui-next`.

## Known limitations

- For a fixed member with enforcement off, the cap live at the provider is whichever sync ran
  last (the override path pushes the override, runtime and toggle resync push
  `budget.max_budget`), so it may differ from the amount shown. Accepted under the
  display-only scope.
- Analytics `{category}_limit` columns will agree with the admin API for fixed members and still
  differ for equal members (persisted equal share vs. `budget.max_budget`), as they do today.
- This narrows the EPMCDME-12959 criterion that every member shows `budget.max_budget` and the
  total is `N × budget.max_budget` when enforcement is off; that now holds only for budgets
  with no fixed members.
