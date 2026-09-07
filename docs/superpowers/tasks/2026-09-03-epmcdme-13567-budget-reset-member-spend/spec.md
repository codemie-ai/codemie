# Spec: Zero Member budget_period_spend on Project Budget Reset (EPMCDME-13567)

## Problem

`POST /v1/admin/project-budgets/{budget_id}/reset` resets the project key's spend in LiteLLM via `reset_project_budget_spend()`, and updates each member's limits and budget pointer via `sync_member_allocation()` (a customer upsert). It never zeros each member's `budget_period_spend`. Member spend counters stay stale until the next natural budget period expiry.

`POST /v1/admin/project-budget-groups/{id}/reset` is affected by the same bug because it delegates entirely to `reset_project_budget()` for each category budget.

## Approach

Add a single new method to the `BudgetEnforcementProvider` protocol — `reset_project_member_spending` — and call it inside the per-member loop in `reset_project_budget()` after `sync_member_allocation()` returns. The LiteLLM implementation reuses the existing `reset_customer_spending_in_litellm()` helper (`budget_helpers.py:160`), which deletes and recreates the customer record to zero its spend counter.

No router changes. No DB schema changes. No migration.

## Behaviour

### Call ordering within the per-member loop

`sync_member_allocation` runs first (upserts limits and the budget pointer into LiteLLM), then `reset_project_member_spending` runs (delete+recreate of the customer record). This ordering ensures the recreated customer record inherits the limits that sync just wrote into the budget record, rather than whatever was there before the reset.

### Unconditional reset

The spend reset runs for every member regardless of whether `ENFORCE_MEMBER_SPEND_LIMITS` is enabled. Zeroing spend is correct semantics for any reset operation; enforcement policy controls whether the counters are acted upon going forward, not whether they are cleared.

### `provider_member_ref=None` guard

If `sync_member_allocation` failed or returned no LiteLLM customer ID, `provider_member_ref` will be absent. In that case the service skips the reset step for that member and logs a structured warning. No `RuntimeError` is raised; the member is already marked with `sync_status=FAILED` from the preceding sync step.

### Failure surfacing

`LiteLLMBudgetProviderAdapter.reset_project_member_spending` raises `RuntimeError` when `reset_customer_spending_in_litellm` returns `False` (LiteLLM unavailable or API failure). The service catches the exception, logs a warning with `budget_event=project_member_spend_reset_failed`, sets the member's `sync_status=FAILED`, and continues the loop (fail-open). The overall reset call does not abort.

### Group reset

`reset_project_budget_group()` delegates each category budget entirely to `reset_project_budget()`. No change is needed there; the fix propagates automatically.

## Files Changed

| File | Change |
|---|---|
| `src/codemie/service/budget/provider.py` | Add `reset_project_member_spending(user_id, budget_id)` abstract method to `BudgetEnforcementProvider` protocol |
| `src/codemie/service/budget/provider_registry.py` | Add noop impl of `reset_project_member_spending` to `_NoopBudgetEnforcementProvider`; return via `self._noop_result(...)` |
| `src/codemie/enterprise/litellm/budget_provider_adapter.py` | Add `reset_project_member_spending` to `LiteLLMBudgetProviderAdapter`; call `reset_customer_spending_in_litellm(user_id, budget_id)`; raise `RuntimeError` on `False` |
| `src/codemie/service/budget/project_budget_service.py` | In `reset_project_budget()`, after `sync_member_allocation()`: guard on `provider_member_ref`, call `provider.reset_project_member_spending(...)`, catch exceptions and mark `sync_status=FAILED` |
| `tests/codemie/service/budget/test_project_budget_service.py` | Add test: `reset_project_member_spending` is called for each member |
| `tests/codemie/service/budget/test_project_budget_service_lifecycle.py` | Add test: failure case marks `sync_status=FAILED`; `provider_member_ref=None` case is skipped gracefully |

## Acceptance Criteria

1. After `reset_project_budget()`, every member's `budget_period_spend` is 0 in LiteLLM.
2. The reset runs for all members regardless of the `ENFORCE_MEMBER_SPEND_LIMITS` flag.
3. Each member's `max_budget`, `budget_duration`, and `budget_id` pointer are correctly set after reset (sync runs before reset).
4. A `reset_customer_spending_in_litellm` failure for a member results in `sync_status=FAILED` for that member and a logged warning; other members and the overall operation are not aborted.
5. A member with `provider_member_ref=None` is skipped without raising; a warning is logged.
6. Tests assert: (a) `reset_project_member_spending` is awaited once per member, (b) failure marks `sync_status=FAILED`, (c) `provider_member_ref=None` skips gracefully.
7. The group reset endpoint (`/project-budget-groups/{id}/reset`) is fixed without any change to `reset_project_budget_group()`.

## Non-Goals

- No change to the router layer (`project_budget_router.py`).
- No change to `reset_project_budget_group()` directly.
- No change to `sync_member_allocation()` behavior or signature.
- No change to `reset_customer_spending_in_litellm()` helper.
- No DB schema migration or new DB columns.
- No gating of spend reset on `ENFORCE_MEMBER_SPEND_LIMITS` value.
- No retry logic for failed member resets.
- No change to the personal budget reset path (`reset_user_budget_spending`).
- No new API endpoints or request/response model changes.
