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

"""Tests for application startup integration with LiteLLM enterprise layer.

These tests verify that the FastAPI application startup (lifespan function)
correctly initializes LiteLLM services, models, and cleanup tasks.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI


@pytest.fixture
def mock_app():
    """Create a mock FastAPI application."""
    app = FastAPI()
    app.state.litellm_service = None
    return app


@pytest.fixture
def mock_litellm_service():
    """Create a mock LiteLLM service."""
    service = MagicMock()
    service.close = MagicMock()
    return service


@pytest.fixture
def mock_non_litellm_startup():
    """Mock all non-LiteLLM startup functions to isolate LiteLLM integration testing."""
    mock_provider = MagicMock()

    with patch("codemie.rest_api.main.alembic_upgrade_postgres"):
        with patch("codemie.rest_api.main.alembic_upgrade_enterprise_postgres"):
            with patch("codemie.rest_api.main.create_default_applications"):
                with patch("codemie.rest_api.main.manage_preconfigured_assistants"):
                    with patch("codemie.rest_api.main.manage_preconfigured_skills"):
                        with patch("codemie.rest_api.main.create_preconfigured_workflows"):
                            with patch("codemie.rest_api.main.import_preconfigured_katas"):
                                with patch("codemie.rest_api.main._setup_memory_profiling_scheduler"):
                                    with patch("codemie.rest_api.main.initialize_mcp_auth"):
                                        with patch("codemie.rest_api.main.shutdown_mcp_auth", new_callable=AsyncMock):
                                            with patch(
                                                "codemie.rest_api.main.ensure_predefined_budgets",
                                                new_callable=AsyncMock,
                                            ):
                                                with patch(
                                                    "codemie.rest_api.main.get_observability_provider",
                                                    return_value=mock_provider,
                                                ):
                                                    yield


def test_initialize_database_and_defaults_runs_only_migrations_and_default_apps():
    from codemie.rest_api import main

    calls = []

    def track(name):
        return MagicMock(side_effect=lambda: calls.append(name))

    with patch("codemie.rest_api.main.alembic_upgrade_postgres", track("core_migrations")):
        with patch("codemie.rest_api.main.alembic_upgrade_enterprise_postgres", track("enterprise_migrations")):
            with patch("codemie.rest_api.main.create_default_applications", track("default_applications")):
                main._initialize_database_and_defaults()

    assert calls == ["core_migrations", "enterprise_migrations", "default_applications"]


def test_initialize_preconfigured_content_runs_all_content_in_order():
    from codemie.rest_api import main

    calls = []

    def track(name):
        return MagicMock(side_effect=lambda: calls.append(name))

    with patch("codemie.rest_api.main.manage_preconfigured_assistants", track("assistants")):
        with patch("codemie.rest_api.main.manage_preconfigured_skills", track("skills")):
            with patch("codemie.rest_api.main.create_preconfigured_workflows", track("workflows")):
                with patch("codemie.rest_api.main.import_preconfigured_katas", track("katas")):
                    main._initialize_preconfigured_content()

    assert calls == ["assistants", "skills", "workflows", "katas"]


@pytest.mark.asyncio
async def test_preconfigured_content_runs_after_litellm_init_in_lifespan():
    """Verify preconfigured content setup happens after LiteLLM models are initialized.

    Guards against the regression where assistants/skills/workflows received the YAML
    fallback model (gpt-4.1) instead of the LiteLLM default because
    manage_preconfigured_assistants ran before _initialize_litellm_models.
    """
    from codemie.rest_api.main import lifespan

    app = FastAPI()
    calls = []
    mock_provider = MagicMock()

    def track(name):
        return MagicMock(side_effect=lambda: calls.append(name))

    startup_patches = [
        patch("codemie.rest_api.main.alembic_upgrade_postgres"),
        patch("codemie.rest_api.main.alembic_upgrade_enterprise_postgres"),
        patch("codemie.rest_api.main.create_default_applications"),
        patch("codemie.rest_api.main._initialize_litellm_models", track("litellm_models")),
        patch("codemie.rest_api.main.manage_preconfigured_assistants", track("assistants")),
        patch("codemie.rest_api.main.manage_preconfigured_skills"),
        patch("codemie.rest_api.main.create_preconfigured_workflows"),
        patch("codemie.rest_api.main.import_preconfigured_katas"),
        patch("codemie.rest_api.main.initialize_litellm_from_config", return_value=None),
        patch("codemie.rest_api.main.is_litellm_enabled", return_value=True),
        patch("codemie.rest_api.main.set_global_litellm_service"),
        patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock),
        patch("codemie.rest_api.main.initialize_mcp_auth"),
        patch("codemie.rest_api.main.shutdown_mcp_auth", new_callable=AsyncMock),
        patch("codemie.rest_api.main.ensure_predefined_budgets", new_callable=AsyncMock),
        patch("codemie.rest_api.main.get_observability_provider", return_value=mock_provider),
        patch("codemie.rest_api.main._setup_memory_profiling_scheduler"),
    ]

    with ExitStack() as stack:
        for p in startup_patches:
            stack.enter_context(p)
        async with lifespan(app):
            pass

    litellm_idx = calls.index("litellm_models")
    assistants_idx = calls.index("assistants")
    assert litellm_idx < assistants_idx, (
        f"_initialize_litellm_models (pos {litellm_idx}) must run before "
        f"manage_preconfigured_assistants (pos {assistants_idx}). Full order: {calls}"
    )


class TestLiteLLMServiceInitialization:
    """Test LiteLLM service initialization during app startup."""

    @pytest.mark.asyncio
    async def test_litellm_service_initialized_when_enabled(
        self, mock_app, mock_litellm_service, mock_non_litellm_startup
    ):
        """Test that LiteLLM service is initialized when enabled."""
        from codemie.rest_api.main import lifespan

        with patch(
            "codemie.rest_api.main.initialize_litellm_from_config",
            return_value=mock_litellm_service,
        ):
            with patch("codemie.rest_api.main.is_litellm_enabled", return_value=True):
                with patch("codemie.rest_api.main._initialize_litellm_models"):
                    with patch("codemie.rest_api.main.set_global_litellm_service"):
                        with patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock):
                            async with lifespan(mock_app):
                                # Verify service was set on app state
                                assert mock_app.state.litellm_service is mock_litellm_service

    @pytest.mark.asyncio
    async def test_litellm_service_not_initialized_when_disabled(self, mock_app, mock_non_litellm_startup):
        """Test that LiteLLM service is None when disabled."""
        from codemie.rest_api.main import lifespan

        with patch("codemie.rest_api.main.initialize_litellm_from_config", return_value=None):
            with patch("codemie.rest_api.main.is_litellm_enabled", return_value=False):
                with patch("codemie.rest_api.main.set_global_litellm_service"):
                    with patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock):
                        async with lifespan(mock_app):
                            # Verify service is None
                            assert mock_app.state.litellm_service is None


class TestLiteLLMModelsInitialization:
    """Test LiteLLM models initialization during app startup."""

    @pytest.mark.asyncio
    async def test_initialize_models_called_when_enabled(self, mock_app, mock_non_litellm_startup):
        """Test that _initialize_litellm_models is called when LiteLLM enabled."""
        from codemie.rest_api.main import lifespan

        mock_initialize = MagicMock()

        with patch("codemie.rest_api.main.initialize_litellm_from_config", return_value=None):
            with patch("codemie.rest_api.main.is_litellm_enabled", return_value=True):
                with patch(
                    "codemie.rest_api.main._initialize_litellm_models",
                    mock_initialize,
                ):
                    with patch("codemie.rest_api.main.set_global_litellm_service"):
                        with patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock):
                            async with lifespan(mock_app):
                                # Verify _initialize_litellm_models was called
                                mock_initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_models_not_called_when_disabled(self, mock_app, mock_non_litellm_startup):
        """Test that _initialize_litellm_models is not called when LiteLLM disabled."""
        from codemie.rest_api.main import lifespan

        mock_initialize = MagicMock()

        with patch("codemie.rest_api.main.initialize_litellm_from_config", return_value=None):
            with patch("codemie.rest_api.main.is_litellm_enabled", return_value=False):
                with patch(
                    "codemie.rest_api.main._initialize_litellm_models",
                    mock_initialize,
                ):
                    with patch("codemie.rest_api.main.set_global_litellm_service"):
                        with patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock):
                            async with lifespan(mock_app):
                                # Verify _initialize_litellm_models was NOT called
                                mock_initialize.assert_not_called()


class TestLiteLLMBudgetInitialization:
    """Test LiteLLM budget startup behavior."""

    @pytest.mark.asyncio
    async def test_budget_setup_when_enabled(self, mock_app, mock_non_litellm_startup):
        """Test that budget cache cleanup is set up when budget checking enabled."""
        from codemie.rest_api.main import lifespan
        from codemie.configs.config import config

        mock_setup_cleanup = MagicMock()
        mock_schedule_reconciliation = MagicMock()

        with patch("codemie.rest_api.main.initialize_litellm_from_config", return_value=None):
            with patch("codemie.rest_api.main.is_litellm_enabled", return_value=True):
                with patch("codemie.rest_api.main._initialize_litellm_models"):
                    with patch(
                        "codemie.rest_api.main._setup_litellm_cache_cleanup_scheduler",
                        mock_setup_cleanup,
                    ):
                        with patch(
                            "codemie.rest_api.main._schedule_budget_reconciliation",
                            mock_schedule_reconciliation,
                        ):
                            with patch.object(config, "LLM_PROXY_BUDGET_CHECK_ENABLED", True):
                                with patch("codemie.rest_api.main.set_global_litellm_service"):
                                    with patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock):
                                        async with lifespan(mock_app):
                                            mock_setup_cleanup.assert_called_once()
                                            mock_schedule_reconciliation.assert_called_once()

    @pytest.mark.asyncio
    async def test_budget_setup_skipped_when_disabled(self, mock_app, mock_non_litellm_startup):
        """Test that budget cache cleanup is skipped when budget checking disabled."""
        from codemie.rest_api.main import lifespan
        from codemie.configs.config import config

        mock_setup_cleanup = MagicMock()
        mock_schedule_reconciliation = MagicMock()

        with patch("codemie.rest_api.main.initialize_litellm_from_config", return_value=None):
            with patch("codemie.rest_api.main.is_litellm_enabled", return_value=True):
                with patch("codemie.rest_api.main._initialize_litellm_models"):
                    with patch(
                        "codemie.rest_api.main._setup_litellm_cache_cleanup_scheduler",
                        mock_setup_cleanup,
                    ):
                        with patch(
                            "codemie.rest_api.main._schedule_budget_reconciliation",
                            mock_schedule_reconciliation,
                        ):
                            with patch.object(config, "LLM_PROXY_BUDGET_CHECK_ENABLED", False):
                                with patch("codemie.rest_api.main.set_global_litellm_service"):
                                    with patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock):
                                        async with lifespan(mock_app):
                                            mock_setup_cleanup.assert_not_called()
                                            mock_schedule_reconciliation.assert_called_once()


class TestLifespanShutdown:
    """Test application shutdown cleanup."""

    @pytest.mark.asyncio
    async def test_litellm_service_shutdown(self, mock_app, mock_litellm_service, mock_non_litellm_startup):
        """Test that LiteLLM service is properly closed on shutdown."""
        from codemie.rest_api.main import lifespan

        with patch(
            "codemie.rest_api.main.initialize_litellm_from_config",
            return_value=mock_litellm_service,
        ):
            with patch("codemie.rest_api.main.is_litellm_enabled", return_value=True):
                with patch("codemie.rest_api.main._initialize_litellm_models"):
                    with patch("codemie.rest_api.main.set_global_litellm_service"):
                        with patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock):
                            async with lifespan(mock_app):
                                pass  # Exit context to trigger shutdown

                            # Verify service.close() was called
                            mock_litellm_service.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_proxy_client_closed(self, mock_app, mock_non_litellm_startup):
        """Test that LLM proxy HTTP client is closed on shutdown."""
        from codemie.rest_api.main import lifespan

        mock_close_client = AsyncMock()

        with patch("codemie.rest_api.main.initialize_litellm_from_config", return_value=None):
            with patch("codemie.rest_api.main.is_litellm_enabled", return_value=False):
                with patch("codemie.rest_api.main.set_global_litellm_service"):
                    with patch("codemie.rest_api.main.close_llm_proxy_client", mock_close_client):
                        async with lifespan(mock_app):
                            pass  # Exit context to trigger shutdown

                        # Verify close_llm_proxy_client was called
                        mock_close_client.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_observability_provider_shutdown(self, mock_app, mock_non_litellm_startup):
        """Test that observability provider shutdown() is called on app shutdown."""
        from codemie.rest_api.main import lifespan

        with patch("codemie.rest_api.main.initialize_litellm_from_config", return_value=None):
            with patch("codemie.rest_api.main.is_litellm_enabled", return_value=False):
                with patch("codemie.rest_api.main.set_global_litellm_service"):
                    with patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock):
                        async with lifespan(mock_app):
                            provider = mock_app.state.observability_provider
                            assert provider is not None

                        # Lifecycle is fully inside the provider
                        provider.initialize.assert_called_once()
                        provider.shutdown.assert_called_once()


class TestGlobalServiceRegistry:
    """Test global service registry during startup."""

    @pytest.mark.asyncio
    async def test_litellm_service_registered_globally(self, mock_app, mock_litellm_service, mock_non_litellm_startup):
        """Test that LiteLLM service is registered in global registry."""
        from codemie.rest_api.main import lifespan

        mock_set_global = MagicMock()

        with patch(
            "codemie.rest_api.main.initialize_litellm_from_config",
            return_value=mock_litellm_service,
        ):
            with patch("codemie.rest_api.main.is_litellm_enabled", return_value=True):
                with patch("codemie.rest_api.main._initialize_litellm_models"):
                    with patch(
                        "codemie.rest_api.main.set_global_litellm_service",
                        mock_set_global,
                    ):
                        with patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock):
                            async with lifespan(mock_app):
                                # Verify service was registered globally
                                mock_set_global.assert_called_once_with(mock_litellm_service)


class TestMCPAuthStartupValidation:
    @pytest.mark.asyncio
    async def test_initialize_mcp_auth_runs_during_startup(self, mock_app, mock_non_litellm_startup):
        from codemie.rest_api.main import lifespan

        with patch("codemie.rest_api.main.initialize_litellm_from_config", return_value=None):
            with patch("codemie.rest_api.main.is_litellm_enabled", return_value=False):
                with patch("codemie.rest_api.main.set_global_litellm_service"):
                    with patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock):
                        with patch("codemie.rest_api.main.initialize_mcp_auth") as mock_initialize_mcp_auth:
                            async with lifespan(mock_app):
                                pass

                            mock_initialize_mcp_auth.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_initialize_mcp_auth_failure_aborts_startup(self, mock_app, mock_non_litellm_startup):
        from codemie.rest_api.main import lifespan

        with patch("codemie.rest_api.main.initialize_litellm_from_config", return_value=None):
            with patch("codemie.rest_api.main.is_litellm_enabled", return_value=False):
                with patch("codemie.rest_api.main.set_global_litellm_service"):
                    with patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock):
                        with patch(
                            "codemie.rest_api.main.initialize_mcp_auth",
                            side_effect=RuntimeError("bad secret"),
                        ):
                            with pytest.raises(RuntimeError, match="bad secret"):
                                async with lifespan(mock_app):
                                    pass

    @pytest.mark.asyncio
    async def test_shutdown_mcp_auth_runs_during_shutdown(self, mock_app, mock_non_litellm_startup):
        from codemie.rest_api.main import lifespan

        with patch("codemie.rest_api.main.initialize_litellm_from_config", return_value=None):
            with patch("codemie.rest_api.main.is_litellm_enabled", return_value=False):
                with patch("codemie.rest_api.main.set_global_litellm_service"):
                    with patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock):
                        with patch("codemie.rest_api.main.initialize_mcp_auth"):
                            with patch(
                                "codemie.rest_api.main.shutdown_mcp_auth",
                                new_callable=AsyncMock,
                            ) as mock_shutdown_mcp_auth:
                                async with lifespan(mock_app):
                                    pass

                                mock_shutdown_mcp_auth.assert_awaited_once_with()
