# Fix Stale Member Budget Allocation Responses — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the `effective_max_budget` formula to the API read path so `GET /v1/admin/project-budgets/{id}/members` and `_build_project_budget_response` return the full project budget per member when `enforce_member_spend_limits` is disabled.

**Architecture:** Two synchronous response-projection sites in `project_budget_router.py` currently read `allocation.allocated_max_budget` directly from the database. The fix adds one `SettingsService.get_enforce_member_spend_limits()` call per site and replaces each direct DB-field read with the conditional formula already established in `project_member_runtime_sync.py`. No database changes.

**Tech Stack:** Python, FastAPI, SQLAlchemy async, pytest, unittest.mock

---

### Task 1: Fix `_build_project_budget_response`

**Test-first: yes — `test_build_project_budget_response_uses_full_budget_when_enforcement_disabled` fails because the formula is not applied**

**Files:**
- Modify: `tests/codemie/rest_api/routers/test_project_budget_router.py`
- Modify: `src/codemie/rest_api/routers/project_budget_router.py`

- [ ] **Step 1: Write the two failing / regression tests**

Open `tests/codemie/rest_api/routers/test_project_budget_router.py` and add the following two functions after the existing `test_build_project_budget_response_includes_member_budget_id` test.

The imports already present in the file cover everything needed. The only new symbol used is `SettingsService`, which is patched via its module path — no import needed in the test file.

```python
def test_build_project_budget_response_uses_full_budget_when_enforcement_disabled():
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_category="cli",
        budget_type="project",
        name="CLI Budget",
        description=None,
        soft_budget=None,
        max_budget=100.0,
        budget_duration="30d",
        budget_reset_at=None,
        provider_metadata={},
        created_by="admin-1",
        created_at=None,
        updated_at=None,
    )
    assignment = SimpleNamespace(project_name="proj-a", allocation_mode="equal")
    allocations = [
        SimpleNamespace(
            user_id="user-1",
            allocation_mode="equal",
            allocated_soft_budget=20.0,
            allocated_max_budget=50.0,
            sync_status="ok",
            provider_metadata={},
        ),
        SimpleNamespace(
            user_id="user-2",
            allocation_mode="equal",
            allocated_soft_budget=20.0,
            allocated_max_budget=50.0,
            sync_status="ok",
            provider_metadata={},
        ),
    ]

    with patch(
        "codemie.rest_api.routers.project_budget_router.SettingsService.get_enforce_member_spend_limits",
        return_value=False,
    ):
        result = _build_project_budget_response(budget, assignment, allocations)

    assert result.member_allocations[0].allocated_max_budget == 100.0
    assert result.member_allocations[1].allocated_max_budget == 100.0
    assert result.allocated_member_budget_total == 200.0


def test_build_project_budget_response_uses_allocated_budget_when_enforcement_enabled():
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_category="cli",
        budget_type="project",
        name="CLI Budget",
        description=None,
        soft_budget=None,
        max_budget=100.0,
        budget_duration="30d",
        budget_reset_at=None,
        provider_metadata={},
        created_by="admin-1",
        created_at=None,
        updated_at=None,
    )
    assignment = SimpleNamespace(project_name="proj-a", allocation_mode="equal")
    allocations = [
        SimpleNamespace(
            user_id="user-1",
            allocation_mode="equal",
            allocated_soft_budget=20.0,
            allocated_max_budget=50.0,
            sync_status="ok",
            provider_metadata={},
        ),
        SimpleNamespace(
            user_id="user-2",
            allocation_mode="equal",
            allocated_soft_budget=20.0,
            allocated_max_budget=50.0,
            sync_status="ok",
            provider_metadata={},
        ),
    ]

    with patch(
        "codemie.rest_api.routers.project_budget_router.SettingsService.get_enforce_member_spend_limits",
        return_value=True,
    ):
        result = _build_project_budget_response(budget, assignment, allocations)

    assert result.member_allocations[0].allocated_max_budget == 50.0
    assert result.member_allocations[1].allocated_max_budget == 50.0
    assert result.allocated_member_budget_total == 100.0
```

- [ ] **Step 2: Run the tests — verify RED**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_project_budget_router.py::test_build_project_budget_response_uses_full_budget_when_enforcement_disabled tests/codemie/rest_api/routers/test_project_budget_router.py::test_build_project_budget_response_uses_allocated_budget_when_enforcement_enabled -v
```

Expected: both FAIL. The `_disabled` test fails because `allocated_max_budget` is `50.0` instead of `100.0`. The `_enabled` test fails because `SettingsService` is not yet imported in the router (`AttributeError` on patch target or `50.0` assertion passes trivially — either way confirm both tests are exercising the right path before proceeding).

- [ ] **Step 3: Add `SettingsService` import to the router**

In `src/codemie/rest_api/routers/project_budget_router.py`, add the import after the existing service imports (around line 34):

```python
from codemie.service.settings.settings import SettingsService
```

The import block will look like:
```python
from codemie.service.budget.project_budget_service import project_budget_service
from codemie.service.settings.settings import SettingsService
```

- [ ] **Step 4: Apply the effective formula in `_build_project_budget_response`**

Replace the current body of `_build_project_budget_response` (lines 136–174) with:

```python
def _build_project_budget_response(
    budget: Budget,
    assignment: ProjectBudgetAssignment | None,
    allocations: list[ProjectMemberBudgetAssignment],
) -> ProjectBudgetResponse:
    provider_meta: dict = budget.provider_metadata or {}
    project_name = assignment.project_name if assignment else ""
    enforce_limit = SettingsService.get_enforce_member_spend_limits(project_name)
    allocated_total = sum(
        a.allocated_max_budget if enforce_limit else budget.max_budget for a in allocations
    )
    return ProjectBudgetResponse(
        budget_id=budget.budget_id,
        project_name=project_name,
        budget_category=BudgetCategory(budget.budget_category),
        budget_type=budget.budget_type,
        name=budget.name,
        description=budget.description,
        soft_budget=budget.soft_budget,
        max_budget=budget.max_budget,
        budget_duration=budget.budget_duration,
        allocation_mode=assignment.allocation_mode if assignment else AllocationMode.EQUAL.value,
        budget_reset_at=budget.budget_reset_at,
        member_count=len(allocations),
        allocated_member_budget_total=allocated_total,
        provider=provider_meta.get("provider"),
        provider_sync_status=provider_meta.get("sync_status"),
        provider_last_synced_at=provider_meta.get("last_synced_at"),
        created_by=budget.created_by,
        created_at=budget.created_at,
        updated_at=budget.updated_at,
        member_allocations=[
            ProjectBudgetMemberAllocationResponse(
                user_id=a.user_id,
                allocation_mode=a.allocation_mode,
                allocated_soft_budget=a.allocated_soft_budget,
                allocated_max_budget=a.allocated_max_budget if enforce_limit else budget.max_budget,
                sync_status=a.sync_status,
                budget_id=_member_budget_id(a),
            )
            for a in allocations
        ],
    )
