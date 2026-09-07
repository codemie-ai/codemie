# Technical Research

**Task**: project-budget reset member-spend litellm budget-groups
**Generated**: 2026-09-03T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Fix project budget reset endpoint to also zero member customer budget_period_spend in LiteLLM (EPMCDME-13567).

Bug: POST /v1/admin/project-budgets/{budget_id}/reset correctly zeros the project key spend via reset_project_budget_spend(), but does NOT zero member customers' budget_period_spend in LiteLLM. For each member, the endpoint calls sync_member_allocation() (a LiteLLM customer upsert that updates limits/pointers) but never calls a spend-reset API. Member spend counters stay stale for the remainder of the budget period.

A proven reset pattern already exists: reset_customer_spending_in_litellm() in budget_helpers.py (used by the personal budget flow) deletes and recreates the LiteLLM customer record, zeroing its spend counter. This function is never called from the project budget reset path.

The user also suspects a second endpoint may need fixing: POST /v1/admin/project-budget-groups/{budget_group_id}/reset — investigate whether it exists and has the same issue.

Also investigate from the UI code at C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie-ui-next whether the UI calls /project-budgets/{id}/reset or /project-budget-groups/{id}/reset (or both), to scope which backend endpoints need fixing.

Acceptance Criteria:
- Every member customer associated with the project budget has budget_period_spend reset to 0 in the same operation
- Works for any project regardless of whether member spend limit enforcement is enabled
- Member max_budget, budget_duration, and budget_id pointer remain correctly applied after reset
- Failure to reset a member spend is surfaced (not silently swallowed)
- Backend tests cover both project key reset and per-member budget_period_spend reset

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/routers/project_budget_router.py` — Router for both `/v1/admin/project-budgets` (individual budget endpoints, lines 49-488) and `/v1/admin/project-budget-groups` (group endpoints, lines 494-764). The reset endpoint for individual budgets is at line 380; for groups at line 736.
- `src/codemie/service/budget/project_budget_service.py` — `ProjectBudgetService` class. `reset_project_budget()` (lines 1292-1373): calls `provider.reset_project_budget_spend()` to reset the project key, then for each member calls `provider.sync_member_allocation()` (upsert limits/pointers) and persists DB metadata. No member spend reset is called. `reset_project_budget_group()` (lines 2259-2293): iterates over category assignments and delegates entirely to `reset_project_budget()` for each.
- `src/codemie/enterprise/litellm/budget_helpers.py` — `reset_customer_spending_in_litellm(user_id, budget_id)` (line 160): calls `service.reset_customer_spending()` which deletes and recreates the LiteLLM customer record, zeroing `budget_period_spend`. Returns `True` on success, `False` on failure. Never raises.
- `src/codemie/enterprise/litellm/budget_provider_adapter.py` — `LiteLLMBudgetProviderAdapter`. `sync_member_allocation()` (line 1143) calls `service.sync_project_member_budget_assignment(user_id=allocation.user_id, ...)` — a customer upsert that updates limits/budget pointer; does not zero spend. `reset_user_budget_spending()` (line 637) is the existing personal budget reset method that uses `reset_customer_spending_in_litellm`; it raises `RuntimeError` on failure. No equivalent method exists for project member spend reset.
- `src/codemie/service/budget/provider.py` — `BudgetEnforcementProvider` `@runtime_checkable Protocol` (line 192). Has `reset_project_budget_spend` and `sync_member_allocation` but no `reset_project_member_spending` method.
- `src/codemie/service/budget/provider_registry.py` — `_NoopBudgetEnforcementProvider` mirrors the protocol with noop implementations. Needs a noop for any new method added to the protocol.

### Architecture and Layers Affected

- **Router layer**: `project_budget_router.py` — no changes needed; it delegates to the service.
- **Service layer**: `project_budget_service.py` — `reset_project_budget()` is the primary change target. Needs to call a new provider method for each member after `sync_member_allocation()`.
- **Provider protocol layer**: `provider.py` — add `reset_project_member_spending` abstract method.
- **Provider noop layer**: `provider_registry.py` — add noop implementation.
- **Provider LiteLLM implementation**: `budget_provider_adapter.py` — add LiteLLM implementation using `reset_customer_spending_in_litellm`.

### Integration Points

- `reset_customer_spending_in_litellm(user_id, budget_id)` in `budget_helpers.py` → calls `service.reset_customer_spending()` from `codemie_enterprise.litellm.LiteLLMService`. This is the proven reset path.
- `member_state.provider_member_ref` returned by `sync_member_allocation()` is the LiteLLM customer ID needed as the `user_id` argument for `reset_customer_spending_in_litellm`.
- `self._effective_member_budget_id(budget.budget_id, allocation)` in the service gives the effective child budget_id (shared or override) needed as the `budget_id` argument.
- `SettingsService.get_enforce_member_spend_limits(project_name)` controls whether per-member limits are enforced; the spend reset must run regardless of this flag (AC: "for any project regardless of whether enforcement is enabled").

### Patterns and Conventions

- All provider calls go through `get_active_provider()` from `provider_registry.py`. Core services must not import `codemie.enterprise.litellm` directly.
- Provider method failures in the reset path are logged as warnings with structured `budget_event=...` log fields; the member's `sync_status` is set to `FAILED` in the DB and the loop continues (fail-open).
- `reset_user_budget_spending()` in `LiteLLMBudgetProviderAdapter` raises `RuntimeError` if the reset call returns `False`; the service layer catches provider exceptions and logs them (see `_sync_created_member_allocations` pattern).
- Noop implementations for the `_NoopBudgetEnforcementProvider` must consume all kwargs via `self._noop_result(...)` and return `None`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/architecture/layered-architecture.md` — describes the provider abstraction pattern; core services must not reach into enterprise packages.
- `.ai-run/guides/agents/agent-tools.md`, `.ai-run/guides/integration/external-services.md` — not directly relevant to this bug.

