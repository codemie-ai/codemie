# Collect Member Spend Before Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture a terminal spend snapshot for a project member in `project_spend_tracking` before their LiteLLM customer is deleted on project removal, preventing loss of spend accrued since the last scheduled collection.

**Architecture:** A new private async helper `_capture_terminal_member_spend()` is added to `ProjectAssignmentService`. The helper fetches the member's current spend from LiteLLM via `provider.collect_member_budget_spend_for_refs()`, computes the delta against the last stored row, and writes a terminal row to `project_spend_tracking` via `ProjectSpendTrackingRepository`. The helper is called from `_sync_project_budget_member_removed()` via the existing `_run_budget_provider_coro()` bridge, strictly before `delete_member_allocation()` is invoked.

**Tech Stack:** Python, SQLModel, asyncio, pytest, `unittest.mock.AsyncMock`, LiteLLM provider protocol

## Global Constraints

- Core code MUST NOT import from `codemie.enterprise.litellm` — use `get_active_provider()` from `provider_registry` only.
- `provider_member_ref` is opaque — read from `allocation.provider_metadata.get("raw", {}).get("provider_member_ref")` only; never construct or decode it.
- Spend capture is fail-open — any exception logs a warning and continues with deletion.
- Spend fetch must occur before BOTH `allocation.deleted_at = now` AND `provider.delete_member_allocation()`.
- Commit message format: `EPMCDME-13619: <description>`.

---

## File Map

| Action | File | What changes |
|---|---|---|
| Modify | `src/codemie/repository/project_spend_tracking_repository.py` | Add module-level singleton `project_spend_tracking_repository` |
| Modify | `src/codemie/service/project/project_assignment_service.py` | New imports, `_capture_terminal_member_spend()` helper, modified `_sync_project_budget_member_removed()` loop |
| Modify | `tests/codemie/service/project/test_project_assignment_service.py` | New `TestSyncProjectBudgetMemberRemoved` and `TestCaptureTerminalMemberSpend` test classes |

---

### Task 1: Export `project_spend_tracking_repository` singleton

**Files:**
- Modify: `src/codemie/repository/project_spend_tracking_repository.py` (last line)

**Interfaces:**
- Produces: `project_spend_tracking_repository: ProjectSpendTrackingRepository` importable from this module

- [ ] **Step 1: Write the failing test**

```python
# In tests/codemie/service/project/test_project_assignment_service.py
# Add this import at the top with the other imports:
from codemie.repository.project_spend_tracking_repository import project_spend_tracking_repository, ProjectSpendTrackingRepository

# Add this test class after the existing ones:
class TestProjectSpendTrackingRepositorySingleton:
    def test_singleton_exists_and_is_correct_type(self):
        assert project_spend_tracking_repository is not None
        assert isinstance(project_spend_tracking_repository, ProjectSpendTrackingRepository)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/service/project/test_project_assignment_service.py::TestProjectSpendTrackingRepositorySingleton -v
```

Expected: `ImportError` or `AttributeError: module has no attribute 'project_spend_tracking_repository'`

- [ ] **Step 3: Add singleton to repository file**

Open `src/codemie/repository/project_spend_tracking_repository.py`. Append to the very end of the file (after all class and method definitions):

```python

project_spend_tracking_repository = ProjectSpendTrackingRepository()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/codemie/service/project/test_project_assignment_service.py::TestProjectSpendTrackingRepositorySingleton -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/codemie/repository/project_spend_tracking_repository.py tests/codemie/service/project/test_project_assignment_service.py
git commit -m "EPMCDME-13619: Export project_spend_tracking_repository singleton"
```

---

### Task 2: Add `_capture_terminal_member_spend()` and modify removal loop

**Files:**
- Modify: `src/codemie/service/project/project_assignment_service.py`

**Interfaces:**
- Consumes from Task 1: `project_spend_tracking_repository` from `codemie.repository.project_spend_tracking_repository`
- Produces:
  - `ProjectAssignmentService._capture_terminal_member_spend(allocation: ProjectMemberBudgetAssignment, provider, now: datetime) -> None` (static async)
  - `_sync_project_budget_member_removed()` now calls spend capture before deletion

