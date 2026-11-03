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

import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.cost_center_budget_summary import (
    CostCenterAssignedBudgetSummary,
    CostCenterBudgetSummary,
    get_cost_center_budget_summary,
)
from codemie.service.spend.coverage import SpendResult, TimeRange

MODULE = "codemie.service.budget.cost_center_budget_summary"
PERIOD = TimeRange(datetime(2099, 8, 1, tzinfo=timezone.utc), datetime(2099, 9, 1, tzinfo=timezone.utc))


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
    assert summary.spend_unavailable is False


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


def _spent(amount: str | None) -> SpendResult:
    """The port's answer for one budget; None answers "no figure"."""
    return SpendResult.no_figure(PERIOD) if amount is None else SpendResult(figure=Decimal(amount))


@contextlib.contextmanager
def _patched(spend_by_project, member_counts=None, removed_by_project=None):
    """Stub the project budget spend the rollup reads and the member-count query."""

    @contextlib.asynccontextmanager
    async def session():
        yield AsyncMock()

    with (
        patch(
            f"{MODULE}.project_budget_spend",
            new=AsyncMock(return_value=(spend_by_project, removed_by_project or {})),
        ) as spend,
        patch(f"{MODULE}.project_budget_assignment_repository") as repo,
        patch(f"{MODULE}.get_async_session", session),
    ):
        repo.get_distinct_member_counts_by_category = AsyncMock(return_value=member_counts or {})
        yield spend, repo


@pytest.mark.asyncio
async def test_sums_across_projects_and_categories():
    spend_by_project = {
        "proj-a": [
            (_Row("proj-a", "b1", "Platform", "platform", 10.0, 20.0, "30d", None, None, 1, 10.0), _spent("5")),
            (_Row("proj-a", "b2", "CLI", "cli", 5.0, 10.0, "30d", None, None, 1, 5.0), _spent("0")),
        ],
        "proj-b": [
            (_Row("proj-b", "b3", "Platform", "platform", 15.0, 30.0, "30d", None, None, 2, 15.0), _spent("10")),
        ],
    }
    # Distinct member counts from the dedicated repo call — deliberately NOT the naive
    # per-row member_count sum (1 + 2 = 3) to prove the rollup uses the deduped count.
    with _patched(spend_by_project, member_counts={"platform": 2, "cli": 1}):
        result = await get_cost_center_budget_summary(["proj-a", "proj-b"])

    by_category = {row.budget_category.value: row for row in result.budgets}
    assert by_category["platform"].max_budget == 50.0
    assert by_category["platform"].current_spending == 15.0
    assert by_category["platform"].member_count == 2
    assert by_category["cli"].max_budget == 10.0
    assert by_category["cli"].current_spending == 0.0
    assert by_category["cli"].member_count == 1
    assert result.budget_limit == 60.0
    assert result.current_spending == 15.0
    assert result.total_percent == 25.0


@pytest.mark.asyncio
async def test_empty_project_list_reads_nothing_and_reports_no_figure():
    with _patched({}) as (spend, repo):
        result = await get_cost_center_budget_summary([])

    spend.assert_not_awaited()
    repo.get_distinct_member_counts_by_category.assert_not_called()
    assert result.budgets == []
    assert result.budget_limit is None
    assert result.current_spending is None
    assert result.total_percent is None


@pytest.mark.asyncio
async def test_a_budget_without_a_figure_is_unavailable_never_zero():
    spend_by_project = {
        "proj-a": [
            (_Row("proj-a", "b1", "Platform", "platform", 10.0, 20.0, "30d", None, None, 1, 10.0), _spent("5")),
            (_Row("proj-a", "b2", "CLI", "cli", 5.0, 10.0, "30d", None, None, 1, 5.0), _spent(None)),
        ],
    }
    with _patched(spend_by_project, member_counts={"platform": 1, "cli": 1}):
        result = await get_cost_center_budget_summary(["proj-a"])

    cli = next(row for row in result.budgets if row.budget_category is BudgetCategory.CLI)
    assert cli.current_spending is None
    assert cli.spend_unavailable is True
    # The total still reports what is known, and says it is short by a part.
    assert result.current_spending == 5.0
    assert result.spend_unavailable is True
    assert result.spend_partial is True


@pytest.mark.asyncio
async def test_invalid_category_is_skipped_not_raised():
    spend_by_project = {
        "proj-a": [
            (_Row("proj-a", "b1", "Bad", "not-a-real-category", 1.0, 1.0, "30d", None, None, 1, 1.0), _spent("1")),
            (_Row("proj-a", "b2", "CLI", "cli", 5.0, 10.0, "30d", None, None, 1, 5.0), _spent("2")),
        ],
    }
    with _patched(spend_by_project, member_counts={"cli": 1}):
        result = await get_cost_center_budget_summary(["proj-a"])

    assert len(result.budgets) == 1
    assert result.budgets[0].budget_category.value == "cli"


@pytest.mark.asyncio
async def test_inactive_provider_sync_row_is_excluded_from_totals():
    spend_by_project = {
        "proj-a": [
            (_Row("proj-a", "b1", "Platform", "platform", 10.0, 20.0, "30d", None, "ok", 1, 10.0), _spent("5")),
            (_Row("proj-a", "b2", "Platform", "platform", 10.0, 20.0, "30d", None, "pending", 1, 10.0), _spent("5")),
            (_Row("proj-a", "b3", "Platform", "platform", 10.0, 20.0, "30d", None, "failed", 1, 10.0), _spent("5")),
        ],
    }
    with _patched(spend_by_project, member_counts={"platform": 1}):
        result = await get_cost_center_budget_summary(["proj-a"])

    assert len(result.budgets) == 1
    platform = result.budgets[0]
    assert platform.max_budget == 20.0
    assert platform.current_spending == 5.0


