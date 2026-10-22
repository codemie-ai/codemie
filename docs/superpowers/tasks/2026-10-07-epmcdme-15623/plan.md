# Cost Center Budget Rollup (Backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a shared, reusable cross-project budget summation helper and a read-only cost-centers endpoint that returns a cost center's combined budget total and platform/cli/premium_models breakdown, computed live from its linked projects.

**Architecture:** A new module `cost_center_budget_summary.py` owns two Pydantic response models (mirroring `ProjectSpendingSummary`/`ProjectAssignedBudgetSummary`) and a pure-async summation function that takes an already-resolved `project_names: list[str]` and groups/sums `get_assigned_budget_summaries_for_projects` rows by `BudgetCategory`. A new async repository method resolves a cost center's linked project names without touching a sync `Session`. The new router endpoint composes: a short sync block for the existing `get_or_404` gate, then an async block for name resolution + summation — the two session kinds run sequentially, never interleaved, resolving the sync/async split without mixing sync calls into the async aggregation path.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy (sync `Session` + `AsyncSession`), Pydantic, pytest + `unittest.mock` (`MagicMock`/`AsyncMock`).

**Spec:** Requirements supplied inline for ticket EPMCDME-15623 (no spec.md); technical analysis at `docs/superpowers/tasks/2026-10-07-epmcdme-15623/technical-analysis.md`.

## Acceptance criteria

- [ ] A shared, reusable summation helper exists (not duplicated per-caller) that returns platform, cli, premium_models and overall budget sums for a cost center's linked projects, usable independently of any specific endpoint/caller.
- [ ] For a cost center with one or more linked projects with assigned budget data, the endpoint returns the combined total and category breakdown correctly summed across those projects.
- [ ] The computation is live/non-cached: calling the endpoint again after a linked project's budget changes reflects the new totals.
- [ ] For a cost center with no linked projects, the endpoint returns a clear zero/empty result (zero totals), not an error.
- [ ] A linked project with no budget assigned in a category contributes zero to that category without preventing other categories from being summed correctly.
- [ ] The endpoint's response shape mirrors the existing project-level `ProjectSpendingSummary`/`ProjectAssignedBudgetSummary` pattern closely enough to be consumed the same way by frontend code (data-contract parity; visual parity is out of scope).
- [ ] Backend only: no `.tsx`/frontend changes; the summation uses `service.budget.budget_enums.BudgetCategory`, not the other `enterprise.litellm.budget_categories.BudgetCategory`; no new `CostCenter` persistence/field is added.

## Global Constraints

- Backend only — no `.tsx`/frontend work; the UI card is built in a separate repository.
- No new persistence: no `CostCenter` budget field, table, or stored rollup — every call computes live from currently-linked projects.
- Use `codemie.service.budget.budget_enums.BudgetCategory` everywhere category typing is needed — never `codemie.enterprise.litellm.budget_categories.BudgetCategory` (two identically-shaped enums exist; importing the wrong one is a silent enum-identity bug).
- Read-only: no PATCH/PUT/DELETE affecting the rollup; no new field on `CostCenterUpdateRequest`.
- The summation core (`get_cost_center_budget_summary`) must be pure-async and callable with a plain `project_names: list[str]` — no sync `Session` call anywhere inside it — so the two sibling stories (`2026-10-05-cost-center-project-drilldown`, `2026-10-05-cost-center-management-budgets-column`) can call it with their own already-resolved project lists without re-deriving names or reimplementing summation.
- Commit per task using the repository's existing convention (see recent commits on this branch for the message format); no new format is introduced by this plan.

## Review Focus

