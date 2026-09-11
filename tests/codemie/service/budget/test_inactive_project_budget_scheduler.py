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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.budget.inactive_project_budget_scheduler import InactiveProjectBudgetScheduler


def test_scheduler_registers_job_when_enabled():
    mock_scheduler = MagicMock()
    mock_scheduler.running = False

    with patch("codemie.service.budget.inactive_project_budget_scheduler.config") as mock_config:
        mock_config.INACTIVE_PROJECT_BUDGET_STOP_ENABLED = True
        mock_config.INACTIVE_PROJECT_BUDGET_STOP_SCHEDULE = "0 0 * * *"

        s = InactiveProjectBudgetScheduler(scheduler=mock_scheduler)
        s.start()

    mock_scheduler.add_job.assert_called_once()
    call_kwargs = mock_scheduler.add_job.call_args
    assert call_kwargs[1]["id"] == "inactive_project_budget_stop"
    mock_scheduler.start.assert_called_once()


def test_scheduler_skips_job_when_disabled():
    mock_scheduler = MagicMock()
    mock_scheduler.running = False

    with patch("codemie.service.budget.inactive_project_budget_scheduler.config") as mock_config:
        mock_config.INACTIVE_PROJECT_BUDGET_STOP_ENABLED = False

        s = InactiveProjectBudgetScheduler(scheduler=mock_scheduler)
        s.start()

    mock_scheduler.add_job.assert_not_called()


@pytest.mark.asyncio
async def test_run_inactive_budget_stop_calls_service():
    mock_scheduler = MagicMock()
    mock_scheduler.running = False

    with patch("codemie.service.budget.inactive_project_budget_scheduler.config") as mock_config:
        mock_config.INACTIVE_PROJECT_BUDGET_STOP_ENABLED = False

        s = InactiveProjectBudgetScheduler(scheduler=mock_scheduler)

    mock_service = AsyncMock()
    mock_session = AsyncMock()

    with (
        patch(
            "codemie.service.budget.inactive_project_budget_scheduler.inactive_project_budget_stop_service",
            mock_service,
        ),
        patch("codemie.service.budget.inactive_project_budget_scheduler.get_async_session") as mock_get_session,
    ):
        mock_get_session.return_value.__aenter__.return_value = mock_session
        mock_get_session.return_value.__aexit__.return_value = AsyncMock(return_value=False)

        lock_ctx = MagicMock()
        lock_ctx.acquired = True
        lock_ctx.__enter__ = MagicMock(return_value=lock_ctx)
        lock_ctx.__exit__ = MagicMock(return_value=False)

        with patch(
            "codemie.service.budget.inactive_project_budget_scheduler.LeaderLockContext",
            return_value=lock_ctx,
        ):
            await s._run_inactive_project_budget_stop()

    mock_service.run.assert_awaited_once_with(mock_session)
