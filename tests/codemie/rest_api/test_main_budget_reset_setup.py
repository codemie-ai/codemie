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

from unittest.mock import MagicMock, patch


def test_setup_budget_reset_scheduler_runs_when_reset_reconciliation_enabled():
    from codemie.rest_api.main import _setup_budget_reset_scheduler

    app = MagicMock()

    with (
        patch("codemie.rest_api.main.config") as mock_config,
        patch("apscheduler.schedulers.asyncio.AsyncIOScheduler", return_value=MagicMock()),
        patch("codemie.service.budget_reset.scheduler.BudgetResetScheduler") as mock_scheduler_cls,
    ):
        mock_config.LITELLM_BUDGET_RESET_RECONCILIATION_ENABLED = True
        mock_config.LLM_PROXY_ENABLED = True
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler

        _setup_budget_reset_scheduler(app)

    mock_scheduler.start.assert_called_once()
    assert app.state.budget_reset_scheduler is mock_scheduler


def test_validation_error_path_skips_body_and_formats_indexes():
    from codemie.rest_api.main import _validation_error_path

    assert _validation_error_path(["body", "history", 0, "role"]) == "history[0].role"


def test_validation_error_message_joins_multiple_errors():
    from codemie.rest_api.main import _validation_error_message

    assert _validation_error_message(["field: bad", "other: worse"]) == "field: bad; other: worse"