- A linked project with rows in only one category (e.g. only `cli`) must not cause `platform`/`premium_models` to be missing from the output incorrectly or to error — each category is summed independently from whatever rows exist. (Task 3)
- A `budget_category` value that fails `BudgetCategory(...)` validation (unknown/invalid string) must be skipped with a warning, not raise and fail the whole rollup — mirrors `_attach_assigned_budget_summaries`'s established tolerance. (Task 3)
- `current_spending` on a row can be `None` (per `ProjectAssignedBudgetSummaryRow`); summation must treat it as `0.0`, not raise `TypeError` on `None + float`. (Task 3)
- A cost center that exists but has zero linked projects must return a `200` with all-zero/empty totals (`budgets=[]`, `budget_limit=None` or `0`, `current_spending=0.0`), not a `404` or `500`. (Task 3 for the core function, Task 5 for the endpoint's empty-list path)
- A cost center id that does not exist must still 404 via the existing `cost_center_service.get_or_404` gate — the new endpoint must not bypass it by jumping straight to the async block. (Task 5)

---

### Task 1: Async repository method to resolve linked project names by cost center id

**Files:**
- Modify: `src/codemie/repository/application_repository.py:859` (insert directly after `aget_project_names_by_cost_center_name`)
- Test: `tests/codemie/repository/test_application_repository.py` (new file)

**Interfaces:**
- Produces: `ApplicationRepository.aget_project_names_by_cost_center_id(self, session: AsyncSession, cost_center_id: UUID) -> list[str]`, used by Task 5's endpoint via the existing `application_repository` singleton.

- [ ] **Step 1: Write the failing test**

```python
# tests/codemie/repository/test_application_repository.py
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from codemie.repository.application_repository import ApplicationRepository


@pytest.mark.asyncio
async def test_aget_project_names_by_cost_center_id_returns_names():
    repo = ApplicationRepository()
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = ["proj-a", "proj-b"]
    session.execute = AsyncMock(return_value=result_mock)

    names = await repo.aget_project_names_by_cost_center_id(session, uuid4())

    assert names == ["proj-a", "proj-b"]
    session.execute.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/repository/test_application_repository.py -v`
Expected: FAIL — `AttributeError: 'ApplicationRepository' object has no attribute 'aget_project_names_by_cost_center_id'`

- [ ] **Step 3: Write minimal implementation**

Add, mirroring `aget_project_names_by_cost_center_name` (`application_repository.py:859-870`) but filtering by id directly (no join needed) and the sync `list_projects_by_cost_center_id`'s filter (`application_repository.py:755-763`):

```python
    async def aget_project_names_by_cost_center_id(self, session: AsyncSession, cost_center_id: UUID) -> list[str]:
        """Return names of non-deleted projects linked to the given cost center id."""
        statement = (
            select(Application.name)
            .where(Application.cost_center_id == cost_center_id)
            .where(Application.deleted_at.is_(None))
        )
        result = await session.execute(statement)
        return list(result.scalars().all())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/repository/test_application_repository.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

Commit per the repository's existing convention (see `AGENTS.md` / recent branch commits).

**Test-first: yes — `test_aget_project_names_by_cost_center_id_returns_names` fails until the new async method exists.**

---

### Task 2: Response models for the cost-center rollup

**Files:**
- Create: `src/codemie/service/budget/cost_center_budget_summary.py`
- Test: `tests/codemie/service/budget/test_cost_center_budget_summary.py` (new file; create `tests/codemie/service/budget/__init__.py` if the directory has no existing test package there — check first)

**Interfaces:**
- Produces: `CostCenterAssignedBudgetSummary(BaseModel)` and `CostCenterBudgetSummary(BaseModel)`, consumed by Task 3's summation function and Task 5's endpoint `response_model`.

- [ ] **Step 1: Write the failing test**

```python
# tests/codemie/service/budget/test_cost_center_budget_summary.py
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.cost_center_budget_summary import (
    CostCenterAssignedBudgetSummary,
    CostCenterBudgetSummary,
)


def test_models_construct_with_expected_fields():
    row = CostCenterAssignedBudgetSummary(
        budget_category=BudgetCategory.CLI,
        soft_budget=10.0,
        max_budget=20.0,
        member_count=2,
        allocated_member_budget_total=15.0,
        current_spending=5.0,
    )
    summary = CostCenterBudgetSummary(
        current_spending=5.0,
        budget_limit=20.0,
        total_percent=25.0,
        budgets=[row],
    )
    assert summary.budgets[0].budget_category is BudgetCategory.CLI
    assert summary.budget_limit == 20.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/service/budget/test_cost_center_budget_summary.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codemie.service.budget.cost_center_budget_summary'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/codemie/service/budget/cost_center_budget_summary.py
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

# Use the service-layer enum — NOT codemie.enterprise.litellm.budget_categories.BudgetCategory,
# which is a separate, identically-shaped enum that project_budget_repository/projects.py do not use.
from codemie.service.budget.budget_enums import BudgetCategory


class CostCenterAssignedBudgetSummary(BaseModel):
    """Per-category budget summary aggregated across a cost center's linked projects."""

    budget_category: BudgetCategory
    soft_budget: float
    max_budget: float
    member_count: int
    allocated_member_budget_total: float
    current_spending: float


class CostCenterBudgetSummary(BaseModel):
    """Combined budget summary aggregated across a cost center's linked projects.

    Mirrors ProjectSpendingSummary (current_spending, budget_limit, total_percent)
    plus a per-category breakdown list, for data-contract parity with the
    existing project-level pattern in rest_api/routers/projects.py.
    """

    current_spending: float
    budget_limit: Optional[float] = None
    total_percent: float
    budgets: list[CostCenterAssignedBudgetSummary]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/service/budget/test_cost_center_budget_summary.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

Commit per the repository's existing convention.

**Test-first: yes — `test_models_construct_with_expected_fields` fails until the module exists.**

---

### Task 3: Pure-async summation core

**Files:**
- Modify: `src/codemie/service/budget/cost_center_budget_summary.py` (same file as Task 2)
- Test: `tests/codemie/service/budget/test_cost_center_budget_summary.py` (same file as Task 2)

**Interfaces:**
- Consumes: `project_budget_assignment_repository.get_assigned_budget_summaries_for_projects(session, project_names, budget_category=None) -> dict[str, list[ProjectAssignedBudgetSummaryRow]]` (`src/codemie/repository/project_budget_repository.py:180`, singleton import `from codemie.repository.project_budget_repository import project_budget_assignment_repository`); `CostCenterAssignedBudgetSummary`/`CostCenterBudgetSummary` from Task 2.
- Produces: `async def get_cost_center_budget_summary(async_session: AsyncSession, project_names: list[str]) -> CostCenterBudgetSummary`, consumed by Task 5's endpoint and (per the ticket's reuse requirement) by the two sibling stories' own code, independently of this endpoint.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/codemie/service/budget/test_cost_center_budget_summary.py
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from codemie.service.budget.cost_center_budget_summary import get_cost_center_budget_summary


