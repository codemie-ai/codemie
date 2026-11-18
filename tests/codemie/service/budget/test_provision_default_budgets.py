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

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs.config import PredefinedBudgetConfig
from codemie.service.budget import provision_limiter
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.budget_service import BudgetService

_MODULE = "codemie.service.budget.budget_service"
_GATE = "codemie.service.budget.feature_gate"
_LIMITER = "codemie.service.budget.provision_limiter"


def _predefined(*pairs: tuple[str, str]) -> list[PredefinedBudgetConfig]:
    return [
        PredefinedBudgetConfig(budget_id=budget_id, name=budget_id, max_budget=10.0, budget_category=category)
        for budget_id, category in pairs
    ]


@pytest.fixture
def predefined():
    entries = _predefined(("platform-default", "platform"), ("premium-default", "premium_models"))
    with patch(f"{_MODULE}.budget_config", SimpleNamespace(predefined_budgets=entries)):
        yield entries


@pytest.fixture
def gate_open():
    with (
        patch(f"{_GATE}.is_budget_management_enabled", return_value=True),
        patch(f"{_GATE}.is_budget_enforcement_active", return_value=True),
    ):
        yield


@pytest.fixture
def provider():
    provider = SimpleNamespace(
        provider_name="litellm",
        provision_category_customer=AsyncMock(side_effect=lambda **kw: f"mirrored-{kw['budget_id']}"),
    )
    with patch(f"{_MODULE}.get_active_provider", return_value=provider):
        yield provider


@pytest.fixture
def service():
    svc = BudgetService()
    svc.track_proxy_budget_assignment_for_request = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_provisions_each_configured_category_and_writes_row_with_returned_id(
    predefined, gate_open, provider, service
):
    await service.provision_default_budgets("user-1", "john_doe")

    calls = {c.kwargs["budget_category"]: c.kwargs for c in provider.provision_category_customer.await_args_list}
    assert set(calls) == {BudgetCategory.PLATFORM, BudgetCategory.PREMIUM_MODELS}
    assert calls[BudgetCategory.PLATFORM] == {
        "user_id": "user-1",
        "username": "john_doe",
        "budget_category": BudgetCategory.PLATFORM,
        "budget_id": "platform-default",
    }
    assert calls[BudgetCategory.PREMIUM_MODELS]["budget_id"] == "premium-default"
    written = {c.args[1]: c.args[2] for c in service.track_proxy_budget_assignment_for_request.await_args_list}
    assert written == {
        BudgetCategory.PLATFORM: "mirrored-platform-default",
        BudgetCategory.PREMIUM_MODELS: "mirrored-premium-default",
    }
    assert all(c.args[0] == "user-1" for c in service.track_proxy_budget_assignment_for_request.await_args_list)


@pytest.mark.asyncio
async def test_calls_provider_once_per_distinct_category(gate_open, provider, service):
    entries = _predefined(("p1", "platform"), ("p2", "platform"), ("c1", "cli"))
    with patch(f"{_MODULE}.budget_config", SimpleNamespace(predefined_budgets=entries)):
        await service.provision_default_budgets("user-1", "john_doe")

    categories = [c.kwargs["budget_category"] for c in provider.provision_category_customer.await_args_list]
    assert sorted(c.value for c in categories) == ["cli", "platform"]


@pytest.mark.asyncio
async def test_cli_is_skipped_when_not_configured(predefined, gate_open, provider, service):
    await service.provision_default_budgets("user-1", "john_doe")

    categories = {c.kwargs["budget_category"] for c in provider.provision_category_customer.await_args_list}
    assert BudgetCategory.CLI not in categories


@pytest.mark.asyncio
@pytest.mark.parametrize("management,enforcement", [(False, True), (True, False), (False, False)])
async def test_closed_gate_makes_no_provider_call_and_no_warning(
    predefined, provider, service, management, enforcement
):
    with (
        patch(f"{_GATE}.is_budget_management_enabled", return_value=management),
        patch(f"{_GATE}.is_budget_enforcement_active", return_value=enforcement),
        patch(f"{_MODULE}.logger") as mock_logger,
    ):
        await service.provision_default_budgets("user-1", "john_doe")

    provider.provision_category_customer.assert_not_awaited()
    service.track_proxy_budget_assignment_for_request.assert_not_awaited()
    mock_logger.warning.assert_not_called()


@pytest.mark.asyncio
async def test_provider_returning_none_writes_no_row_and_warns_once(gate_open, service):
    entries = _predefined(("platform-default", "platform"))
    provider = SimpleNamespace(provision_category_customer=AsyncMock(return_value=None))
    with (
        patch(f"{_MODULE}.budget_config", SimpleNamespace(predefined_budgets=entries)),
        patch(f"{_MODULE}.get_active_provider", return_value=provider),
        patch(f"{_MODULE}.logger") as mock_logger,
    ):
        await service.provision_default_budgets("user-1", "john_doe")

    service.track_proxy_budget_assignment_for_request.assert_not_awaited()
    assert mock_logger.warning.call_count == 1


@pytest.mark.asyncio
async def test_one_category_raising_still_writes_the_others(predefined, gate_open, service):
    async def provision(**kwargs):
        if kwargs["budget_category"] == BudgetCategory.PLATFORM:
            raise RuntimeError("proxy down")
        return "mirrored-premium"

    provider = SimpleNamespace(provision_category_customer=AsyncMock(side_effect=provision))
    with patch(f"{_MODULE}.get_active_provider", return_value=provider):
        await service.provision_default_budgets("user-1", "john_doe")

    written = [c.args[1:] for c in service.track_proxy_budget_assignment_for_request.await_args_list]
    assert written == [(BudgetCategory.PREMIUM_MODELS, "mirrored-premium")]


