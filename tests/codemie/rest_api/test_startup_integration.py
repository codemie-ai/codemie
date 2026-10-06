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

import asyncio
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from codemie.service.model_provider_readiness import ModelProviderReadiness, ModelProviderStatus

# Stub result for the startup readiness check. lifespan() runs the real check otherwise, which for a
# Bedrock-default profile (MODELS_ENV=aws) resolves the AWS credential chain and can reach IMDS/STS —
# unit tests must not depend on ambient AWS configuration or make network calls.
_STUB_READINESS = ModelProviderReadiness(status=ModelProviderStatus.NOT_CHECKED, provider=None, missing=[])


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
                                    with patch("codemie.rest_api.main._setup_activity_events_retention_scheduler"):
                                        with patch("codemie.rest_api.main.initialize_mcp_auth"):
                                            with patch(
                                                "codemie.rest_api.main.shutdown_mcp_auth", new_callable=AsyncMock
                                            ):
                                                with patch(
                                                    "codemie.rest_api.main.ensure_predefined_budgets",
                                                    new_callable=AsyncMock,
                                                ):
                                                    with patch(
                                                        "codemie.rest_api.main.get_observability_provider",
                                                        return_value=mock_provider,
                                                    ):
                                                        with patch(
                                                            "codemie.rest_api.main.check_model_provider_readiness",
                                                            return_value=_STUB_READINESS,
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


def test_startup_recovery_receives_cutoff_captured_before_background_execution():
    """The recovery scan must not include workflow states created after scheduling."""
    from codemie.rest_api.main import _schedule_startup_recovery

    tasks = []
    background_awaitable = object()
    recovery_task = MagicMock()
    mock_to_thread = MagicMock(return_value=background_awaitable)
    before = datetime.now()

    with (
        patch("codemie.service.workflow_execution.startup_recovery.recover_orphaned_workflow_states") as mock_recover,
        patch("codemie.rest_api.main.asyncio.to_thread", mock_to_thread),
        patch("codemie.rest_api.main.asyncio.create_task", return_value=recovery_task),
    ):
        _schedule_startup_recovery(tasks)

    after = datetime.now()
    mock_to_thread.assert_called_once()
    recover_callable, cutoff = mock_to_thread.call_args.args
    assert recover_callable is mock_recover
    assert before <= cutoff <= after
    assert tasks == [recovery_task]


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
        patch("codemie.rest_api.main._setup_activity_events_retention_scheduler"),
        patch("codemie.rest_api.main.check_model_provider_readiness", return_value=_STUB_READINESS),
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


class TestCliAnalyticsRuntimeLifecycle:
    """The CLI Analytics storage runtime (PostgreSQL migrations and jobs) follows the app lifespan."""

    @pytest.mark.asyncio
    async def test_runtime_is_started_on_boot_and_stopped_on_shutdown(self, mock_app, mock_non_litellm_startup):
        from codemie.rest_api.main import lifespan

        jobs = MagicMock(name="cli_analytics_jobs")
        start = AsyncMock(return_value=jobs)
        stop = AsyncMock()

        with (
            patch("codemie.rest_api.main.initialize_litellm_from_config", return_value=None),
            patch("codemie.rest_api.main.is_litellm_enabled", return_value=False),
            patch("codemie.rest_api.main.set_global_litellm_service"),
            patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock),
            patch("codemie.rest_api.main.start_cli_analytics_runtime", start),
            patch("codemie.rest_api.main.stop_cli_analytics_runtime", stop),
        ):
            async with lifespan(mock_app):
                start.assert_awaited_once_with()
                assert mock_app.state.cli_analytics_jobs is jobs
                stop.assert_not_awaited()

        stop.assert_awaited_once_with(jobs)


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


@pytest.mark.asyncio
async def test_lifespan_computes_model_provider_readiness_and_stores_it_on_app_state(
    mock_app, mock_non_litellm_startup
):
    """lifespan() must actually reach the readiness check.

    The unit tests below drive _check_model_provider_readiness() directly, so they stay green even if
    the lifespan() call site is removed — at which point readiness silently never runs and
    GET /healthcheck reports status "unknown" forever. This test covers that wiring.
    """
    from codemie.rest_api.main import lifespan

    fake_result = ModelProviderReadiness(
        status=ModelProviderStatus.CONFIGURED,
        provider="azure_openai",
        missing=[],
    )

    with patch("codemie.rest_api.main.initialize_litellm_from_config", return_value=None):
        with patch("codemie.rest_api.main.is_litellm_enabled", return_value=False):
            with patch("codemie.rest_api.main.set_global_litellm_service"):
                with patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock):
                    with patch(
                        "codemie.rest_api.main.check_model_provider_readiness",
                        return_value=fake_result,
                    ) as mock_check:
                        async with lifespan(mock_app):
                            assert mock_app.state.model_provider_readiness == fake_result

    mock_check.assert_called_once_with()