- [ ] **Step 1: Write the failing test for `_capture_terminal_member_spend` happy path**

Add to `tests/codemie/service/project/test_project_assignment_service.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal
import pytest

from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.provider import MemberBudgetSpendSnapshot
from codemie.service.spend_tracking.spend_models import ProjectSpendTracking
```

_(Add these imports to the top of the test file alongside existing imports.)_

Then add the test class:

```python
class TestCaptureTerminalMemberSpend:
    """Unit tests for ProjectAssignmentService._capture_terminal_member_spend."""

    def _make_allocation(self, provider_member_ref=None):
        raw = {}
        if provider_member_ref:
            raw["provider_member_ref"] = provider_member_ref
        return ProjectMemberBudgetAssignment(
            project_name="myproject",
            budget_category="platform",
            project_budget_id="bgt-1",
            user_id="user-abc",
            allocation_mode="equal",
            allocated_soft_budget=50.0,
            allocated_max_budget=100.0,
            assigned_by="admin",
            budget_id="bgt-1",
            provider_metadata={"raw": raw} if raw else {},
        )

    def _make_snapshot(self, spend="5.00"):
        return MemberBudgetSpendSnapshot(
            project_name="myproject",
            budget_category=BudgetCategory.PLATFORM,
            budget_id="bgt-1",
            user_id="user-abc",
            spend=Decimal(spend),
            provider_subject_id="subj-1",
        )

    @pytest.mark.asyncio
    @patch("codemie.service.project.project_assignment_service.project_spend_tracking_repository")
    @patch("codemie.service.project.project_assignment_service.get_async_session")
    async def test_happy_path_bootstrap_no_prev_row(self, mock_get_session, mock_repo):
        """Bootstrap case: no previous row — daily_spend equals full snapshot spend."""
        now = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
        allocation = self._make_allocation(provider_member_ref="ref-1")
        snapshot = self._make_snapshot("5.00")

        mock_provider = AsyncMock()
        mock_provider.collect_member_budget_spend_for_refs.return_value = [snapshot]

        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session

        mock_repo.get_latest_before_by_member_budget_ids = AsyncMock(return_value={})
        mock_repo.insert_member_budget_entries = AsyncMock()

        await ProjectAssignmentService._capture_terminal_member_spend(allocation, mock_provider, now)

        mock_provider.collect_member_budget_spend_for_refs.assert_awaited_once_with({"ref-1"})
        mock_repo.insert_member_budget_entries.assert_awaited_once()
        inserted_rows = mock_repo.insert_member_budget_entries.call_args[0][1]
        assert len(inserted_rows) == 1
        row = inserted_rows[0]
        assert row.daily_spend == Decimal("5.00")
        assert row.cumulative_spend == Decimal("5.00")
        assert row.budget_period_spend == Decimal("5.00")
        assert row.project_name == "myproject"
        assert row.user_id == "user-abc"
        assert row.budget_id == "bgt-1"
        assert row.budget_category == "platform"
        assert row.spend_date == now

    @pytest.mark.asyncio
    @patch("codemie.service.project.project_assignment_service.project_spend_tracking_repository")
    @patch("codemie.service.project.project_assignment_service.get_async_session")
    async def test_happy_path_with_prev_row_computes_delta(self, mock_get_session, mock_repo):
        """Delta case: previous row exists — daily_spend is the difference."""
        now = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
        allocation = self._make_allocation(provider_member_ref="ref-1")
        snapshot = self._make_snapshot("9.00")

        prev_row = MagicMock()
        prev_row.budget_period_spend = Decimal("6.00")
        prev_row.cumulative_spend = Decimal("20.00")

        mock_provider = AsyncMock()
        mock_provider.collect_member_budget_spend_for_refs.return_value = [snapshot]

        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session

        mock_repo.get_latest_before_by_member_budget_ids = AsyncMock(
            return_value={("myproject", "bgt-1", "user-abc"): prev_row}
        )
        mock_repo.insert_member_budget_entries = AsyncMock()

        await ProjectAssignmentService._capture_terminal_member_spend(allocation, mock_provider, now)

        inserted_rows = mock_repo.insert_member_budget_entries.call_args[0][1]
        row = inserted_rows[0]
        assert row.daily_spend == Decimal("3.00")       # 9 - 6
        assert row.cumulative_spend == Decimal("23.00") # 20 + 3
        assert row.budget_period_spend == Decimal("9.00")

    @pytest.mark.asyncio
    async def test_no_provider_member_ref_skips_silently(self):
        """No provider_member_ref in metadata — collection skipped, no error."""
        now = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
        allocation = self._make_allocation(provider_member_ref=None)
        mock_provider = AsyncMock()

        await ProjectAssignmentService._capture_terminal_member_spend(allocation, mock_provider, now)

        mock_provider.collect_member_budget_spend_for_refs.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("codemie.service.project.project_assignment_service.project_spend_tracking_repository")
    @patch("codemie.service.project.project_assignment_service.get_async_session")
    async def test_zero_delta_skips_insert(self, mock_get_session, mock_repo):
        """Delta is zero — row insert is skipped."""
        now = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
        allocation = self._make_allocation(provider_member_ref="ref-1")
        snapshot = self._make_snapshot("6.00")

        prev_row = MagicMock()
        prev_row.budget_period_spend = Decimal("6.00")  # same as snapshot
        prev_row.cumulative_spend = Decimal("20.00")

        mock_provider = AsyncMock()
        mock_provider.collect_member_budget_spend_for_refs.return_value = [snapshot]

        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session

        mock_repo.get_latest_before_by_member_budget_ids = AsyncMock(
            return_value={("myproject", "bgt-1", "user-abc"): prev_row}
        )
        mock_repo.insert_member_budget_entries = AsyncMock()

        await ProjectAssignmentService._capture_terminal_member_spend(allocation, mock_provider, now)

        mock_repo.insert_member_budget_entries.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_provider_snapshots_skips(self):
        """Provider returns no snapshots — insert skipped."""
        now = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
        allocation = self._make_allocation(provider_member_ref="ref-1")
        mock_provider = AsyncMock()
        mock_provider.collect_member_budget_spend_for_refs.return_value = []

        await ProjectAssignmentService._capture_terminal_member_spend(allocation, mock_provider, now)
        # No assert needed — no exception = pass (the insert path is unreachable)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie/service/project/test_project_assignment_service.py::TestCaptureTerminalMemberSpend -v
```