@pytest.mark.asyncio
async def test_row_write_failure_in_one_category_leaves_the_others_written(predefined, gate_open, provider, service):
    async def track(user_id, category, budget_id, *args, **kwargs):
        if category == BudgetCategory.PLATFORM:
            raise RuntimeError("fk violation")

    service.track_proxy_budget_assignment_for_request = AsyncMock(side_effect=track)

    await service.provision_default_budgets("user-1", "john_doe")

    attempted = {c.args[1] for c in service.track_proxy_budget_assignment_for_request.await_args_list}
    assert attempted == {BudgetCategory.PLATFORM, BudgetCategory.PREMIUM_MODELS}


@pytest.mark.asyncio
async def test_hanging_provider_returns_at_deadline_with_warning_and_no_row(predefined, gate_open, service):
    async def hang(**kwargs):
        await asyncio.sleep(30)

    provider = SimpleNamespace(provision_category_customer=AsyncMock(side_effect=hang))
    with (
        patch(f"{_MODULE}.get_active_provider", return_value=provider),
        patch(f"{_MODULE}.PROVISION_DEADLINE_SECONDS", 0.05),
        patch(f"{_MODULE}.logger") as mock_logger,
    ):
        loop = asyncio.get_running_loop()
        started = loop.time()
        await service.provision_default_budgets("user-1", "john_doe")
        elapsed = loop.time() - started

    assert elapsed < 2
    service.track_proxy_budget_assignment_for_request.assert_not_awaited()
    assert mock_logger.warning.call_count >= 1


@pytest.mark.asyncio
async def test_timed_out_call_keeps_semaphore_slots_until_provider_work_finishes(predefined, gate_open, service):
    release = asyncio.Event()

    async def hang(**kwargs):
        await release.wait()

    provider = SimpleNamespace(provision_category_customer=AsyncMock(side_effect=hang))
    with (
        patch(f"{_MODULE}.get_active_provider", return_value=provider),
        patch(f"{_MODULE}.PROVISION_DEADLINE_SECONDS", 0.05),
    ):
        await service.provision_default_budgets("user-1", "john_doe")

        semaphore = provision_limiter.provision_semaphore()
        assert semaphore._value == provision_limiter.PROVISION_PROVIDER_CONCURRENCY - 2

        release.set()
        await asyncio.sleep(0.01)

    assert semaphore._value == provision_limiter.PROVISION_PROVIDER_CONCURRENCY


@pytest.mark.asyncio
async def test_slot_is_released_when_provider_raises(predefined, gate_open, service):
    provider = SimpleNamespace(provision_category_customer=AsyncMock(side_effect=RuntimeError("proxy down")))
    with patch(f"{_MODULE}.get_active_provider", return_value=provider):
        await service.provision_default_budgets("user-1", "john_doe")

    semaphore = provision_limiter.provision_semaphore()
    assert semaphore._value == provision_limiter.PROVISION_PROVIDER_CONCURRENCY


@pytest.mark.asyncio
async def test_provider_concurrency_is_bounded(gate_open, service):
    entries = _predefined(("a", "platform"), ("b", "cli"), ("c", "premium_models"))
    running = 0
    peak = 0

    async def provision(**kwargs):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return kwargs["budget_id"]

    provider = SimpleNamespace(provision_category_customer=AsyncMock(side_effect=provision))
    with (
        patch(f"{_MODULE}.budget_config", SimpleNamespace(predefined_budgets=entries)),
        patch(f"{_MODULE}.get_active_provider", return_value=provider),
        patch(f"{_LIMITER}.provision_semaphore", return_value=asyncio.Semaphore(2)),
    ):
        await service.provision_default_budgets("user-1", "john_doe")

    assert peak == 2


@pytest.mark.asyncio
async def test_never_raises_when_gate_check_fails(predefined, provider, service):
    with patch(f"{_GATE}.is_budget_management_enabled", side_effect=RuntimeError("boom")):
        await service.provision_default_budgets("user-1", "john_doe")

    provider.provision_category_customer.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_returns_before_provisioning_runs_and_then_runs_it(service):
    started = asyncio.Event()
    finish = asyncio.Event()

    async def provision(user_id, username):
        started.set()
        await finish.wait()

    service.provision_default_budgets = AsyncMock(side_effect=provision)
    with patch("codemie.core.event_loop.get_main_event_loop", return_value=None):
        service.schedule_default_budget_provisioning("user-1", "john_doe")

    assert not started.is_set()
    await asyncio.wait_for(started.wait(), timeout=1)
    service.provision_default_budgets.assert_awaited_once_with("user-1", "john_doe")
    finish.set()


def test_schedule_without_a_running_loop_creates_no_coroutine_and_logs_nothing_above_debug(service):
    service.provision_default_budgets = MagicMock()
    with (
        patch("codemie.core.event_loop.get_main_event_loop", return_value=None),
        patch(f"{_MODULE}.logger") as mock_logger,
    ):
        service.schedule_default_budget_provisioning("user-1", "john_doe")

    service.provision_default_budgets.assert_not_called()
    mock_logger.warning.assert_not_called()


def test_schedule_never_raises(service):
    with (
        patch(f"{_MODULE}.run_in_background", side_effect=RuntimeError("event loop is closed")),
        patch(f"{_MODULE}.logger") as mock_logger,
    ):
        service.schedule_default_budget_provisioning("user-1", "john_doe")

    mock_logger.warning.assert_called_once()