@pytest.mark.asyncio
async def test_check_model_provider_readiness_stores_result_on_app_state_and_logs():
    from codemie.rest_api import main

    app = FastAPI()
    fake_result = ModelProviderReadiness(
        status=ModelProviderStatus.CONFIGURED,
        provider="azure_openai",
        missing=[],
    )
    logged = []

    with patch("codemie.rest_api.main.check_model_provider_readiness", return_value=fake_result):
        with patch("codemie.rest_api.main.log_model_provider_readiness", side_effect=logged.append):
            await main._check_model_provider_readiness(app)

    assert app.state.model_provider_readiness == fake_result
    assert logged == [fake_result]


@pytest.mark.asyncio
async def test_check_model_provider_readiness_does_not_raise_when_not_configured():
    """The app must still start when no model provider is configured."""
    from codemie.rest_api import main

    app = FastAPI()
    fake_result = ModelProviderReadiness(
        status=ModelProviderStatus.NOT_CONFIGURED,
        provider=None,
        missing=["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_URL"],
    )

    with patch("codemie.rest_api.main.check_model_provider_readiness", return_value=fake_result):
        with patch("codemie.rest_api.main.log_model_provider_readiness"):
            await main._check_model_provider_readiness(app)  # must not raise

    assert app.state.model_provider_readiness == fake_result


@pytest.mark.asyncio
async def test_check_model_provider_readiness_swallows_exceptions_and_still_starts():
    """CR-001: a bug in the readiness check itself must never abort startup."""
    from codemie.rest_api import main

    app = FastAPI()

    with patch("codemie.rest_api.main.check_model_provider_readiness", side_effect=RuntimeError("boom")):
        with patch("codemie.rest_api.main.logger.exception") as mock_exception_log:
            with patch("codemie.rest_api.main.log_model_provider_readiness") as mock_log:
                await main._check_model_provider_readiness(app)  # must not raise

    assert app.state.model_provider_readiness.status == ModelProviderStatus.NOT_CHECKED
    assert app.state.model_provider_readiness.provider is None
    assert app.state.model_provider_readiness.missing == ["model provider readiness check failed; see startup logs"]
    assert "boom" not in app.state.model_provider_readiness.missing[0]
    mock_exception_log.assert_called_once_with("Model provider readiness check failed")
    mock_log.assert_called_once_with(app.state.model_provider_readiness)


@pytest.mark.asyncio
async def test_check_model_provider_readiness_runs_off_the_event_loop():
    """CR-003: the synchronous check must not block the event loop — other async work
    scheduled concurrently must be able to run while the check is in flight."""
    from codemie.rest_api import main

    app = FastAPI()
    marker = {"other_task_ran": False}

    def slow_sync_check():
        # Runs inside asyncio.to_thread; sleeping here must not block the loop.
        import time

        time.sleep(0.2)
        return ModelProviderReadiness(status=ModelProviderStatus.CONFIGURED, provider="azure_openai", missing=[])

    async def other_task():
        await asyncio.sleep(0.01)
        marker["other_task_ran"] = True

    with patch("codemie.rest_api.main.check_model_provider_readiness", side_effect=slow_sync_check):
        with patch("codemie.rest_api.main.log_model_provider_readiness"):
            other = asyncio.create_task(other_task())
            await main._check_model_provider_readiness(app)
            await other

    assert marker["other_task_ran"] is True
    assert app.state.model_provider_readiness.status == ModelProviderStatus.CONFIGURED


@pytest.mark.asyncio
async def test_check_model_provider_readiness_times_out_without_blocking_startup():
    """CR-003: a hanging credential_process (no deadline of its own) must not hang startup —
    the bounded wait_for must fire and startup must continue with NOT_CHECKED."""
    from codemie.rest_api import main

    app = FastAPI()

    def hanging_sync_check():
        import time

        time.sleep(5)  # far longer than the test's patched timeout
        return ModelProviderReadiness(status=ModelProviderStatus.CONFIGURED, provider="aws_bedrock", missing=[])

    with patch("codemie.rest_api.main.check_model_provider_readiness", side_effect=hanging_sync_check):
        with patch("codemie.rest_api.main._MODEL_PROVIDER_READINESS_TIMEOUT_SECONDS", 0.05):
            with patch("codemie.rest_api.main.logger.warning") as mock_warning:
                with patch("codemie.rest_api.main.log_model_provider_readiness"):
                    await main._check_model_provider_readiness(app)  # must not raise or hang

    assert app.state.model_provider_readiness.status == ModelProviderStatus.NOT_CHECKED
    assert app.state.model_provider_readiness.provider is None
    assert any("timed out" in str(call.args[0]) for call in mock_warning.call_args_list)
