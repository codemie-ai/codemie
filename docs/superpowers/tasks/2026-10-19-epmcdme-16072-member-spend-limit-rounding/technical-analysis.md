# Technical Research

**Task**: budget project member allocation spend-limit
**Generated**: 2026-10-19
**Research path**: filesystem

---

## 1. Original Context

Jira EPMCDME-16072 (Bug): Incorrect per-member spend limit calculation for small platform budgets when 'Enforce member spend limits' is enabled. When enabled, the platform budget is split across all project members. With small budgets (e.g. 70 members, $1 platform budget) some members get $0.02 and one gets a negative limit (-$0.29), see screenshot docs/superpowers/tasks/2026-10-19-epmcdme-16072-member-spend-limit-rounding/ticket-screenshot.png. Expected: every member gets valid non-negative limit; per-member limits consistent; sum does not exceed platform budget. ACs: 1) 70 members/$1 -> no negative limit. 2) sum of member limits <= platform budget. 3) all members get limits from same rounding+distribution rule, no unexplained differences. 4) small budgets or large member counts follow same rule as larger budgets. 5) regression tests cover small budget values and large member counts. Known suspects: src/codemie/service/budget/project_budget_service.py _allocate_equal and _equal_amounts_with_fixed_overrides use ROUND_HALF_UP cent share then put (possibly negative) remainder on last member. Also check all other callers/places computing equal member shares (backend services, litellm runtime sync, migration/backfill in budget_service.py) and whether codemie-ui (D:\Projects\codemie-ui) computes preview shares client-side.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/service/budget/project_budget_service.py`
  - `_CENT = Decimal("0.01")` (L68), `DEFAULT_SOFT_LIMIT_PCT = 80.0` (L70).
  - `_allocate_equal` (L184-234): `share = (B/n).quantize(_CENT, ROUND_HALF_UP)`; `remainder = B - share*n`; the last member in sorted `user_id` order gets `share + remainder`. Max and soft are split independently.
  - `_equal_amounts_with_fixed_overrides` (L246-285): the same algorithm applied to `remaining = B - Σfixed` across EQUAL members sorted by user_id.
  - `_validate_member_amounts` (L237-243): validates manual overrides only. Computed shares are never checked for being negative or for soft > max.
  - Callers of `_allocate_equal`: `create_project_budget` (L936), `create_project_budget_group` (L1909), `_create_new_group_category` (L2292).
  - Caller of `_equal_amounts_with_fixed_overrides`: `_resync_member_allocations` (L1193). That in turn backs `update_project_budget`, `rebalance_project_budget`, `override_member_allocation`, `clear_member_override` and the group category update.
  - The shared child `Budget` for EQUAL members is seeded on create from `allocation_rows[0]` (L960-963, L1925-1928, L2308-2309). On resync it is seeded from the first non-None value of an unordered query (L1199-1216).
  - `_sync_created_member_allocations` (L512-585) pushes each member's `allocated_max_budget` to the provider when enforcement is on.
  - Group category amount is `float(total * pct / 100)` (L1878, L2192). It is not quantized to cents.
- `src/codemie/service/budget/budget_service.py`: the backfill `_create_backfilled_project_budget` (L1497-1583) reuses `_allocate_equal` (L1546).
- `src/codemie/service/project/project_assignment_service.py`
  - `_get_member_added_allocation_amounts` (L181-206) copies the `.first()` EQUAL allocation with no ordering, so it can copy the remainder member's value.
  - Member removal does not redistribute the freed share.
- `src/codemie/enterprise/litellm/project_member_runtime_sync.py` and `budget_provider_adapter.py` compute nothing. They push the stored per-member value to LiteLLM with no negative or zero guard.
- `src/codemie/rest_api/routers/project_budget_router.py` `_displayed_member_max_budget` (L72-78) shows the stored DB value. This is why the UI shows -$0.29.

### Architecture and Layers Affected
- Service layer: `ProjectBudgetService` static allocation helpers. This is the primary fix location.
- Service layer, secondary: `ProjectAssignmentService._get_member_added_allocation_amounts` (it copies a share).
- No REST, model, migration or UI change is needed. Repositories are unchanged.

### Integration Points
- codemie-enterprise `LiteLLMService.sync_project_member_budget_assignment` → `_ensure_project_member_budget` creates or updates the **shared** LiteLLM budget row for all EQUAL members. Each member sync overwrites it, and the last one synced wins.
- The per-project setting "Enforce member spend limits" decides whether the provider gets `allocated_max_budget` (on) or `budget.max_budget` (off).

### Patterns and Conventions
- Static helpers on the service singleton; Decimal math is persisted as float.
- Structured `budget_event=` logging.
- Deterministic ordering of members by `user_id`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/testing/testing-patterns.md`: the "Seam Tests for Policy Helpers" rule requires a callsite test that asserts the boundary value, not only unit tests of the helper.
- `.ai-run/guides/testing/testing-service-patterns.md`, `architecture/service-layer-patterns.md`, `quality-gates.md`.
- No budget-specific guide.

