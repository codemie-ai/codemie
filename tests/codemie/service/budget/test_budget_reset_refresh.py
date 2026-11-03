# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""`refresh_overdue`: an overdue stored reset is reconciled on read, and nothing else is touched.

The screens that derive a budget period from the stored reset instant call this first. An instant that
has passed names the period that just closed - so without it every such screen shows last period's
spend as current, for as long as the daily reconciliation has not run (always, where it is disabled).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.budget.daily_reset_reconciliation_service import (
    REFRESH_RETRY_INTERVAL,
    DailyBudgetResetReconciliationService,
)
from codemie.service.budget.provider import BudgetResetReconciliationItem, BudgetResetReconciliationResult

MODULE = "codemie.service.budget.daily_reset_reconciliation_service"

PAST = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
FUTURE = (datetime.now(UTC) + timedelta(days=29)).strftime("%Y-%m-%dT%H:%M:%SZ")
NEXT = (datetime.now(UTC) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _budget(budget_id: str, reset_at: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        budget_id=budget_id, budget_reset_at=reset_at, provider_metadata={"provider_budget_ref": budget_id}
    )


def _item(budget_id: str, refreshed: str | None = NEXT, error: str | None = None) -> BudgetResetReconciliationItem:
    return BudgetResetReconciliationItem(
        entity_type="budget", budget_id=budget_id, refreshed_budget_reset_at=refreshed, error=error
    )


@pytest.fixture
def provider():
    provider = MagicMock()
    provider.reconcile_budget_reset_timestamps = AsyncMock(return_value=BudgetResetReconciliationResult(items=[]))
    with patch(f"{MODULE}.get_active_provider", return_value=provider):
        yield provider


@pytest.fixture
def repository():
    repository = MagicMock()
    repository.update_budget_reset_at = AsyncMock(return_value=object())
    with patch(f"{MODULE}.budget_repository", repository):
        yield repository


@pytest.fixture
def session():
    session = AsyncMock()

    @asynccontextmanager
    async def _session():
        yield session

    with patch(f"{MODULE}.get_async_session", _session):
        yield session


async def _refresh(budgets) -> set[str]:
    return await DailyBudgetResetReconciliationService().refresh_overdue(budgets)


@pytest.mark.asyncio
async def test_a_current_reset_asks_the_provider_nothing(provider, repository, session) -> None:
    assert await _refresh([_budget("b-current", FUTURE), _budget("b-none", None)]) == set()
    provider.reconcile_budget_reset_timestamps.assert_not_awaited()
    repository.update_budget_reset_at.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_the_overdue_budgets_are_reconciled_and_the_moved_ones_persisted(provider, repository, session):
    provider.reconcile_budget_reset_timestamps.return_value = BudgetResetReconciliationResult(items=[_item("b-old")])

    moved = await _refresh([_budget("b-old", PAST), _budget("b-current", FUTURE)])

    assert moved == {"b-old"}
    targets = provider.reconcile_budget_reset_timestamps.await_args.kwargs["targets"]
    assert [target.budget_id for target in targets] == ["b-old"]
    repository.update_budget_reset_at.assert_awaited_once_with(session, budget_id="b-old", budget_reset_at=NEXT)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_instant_the_provider_has_not_moved_yet_is_not_rewritten(provider, repository, session) -> None:
    """Between the reset instant and the provider's own reset run, the provider still reports it."""
    provider.reconcile_budget_reset_timestamps.return_value = BudgetResetReconciliationResult(
        items=[_item("b-old", refreshed=PAST), _item("b-gone", refreshed=None, error="provider entity missing")]
    )

    assert await _refresh([_budget("b-old", PAST), _budget("b-gone", PAST)]) == set()
    repository.update_budget_reset_at.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_provider_failure_moves_nothing_and_does_not_fail_the_read(provider, repository, session) -> None:
    provider.reconcile_budget_reset_timestamps.side_effect = RuntimeError("litellm down")

    assert await _refresh([_budget("b-old", PAST)]) == set()
    repository.update_budget_reset_at.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_row_gone_before_persistence_is_not_reported_as_moved(provider, repository, session) -> None:
    """The caller reloads every id returned; a row deleted meanwhile has nothing to reload."""
    provider.reconcile_budget_reset_timestamps.return_value = BudgetResetReconciliationResult(
        items=[_item("b-kept"), _item("b-deleted")]
    )
    repository.update_budget_reset_at.side_effect = lambda _session, budget_id, budget_reset_at: (
        None if budget_id == "b-deleted" else object()
    )

    assert await _refresh([_budget("b-kept", PAST), _budget("b-deleted", PAST)]) == {"b-kept"}


@pytest.mark.asyncio
async def test_an_instant_already_asked_about_is_not_asked_about_again_on_every_read(
    provider, repository, session
) -> None:
    """Until the provider's own reset runs, each read would otherwise call it - and page every key for a gone one."""
    service = DailyBudgetResetReconciliationService()
    stale = _budget("b-old", PAST)

    await service.refresh_overdue([stale])
    await service.refresh_overdue([stale])
    assert provider.reconcile_budget_reset_timestamps.await_count == 1

    with patch(f"{MODULE}.datetime", wraps=datetime) as clock:
        clock.now.return_value = datetime.now(UTC) + REFRESH_RETRY_INTERVAL
        await service.refresh_overdue([stale])
    assert provider.reconcile_budget_reset_timestamps.await_count == 2, "the same instant is asked about again later"

    await service.refresh_overdue([_budget("b-old", (datetime.now(UTC) - timedelta(hours=2)).isoformat())])
    assert provider.reconcile_budget_reset_timestamps.await_count == 3, "a different stored instant is asked about"
