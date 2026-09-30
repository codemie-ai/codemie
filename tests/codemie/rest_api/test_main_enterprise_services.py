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

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


async def _run(
    mock_litellm_service: MagicMock | None,
    budget_check_enabled: bool,
    budget_register_side_effect: Callable[..., Any] | None = None,
) -> tuple[MagicMock, MagicMock]:
    from codemie.rest_api.main import _initialize_enterprise_services

    app = MagicMock()
    with (
        patch("codemie.rest_api.main.config") as mock_config,
        patch("codemie.rest_api.main.get_observability_provider") as mock_get_obs,
        patch("codemie.rest_api.main.initialize_litellm_from_config", return_value=mock_litellm_service),
        patch("codemie.rest_api.main.set_global_litellm_service"),
        patch("codemie.enterprise.litellm.llm_proxy_provider_adapter.LiteLLMLLMProxyProvider"),
        patch("codemie.service.llm_proxy.provider_registry.register_llm_proxy_provider"),
        patch(
            "codemie.enterprise.litellm.budget_provider_adapter.LiteLLMBudgetEnforcementProvider"
        ) as mock_budget_provider_cls,
        patch("codemie.service.budget.provider_registry.register_budget_enforcement_provider") as mock_register_budget,
    ):
        mock_config.LLM_PROXY_BUDGET_CHECK_ENABLED = budget_check_enabled
        mock_get_obs.return_value = MagicMock()
        if budget_register_side_effect is not None:
            mock_register_budget.side_effect = budget_register_side_effect
        await _initialize_enterprise_services(app)
    return mock_budget_provider_cls, mock_register_budget


@pytest.mark.asyncio
async def test_ensure_team_exists_called_before_budget_provider_registration_when_gated() -> None:
    mock_service = MagicMock()
    call_order: list[str] = []
    mock_service.ensure_team_exists.side_effect = lambda: call_order.append("team") or True
    await _run(
        mock_service,
        budget_check_enabled=True,
        budget_register_side_effect=lambda *_: call_order.append("budget"),
    )
    mock_service.ensure_team_exists.assert_called_once()
    assert call_order == ["team", "budget"]


@pytest.mark.asyncio
async def test_ensure_team_exists_skipped_when_budget_check_disabled() -> None:
    mock_service = MagicMock()
    await _run(mock_service, budget_check_enabled=False)
    mock_service.ensure_team_exists.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_team_exists_skipped_when_litellm_service_is_none() -> None:
    await _run(None, budget_check_enabled=True)
    # no service to assert on; absence of AttributeError/None-call is the assertion


@pytest.mark.asyncio
async def test_ensure_team_exists_failure_is_logged_and_does_not_block_budget_registration() -> None:
    mock_service = MagicMock()
    mock_service.ensure_team_exists.side_effect = RuntimeError("litellm unreachable")
    with patch("codemie.rest_api.main.logger") as mock_logger:
        _, mock_register_budget = await _run(mock_service, budget_check_enabled=True)
    mock_logger.warning.assert_called_once()
    mock_register_budget.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_team_exists_returning_false_is_logged_as_warning_not_success() -> None:
    mock_service = MagicMock()
    mock_service.ensure_team_exists.return_value = False
    with patch("codemie.rest_api.main.logger") as mock_logger:
        await _run(mock_service, budget_check_enabled=True)
    mock_logger.warning.assert_called_once()
    assert "LiteLLM codemie-projects team provisioning ensured" not in [
        call.args[0] for call in mock_logger.info.call_args_list
    ]


@pytest.mark.asyncio
async def test_ensure_team_exists_runs_off_the_event_loop() -> None:
    """The sync HTTP call must be offloaded via asyncio.to_thread, not run inline."""
    from codemie.rest_api import main as main_module

    mock_service = MagicMock()
    mock_service.ensure_team_exists.return_value = True

    with patch.object(main_module.asyncio, "to_thread", wraps=main_module.asyncio.to_thread) as mock_to_thread:
        await _run(mock_service, budget_check_enabled=True)

    mock_to_thread.assert_called_once_with(mock_service.ensure_team_exists)