@dataclass
class _Row:
    project_name: str
    budget_id: str
    name: str
    budget_category: str
    soft_budget: float
    max_budget: float
    budget_duration: str
    budget_reset_at: str | None
    provider_sync_status: str | None
    member_count: int
    allocated_member_budget_total: float
    current_spending: float | None


@pytest.mark.asyncio
async def test_sums_across_projects_and_categories():
    rows_by_project = {
        "proj-a": [
            _Row("proj-a", "b1", "Platform", "platform", 10.0, 20.0, "30d", None, None, 1, 10.0, 5.0),
            _Row("proj-a", "b2", "CLI", "cli", 5.0, 10.0, "30d", None, None, 1, 5.0, None),
        ],
        "proj-b": [
            _Row("proj-b", "b3", "Platform", "platform", 15.0, 30.0, "30d", None, None, 2, 15.0, 10.0),
        ],
    }
    with patch(
        "codemie.service.budget.cost_center_budget_summary.project_budget_assignment_repository"
    ) as mock_repo:
        mock_repo.get_assigned_budget_summaries_for_projects = AsyncMock(return_value=rows_by_project)
        result = await get_cost_center_budget_summary(AsyncMock(), ["proj-a", "proj-b"])

    by_category = {row.budget_category.value: row for row in result.budgets}
    assert by_category["platform"].max_budget == 50.0
    assert by_category["platform"].current_spending == 15.0
    assert by_category["cli"].max_budget == 10.0
    assert by_category["cli"].current_spending == 0.0  # None treated as 0.0
    assert result.budget_limit == 60.0
    assert result.current_spending == 15.0