@pytest.mark.asyncio
async def test_mismatched_budget_duration_row_is_skipped():
    spend_by_project = {
        "proj-a": [
            (_Row("proj-a", "b1", "Platform", "platform", 10.0, 20.0, "30d", None, None, 1, 10.0), _spent("5")),
        ],
        "proj-b": [
            (_Row("proj-b", "b2", "Platform", "platform", 15.0, 30.0, "7d", None, None, 2, 15.0), _spent("10")),
        ],
    }
    with _patched(spend_by_project, member_counts={"platform": 3}):
        result = await get_cost_center_budget_summary(["proj-a", "proj-b"])

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
    project_name order."""
    spend_by_project = {
        "alpha": [(_Row("alpha", "b-alpha", "Platform", "platform", 1.0, 1.0, "7d", None, None, 1, 1.0), _spent("1"))],
        **{
            name: [
                (_Row(name, f"b-{name}", "Platform", "platform", 50.0, 100.0, "30d", None, None, 1, 50.0), _spent("25"))
            ]
            for name in ("big-one", "big-two", "big-three")
        },
    }
    with _patched(spend_by_project, member_counts={"platform": 3}):
        result = await get_cost_center_budget_summary(["alpha", "big-one", "big-two", "big-three"])

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
    spend_by_project = {
        "proj-a": [
            (_Row("proj-a", "b1", "Platform", "platform", 10.0, 20.0, "30d", None, "pending", 1, 10.0), _spent("5")),
        ],
    }
    with _patched(spend_by_project):
        result = await get_cost_center_budget_summary(["proj-a"])

    assert len(result.budgets) == 1
    platform = result.budgets[0]
    assert platform.unsynced_count == 1
    assert platform.max_budget == 0.0
    # No accepted budget measures this category, so it has no figure rather than a zero.
    assert platform.current_spending is None
    assert platform.member_count == 0


@pytest.mark.asyncio
async def test_spend_in_a_removed_category_counts_toward_the_total_but_not_the_limit():
    """Parity with the project page (EPMCDME-15048): a category removed mid-period keeps its spend."""
    spend_by_project = {
        "proj-a": [
            (_Row("proj-a", "b1", "Platform", "platform", 10.0, 20.0, "30d", None, None, 1, 10.0), _spent("5")),
        ],
    }
    removed = {"proj-a": [(_Row("proj-a", "b0", "CLI", "cli", 5.0, 10.0, "30d", None, None, 0, 0.0), _spent("3.5"))]}
    with _patched(spend_by_project, member_counts={"platform": 1}, removed_by_project=removed):
        result = await get_cost_center_budget_summary(["proj-a"])

    assert result.current_spending == 8.5
    assert result.budget_limit == 20.0
    assert [b.budget_category for b in result.budgets] == [BudgetCategory.PLATFORM]


@pytest.mark.asyncio
async def test_budget_limit_dropped_when_categories_have_different_durations():
    """A platform budget on a 30d window and a cli budget on a 7d window can't be summed
    into one meaningful grand total — budget_limit must come back None, not a mixed number."""
    spend_by_project = {
        "proj-a": [
            (_Row("proj-a", "b1", "Platform", "platform", 10.0, 20.0, "30d", None, None, 1, 10.0), _spent("5")),
            (_Row("proj-a", "b2", "CLI", "cli", 5.0, 10.0, "7d", None, None, 1, 5.0), _spent("2")),
        ],
    }
    with _patched(spend_by_project, member_counts={"platform": 1, "cli": 1}):
        result = await get_cost_center_budget_summary(["proj-a"])

    assert result.budget_limit is None
    assert result.current_spending == 7.0
    assert result.total_percent is None


@pytest.mark.asyncio
async def test_total_percent_clamped_at_100():
    spend_by_project = {
        "proj-a": [
            (_Row("proj-a", "b1", "Platform", "platform", 10.0, 20.0, "30d", None, None, 1, 10.0), _spent("50")),
        ],
    }
    with _patched(spend_by_project, member_counts={"platform": 1}):
        result = await get_cost_center_budget_summary(["proj-a"])

    assert result.budget_limit == 20.0
    assert result.current_spending == 50.0
    assert result.total_percent == 100.0


@pytest.mark.asyncio
async def test_member_count_scoped_to_accepted_budget_ids():
    """Member counts must be queried against the same accepted budget_ids the money totals
    used, not every active assignment for the project."""
    spend_by_project = {
        "proj-a": [
            (_Row("proj-a", "b1", "Platform", "platform", 10.0, 20.0, "30d", None, None, 1, 10.0), _spent("5")),
            (_Row("proj-a", "b2", "Platform", "platform", 10.0, 20.0, "30d", None, "pending", 1, 10.0), _spent("5")),
        ],
    }
    with _patched(spend_by_project, member_counts={"platform": 1}) as (_, repo):
        await get_cost_center_budget_summary(["proj-a"])

    call_args = repo.get_distinct_member_counts_by_category.call_args
    assert call_args.kwargs["budget_ids"] == ["b1"]  # b2 excluded (unsynced), not passed through
