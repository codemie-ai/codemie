# Spec: Fix Stale Member Budget Allocation Responses

**Ticket**: EPMCDME-12959
**Branch**: EPMCDME-12959

## Problem

When `enforce_member_spend_limits` is disabled on a project, the admin API continues to return `allocation.allocated_max_budget` — the persisted per-member split from the last rebalance — instead of the full project budget each member is now entitled to.

Example: project `max_budget = 100`, two members, enforcement enabled and rebalanced (each DB row stores `allocated_max_budget = 50`). After disabling enforcement the API still returns `50` per member and `100` as the aggregate, while every member should show `100` and the aggregate should show `200`.

The enforcement layer already computes the correct value:

```python
# project_member_runtime_sync.py:184, 290
effective_max_budget = allocation.allocated_max_budget if enforce_limit else budget.max_budget
```

The API read path does not apply this formula.

## Fix

Apply the same effective formula to both per-member values and the aggregate in the two API response-building sites in `project_budget_router.py`. No database changes. No changes to the runtime sync or LiteLLM paths.

### `_build_project_budget_response` (line 136)

Add a single `SettingsService.get_enforce_member_spend_limits(project_name)` call before the member comprehension. Replace every `a.allocated_max_budget` reference used for response output with the effective value:

```python
project_name = assignment.project_name if assignment else ""
enforce_limit = SettingsService.get_enforce_member_spend_limits(project_name)
effective_max = lambda a: a.allocated_max_budget if enforce_limit else budget.max_budget

allocated_total = sum(effective_max(a) for a in allocations)
# ...
member_allocations=[
    ProjectBudgetMemberAllocationResponse(
        ...
        allocated_max_budget=effective_max(a),
        ...
    )
    for a in allocations
]
```

`allocated_soft_budget` is not changed — soft limits are not governed by the enforcement flag.

### `list_project_budget_members` (line 366)

Stop discarding the `_budget` return value from `get_project_budget`. Rename to `budget`. Add the same `SettingsService` call and apply the effective formula:

```python
budget, assignment, allocations = await project_budget_service.get_project_budget(session, budget_id)
# ...
enforce_limit = SettingsService.get_enforce_member_spend_limits(assignment.project_name if assignment else "")
# ...
allocated_max_budget=a.allocated_max_budget if enforce_limit else budget.max_budget,
```

### Import

Add to `project_budget_router.py`:

```python
from codemie.service.settings.settings import SettingsService
```

### Unchanged behaviour

- `_load_and_build_response` delegates to `_build_project_budget_response` and picks up the fix automatically. No change needed.
- All mutation endpoints (`rebalance`, `override_member_allocation`, `clear_member_override`) return via `_build_project_budget_response` or `_load_and_build_response` and also pick up the fix.
- `project_member_runtime_sync.py` is not touched.
- No DB schema or migration changes.

## Acceptance Criteria

- When `enforce_member_spend_limits` is disabled, `GET /v1/admin/project-budgets/{id}/members` returns `allocated_max_budget = budget.max_budget` for every member and `allocated_member_budget_total = N × budget.max_budget`.
- When `enforce_member_spend_limits` is enabled, both endpoints return the persisted `allocation.allocated_max_budget` values.
- `_build_project_budget_response` applies the effective formula consistently to both per-member values and the aggregate total.
- LiteLLM synchronisation behaviour is unchanged.
- Rebalance behaviour is unchanged when enforcement is enabled.
- Regression coverage is added for the enforcement-disabled read path.

## Tests

File: `tests/codemie/rest_api/routers/test_project_budget_router.py`

Three new test functions following the `SimpleNamespace` + `unittest.mock.patch` pattern already used in the file:

1. **`test_build_project_budget_response_uses_full_budget_when_enforcement_disabled`**
   - Patch `SettingsService.get_enforce_member_spend_limits` → `False`
   - Two allocations with `allocated_max_budget = 50`, `budget.max_budget = 100`
   - Assert each member's `allocated_max_budget == 100`
   - Assert `allocated_member_budget_total == 200`

2. **`test_build_project_budget_response_uses_allocated_budget_when_enforcement_enabled`**
   - Patch `SettingsService.get_enforce_member_spend_limits` → `True`
   - Same setup
   - Assert each member's `allocated_max_budget == 50`
   - Assert `allocated_member_budget_total == 100`

3. **`test_list_project_budget_members_returns_effective_budget_when_enforcement_disabled`**
   - Patch `project_budget_service.get_project_budget` returning `(budget, assignment, [allocation])`
   - Patch `SettingsService.get_enforce_member_spend_limits` → `False`
   - `allocation.allocated_max_budget = 50`, `budget.max_budget = 100`
   - Assert `result.data[0].allocated_max_budget == 100`