Expected: `AttributeError: type object 'ProjectAssignmentService' has no attribute '_capture_terminal_member_spend'`

- [ ] **Step 3: Add imports to `project_assignment_service.py`**

Open `src/codemie/service/project/project_assignment_service.py`.

After the existing import block (after line 50 — `from codemie.service.budget.provider_registry import get_active_provider`), add:

```python
from decimal import Decimal
from uuid import uuid4

from codemie.clients.postgres import get_async_session
from codemie.repository.project_spend_tracking_repository import project_spend_tracking_repository
from codemie.service.spend_tracking.spend_models import ProjectSpendTracking
```

- [ ] **Step 4: Add `_capture_terminal_member_spend()` static async method**

Insert this method inside `ProjectAssignmentService`, after `_build_member_provider_metadata()` (around line 88) and before `_sync_project_budget_member_added()`:

```python
@staticmethod
async def _capture_terminal_member_spend(
    allocation: ProjectMemberBudgetAssignment,
    provider,
    now: datetime,
) -> None:
    """Capture a terminal spend snapshot before the LiteLLM customer is deleted."""
    ref = (allocation.provider_metadata or {}).get("raw", {}).get("provider_member_ref")
    if not ref:
        logger.debug(
            f"budget_event=project_member_spend_snapshot_skipped component=project_assignment_service "
            f"user_id={allocation.user_id!r} project_name={allocation.project_name!r} "
            f"reason=no_provider_member_ref"
        )
        return

    snapshots = await provider.collect_member_budget_spend_for_refs({ref})
    if not snapshots:
        logger.debug(
            f"budget_event=project_member_spend_snapshot_skipped component=project_assignment_service "
            f"user_id={allocation.user_id!r} project_name={allocation.project_name!r} "
            f"reason=no_snapshots_returned"
        )
        return

    snapshot = snapshots[0]
    current_spend = Decimal(str(snapshot.spend)).quantize(Decimal("0.0000000001"))

    async with get_async_session() as session:
        prev_rows = await project_spend_tracking_repository.get_latest_before_by_member_budget_ids(
            session,
            [(snapshot.project_name, snapshot.budget_id, snapshot.user_id)],
            now,
        )
        prev_row = prev_rows.get((snapshot.project_name, snapshot.budget_id, snapshot.user_id))

        if prev_row is None:
            daily_spend = current_spend
            cumulative_spend = current_spend
        else:
            prev_period_spend = Decimal(str(prev_row.budget_period_spend)).quantize(Decimal("0.0000000001"))
            delta = current_spend - prev_period_spend
            if delta <= Decimal("0"):
                logger.debug(
                    f"budget_event=project_member_spend_snapshot_skipped component=project_assignment_service "
                    f"user_id={allocation.user_id!r} project_name={allocation.project_name!r} "
                    f"reason=zero_delta"
                )
                return
            daily_spend = delta
            cumulative_spend = Decimal(str(prev_row.cumulative_spend)).quantize(Decimal("0.0000000001")) + delta

        row = ProjectSpendTracking(
            id=uuid4(),
            project_name=snapshot.project_name,
            spend_date=now,
            daily_spend=daily_spend,
            cumulative_spend=cumulative_spend,
            budget_period_spend=current_spend,
            budget_id=snapshot.budget_id,
            budget_category=snapshot.budget_category.value,
            user_id=snapshot.user_id,
            provider_subject_id=snapshot.provider_subject_id,
        )
        await project_spend_tracking_repository.insert_member_budget_entries(session, [row])

    logger.info(
        f"budget_event=project_member_spend_snapshot_captured component=project_assignment_service "
        f"user_id={allocation.user_id!r} project_name={allocation.project_name!r} "
        f"budget_id={snapshot.budget_id!r} daily_spend={daily_spend} cumulative_spend={cumulative_spend}"
    )
```

