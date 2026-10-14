# Show Member Budget Override With Enforcement Off — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** With "Enforce member spend limits" off, the admin project-budget API returns a fixed-mode member's stored override as `allocated_max_budget` instead of `budget.max_budget`, and the total matches.

**Architecture:** The rule lives only in `src/codemie/rest_api/routers/project_budget_router.py`, inlined three times. One module-level private helper expresses it; the three sites call it.

**Tech Stack:** Python, FastAPI, pytest (`pytest-asyncio`), run through Poetry.

**Spec:** `docs/superpowers/tasks/2026-10-13-show-member-budget-override-enforcement-off/spec.md`

## Global Constraints

- Commit per task using the repository's existing convention (`EPMCDME-15764: Description`).
- The diff touches exactly two files: `src/codemie/rest_api/routers/project_budget_router.py` and `tests/codemie/rest_api/routers/test_project_budget_router.py`.
- Negative constraints (all honored by Task 1, which edits nothing else):
  - No diff in `src/codemie/enterprise/litellm/` or `src/codemie/service/budget/` — no sync, adapter, write-path or validation change.
  - `_build_project_budget_group_response` (`project_budget_router.py:458`), `member_spend_service._limit_value` and the project list/detail total keep using persisted amounts.
  - `allocated_soft_budget`, `allocation_mode` and all other response fields still come straight from the row.
  - No response model, OpenAPI baseline, migration or setting change.
  - Branch on `allocation_mode`, never on the amount: an override of `0` is returned as `0`.
  - Existing tests in the test file pass unmodified; add tests, do not edit existing ones.

## Review Focus

- Zero-amount override with enforcement off must show `0`, not the full budget — pinned by the `0.0` row in both new tests.
- The three sites must agree — both new tests use the same rows and expectations, and the total is asserted against the same per-member values.
- `assignment is None` in the builder (enforcement resolves to off): a fixed row shows its override — pinned by the third new test.

---

### Task 1: Return fixed overrides on the enforcement-off read path

Test-first: yes — with enforcement patched off and rows `fixed 30 / fixed 0 / equal 70` on a budget of 100, the builder and the members endpoint return `[100, 100, 100]` (total `300`) instead of `[30, 0, 100]` (total `130`).

**Files:**
- Modify: `src/codemie/rest_api/routers/project_budget_router.py:79-82, 109, 355`
- Test: `tests/codemie/rest_api/routers/test_project_budget_router.py`

**Interfaces:**
- Produces: `_displayed_member_max_budget(allocation, budget, enforce_limit) -> float`, private to the router module.

- [ ] **Step 1: Write the failing tests.** Add three tests after `test_list_project_budget_members_returns_effective_budget_when_enforcement_disabled` (ends at line 253), reusing the `SimpleNamespace` fixtures and the `SettingsService.get_enforce_member_spend_limits` patch from the tests at 94-139 and 224-253. Rows for all three: `user-1` `fixed` max `30.0` soft `10.0`; `user-2` `fixed` max `0.0` soft `0.0`; `user-3` `equal` max `70.0` soft `20.0`; budget `max_budget=100.0`.
  - `test_build_project_budget_response_fixed_override` — `@pytest.mark.parametrize` over `(enforce_limit, expected_max, expected_total)`: `(False, [30.0, 0.0, 100.0], 130.0)` and `(True, [30.0, 0.0, 70.0], 100.0)`. Assert each `member_allocations[i].allocated_max_budget`, `allocated_member_budget_total`, and that `allocated_soft_budget` is `[10.0, 0.0, 20.0]` in both cases.
  - `test_list_project_budget_members_fixed_override` — async, same parametrization without the total, asserting `result.data[i].allocated_max_budget`.
  - `test_build_project_budget_response_fixed_override_without_assignment` — `assignment=None`, enforcement patched `False`, same rows; expect `[30.0, 0.0, 100.0]` and total `130.0`.

- [ ] **Step 2: Run them and confirm the failure.**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_project_budget_router.py -k "fixed_override"`
Expected: the `False` cases and the no-assignment test FAIL with `100.0 != 30.0`; the `True` cases PASS.

- [ ] **Step 3: Add the helper** above `_build_project_budget_response` (line 72):

```python
def _displayed_member_max_budget(
    allocation: ProjectMemberBudgetAssignment, budget: Budget, enforce_limit: bool
) -> float:
    """Return the member max to display: the stored amount when enforced or overridden, else the project max."""
    if enforce_limit or allocation.allocation_mode == AllocationMode.FIXED.value:
        return allocation.allocated_max_budget
    return budget.max_budget
```

- [ ] **Step 4: Use it at the three sites.** Replace the ternary `a.allocated_max_budget if enforce_limit else budget.max_budget` with `_displayed_member_max_budget(a, budget, enforce_limit)` at lines 82 (inside `sum(...)`), 109 and 355. Delete the comment at lines 80-81; it is false once fixed members show their override. Leave the `enforce_limit` lookups at 79 and 348 as they are.

- [ ] **Step 5: Run the file's tests.**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_project_budget_router.py`
Expected: all PASS, including the unmodified enforcement tests at lines 94, 142 and 224.
