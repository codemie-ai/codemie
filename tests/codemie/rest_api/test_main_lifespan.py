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

"""Unit tests for the retrieval-unavailable startup warning in lifespan()."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


# ===========================================
# Tests: lifespan() retrieval warning
# ===========================================

# Marker used to abort lifespan() right after the code under test runs, so the
# test never needs to mock the rest of the (heavy) startup sequence.
_STOP_HERE = RuntimeError("stop-test-here")


@pytest.mark.asyncio
@patch("codemie.rest_api.main._initialize_database_and_defaults", side_effect=_STOP_HERE)
@patch("codemie.rest_api.main.logger")
@patch("codemie.rest_api.main.config")
async def test_lifespan_logs_retrieval_unavailable_warning_when_disabled(mock_config, mock_logger, mock_init_db):
    """RETRIEVAL_BACKEND=none logs a warning naming retrieval as unavailable."""
    from codemie.rest_api.main import lifespan

    mock_config.STANDALONE = False
    mock_config.RETRIEVAL_BACKEND = "none"
    mock_config.to_safe_dict.return_value = {}

    app = MagicMock()

    with pytest.raises(RuntimeError):
        async with lifespan(app):
            pass

    warning_messages = [call.args[0] for call in mock_logger.warning.call_args_list]
    assert any("retrieval" in msg.lower() and "RETRIEVAL_BACKEND" in msg for msg in warning_messages)


@pytest.mark.asyncio
@patch("codemie.rest_api.main._initialize_database_and_defaults", side_effect=_STOP_HERE)
@patch("codemie.rest_api.main.logger")
@patch("codemie.rest_api.main.config")
async def test_lifespan_does_not_log_when_retrieval_available(mock_config, mock_logger, mock_init_db):
    """A supported RETRIEVAL_BACKEND does not log the retrieval-unavailable warning."""
    from codemie.rest_api.main import lifespan

    mock_config.RETRIEVAL_BACKEND = "elasticsearch"
    mock_config.to_safe_dict.return_value = {}

    app = MagicMock()

    with pytest.raises(RuntimeError):
        async with lifespan(app):
            pass

    warning_messages = [call.args[0] for call in mock_logger.warning.call_args_list]
    assert not any("retrieval" in msg.lower() and "RETRIEVAL_BACKEND" in msg for msg in warning_messages)


# ===========================================
# Tests: _shutdown_services() executor shutdown ordering (T7)
# ===========================================


class _StateStub:
    """An app.state stand-in with no attributes, so getattr(..., default) hits the default."""


@pytest.mark.asyncio
@patch("codemie.rest_api.main.shutdown_project_member_sync_executor", new_callable=AsyncMock)
@patch("codemie.service.llm_proxy.provider_registry.get_active_llm_proxy_provider")
@patch("codemie.rest_api.main.stop_cli_analytics_runtime", new_callable=AsyncMock)
@patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock)
@patch("codemie.rest_api.main.get_global_plugin_service", return_value=None)
@patch("codemie.rest_api.main.shutdown_mcp_auth", new_callable=AsyncMock)
async def test_shutdown_services_awaits_sync_executor_before_closing_llm_proxy_provider(
    mock_shutdown_mcp_auth,
    mock_get_global_plugin_service,
    mock_close_llm_proxy_client,
    mock_stop_cli_analytics_runtime,
    mock_get_active_llm_proxy_provider,
    mock_shutdown_executor,
):
    """The dedicated member-sync executor is shut down before the LLM proxy provider is closed."""
    from codemie.rest_api.main import _shutdown_services

    parent = Mock()
    parent.attach_mock(mock_shutdown_executor, "shutdown_executor")

    mock_provider = MagicMock()
    mock_get_active_llm_proxy_provider.return_value = mock_provider
    parent.attach_mock(mock_provider.close, "provider_close")

    app = MagicMock()
    app.state = _StateStub()

    await _shutdown_services(app, [])

    call_names = [call[0] for call in parent.mock_calls]
    assert "shutdown_executor" in call_names
    assert "provider_close" in call_names
    assert call_names.index("shutdown_executor") < call_names.index("provider_close")


@pytest.mark.asyncio
@patch("codemie.rest_api.main.logger")
@patch("codemie.rest_api.main.shutdown_project_member_sync_executor", new_callable=AsyncMock)
@patch("codemie.service.llm_proxy.provider_registry.get_active_llm_proxy_provider")
@patch("codemie.rest_api.main.stop_cli_analytics_runtime", new_callable=AsyncMock)
@patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock)
@patch("codemie.rest_api.main.get_global_plugin_service", return_value=None)
@patch("codemie.rest_api.main.shutdown_mcp_auth", new_callable=AsyncMock)
async def test_shutdown_services_logs_and_continues_when_executor_shutdown_raises(
    mock_shutdown_mcp_auth,
    mock_get_global_plugin_service,
    mock_close_llm_proxy_client,
    mock_stop_cli_analytics_runtime,
    mock_get_active_llm_proxy_provider,
    mock_shutdown_executor,
    mock_logger,
):
    """A raising executor shutdown is logged, and the LLM proxy provider is still closed."""
    from codemie.rest_api.main import _shutdown_services

    mock_shutdown_executor.side_effect = RuntimeError("boom")
    mock_provider = MagicMock()
    mock_get_active_llm_proxy_provider.return_value = mock_provider

    app = MagicMock()
    app.state = _StateStub()

    await _shutdown_services(app, [])

    mock_provider.close.assert_called_once()
    assert mock_logger.error.called