- [ ] **Step 5: Run `TestCaptureTerminalMemberSpend` to verify it passes**

```bash
poetry run pytest tests/codemie/service/project/test_project_assignment_service.py::TestCaptureTerminalMemberSpend -v
```

Expected: all 5 tests `PASSED`

- [ ] **Step 6: Write failing tests for `_sync_project_budget_member_removed`**

Add this test class to `tests/codemie/service/project/test_project_assignment_service.py`:

```python
class TestSyncProjectBudgetMemberRemoved:
    """Unit tests for ProjectAssignmentService._sync_project_budget_member_removed."""

    def _make_allocation(self, category="platform", provider_member_ref="ref-1"):
        raw = {"provider_member_ref": provider_member_ref} if provider_member_ref else {}
        return ProjectMemberBudgetAssignment(
            project_name="myproject",
            budget_category=category,
            project_budget_id="bgt-1",
            user_id="user-abc",
            allocation_mode="equal",
            allocated_soft_budget=50.0,
            allocated_max_budget=100.0,
            assigned_by="admin",
            budget_id="bgt-1",
            provider_metadata={"raw": raw},
        )

    @patch("codemie.service.project.project_assignment_service.ProjectAssignmentService._capture_terminal_member_spend")
    @patch("codemie.service.project.project_assignment_service.get_active_provider")
    def test_spend_captured_before_deletion(self, mock_get_provider, mock_capture):
        """Spend capture is called before delete_member_allocation on the same allocation."""
        session = MagicMock()
        alloc = self._make_allocation()
        session.exec.return_value.all.return_value = [alloc]

        call_order = []
        mock_capture.return_value = MagicMock()  # returns a coroutine-like mock
        mock_capture.side_effect = lambda *a, **kw: call_order.append("capture") or mock_capture.return_value

        mock_provider = MagicMock()
        mock_provider.delete_member_allocation.side_effect = lambda **kw: call_order.append("delete") or MagicMock()
        mock_get_provider.return_value = mock_provider

        # _run_budget_provider_coro will call asyncio.run() on the coroutine;
        # since mock_capture returns a MagicMock (not a coroutine), patch _run_budget_provider_coro too.
        with patch.object(ProjectAssignmentService, "_run_budget_provider_coro", side_effect=lambda coro: coro):
            ProjectAssignmentService._sync_project_budget_member_removed(session, "myproject", "user-abc")

        assert call_order.index("capture") < call_order.index("delete")

    @patch("codemie.service.project.project_assignment_service.ProjectAssignmentService._capture_terminal_member_spend")
    @patch("codemie.service.project.project_assignment_service.get_active_provider")
    def test_spend_capture_failure_is_fail_open(self, mock_get_provider, mock_capture):
        """Exception in spend capture is swallowed; deletion still proceeds."""
        session = MagicMock()
        alloc = self._make_allocation()
        session.exec.return_value.all.return_value = [alloc]

        mock_provider = MagicMock()
        mock_provider.delete_member_allocation.return_value = MagicMock()
        mock_get_provider.return_value = mock_provider

        with patch.object(ProjectAssignmentService, "_run_budget_provider_coro") as mock_run:
            mock_run.side_effect = [Exception("LiteLLM timeout"), None]  # capture raises, delete succeeds
            ProjectAssignmentService._sync_project_budget_member_removed(session, "myproject", "user-abc")

        assert alloc.deleted_at is not None
        assert mock_run.call_count == 2  # capture attempted, delete attempted

    @patch("codemie.service.project.project_assignment_service.get_active_provider")
    def test_no_allocations_returns_early(self, mock_get_provider):
        """No active allocations: provider never called."""
        session = MagicMock()
        session.exec.return_value.all.return_value = []

        ProjectAssignmentService._sync_project_budget_member_removed(session, "myproject", "user-abc")

        mock_get_provider.assert_not_called()

    @patch("codemie.service.project.project_assignment_service.ProjectAssignmentService._capture_terminal_member_spend")
    @patch("codemie.service.project.project_assignment_service.get_active_provider")
    def test_allocation_soft_deleted_after_capture_and_delete(self, mock_get_provider, mock_capture):
        """deleted_at is set on allocation after both capture and delete run."""
        session = MagicMock()
        alloc = self._make_allocation()
        session.exec.return_value.all.return_value = [alloc]

        mock_provider = MagicMock()
        mock_get_provider.return_value = mock_provider

        with patch.object(ProjectAssignmentService, "_run_budget_provider_coro", return_value=None):
            ProjectAssignmentService._sync_project_budget_member_removed(session, "myproject", "user-abc")

        assert alloc.deleted_at is not None
        session.flush.assert_called_once()

    @patch("codemie.service.project.project_assignment_service.ProjectAssignmentService._capture_terminal_member_spend")
    @patch("codemie.service.project.project_assignment_service.get_active_provider")
    def test_bulk_removal_captures_spend_per_member(self, mock_get_provider, mock_capture):
        """Bulk removal: spend capture fires once per allocation."""
        session = MagicMock()
        alloc1 = self._make_allocation(category="platform", provider_member_ref="ref-1")
        alloc2 = self._make_allocation(category="cli", provider_member_ref="ref-2")
        session.exec.return_value.all.return_value = [alloc1, alloc2]

        mock_provider = MagicMock()
        mock_get_provider.return_value = mock_provider

        with patch.object(ProjectAssignmentService, "_run_budget_provider_coro", return_value=None):
            ProjectAssignmentService._sync_project_budget_member_removed(session, "myproject", "user-abc")

        assert mock_capture.call_count == 2
```

