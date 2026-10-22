# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from dataclasses import dataclass
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.cost_center_budget_summary import (
    CostCenterAssignedBudgetSummary,
    CostCenterBudgetSummary,
    get_cost_center_budget_summary,
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


@dataclass
class _KeyRow:
    project_name: str
    spend_date: object
    budget_period_spend: float


def _patch_repo(rows_by_project, member_counts=None, key_rows=None):
    patcher = patch("codemie.service.budget.cost_center_budget_summary.project_budget_assignment_repository")
    mock_repo = patcher.start()
    mock_repo.get_assigned_budget_summaries_for_projects = AsyncMock(return_value=rows_by_project)
    mock_repo.get_distinct_member_counts_by_category = AsyncMock(return_value=member_counts or {})

    spend_patcher = patch("codemie.service.budget.cost_center_budget_summary.spend_repo")
    mock_spend_repo = spend_patcher.start()
    mock_spend_repo.get_latest_spending_by_project = AsyncMock(return_value=key_rows or [])

    class _CombinedPatcher:
        def stop(self):
            patcher.stop()
            spend_patcher.stop()

    return _CombinedPatcher(), mock_repo


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
    # Distinct member counts from the dedicated repo call — deliberately NOT the naive
    # per-row member_count sum (1 + 2 = 3) to prove the rollup uses the deduped count.
    patcher, _ = _patch_repo(rows_by_project, member_counts={"platform": 2, "cli": 1})
    try:
        result = await get_cost_center_budget_summary(AsyncMock(), ["proj-a", "proj-b"])
    finally:
        patcher.stop()

    by_category = {row.budget_category.value: row for row in result.budgets}
    assert by_category["platform"].max_budget == 50.0
    assert by_category["platform"].current_spending == 15.0
    assert by_category["platform"].member_count == 2
    assert by_category["cli"].max_budget == 10.0
    assert by_category["cli"].current_spending == 0.0  # None treated as 0.0
    assert by_category["cli"].member_count == 1
    assert result.budget_limit == 60.0
    assert result.current_spending == 15.0


@pytest.mark.asyncio
async def test_empty_project_list_returns_zero_summary():
    patcher, mock_repo = _patch_repo({})
    try:
        result = await get_cost_center_budget_summary(AsyncMock(), [])
    finally:
        patcher.stop()

    mock_repo.get_assigned_budget_summaries_for_projects.assert_not_called()
    mock_repo.get_distinct_member_counts_by_category.assert_not_called()
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
    patcher, _ = _patch_repo(rows_by_project, member_counts={"cli": 1})
    try:
        result = await get_cost_center_budget_summary(AsyncMock(), ["proj-a"])
    finally:
        patcher.stop()

    assert len(result.budgets) == 1
    assert result.budgets[0].budget_category.value == "cli"


@pytest.mark.asyncio
async def test_inactive_provider_sync_row_is_excluded_from_totals():
    rows_by_project = {
        "proj-a": [
            _Row("proj-a", "b1", "Platform", "platform", 10.0, 20.0, "30d", None, "ok", 1, 10.0, 5.0),
            _Row("proj-a", "b2", "Platform", "platform", 10.0, 20.0, "30d", None, "pending", 1, 10.0, 5.0),
            _Row("proj-a", "b3", "Platform", "platform", 10.0, 20.0, "30d", None, "failed", 1, 10.0, 5.0),
        ],
    }
    patcher, _ = _patch_repo(rows_by_project, member_counts={"platform": 1})
    try:
        result = await get_cost_center_budget_summary(AsyncMock(), ["proj-a"])
    finally:
        patcher.stop()

    assert len(result.budgets) == 1
    platform = result.budgets[0]
    assert platform.max_budget == 20.0
    assert platform.current_spending == 5.0


@pytest.mark.asyncio
async def test_mismatched_budget_duration_row_is_skipped():
    rows_by_project = {
        "proj-a": [
            _Row("proj-a", "b1", "Platform", "platform", 10.0, 20.0, "30d", None, None, 1, 10.0, 5.0),
        ],
        "proj-b": [
            _Row("proj-b", "b2", "Platform", "platform", 15.0, 30.0, "7d", None, None, 2, 15.0, 10.0),
        ],
    }
    patcher, _ = _patch_repo(rows_by_project, member_counts={"platform": 3})
    try:
        result = await get_cost_center_budget_summary(AsyncMock(), ["proj-a", "proj-b"])
    finally:
        patcher.stop()

    assert len(result.budgets) == 1
    platform = result.budgets[0]
    # The monthly (30d) window wins; the mismatched 7d row is skipped.
    assert platform.max_budget == 20.0
    assert platform.current_spending == 5.0
    assert platform.budget_duration == "30d"
    # The skipped row is no longer silently dropped — it's counted, not just logged.
    assert platform.excluded_count == 1


@pytest.mark.asyncio
async def test_duration_tie_break_prefers_monthly_window_regardless_of_row_order():
    """A single non-monthly project processed before the monthly ones must not exclude the
    monthly majority instead — the 30d window wins whenever any row has it, independent of
    project_name order. Worked example from review: an "alpha" project (7d) alongside three
    monthly projects must not leave the three monthly ones excluded."""
    rows_by_project = {
        "alpha": [
            _Row("alpha", "b-alpha", "Platform", "platform", 1.0, 1.0, "7d", None, None, 1, 1.0, 1.0),
        ],
        "big-one": [
            _Row("big-one", "b1", "Platform", "platform", 50.0, 100.0, "30d", None, None, 1, 50.0, 25.0),
        ],
        "big-two": [
            _Row("big-two", "b2", "Platform", "platform", 50.0, 100.0, "30d", None, None, 1, 50.0, 25.0),
        ],
        "big-three": [
            _Row("big-three", "b3", "Platform", "platform", 50.0, 100.0, "30d", None, None, 1, 50.0, 25.0),
        ],
    }
    patcher, _ = _patch_repo(rows_by_project, member_counts={"platform": 3})
    try:
        result = await get_cost_center_budget_summary(AsyncMock(), ["alpha", "big-one", "big-two", "big-three"])
    finally:
        patcher.stop()

    assert len(result.budgets) == 1
    platform = result.budgets[0]
    assert platform.budget_duration == "30d"
    assert platform.max_budget == 300.0
    assert platform.current_spending == 75.0
    # Only the non-monthly "alpha" row is excluded — not the three monthly projects.
    assert platform.excluded_count == 1


@pytest.mark.asyncio
async def test_unsynced_category_still_appears_with_unsynced_count():
    """A category whose rows are ALL pending/failed must not vanish from `budgets` —
    the UI needs to tell "not synced yet" apart from "no budget assigned at all"."""
    rows_by_project = {
        "proj-a": [
            _Row("proj-a", "b1", "Platform", "platform", 10.0, 20.0, "30d", None, "pending", 1, 10.0, 5.0),
        ],
    }
    patcher, _ = _patch_repo(rows_by_project, member_counts={})
    try:
        result = await get_cost_center_budget_summary(AsyncMock(), ["proj-a"])
    finally:
        patcher.stop()

    assert len(result.budgets) == 1
    platform = result.budgets[0]
    assert platform.unsynced_count == 1
    assert platform.max_budget == 0.0
    assert platform.current_spending == 0.0
    assert platform.member_count == 0


@pytest.mark.asyncio
async def test_current_spending_includes_key_spend():
    """ "Spend" must match the project page's parity definition: assigned-budget spend
    plus key spend, not assigned-budget spend alone."""
    rows_by_project = {
        "proj-a": [
            _Row("proj-a", "b1", "Platform", "platform", 10.0, 20.0, "30d", None, None, 1, 10.0, 5.0),
        ],
    }
    key_rows = [_KeyRow("proj-a", datetime(2026, 1, 1), 3.5)]
    patcher, _ = _patch_repo(rows_by_project, member_counts={"platform": 1}, key_rows=key_rows)
    try:
        result = await get_cost_center_budget_summary(AsyncMock(), ["proj-a"])
    finally:
        patcher.stop()

    assert result.current_spending == 8.5  # 5.0 assigned-budget spend + 3.5 key spend


@pytest.mark.asyncio
async def test_budget_limit_dropped_when_categories_have_different_durations():
    """A platform budget on a 30d window and a cli budget on a 7d window can't be summed
    into one meaningful grand total — budget_limit must come back None, not a mixed number."""
    rows_by_project = {
        "proj-a": [
            _Row("proj-a", "b1", "Platform", "platform", 10.0, 20.0, "30d", None, None, 1, 10.0, 5.0),
            _Row("proj-a", "b2", "CLI", "cli", 5.0, 10.0, "7d", None, None, 1, 5.0, 2.0),
        ],
    }
    patcher, _ = _patch_repo(rows_by_project, member_counts={"platform": 1, "cli": 1})
    try:
        result = await get_cost_center_budget_summary(AsyncMock(), ["proj-a"])
    finally:
        patcher.stop()

    assert result.budget_limit is None
    assert result.current_spending == 7.0
    assert result.total_percent == 0.0


@pytest.mark.asyncio
async def test_total_percent_clamped_at_100():
    rows_by_project = {
        "proj-a": [
            _Row("proj-a", "b1", "Platform", "platform", 10.0, 20.0, "30d", None, None, 1, 10.0, 50.0),
        ],
    }
    patcher, _ = _patch_repo(rows_by_project, member_counts={"platform": 1})
    try:
        result = await get_cost_center_budget_summary(AsyncMock(), ["proj-a"])
    finally:
        patcher.stop()

    assert result.budget_limit == 20.0
    assert result.current_spending == 50.0
    assert result.total_percent == 100.0


@pytest.mark.asyncio
async def test_member_count_scoped_to_accepted_budget_ids():
    """Member counts must be queried against the same accepted budget_ids the money totals
    used, not every active assignment for the project — proven by passing a distinct
    member_counts fixture keyed to a specific call and asserting the call args."""
    rows_by_project = {
        "proj-a": [
            _Row("proj-a", "b1", "Platform", "platform", 10.0, 20.0, "30d", None, None, 1, 10.0, 5.0),
            _Row("proj-a", "b2", "Platform", "platform", 10.0, 20.0, "30d", None, "pending", 1, 10.0, 5.0),
        ],
    }
    patcher, mock_repo = _patch_repo(rows_by_project, member_counts={"platform": 1})
    try:
        await get_cost_center_budget_summary(AsyncMock(), ["proj-a"])
    finally:
        patcher.stop()

    call_args = mock_repo.get_distinct_member_counts_by_category.call_args
    assert call_args.kwargs["budget_ids"] == ["b1"]  # b2 excluded (unsynced), not passed through