@pytest.mark.asyncio
async def test_empty_project_list_returns_zero_summary():
    with patch(
        "codemie.service.budget.cost_center_budget_summary.project_budget_assignment_repository"
    ) as mock_repo:
        result = await get_cost_center_budget_summary(AsyncMock(), [])

    mock_repo.get_assigned_budget_summaries_for_projects.assert_not_called()
    assert result.budgets == []
    assert result.budget_limit is None
    assert result.current_spending == 0.0
    assert result.total_percent == 0.0


@pytest.mark.asyncio
async def test_invalid_category_is_skipped_not_raised():
    rows_by_project = {
        "proj-a": [
            _Row("proj-a", "b1", "Bad", "not-a-real-category", 1.0, 1.0, "30d", None, None, 1, 1.0, 1.0),
            _Row("proj-a", "b2", "CLI", "cli", 5.0, 10.0, "30d", None, None, 1, 5.0, 2.0),
        ],
    }
    with patch(
        "codemie.service.budget.cost_center_budget_summary.project_budget_assignment_repository"
    ) as mock_repo:
        mock_repo.get_assigned_budget_summaries_for_projects = AsyncMock(return_value=rows_by_project)
        result = await get_cost_center_budget_summary(AsyncMock(), ["proj-a"])

    assert len(result.budgets) == 1
    assert result.budgets[0].budget_category.value == "cli"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/budget/test_cost_center_budget_summary.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_cost_center_budget_summary'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/codemie/service/budget/cost_center_budget_summary.py`:

```python
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from codemie.repository.project_budget_repository import project_budget_assignment_repository

logger = logging.getLogger(__name__)


async def get_cost_center_budget_summary(
    async_session: AsyncSession,
    project_names: list[str],
) -> CostCenterBudgetSummary:
    """Sum platform/cli/premium_models budget rows across the given projects.

    Pure-async and takes an already-resolved project name list so sibling callers
    with their own project lists can reuse this without re-resolving names or
    reimplementing summation. Never touches a sync Session.
    """
    rows_by_project: dict[str, list] = {}
    if project_names:
        rows_by_project = await project_budget_assignment_repository.get_assigned_budget_summaries_for_projects(
            async_session, project_names
        )

    totals: dict[BudgetCategory, dict[str, float]] = {}
    for rows in rows_by_project.values():
        for row in rows:
            try:
                category = BudgetCategory(row.budget_category)
            except ValueError:
                logger.warning(
                    f"Skipping assigned budget row with invalid category "
                    f"for project={row.project_name} budget_id={row.budget_id} category={row.budget_category}"
                )
                continue
            bucket = totals.setdefault(
                category,
                {"soft_budget": 0.0, "max_budget": 0.0, "member_count": 0, "allocated": 0.0, "spend": 0.0},
            )
            bucket["soft_budget"] += float(row.soft_budget)
            bucket["max_budget"] += float(row.max_budget)
            bucket["member_count"] += int(row.member_count)
            bucket["allocated"] += float(row.allocated_member_budget_total)
            bucket["spend"] += float(row.current_spending or 0.0)

    budgets = [
        CostCenterAssignedBudgetSummary(
            budget_category=category,
            soft_budget=round(bucket["soft_budget"], 2),
            max_budget=round(bucket["max_budget"], 2),
            member_count=bucket["member_count"],
            allocated_member_budget_total=round(bucket["allocated"], 2),
            current_spending=round(bucket["spend"], 2),
        )
        for category, bucket in totals.items()
    ]

    budget_limit = round(sum(b.max_budget for b in budgets), 2) if budgets else None
    current_spending = round(sum(b.current_spending for b in budgets), 2)
    total_percent = round((current_spending / budget_limit * 100), 2) if budget_limit else 0.0

    return CostCenterBudgetSummary(
        current_spending=current_spending,
        budget_limit=budget_limit,
        total_percent=total_percent,
        budgets=budgets,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/budget/test_cost_center_budget_summary.py -v`
Expected: PASS (all tests from Task 2 and Task 3 in this file)

- [ ] **Step 5: Commit**