- [ ] **Step 7: Run failing tests to confirm they fail**

```bash
poetry run pytest tests/codemie/service/project/test_project_assignment_service.py::TestSyncProjectBudgetMemberRemoved -v
```

Expected: failures — `_capture_terminal_member_spend` not yet called from `_sync_project_budget_member_removed`.

- [ ] **Step 8: Modify `_sync_project_budget_member_removed()` to call spend capture before deletion**

In `src/codemie/service/project/project_assignment_service.py`, replace `_sync_project_budget_member_removed()` (lines 175–206) with:

```python
@staticmethod
def _sync_project_budget_member_removed(session: Session, project_name: str, user_id: str) -> None:
    """Soft-delete active allocations for a removed member and disable provider state."""
    allocations = session.exec(
        select(ProjectMemberBudgetAssignment).where(
            ProjectMemberBudgetAssignment.project_name == project_name,
            ProjectMemberBudgetAssignment.user_id == user_id,
            ProjectMemberBudgetAssignment.deleted_at.is_(None),
        )
    ).all()
    if not allocations:
        return

    provider = get_active_provider()
    now = datetime.now(tz=timezone.utc)
    changed = False
    for allocation in allocations:
        try:
            ProjectAssignmentService._run_budget_provider_coro(
                ProjectAssignmentService._capture_terminal_member_spend(allocation, provider, now)
            )
        except Exception as exc:
            logger.warning(
                f"budget_event=project_member_spend_snapshot_failed component=project_assignment_service "
                f"user_id={user_id!r} allocation_id={allocation.id!r}: {exc}"
            )
        try:
            ProjectAssignmentService._run_budget_provider_coro(
                provider.delete_member_allocation(allocation=allocation)
            )
        except Exception as exc:
            logger.warning(
                f"Failed to delete provider member allocation {allocation.id!r} "
                f"for removed member {user_id!r}: {exc}"
            )
        allocation.deleted_at = now
        session.add(allocation)
        _resolution_cache.pop((project_name, allocation.budget_category, user_id), None)
        changed = True
    if changed:
        session.flush()
```