### Architectural Decisions

- The provider pattern with a protocol in `provider.py` + noop in `provider_registry.py` + LiteLLM implementation in `budget_provider_adapter.py` is the established ADR for budget enforcement. Adding a new capability requires implementing it at all three layers.
- Comment in `budget_provider_adapter.py` (line 18-22): "All LiteLLM-specific details (customer id construction, budget_id mapping, spend-reset semantics) are confined here. Core services MUST NOT import from codemie.enterprise.litellm directly."

### Derived Conventions

- Structured log lines use `budget_event=<verb>_<noun>_<phase>` keys (e.g., `budget_event=provider_customer_spending_reset_started`).
- New provider methods that can fail should raise on hard failure (following `reset_user_budget_spending`) so the service layer can decide whether to abort or log-and-continue.
- The service layer's existing reset loop logs a warning on per-member exceptions and marks `sync_status=FAILED`; the same pattern should apply for the new spend reset step.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/budget/test_project_budget_service.py` — `test_reset_project_budget_persists_provider_budget_id_for_each_member()` (line 331): verifies that `reset_project_budget_spend` is called and that `sync_member_allocation` metadata is persisted, but does not assert that any member spend reset is called.
- `tests/codemie/service/budget/test_project_budget_service_lifecycle.py` — `test_reset_project_budget_uses_provider_reset_when_provider_budget_ref_missing()` (line 30): similar scope; no member spend reset assertion.
- `tests/enterprise/litellm/test_budget_helpers.py` — three tests for `reset_customer_spending_in_litellm`: service unavailable, success, and exception cases. These are sufficient; no new tests needed there.
- No test covers `reset_project_budget_group` delegating to `reset_project_budget`.

### Testing Framework and Patterns

- pytest with `@pytest.mark.asyncio`, `AsyncMock` for async collaborators, `SimpleNamespace` for ad-hoc stubs, `patch.object` and `patch` from `unittest.mock`. Pattern: arrange a `SimpleNamespace` provider mock, patch service collaborators, call service method, assert provider mock was awaited with correct args.

### Coverage Gaps

- No test asserts that a member spend reset call is made in `reset_project_budget()` — this is the primary missing test.
- No test covers the failure path for member spend reset (i.e., what happens when the new reset method raises or returns failure).
- No test for `reset_project_budget_group` delegating correctly to `reset_project_budget` for each category.

---

## 5. Configuration and Environment

### Environment Variables

- `ENFORCE_MEMBER_SPEND_LIMITS` (or equivalent read via `SettingsService.get_enforce_member_spend_limits`) — controls per-member limit enforcement. The fix must not gate the spend reset on this flag.

### Configuration Files