```

The only structural differences from the original:
1. `project_name` extracted as a local variable (was inlined twice before).
2. `enforce_limit` computed via `SettingsService`.
3. `allocated_total` and `allocated_max_budget` in the comprehension use the conditional formula.

- [ ] **Step 5: Run the tests — verify GREEN**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_project_budget_router.py -v
```

Expected: all tests PASS, including the two new ones and the pre-existing `test_build_project_budget_response_includes_member_budget_id`.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/rest_api/routers/project_budget_router.py tests/codemie/rest_api/routers/test_project_budget_router.py
git commit -m "EPMCDME-12959: Apply effective_max_budget formula in _build_project_budget_response"
```

---

### Task 2: Fix `list_project_budget_members`

**Test-first: yes — `test_list_project_budget_members_returns_effective_budget_when_enforcement_disabled` fails because `_budget` is discarded and the formula is not applied**

**Files:**
- Modify: `tests/codemie/rest_api/routers/test_project_budget_router.py`
- Modify: `src/codemie/rest_api/routers/project_budget_router.py`

- [ ] **Step 1: Write the failing test**

Add the following test after `test_list_project_budget_members_returns_nullable_budget_id`:

```python
@pytest.mark.asyncio
async def test_list_project_budget_members_returns_effective_budget_when_enforcement_disabled():
    session = AsyncMock()
    budget = SimpleNamespace(max_budget=100.0)
    assignment = SimpleNamespace(project_name="proj-a")
    allocation = SimpleNamespace(
        user_id="user-1",
        allocation_mode="equal",
        allocated_soft_budget=20.0,
        allocated_max_budget=50.0,
        sync_status="ok",
        provider_metadata={},
    )

    with (
        patch(
            "codemie.rest_api.routers.project_budget_router.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.rest_api.routers.project_budget_router.project_budget_service.get_project_budget",
            new=AsyncMock(return_value=(budget, assignment, [allocation])),
        ),
        patch(
            "codemie.rest_api.routers.project_budget_router.SettingsService.get_enforce_member_spend_limits",
            return_value=False,
        ),
    ):
        result = await list_project_budget_members("proj-budget-1", user=_admin_user())

    assert result.data[0].allocated_max_budget == 100.0
```

- [ ] **Step 2: Run the test — verify RED**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_project_budget_router.py::test_list_project_budget_members_returns_effective_budget_when_enforcement_disabled -v
```

Expected: FAIL — `allocated_max_budget` is `50.0` instead of `100.0` (the current code reads the DB value directly and discards `budget`).

- [ ] **Step 3: Fix `list_project_budget_members`**

Replace the current `list_project_budget_members` handler (lines 365–388) with:

```python
@router.get("/{budget_id}/members", response_model=ProjectBudgetMembersResponse)
async def list_project_budget_members(
    budget_id: str,
    user: User = Depends(authenticate),
):
    """List active member allocations for a project budget when authorized."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        budget, assignment, allocations = await project_budget_service.get_project_budget(session, budget_id)
        if assignment is not None:
            _ensure_project_budget_read_access(user, assignment.project_name)
    project_name = assignment.project_name if assignment else ""
    enforce_limit = SettingsService.get_enforce_member_spend_limits(project_name)
    return ProjectBudgetMembersResponse(
        data=[
            ProjectBudgetMemberAllocationResponse(
                user_id=a.user_id,
                allocation_mode=a.allocation_mode,
                allocated_soft_budget=a.allocated_soft_budget,
                allocated_max_budget=a.allocated_max_budget if enforce_limit else budget.max_budget,
                sync_status=a.sync_status,
                budget_id=_member_budget_id(a),
            )
            for a in allocations
        ]
    )
```

The only differences from the original:
1. `_budget` renamed to `budget` (retained, not discarded).
2. `project_name` and `enforce_limit` computed after the `async with` block.
3. `allocated_max_budget` uses the conditional formula.

- [ ] **Step 4: Run all router tests — verify GREEN**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_project_budget_router.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
poetry run pytest tests/ -x -q
```

Expected: no failures introduced by the change.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/rest_api/routers/project_budget_router.py tests/codemie/rest_api/routers/test_project_budget_router.py
git commit -m "EPMCDME-12959: Apply effective_max_budget formula in list_project_budget_members"
```