Commit per the repository's existing convention.

**Test-first: yes — `test_sums_across_projects_and_categories`, `test_empty_project_list_returns_zero_summary`, `test_invalid_category_is_skipped_not_raised` all fail until the function exists.**

---

### Task 4: (skipped as a separate task)

A dedicated export/facade task is unnecessary — `src/codemie/service/budget/cost_center_budget_summary.py` created in Tasks 2-3 already is the stable import path (`from codemie.service.budget.cost_center_budget_summary import get_cost_center_budget_summary`) the two sibling stories need; no package `__init__.py` re-export or facade is required since nothing in this codebase funnels budget-module imports through an `__init__.py` (confirmed by `projects.py`'s direct `from codemie.repository.project_budget_repository import project_budget_assignment_repository` style import). The next task is Task 5.

---

### Task 5: Read-only `GET /{costCenterId}/budget-rollup` endpoint

**Files:**
- Modify: `src/codemie/rest_api/routers/cost_centers.py` (add import near existing imports at the top; add endpoint after `get_cost_center_detail`, i.e. after line 180)
- Test: `tests/codemie/rest_api/routers/test_cost_centers_router.py`

**Interfaces:**
- Consumes: `cost_center_service.get_or_404(session, user=user, cost_center_id=cost_center_id)` (existing, `cost_center_service.py`); `application_repository.aget_project_names_by_cost_center_id(async_session, cost_center_id)` (Task 1); `get_cost_center_budget_summary(async_session, project_names)` and `CostCenterBudgetSummary` (Tasks 2-3); `get_session` (existing sync import) and `get_async_session` (new import, `from codemie.clients.postgres import get_async_session`).
- Produces: `GET /v1/admin/cost-centers/{costCenterId}/budget-rollup` returning `CostCenterBudgetSummary` — the externally observable deliverable of this plan.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/codemie/rest_api/routers/test_cost_centers_router.py
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.cost_center_budget_summary import (
    CostCenterAssignedBudgetSummary,
    CostCenterBudgetSummary,
)


def _async_result(value):
    """One-shot coroutine factory for patching an awaited async function's return value."""

    async def _coro(*_args, **_kwargs):
        return value

    return _coro()


class TestCostCenterBudgetRollup:
    @patch("codemie.rest_api.routers.cost_centers.config")
    @patch("codemie.rest_api.routers.cost_centers.get_cost_center_budget_summary")
    @patch("codemie.rest_api.routers.cost_centers.get_async_session")
    @patch("codemie.rest_api.routers.cost_centers.application_repository")
    @patch("codemie.rest_api.routers.cost_centers.get_session")
    @patch("codemie.rest_api.routers.cost_centers.cost_center_service")
    def test_get_cost_center_budget_rollup_sums_linked_projects(
        self,
        mock_cost_center_service,
        mock_get_session,
        mock_application_repository,
        mock_get_async_session,
        mock_get_cost_center_budget_summary,
        mock_config,
    ):
        import asyncio

        from codemie.rest_api.routers.cost_centers import get_cost_center_budget_rollup

        mock_config.ENABLE_USER_MANAGEMENT = True
        cost_center_id = uuid4()
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_cost_center_service.get_or_404.return_value = SimpleNamespace(id=cost_center_id, name="epm-cdme")
        mock_get_async_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_get_async_session.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_application_repository.aget_project_names_by_cost_center_id = AsyncMock(
            return_value=["proj-a", "proj-b"]
        )
        expected = CostCenterBudgetSummary(
            current_spending=15.0,
            budget_limit=60.0,
            total_percent=25.0,
            budgets=[
                CostCenterAssignedBudgetSummary(
                    budget_category=BudgetCategory.PLATFORM,
                    soft_budget=25.0,
                    max_budget=50.0,
                    member_count=3,
                    allocated_member_budget_total=25.0,
                    current_spending=15.0,
                )
            ],
        )
        mock_get_cost_center_budget_summary.return_value = _async_result(expected)

        response = asyncio.run(
            get_cost_center_budget_rollup(cost_center_id=cost_center_id, user=_super_admin(), _=None)
        )

        mock_application_repository.aget_project_names_by_cost_center_id.assert_awaited_once()
        mock_get_cost_center_budget_summary.assert_called_once()
        assert response.budget_limit == 60.0
        assert response.budgets[0].budget_category == BudgetCategory.PLATFORM

    @patch("codemie.rest_api.routers.cost_centers.config")
    @patch("codemie.rest_api.routers.cost_centers.get_cost_center_budget_summary")
    @patch("codemie.rest_api.routers.cost_centers.get_async_session")
    @patch("codemie.rest_api.routers.cost_centers.application_repository")
    @patch("codemie.rest_api.routers.cost_centers.get_session")
    @patch("codemie.rest_api.routers.cost_centers.cost_center_service")
    def test_get_cost_center_budget_rollup_zero_for_no_linked_projects(
        self,
        mock_cost_center_service,
        mock_get_session,
        mock_application_repository,
        mock_get_async_session,
        mock_get_cost_center_budget_summary,
        mock_config,
    ):
        import asyncio

        from codemie.rest_api.routers.cost_centers import get_cost_center_budget_rollup

        mock_config.ENABLE_USER_MANAGEMENT = True
        cost_center_id = uuid4()
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_cost_center_service.get_or_404.return_value = SimpleNamespace(id=cost_center_id, name="epm-cdme")
        mock_get_async_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_get_async_session.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_application_repository.aget_project_names_by_cost_center_id = AsyncMock(return_value=[])
        zero_summary = CostCenterBudgetSummary(current_spending=0.0, budget_limit=None, total_percent=0.0, budgets=[])
        mock_get_cost_center_budget_summary.return_value = _async_result(zero_summary)

        response = asyncio.run(
            get_cost_center_budget_rollup(cost_center_id=cost_center_id, user=_super_admin(), _=None)
        )

        assert response.budgets == []
        assert response.budget_limit is None
```

Note: `_async_result(...)` returns a one-shot coroutine — each test above calls it exactly once, matching the single call the endpoint makes.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_cost_centers_router.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_cost_center_budget_rollup'`

- [ ] **Step 3: Write minimal implementation**

Add imports near the top of `src/codemie/rest_api/routers/cost_centers.py` (alongside the existing `from codemie.clients.postgres import get_session` at line 24):

```python
from codemie.clients.postgres import get_async_session
from codemie.service.budget.cost_center_budget_summary import (
    CostCenterBudgetSummary,
    get_cost_center_budget_summary,
)
```

Add the endpoint after `get_cost_center_detail` (after line 180):

```python
@router.get("/{costCenterId}/budget-rollup", response_model=CostCenterBudgetSummary)
async def get_cost_center_budget_rollup(
    cost_center_id: UUID = Path(alias="costCenterId"),
    user: User = Depends(authenticate),
    _: None = Depends(admin_access_only),
):
    _ensure_user_management_enabled()
    with get_session() as session:
        cost_center_service.get_or_404(session, user=user, cost_center_id=cost_center_id)

    async with get_async_session() as async_session:
        project_names = await application_repository.aget_project_names_by_cost_center_id(
            async_session, cost_center_id
        )
        return await get_cost_center_budget_summary(async_session, project_names)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_cost_centers_router.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

Commit per the repository's existing convention.

**Test-first: yes — `test_get_cost_center_budget_rollup_sums_linked_projects` and `test_get_cost_center_budget_rollup_zero_for_no_linked_projects` both fail (`ImportError`) until the endpoint exists.**

---

## Negative-constraint pass

- No new `CostCenter` persistence/field — honored by Tasks 1-5: no model/migration file touched anywhere in this plan; `get_cost_center_budget_summary` computes and returns, never writes.
- Sync/async split must not mix sync `Session` calls into the async aggregation path — honored by Task 5's endpoint, which closes the sync `with get_session()` block (used only for the existing `get_or_404` gate) before opening the async block; Task 3's `get_cost_center_budget_summary` takes only `AsyncSession` and never imports `Session`.
- Must use `codemie.service.budget.budget_enums.BudgetCategory`, not `codemie.enterprise.litellm.budget_categories.BudgetCategory` — honored by Task 2's explicit import and inline comment; no task imports the `enterprise.litellm` enum.
- Read-only (no edit/rebalance of rollup figures) — honored: Task 5 adds only a `GET` route; no `PATCH`/`PUT`/`DELETE` touches the rollup, and `CostCenterUpdateRequest` is untouched by every task.
- Out of scope: frontend/.tsx, cost-center budget entity/persistence, per-project drill-down list, project-budget CRUD changes, cost-center/project linking permissions, cost-center filter on project management — no task in this plan touches `rest_api/routers/projects.py`, `cost_center_service.py`'s `create`/`update`/`delete`, any `.tsx` file (none exist in this repo), or any permission/linking code; the only files modified are `application_repository.py` (one new async method), the new `cost_center_budget_summary.py` module, and `cost_centers.py` (one new `GET` route).
- Helper must be independently reusable by two sibling stories without reimplementing summation — honored by Task 3's design: `get_cost_center_budget_summary(async_session, project_names)` takes a plain list, not a cost-center id or router-specific type, so a caller that already has its own resolved project names (e.g. the drill-down story) calls it directly without going through Task 1's resolver or this endpoint at all.

## Self-review

**Spec coverage:** AC1 (reusable helper) → Task 3. AC2 (combined total + breakdown for linked projects) → Task 3 (core math) + Task 5 (endpoint wiring). AC3 (live/no caching) → Task 5 computes on every request, nothing is stored or memoized anywhere in this plan. AC4 (zero/empty for no linked projects) → Task 3's `test_empty_project_list_returns_zero_summary` + Task 5's `test_get_cost_center_budget_rollup_zero_for_no_linked_projects`. AC5 (missing category contributes zero, others unaffected) → Task 3's `test_sums_across_projects_and_categories` (the `cli` category's `None` spend) and `test_invalid_category_is_skipped_not_raised`. AC6 (data-contract parity with `ProjectSpendingSummary`/`ProjectAssignedBudgetSummary`) → Task 2's model shapes. Sync/async risk → Task 1 (async resolver) + Task 5 (sequential session composition). Enum risk → Task 2. No-persistence constraint → no task adds a model/migration. No gaps found.

**Placeholder scan:** none — every step has runnable code or an exact file:line citation.

**Type consistency:** `project_names: list[str]` is the same type across Task 1's return, Task 3's parameter, and Task 5's local variable. `CostCenterBudgetSummary`/`CostCenterAssignedBudgetSummary` field names are identical between Task 2's definition, Task 3's construction, and Task 5's test assertions. `BudgetCategory` is imported from `codemie.service.budget.budget_enums` consistently in Tasks 2, 3, and 5's tests.

**Review Focus:** see the dedicated section above — all five items are each pinned to a specific task's test.

## Post-review update (2026-10-21)

What actually shipped diverges from this plan in ways later reviews (and sibling
implementations) should copy from the code, not from the plan text above:

- **Member count** is not a per-row sum (`bucket["member_count"] += int(row.member_count)` as
  shown above) — it's a dedicated SQL `COUNT(DISTINCT user_id)` query
  (`get_distinct_member_counts_by_category`), now additionally scoped to the exact
  `budget_id`s the money aggregation accepted (see `budget_ids` param).
- **Sync-status skip rule**: rows with `provider_sync_status` in `{pending, failed}` are
  excluded from totals, counted in a new `unsynced_count` per category, and the category
  still appears in the response (it no longer vanishes when every row is unsynced).
- **Budget-duration skip rule**: a category's first-seen `budget_duration` wins; later rows
  with a different duration are excluded and counted in `excluded_count`, not silently
  dropped.
- **current_spending** includes key spend (reusing `project_spending_service`'s bulk
  key-row helpers), for parity with the project page's "Spend".
- **budget_limit**/**total_percent**: `budget_limit` is only returned when every category
  shares one `budget_duration`; `total_percent` is clamped at 100.

None of this was in the original plan's Task 2/Task 3 design — see
`cost_center_budget_summary.py` for the current source of truth.