- `litellm_config.yaml` — LiteLLM proxy config; not affected by this change.

### Feature Flags and Deployment Concerns

- LiteLLM availability is guarded by `get_litellm_service_or_none()` in `budget_helpers.py`; when unavailable, `reset_customer_spending_in_litellm` returns `False`. The fix must handle this gracefully (consistent with existing fail-open pattern).
- No migration needed — no DB schema change is required.

---

## 6. Risk Indicators

- **`sync_member_allocation` does not zero spend**: Confirmed from `budget_provider_adapter.py` line 1174 — it calls `service.sync_project_member_budget_assignment(...)`, which is a customer upsert (create or update), not a delete+recreate. Spend counter is not touched.
- **Group reset inherits the bug**: `reset_project_budget_group()` (line 2259) delegates entirely to `reset_project_budget()`. Both endpoints are affected; both will be fixed once `reset_project_budget()` is fixed.
- **`provider_member_ref` availability**: The `provider_member_ref` required for the reset call is available as `member_state.provider_member_ref` after `sync_member_allocation()` returns. If `sync_member_allocation` fails for a member (returns `sync_status=FAILED`), `provider_member_ref` may be `None` or empty — the spend reset must guard against this.
- **Protocol extension**: Adding a method to `BudgetEnforcementProvider` (a `@runtime_checkable Protocol`) requires implementations in three places: `provider.py`, `provider_registry.py`, and `budget_provider_adapter.py`. Missing any one causes a `TypeError` at runtime or breaks the noop path.
- **Ordering concern**: The spend reset (delete+recreate) and the `sync_member_allocation` (upsert) both touch the same LiteLLM customer record. If reset runs after sync, the delete step removes the just-updated customer; the recreate step sets limits from the budget record. This is correct if the budget record's limits were updated by the project key reset. Call order matters: sync first, then reset, to ensure the DB metadata (from sync) is captured before spend is zeroed.
- **Failure surfacing**: AC requires failures are "not silently swallowed". Current `reset_customer_spending_in_litellm` logs warnings; the new provider method should follow `reset_user_budget_spending`'s pattern of raising `RuntimeError` on `False`, allowing the service to log a warning per member and set `sync_status=FAILED`.

---

## 7. Summary for Complexity Assessment

The bug is a single missing step in `reset_project_budget()` in `project_budget_service.py`: the per-member `budget_period_spend` in LiteLLM is never zeroed because only `sync_member_allocation()` (an upsert) is called rather than `reset_customer_spending_in_litellm()` (a delete+recreate). The group reset endpoint is also affected because it delegates entirely to `reset_project_budget()` for each category budget. Both UI-facing endpoints (`/project-budgets/{id}/reset` and `/project-budget-groups/{id}/reset`) call these backend paths. The fix requires adding one new method to the `BudgetEnforcementProvider` protocol (`reset_project_member_spending`) and implementing it in three places: the abstract protocol in `provider.py`, the noop provider in `provider_registry.py`, and the LiteLLM adapter in `budget_provider_adapter.py`. The implementation reuses the existing `reset_customer_spending_in_litellm` helper directly.

The service change itself is narrow: in `reset_project_budget()`, after `sync_member_allocation()` returns a `member_state`, call the new provider method using `member_state.provider_member_ref` and `self._effective_member_budget_id(...)`. Failure handling should follow the existing per-member exception pattern (log warning, update `sync_status=FAILED`). No DB schema changes are needed. The noop provider path ensures the fix is transparent in non-enterprise mode. Test additions cover: (a) the new reset call is made for each member, (b) failure is surfaced, and (c) group reset delegates correctly. Total file surface: 4 source files + 2 test files. Complexity is low-to-medium: the logic is straightforward, the integration point is well-understood, but the three-layer provider extension requires discipline.

---

## 8. External References

**Source**: `C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie-ui-next` — resolved and readable.

Key facts sourced from `src/store/projectBudgets.ts`:
- `resetProjectBudget(budgetId)` → `POST v1/admin/project-budgets/${budgetId}/reset` (line 193)
- `resetProjectBudgetGroup(groupId)` → `POST v1/admin/project-budget-groups/${groupId}/reset` (line 357)

Both reset actions are wired in the UI. Both backend endpoints exist and both are affected by the bug (group reset delegates to individual reset). The fix to `reset_project_budget()` covers both call paths.
