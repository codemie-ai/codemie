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

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.rest_api import main


@pytest.mark.asyncio
async def test_warm_up_loads_the_snapshot():
    with patch.object(main, "refresh_overrides", AsyncMock()) as refresh:
        await main._warm_customer_config()

    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_warm_up_failure_does_not_crash_startup():
    with patch.object(main, "refresh_overrides", AsyncMock(side_effect=RuntimeError("db down"))):
        await main._warm_customer_config()


def test_refresh_loop_is_scheduled_as_a_background_task():
    tasks: list[asyncio.Task] = []
    created = MagicMock(return_value="task-handle")

    with patch.object(main.asyncio, "create_task", created):
        main._schedule_customer_config_refresh(tasks)

    assert created.call_count == 1
    assert created.call_args.kwargs["name"] == "customer_config_refresh"
    assert tasks == ["task-handle"]
    created.call_args.args[0].close()