- [ ] **Step 9: Run all new tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/project/test_project_assignment_service.py::TestSyncProjectBudgetMemberRemoved tests/codemie/service/project/test_project_assignment_service.py::TestCaptureTerminalMemberSpend -v
```

Expected: all tests `PASSED`

- [ ] **Step 10: Run full test file to check no regressions**

```bash
poetry run pytest tests/codemie/service/project/test_project_assignment_service.py -v
```

Expected: all tests `PASSED`

- [ ] **Step 11: Run linting**

```bash
make ruff
```

Expected: no errors

- [ ] **Step 12: Commit**

```bash
git add src/codemie/service/project/project_assignment_service.py \
        tests/codemie/service/project/test_project_assignment_service.py
git commit -m "EPMCDME-13619: Capture terminal member spend before LiteLLM customer deletion"
```

---

## Self-Review

**Spec coverage:**
- ✅ `_capture_terminal_member_spend()` added — fetches via `collect_member_budget_spend_for_refs()`, writes to `project_spend_tracking`
- ✅ Called BEFORE `delete_member_allocation()` — ordering enforced in loop
- ✅ Fail-open — two separate try/except blocks
- ✅ No enterprise imports in core — uses `get_active_provider()` only
- ✅ `provider_member_ref` read from metadata dict, not constructed
- ✅ Tests: happy path (bootstrap + delta), no-ref skip, zero-delta skip, empty-snapshots skip, fail-open, ordering, bulk removal

**Placeholder scan:** No TBDs or incomplete steps found.

**Type consistency:** `MemberBudgetSpendSnapshot` fields `project_name`, `budget_id`, `user_id`, `spend`, `budget_category`, `provider_subject_id` match `provider.py:109–119`. `ProjectSpendTracking` fields match `spend_models.py`. `insert_member_budget_entries(session, rows: list[ProjectSpendTracking])` matches `project_spend_tracking_repository.py:437`.