### Architectural Decisions
- The `_allocate_equal` docstring records the "remainder to deterministic last member" rule. `ROUND_HALF_UP` was introduced in commit 9c86cc862 (EPMCDME-11836).
- Prior specs:
  - 2026-06-22 epmcdme-12959 (when enforcement is off, members are shown the project max).
  - 2026-10-13 EPMCDME-15764 (FIXED overrides are shown even when enforcement is off; a 0 override is a real value).

### Derived Conventions
- EQUAL members share a single provider budget, so per-member cent differences cannot be enforced. Only one value is effective.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/budget/test_project_budget_service_lifecycle.py` L542-589: `_equal_amounts_with_fixed_overrides` exceed messages and one remainder case.
- `tests/codemie/service/budget/test_project_budget_service.py`: shared child update and the sync tests.
- `tests/codemie/service/project/test_project_assignment_service.py`: `TestGetMemberAddedAllocationAmounts` (L620-710).
- `tests/enterprise/litellm/test_project_member_runtime_sync.py`, `test_budget_provider_adapter.py`.

### Testing Framework and Patterns
- pytest, pytest-asyncio, xdist (`-n 3`, importlib).
- `SimpleNamespace` allocation fakes; `AsyncMock` sessions with `patch` of repositories by full module path.

### Coverage Gaps
- `_allocate_equal` has zero direct tests.
- No tests with small budgets or large member counts, of sum <= budget, of non-negative shares, or of soft <= max.
- No seam test of the value seeded into the shared child budget or of the add-member copy ordering.

---

## 5. Configuration and Environment

### Environment Variables
- `BUDGET_STOP_ENABLED` is unrelated to the math. No env var governs allocation.

### Configuration Files
- `pytest.ini`, `pyproject.toml` (codemie-enterprise 2.3.44).

### Feature Flags and Deployment Concerns
- Per-project setting `enforce_member_spend_limits`.
- No migration is needed. Existing bad rows heal on the next rebalance, budget update or override change.

---

## 6. Risk Indicators

- The shared LiteLLM budget is overwritten by each member sync. Any rule that gives EQUAL members different values (last-member remainder, or +1 cent largest-remainder) makes the effective limit depend on sync order. Use a uniform per-member value.
- When the budget is below n × $0.01, the floor share is $0.00, which blocks members at runtime. This is non-negative and AC-compliant but is a product decision.
- Soft and max are split independently, so soft > max per member is possible. The fix must keep soft <= max.
- The group category amount can have sub-cent precision (`total*pct/100`), so flooring must handle non-cent inputs.
- `_get_member_added_allocation_amounts` uses unordered `.first()`. Once all shares are uniform the result is correct regardless of order, but a newly added member makes the sum exceed the budget by one share until the next rebalance (pre-existing, out of scope).
- Existing tests that expect the remainder on the last member (lifecycle remainder case) may need updating.

---

## 7. Summary for Complexity Assessment

The fix is centered on two static helpers in `src/codemie/service/budget/project_budget_service.py` (`_allocate_equal`, `_equal_amounts_with_fixed_overrides`). They feed every create, update, rebalance, override and backfill path, plus the shared child budget that LiteLLM enforces for all EQUAL members. One service layer is touched; no REST, model, migration or UI changes. The expected change surface is one source file plus two or three test files.

The technical novelty is low: replace HALF_UP plus last-member remainder with a uniform cent-floored share, with leftover cents unallocated, and enforce soft <= max. The important non-obvious constraint is that EQUAL members share one provider budget, so a uniform value is the only rule LiteLLM can enforce consistently. A largest-remainder scheme would look fair in the DB but not hold at runtime.

Test posture is weak: `_allocate_equal` has no tests, and there are no small-budget or large-n cases. Regression tests for the helpers plus seam tests at the create and resync callsites are needed, per the testing guide. The key risks are the $0.00 share when the budget is below n cents (product choice), existing tests asserting the old remainder behavior, and pre-existing member-add drift (out of scope).
