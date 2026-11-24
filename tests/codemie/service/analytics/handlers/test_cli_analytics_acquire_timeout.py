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

"""A dashboard request that waits too long for a slot is a 504 of its own, never the 503 of a dead store."""

from __future__ import annotations

import asyncio

import pytest

from codemie.configs.config import config as app_config
from codemie.repository.cli_analytics.ports import DashboardAcquireTimeout, RequestBudgetExceeded
from codemie.rest_api.routers import cli_analytics as router
from codemie.service.analytics.handlers.cli_analytics_handler import dashboard_request, dashboard_slot


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_MAX_CONCURRENT", 0)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_POOL_SIZE", 8)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_INGEST_RESERVE", 2)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_HEAVY_SLOTS", 1)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_LIGHT_SLOTS", 1)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_JOBS_SLOTS", 1)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_REQUEST_BUDGET_S", 5)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_ACQUIRE_TIMEOUT_S", 0.05)


def test_the_subtype_is_a_budget_error():
    assert issubclass(DashboardAcquireTimeout, RequestBudgetExceeded)


def test_the_setting_defaults_to_thirty_seconds():
    assert type(app_config).model_fields["CLI_ANALYTICS_DASHBOARD_ACQUIRE_TIMEOUT_S"].default == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["heavy", "light"])
async def test_the_wait_for_a_slot_is_bounded_by_the_budget_only_not_the_acquire_timeout(lane):
    hold = asyncio.Event()

    async def holder():
        async with dashboard_request(lane):
            await hold.wait()

    first = asyncio.create_task(holder())
    await asyncio.sleep(0.01)

    async def waiter():
        async with dashboard_request(lane):
            return "served"

    second = asyncio.create_task(waiter())
    await asyncio.sleep(0.15)  # past the 0.05 s acquire timeout: still queued, not failed
    assert not second.done()
    hold.set()
    assert await asyncio.wait_for(second, 1) == "served"
    await first


@pytest.mark.asyncio
async def test_a_budget_running_out_in_the_queue_is_not_the_acquire_timeout(monkeypatch):
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_ACQUIRE_TIMEOUT_S", 5)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_REQUEST_BUDGET_S", 0.05)
    hold = asyncio.Event()

    async def holder():
        async with dashboard_slot():
            await hold.wait()

    first = asyncio.create_task(holder())
    await asyncio.sleep(0.01)
    with pytest.raises(RequestBudgetExceeded) as raised:
        async with dashboard_request():
            pytest.fail("must not get a slot")
    assert not isinstance(raised.value, DashboardAcquireTimeout)
    hold.set()
    await first


def test_the_504_names_the_setting_and_not_the_request_budget():
    with pytest.raises(router.ExtendedHTTPException) as raised:
        with router._statement_timeout_as_504("overview"):
            raise DashboardAcquireTimeout("busy")
    assert raised.value.code == 504
    assert "CLI_ANALYTICS_DASHBOARD_ACQUIRE_TIMEOUT_S" in raised.value.details
    assert "budget" not in raised.value.details


def test_a_typed_route_maps_it_to_504_with_the_same_message():
    error = router._typed_error("local analytics cost", DashboardAcquireTimeout("busy"), "too slow")
    assert error.code == 504
    assert "CLI_ANALYTICS_DASHBOARD_ACQUIRE_TIMEOUT_S" in error.details


def test_a_dead_store_is_still_storage_unavailable():
    error = router._typed_error("local analytics cost", TimeoutError("pool"), "too slow")
    assert error.code == 503
    assert "cannot be reached" in error.details
