# Copyright 2026 EPAM Systems, Inc. (“EPAM”)test_proxy
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

"""Tests for proxy_router.py integration layer."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

from codemie.core.constants import (
    CODEMIE_CLI,
    LLM_MODEL,
    PROJECT,
    HEADER_CODEMIE_CLI,
    HEADER_CODEMIE_CLI_MODEL,
    HEADER_CODEMIE_CLIENT,
    HEADER_CODEMIE_INTEGRATION,
    HEADER_CODEMIE_REQUEST_ID,
    HEADER_CODEMIE_SESSION_ID,
)
from codemie.enterprise.litellm.budget_categories import BudgetCategory
from codemie.enterprise.litellm.credentials import ResolvedLiteLLMUserCredentials
from codemie.enterprise.litellm.proxy_router import (
    _build_premium_budget_error_body,
    _check_cli_version,
    _extract_request_info,
    _get_integration_api_key,
    _handle_error_response,
    _prepare_proxy_headers,
    _resolve_non_premium_tracking_identity,
    _resolve_tracking_identity,
    _resolve_project_budget_runtime,
    register_proxy_endpoints,
)
from codemie.rest_api.models.settings import LiteLLMCredentials
from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory


class TestExtractRequestInfo:
    """Test _extract_request_info function."""

    def test_extract_all_headers(self):
        """Test extracting all request info when headers present."""
        headers = Headers(
            {
                HEADER_CODEMIE_CLIENT: "cli",
                HEADER_CODEMIE_SESSION_ID: "session-123",
                HEADER_CODEMIE_REQUEST_ID: "request-456",
                HEADER_CODEMIE_CLI_MODEL: "gpt-4",
                "User-Agent": "CodeMie CLI/1.0",
            }
        )

        result = _extract_request_info(headers)

        assert result["client_type"] == "cli"
        assert result["session_id"] == "session-123"
        assert result["request_id"] == "request-456"
        assert result["llm_model"] == "gpt-4"
        assert result["user_agent"] == "CodeMie CLI/1.0"

    def test_extract_with_missing_headers(self):
        """Test extracting request info with missing headers (defaults)."""
        headers = Headers({})

        result = _extract_request_info(headers)

        assert result["client_type"] == "unknown"
        # session_id and request_id should be UUIDs (not empty)
        assert len(result["session_id"]) > 0
        assert len(result["request_id"]) > 0
        assert result["llm_model"] == "unknown"
        assert result["user_agent"] == "unknown"

    def test_extract_with_httpx_headers(self):
        """Test extracting from httpx.Headers."""
        headers = httpx.Headers(
            {
                HEADER_CODEMIE_CLIENT: "web",
                HEADER_CODEMIE_CLI_MODEL: "claude-3",
            }
        )

        result = _extract_request_info(headers)

        assert result["client_type"] == "web"
        assert result["llm_model"] == "claude-3"

    def test_extract_with_dict(self):
        """Test extracting from plain dict."""
        headers = {
            HEADER_CODEMIE_CLIENT: "api",
        }

        result = _extract_request_info(headers)

        assert result["client_type"] == "api"

    def test_extract_cli_header_present(self):
        """Test that X-CodeMie-CLI header is extracted into CODEMIE_CLI key."""
        headers = Headers(
            {
                HEADER_CODEMIE_CLI: "codemie-claude/1.2.0",
            }
        )

        result = _extract_request_info(headers)

        assert result[CODEMIE_CLI] == "codemie-claude/1.2.0"

    def test_extract_cli_header_absent(self):
        """Test that missing X-CodeMie-CLI header produces empty string (non-CLI)."""
        headers = Headers({})

        result = _extract_request_info(headers)

        assert result[CODEMIE_CLI] == ""


class TestResolveProjectBudgetRuntime:
    """Tests for _resolve_project_budget_runtime."""

    @staticmethod
    def _build_session_context(session: MagicMock) -> MagicMock:
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=session)
        context.__aexit__ = AsyncMock(return_value=None)
        return context

    @pytest.mark.asyncio
    async def test_returns_none_without_project_name_and_skips_runtime_resolution(self):
        user = MagicMock()
        user.id = "user-1"
        user.username = "user@example.com"
        request_info = {LLM_MODEL: "gpt-4.1-mini"}

        with patch(
            "codemie.enterprise.litellm.proxy_router.ensure_project_member_runtime_ready",
            new_callable=AsyncMock,
        ) as mock_ensure:
            with patch(
                "codemie.enterprise.litellm.proxy_router.budget_resolution_service.resolve",
                new_callable=AsyncMock,
            ) as mock_resolve:
                with patch(
                    "codemie.enterprise.litellm.proxy_router.budget_resolution_service.dispatch_runtime",
                    new_callable=AsyncMock,
                ) as mock_dispatch:
                    result = await _resolve_project_budget_runtime(
                        user=user,
                        category=BudgetCategory.CLI,
                        request_info=request_info,
                    )

        assert result is None
        mock_ensure.assert_not_called()
        mock_resolve.assert_not_called()
        mock_dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_awaits_member_sync_before_dispatch_and_applies_provider_overrides(self):
        user = MagicMock()
        user.id = "user-1"
        user.username = "user@example.com"
        request_info = {PROJECT: "project-a", LLM_MODEL: "gpt-4.1-mini"}
        session = MagicMock()
        session_context = self._build_session_context(session)
        resolved_context = MagicMock()
        provider_result = MagicMock()
        provider_result.headers = {"x-budget-header": "budget-value"}
        provider_result.api_key = "provider-api-key"
        provider_result.base_url = "https://runtime.provider"
        provider_result.body_overrides = {"user": "runtime-subject"}

        call_order: list[str] = []

        async def _ensure_side_effect(*, user_id: str, user_email: str, project_name: str, budget_category):
            assert user_id == user.id
            assert user_email == user.username
            assert project_name == "project-a"
            assert budget_category == CoreBudgetCategory.CLI
            call_order.append("ensure")

        async def _resolve_side_effect(*args, **kwargs):
            call_order.append("resolve")
            return resolved_context

        async def _dispatch_side_effect(*args, **kwargs):
            call_order.append("dispatch")
            return provider_result

        with patch(
            "codemie.enterprise.litellm.proxy_router.ensure_project_member_runtime_ready",
            new_callable=AsyncMock,
            side_effect=_ensure_side_effect,
        ):
            with patch(
                "codemie.service.settings.settings.SettingsService.get_project_member_budget_tracking_enabled",
                return_value=True,
            ):
                with patch(
                    "codemie.enterprise.litellm.proxy_router.get_async_session",
                    return_value=session_context,
                ):
                    with patch(
                        "codemie.enterprise.litellm.proxy_router.budget_resolution_service.resolve",
                        new_callable=AsyncMock,
                        side_effect=_resolve_side_effect,
                    ):
                        with patch(
                            "codemie.enterprise.litellm.proxy_router.budget_resolution_service.dispatch_runtime",
                            new_callable=AsyncMock,
                            side_effect=_dispatch_side_effect,
                        ):
                            result = await _resolve_project_budget_runtime(
                                user=user,
                                category=BudgetCategory.CLI,
                                request_info=request_info,
                            )

        assert result is provider_result
        assert call_order == ["ensure", "resolve", "dispatch"]
        assert request_info["budget_provider_headers"] == {"x-budget-header": "budget-value"}
        assert request_info["budget_provider_api_key"] == "provider-api-key"
        assert request_info["budget_provider_base_url"] == "https://runtime.provider"

    @pytest.mark.asyncio
    async def test_member_sync_failure_propagates_without_fallback(self):
        user = MagicMock()
        user.id = "user-1"
        user.username = "user@example.com"
        request_info = {PROJECT: "project-a", LLM_MODEL: "gpt-4.1-mini"}
        session_context = self._build_session_context(MagicMock())

        with patch(
            "codemie.enterprise.litellm.proxy_router.ensure_project_member_runtime_ready",
            new_callable=AsyncMock,
            side_effect=RuntimeError("sync failed"),
        ):
            with patch(
                "codemie.enterprise.litellm.proxy_router.get_async_session",
                return_value=session_context,
            ):
                with patch(
                    "codemie.enterprise.litellm.proxy_router.budget_resolution_service.resolve",
                    new_callable=AsyncMock,
                ) as mock_resolve:
                    with patch(
                        "codemie.enterprise.litellm.proxy_router.budget_resolution_service.dispatch_runtime",
                        new_callable=AsyncMock,
                    ) as mock_dispatch:
                        with pytest.raises(RuntimeError, match="sync failed"):
                            await _resolve_project_budget_runtime(
                                user=user,
                                category=BudgetCategory.CLI,
                                request_info=request_info,
                            )

        mock_resolve.assert_not_called()
        mock_dispatch.assert_not_called()


class TestResolveNonPremiumTrackingIdentity:
    """Tests for non-premium proxy budget category selection."""

    def test_web_request_uses_platform_even_when_cli_budget_configured(self):
        user = MagicMock()
        user.username = "user@example.com"
        request_info = {
            LLM_MODEL: "gpt-4.1-mini",
            "client_type": "web",
            CODEMIE_CLI: "",
        }
        category_budget_ids = {
            BudgetCategory.PLATFORM.value: "platform-budget",
            BudgetCategory.CLI.value: "cli-budget",
        }

        with patch(
            "codemie.enterprise.litellm.proxy_router.get_category_budget_id",
            return_value="cli-budget",
        ):
            category, username, budget_id, llm_model = _resolve_non_premium_tracking_identity(
                user=user,
                request_info=request_info,
                category_budget_ids=category_budget_ids,
                llm_model="gpt-4.1-mini",
            )

        assert category == BudgetCategory.PLATFORM
        assert username == "user@example.com"
        assert budget_id == "platform-budget"
        assert llm_model == "gpt-4.1-mini"

    def test_cli_header_request_uses_cli_budget(self):
        user = MagicMock()
        user.username = "user@example.com"
        request_info = {
            LLM_MODEL: "gpt-4.1-mini",
            "client_type": "web",
            CODEMIE_CLI: "codemie-cli/1.2.3",
        }
        category_budget_ids = {
            BudgetCategory.PLATFORM.value: "platform-budget",
            BudgetCategory.CLI.value: "cli-budget",
        }

        category, username, budget_id, llm_model = _resolve_non_premium_tracking_identity(
            user=user,
            request_info=request_info,
            category_budget_ids=category_budget_ids,
            llm_model="gpt-4.1-mini",
        )

        assert category == BudgetCategory.CLI
        assert username == "user@example.com_codemie_cli"
        assert budget_id == "cli-budget"
        assert llm_model == "gpt-4.1-mini"


class TestResolveTrackingIdentity:
    """Tests for availability-first proxy budget category selection."""

    def test_project_platform_only_suppresses_global_premium_selection(self):
        from codemie.enterprise.litellm.proxy_router import BudgetAvailability

        user = MagicMock()
        user.id = "user-1"
        user.username = "user@example.com"
        request_info = {
            PROJECT: "proj-a",
            LLM_MODEL: "claude-opus-4-6-20260205",
            CODEMIE_CLI: "",
            "client_type": "web",
        }
        availability = BudgetAvailability(
            user_budget_ids={
                BudgetCategory.PLATFORM.value: None,
                BudgetCategory.CLI.value: None,
                BudgetCategory.PREMIUM_MODELS.value: None,
            },
            project_scopes={BudgetCategory.PLATFORM},
        )

        with patch(
            "codemie.enterprise.litellm.proxy_router.get_premium_username",
            return_value="user@example.com_codemie_premium_models",
        ):
            category, username, tracking_budget_id, llm_model = _resolve_tracking_identity(
                user=user,
                request_info=request_info,
                availability=availability,
            )

        assert category == BudgetCategory.PLATFORM
        assert username == "user@example.com"
        assert tracking_budget_id is None
        assert llm_model == "claude-opus-4-6-20260205"

    def test_project_platform_only_suppresses_global_cli_selection(self):
        from codemie.enterprise.litellm.proxy_router import BudgetAvailability

        user = MagicMock()
        user.id = "user-1"
        user.username = "user@example.com"
        request_info = {
            PROJECT: "proj-a",
            LLM_MODEL: "gpt-4.1-mini",
            CODEMIE_CLI: "codemie-cli/1.2.3",
            "client_type": "web",
        }
        availability = BudgetAvailability(
            user_budget_ids={
                BudgetCategory.PLATFORM.value: None,
                BudgetCategory.CLI.value: None,
                BudgetCategory.PREMIUM_MODELS.value: None,
            },
            project_scopes={BudgetCategory.PLATFORM},
        )

        category, username, tracking_budget_id, llm_model = _resolve_tracking_identity(
            user=user,
            request_info=request_info,
            availability=availability,
        )

        assert category == BudgetCategory.PLATFORM
        assert username == "user@example.com"
        assert tracking_budget_id is None
        assert llm_model == "gpt-4.1-mini"


class TestProbeProjectBudgetScopes:
    """Tests for project budget availability probing."""

    @pytest.mark.asyncio
    async def test_probe_project_budget_scopes_populates_resolution_cache_for_all_categories(self):
        from codemie.enterprise.litellm.proxy_router import _probe_project_budget_scopes
        from codemie.service.budget.budget_resolution_service import (
            BudgetScope as CoreBudgetScope,
            ResolvedBudgetContext,
        )

        fake_session = MagicMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=fake_session)
        ctx.__aexit__ = AsyncMock(return_value=None)

        rows = {
            BudgetCategory.PLATFORM: ResolvedBudgetContext(
                scope=CoreBudgetScope.PROJECT,
                project_name="proj-a",
                budget_category=CoreBudgetCategory.PLATFORM,
                budget_id="budget-platform",
            ),
            BudgetCategory.CLI: ResolvedBudgetContext(
                scope=CoreBudgetScope.PROJECT,
                project_name="proj-a",
                budget_category=CoreBudgetCategory.CLI,
                budget_id="budget-cli",
            ),
        }

        with (
            patch("codemie.enterprise.litellm.proxy_router.get_async_session", return_value=ctx),
            patch(
                "codemie.enterprise.litellm.proxy_router.project_budget_assignment_repository.get_project_budget_categories_batch",
                new=AsyncMock(return_value=rows),
            ),
        ):
            scopes = await _probe_project_budget_scopes("proj-a", "user-1")

        assert scopes == {BudgetCategory.PLATFORM, BudgetCategory.CLI}

    @pytest.mark.parametrize("client_type", ["codemie-cli", "codemie_cli"])
    def test_cli_client_type_uses_cli_budget(self, client_type):
        user = MagicMock()
        user.username = "user@example.com"
        request_info = {
            LLM_MODEL: "gpt-4.1-mini",
            "client_type": client_type,
            CODEMIE_CLI: "",
        }
        category_budget_ids = {
            BudgetCategory.PLATFORM.value: "platform-budget",
            BudgetCategory.CLI.value: "cli-budget",
        }

        category, username, budget_id, llm_model = _resolve_non_premium_tracking_identity(
            user=user,
            request_info=request_info,
            category_budget_ids=category_budget_ids,
            llm_model="gpt-4.1-mini",
        )

        assert category == BudgetCategory.CLI
        assert username == "user@example.com_codemie_cli"
        assert budget_id == "cli-budget"
        assert llm_model == "gpt-4.1-mini"


class TestPrepareProxyHeaders:
    """Test _prepare_proxy_headers function."""

    def test_prepare_headers_filters_hop_by_hop(self):
        """Test that hop-by-hop headers are filtered out."""
        mock_request = MagicMock()
        mock_request.headers = Headers(
            {
                "content-type": "application/json",
                "connection": "keep-alive",
                "host": "example.com",
                "transfer-encoding": "chunked",
                HEADER_CODEMIE_CLIENT: "cli",
                "authorization": "Bearer old-token",
            }
        )

        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
            mock_config.LITE_LLM_APP_KEY = "test-app-key"
            mock_config.LITE_LLM_PROXY_APP_KEY = ""
            with patch("codemie.enterprise.litellm.proxy_router.litellm_context") as mock_context:
                mock_context.get.side_effect = LookupError()

                result = _prepare_proxy_headers(mock_request)

        # Should keep content-type
        assert "content-type" in result
        assert result["content-type"] == "application/json"

        # Should filter out hop-by-hop headers
        assert "connection" not in result
        assert "host" not in result
        assert "transfer-encoding" not in result
        assert HEADER_CODEMIE_CLIENT not in result

        # Should fall back to app key when proxy key is not set
        assert result["Authorization"] == "Bearer test-app-key"

    def test_prepare_headers_uses_proxy_key_when_set(self):
        """Test that proxy key takes precedence over app key when configured."""
        mock_request = MagicMock()
        mock_request.headers = Headers({"content-type": "application/json"})

        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
            mock_config.LITE_LLM_APP_KEY = "platform-key"
            mock_config.LITE_LLM_PROXY_APP_KEY = "proxy-key"
            with patch("codemie.enterprise.litellm.proxy_router.litellm_context") as mock_context:
                mock_context.get.side_effect = LookupError()

                result = _prepare_proxy_headers(mock_request)

        assert result["Authorization"] == "Bearer proxy-key"

    def test_prepare_headers_ignores_integration_header_without_user_credentials(self):
        """Integration header should not select project credentials for proxy auth."""
        mock_request = MagicMock()
        mock_request.headers = Headers(
            {
                "content-type": "application/json",
                HEADER_CODEMIE_INTEGRATION: "project-integration",
            }
        )

        with patch("codemie.enterprise.litellm.proxy_router._get_integration_api_key") as mock_get_key:
            with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
                mock_config.LITE_LLM_APP_KEY = "platform-key"
                mock_config.LITE_LLM_PROXY_APP_KEY = ""
                with patch("codemie.enterprise.litellm.proxy_router.litellm_context") as mock_context:
                    mock_context.get.side_effect = LookupError()

                    result = _prepare_proxy_headers(mock_request, request_info={}, user_credentials=None)

        assert result["Authorization"] == "Bearer platform-key"
        mock_get_key.assert_not_called()

    def test_prepare_headers_uses_resolved_user_credentials(self):
        """Resolved user credentials should be used for proxy auth."""
        mock_request = MagicMock()
        mock_request.headers = Headers({"content-type": "application/json"})
        user_credentials = ResolvedLiteLLMUserCredentials(
            credentials=LiteLLMCredentials(api_key="sk-user", url=""),
            setting_id="setting-1",
            alias="personal-key",
        )

        with patch("codemie.enterprise.litellm.proxy_router.litellm_context") as mock_context:
            mock_context.get.side_effect = LookupError()

            result = _prepare_proxy_headers(
                mock_request,
                request_info={},
                user_credentials=user_credentials,
            )

        assert result["Authorization"] == "Bearer sk-user"

    def test_prepare_headers_prefers_project_budget_key_over_user_credentials(self):
        """Project budget runtime key has higher precedence than user credentials."""
        mock_request = MagicMock()
        mock_request.headers = Headers({"content-type": "application/json"})
        user_credentials = ResolvedLiteLLMUserCredentials(
            credentials=LiteLLMCredentials(api_key="sk-user", url=""),
            setting_id="setting-1",
            alias="personal-key",
        )
        request_info = {"budget_provider_api_key": "project-budget-key"}

        with patch("codemie.enterprise.litellm.proxy_router.litellm_context") as mock_context:
            mock_context.get.side_effect = LookupError()

            result = _prepare_proxy_headers(
                mock_request,
                request_info=request_info,
                user_credentials=user_credentials,
            )

        assert result["Authorization"] == "Bearer project-budget-key"

    def test_prepare_headers_with_context(self):
        """Test preparing headers with litellm context."""
        mock_request = MagicMock()
        mock_request.headers = Headers(
            {
                "content-type": "application/json",
            }
        )

        mock_context_obj = MagicMock()

        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
            mock_config.LITE_LLM_APP_KEY = "test-app-key"
            mock_config.LITE_LLM_PROXY_APP_KEY = ""
            with patch("codemie.enterprise.litellm.proxy_router.litellm_context") as mock_context:
                mock_context.get.return_value = mock_context_obj
                with patch("codemie.enterprise.litellm.proxy_router.generate_litellm_headers_from_context") as mock_gen:
                    mock_gen.return_value = {"x-custom-header": "custom-value"}

                    result = _prepare_proxy_headers(mock_request)

        assert result["Authorization"] == "Bearer test-app-key"
        assert result["x-custom-header"] == "custom-value"


class TestGetIntegrationApiKey:
    """Test _get_integration_api_key function."""

    def test_get_api_key_success(self):
        """Test successfully retrieving API key."""
        mock_credentials = MagicMock()
        mock_credentials.api_key = "decrypted-api-key"

        # Clear cache first
        _get_integration_api_key.cache_clear()

        # Patch where SettingsService is used (inside the function)
        with patch("codemie.service.settings.settings.SettingsService") as mock_service:
            mock_service.get_credentials.return_value = mock_credentials
            mock_service.LITELLM_FIELDS = ["api_key"]

            result = _get_integration_api_key("integration-123")

        assert result == "decrypted-api-key"
        mock_service.get_credentials.assert_called_once()

    def test_get_api_key_not_found(self):
        """Test when integration not found."""
        # Clear cache first
        _get_integration_api_key.cache_clear()

        with patch("codemie.service.settings.settings.SettingsService") as mock_service:
            mock_service.get_credentials.return_value = None
            mock_service.LITELLM_FIELDS = ["api_key"]

            with pytest.raises(HTTPException) as exc_info:
                _get_integration_api_key("nonexistent")

        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail

    def test_get_api_key_error(self):
        """Test when error occurs during retrieval."""
        # Clear cache first
        _get_integration_api_key.cache_clear()

        with patch("codemie.service.settings.settings.SettingsService") as mock_service:
            mock_service.get_credentials.side_effect = Exception("Database error")
            mock_service.LITELLM_FIELDS = ["api_key"]

            with pytest.raises(HTTPException) as exc_info:
                _get_integration_api_key("integration-123")

        assert exc_info.value.status_code == 500
        assert "Failed to retrieve API key" in exc_info.value.detail

    def test_get_api_key_cached(self):
        """Test that API key is cached."""
        mock_credentials = MagicMock()
        mock_credentials.api_key = "cached-api-key"

        # Clear cache first
        _get_integration_api_key.cache_clear()

        with patch("codemie.service.settings.settings.SettingsService") as mock_service:
            mock_service.get_credentials.return_value = mock_credentials
            mock_service.LITELLM_FIELDS = ["api_key"]

            # First call
            result1 = _get_integration_api_key("integration-123")
            # Second call (should use cache)
            result2 = _get_integration_api_key("integration-123")

        assert result1 == "cached-api-key"
        assert result2 == "cached-api-key"
        # Should only call get_credentials once (second call uses cache)
        assert mock_service.get_credentials.call_count == 1


class TestInjectUserIntoRequestBody:
    """Test _inject_user_into_request_body_from_bytes function."""

    @pytest.mark.asyncio
    async def test_inject_user_into_body(self):
        """Test injecting user into request body."""
        from codemie.enterprise.litellm.proxy_router import _inject_user_into_request_body_from_bytes

        body_bytes = b'{"messages": [{"role": "user", "content": "Hello"}]}'

        async def mock_stream():
            yield b'{"messages": [{"role": "user", "content": "Hello"}]}'

        request_info = {
            "session_id": "session-123",
            "request_id": "request-456",
        }

        with patch("codemie.enterprise.litellm.proxy_router.inject_user_into_body") as mock_inject:
            # Mock the enterprise function to return the same stream
            mock_inject.return_value = mock_stream()

            _inject_user_into_request_body_from_bytes(body_bytes, "test-user", request_info)

            # Verify enterprise function was called with correct args
            mock_inject.assert_called_once()
            call_args = mock_inject.call_args
            assert call_args.kwargs["username"] == "test-user"
            assert call_args.kwargs["session_id"] == "session-123"
            assert call_args.kwargs["request_id"] == "request-456"
            assert call_args.kwargs["content_type"] == "application/json"


class TestCreateBodyStreamWithOptionalInjection:
    """Test _create_body_stream_with_optional_injection function."""

    @pytest.mark.asyncio
    async def test_premium_model_uses_premium_budget_id(self):
        """Premium model requests must pass the premium budget id to budget checks."""
        from codemie.enterprise.litellm.proxy_router import _create_body_stream_with_optional_injection

        user = MagicMock()
        user.username = "user@example.com"
        request_info = {"llm_model": "claude-opus-4", "session_id": "session-123", "request_id": "request-456"}

        with patch("codemie.enterprise.litellm.proxy_router.get_premium_username") as mock_get_premium_username:
            mock_get_premium_username.return_value = "user@example.com_codemie_premium_models"
            with patch(
                "codemie.enterprise.litellm.proxy_router.get_category_budget_id",
                return_value="premium_models",
            ):
                with patch("codemie.enterprise.litellm.proxy_router.check_user_budget") as mock_check_user_budget:
                    with patch(
                        "codemie.enterprise.litellm.proxy_router._inject_user_into_request_body_from_bytes"
                    ) as mock_inject:
                        mock_inject.return_value = "stream"

                        result = await _create_body_stream_with_optional_injection(
                            body_bytes=b"{}",
                            user=user,
                            request_info=request_info,
                        )

        assert result == "stream"
        mock_check_user_budget.assert_called_once_with(
            user_email="user@example.com_codemie_premium_models",
            budget_id="premium_models",
            user_id=user.id,
        )
        mock_inject.assert_called_once_with(
            body_bytes=b"{}", user_id="user@example.com_codemie_premium_models", request_info=request_info
        )

    @pytest.mark.asyncio
    async def test_premium_model_uses_runtime_assigned_budget_without_predefined_budget(self):
        """Runtime-assigned premium budget must enable premium routing without predefined config."""
        from codemie.enterprise.litellm.proxy_router import _create_body_stream_with_optional_injection

        user = MagicMock()
        user.id = "user-1"
        user.username = "user@example.com"
        request_info = {
            "llm_model": "claude-opus-4-6-20260205",
            "session_id": "session-123",
            "request_id": "request-456",
        }

        with patch(
            "codemie.enterprise.litellm.proxy_router.get_premium_username",
            return_value="user@example.com_codemie_premium_models",
        ):
            with patch("codemie.enterprise.litellm.proxy_router.get_category_budget_id", return_value=None):
                with patch(
                    "codemie.enterprise.litellm.proxy_router.budget_service.get_all_category_budget_ids_for_request",
                    new=AsyncMock(
                        return_value={
                            "platform": None,
                            "cli": None,
                            "premium_models": "runtime-premium-budget",
                        }
                    ),
                ):
                    with patch("codemie.enterprise.litellm.proxy_router.check_user_budget") as mock_check_user_budget:
                        with patch(
                            "codemie.enterprise.litellm.proxy_router._inject_user_into_request_body_from_bytes"
                        ) as mock_inject:
                            mock_inject.return_value = "stream"

                            result = await _create_body_stream_with_optional_injection(
                                body_bytes=b"{}",
                                user=user,
                                request_info=request_info,
                            )

        assert result == "stream"
        mock_check_user_budget.assert_called_once_with(
            user_email="user@example.com_codemie_premium_models",
            budget_id="runtime-premium-budget",
            user_id=user.id,
        )
        mock_inject.assert_called_once_with(
            body_bytes=b"{}",
            user_id="user@example.com_codemie_premium_models",
            request_info=request_info,
        )

    @pytest.mark.asyncio
    async def test_regular_model_keeps_default_budget_flow(self):
        """Non-premium requests should not pass an explicit premium budget id."""
        from codemie.enterprise.litellm.proxy_router import _create_body_stream_with_optional_injection

        user = MagicMock()
        user.username = "user@example.com"
        request_info = {"llm_model": "gpt-4.1-mini", "session_id": "session-123", "request_id": "request-456"}

        with patch("codemie.enterprise.litellm.proxy_router.get_premium_username", return_value=None):
            with patch("codemie.enterprise.litellm.proxy_router.check_user_budget") as mock_check_user_budget:
                with patch(
                    "codemie.enterprise.litellm.proxy_router._inject_user_into_request_body_from_bytes"
                ) as mock_inject:
                    mock_inject.return_value = "stream"

                    result = await _create_body_stream_with_optional_injection(
                        body_bytes=b"{}",
                        user=user,
                        request_info=request_info,
                    )

        assert result == "stream"
        mock_check_user_budget.assert_called_once_with(user_email="user@example.com", budget_id=None, user_id=user.id)

    @pytest.mark.asyncio
    async def test_non_premium_web_request_uses_platform_budget_even_when_cli_configured(self):
        """Non-premium web proxy requests should not use the dedicated CLI budget."""
        from codemie.enterprise.litellm.proxy_router import _create_body_stream_with_optional_injection

        user = MagicMock()
        user.id = "user-1"
        user.username = "user@example.com"
        request_info = {
            "llm_model": "gpt-4.1-mini",
            "session_id": "session-123",
            "request_id": "request-456",
            "client_type": "web",
            CODEMIE_CLI: "",
        }

        with patch("codemie.enterprise.litellm.proxy_router.get_premium_username", return_value=None):
            with patch(
                "codemie.enterprise.litellm.proxy_router.budget_service.get_all_category_budget_ids_for_request",
                new=AsyncMock(
                    return_value={
                        "platform": "platform-budget",
                        "cli": "cli-budget",
                        "premium_models": None,
                    }
                ),
            ):
                with patch(
                    "codemie.enterprise.litellm.proxy_router.get_category_budget_id",
                    return_value="cli-budget",
                ):
                    with patch("codemie.enterprise.litellm.proxy_router.check_user_budget") as mock_check_user_budget:
                        with patch(
                            "codemie.enterprise.litellm.proxy_router._inject_user_into_request_body_from_bytes"
                        ) as mock_inject:
                            mock_inject.return_value = "stream"

                            result = await _create_body_stream_with_optional_injection(
                                body_bytes=b"{}",
                                user=user,
                                request_info=request_info,
                            )

        assert result == "stream"
        mock_check_user_budget.assert_called_once_with(
            user_email="user@example.com",
            budget_id=None,
            user_id="user-1",
        )
        mock_inject.assert_called_once_with(
            body_bytes=b"{}",
            user_id="user@example.com",
            request_info=request_info,
        )

    @pytest.mark.asyncio
    async def test_non_premium_cli_request_uses_cli_budget(self):
        """Non-premium CLI proxy requests should use the dedicated CLI budget."""
        from codemie.enterprise.litellm.proxy_router import _create_body_stream_with_optional_injection

        user = MagicMock()
        user.id = "user-1"
        user.username = "user@example.com"
        request_info = {
            "llm_model": "gpt-4.1-mini",
            "session_id": "session-123",
            "request_id": "request-456",
            "client_type": "codemie-cli",
            CODEMIE_CLI: "codemie-cli/1.2.3",
        }

        with patch("codemie.enterprise.litellm.proxy_router.get_premium_username", return_value=None):
            with patch(
                "codemie.enterprise.litellm.proxy_router.budget_service.get_all_category_budget_ids_for_request",
                new=AsyncMock(
                    return_value={
                        "platform": "platform-budget",
                        "cli": "cli-budget",
                        "premium_models": None,
                    }
                ),
            ):
                with patch(
                    "codemie.enterprise.litellm.proxy_router.get_category_budget_id",
                    return_value="cli-budget",
                ):
                    with patch("codemie.enterprise.litellm.proxy_router.check_user_budget") as mock_check_user_budget:
                        with patch(
                            "codemie.enterprise.litellm.proxy_router._inject_user_into_request_body_from_bytes"
                        ) as mock_inject:
                            mock_inject.return_value = "stream"

                            result = await _create_body_stream_with_optional_injection(
                                body_bytes=b"{}",
                                user=user,
                                request_info=request_info,
                            )

        assert result == "stream"
        mock_check_user_budget.assert_called_once_with(
            user_email="user@example.com_codemie_cli",
            budget_id="cli-budget",
            user_id="user-1",
        )
        mock_inject.assert_called_once_with(
            body_bytes=b"{}",
            user_id="user@example.com_codemie_cli",
            request_info=request_info,
        )

    @pytest.mark.asyncio
    async def test_web_project_runtime_resolves_platform_category(self):
        """Web project requests must resolve project runtime against the platform category."""
        from codemie.enterprise.litellm.proxy_router import _create_body_stream_with_optional_injection

        user = MagicMock()
        user.id = "user-1"
        user.username = "user@example.com"
        request_info = {
            "llm_model": "gpt-4.1-mini",
            "session_id": "session-123",
            "request_id": "request-456",
            "client_type": "web",
            CODEMIE_CLI: "",
            PROJECT: "project-a",
        }

        with patch("codemie.enterprise.litellm.proxy_router.get_premium_username", return_value=None):
            with patch(
                "codemie.enterprise.litellm.proxy_router.budget_service.get_all_category_budget_ids_for_request",
                new=AsyncMock(
                    return_value={
                        "platform": "platform-budget",
                        "cli": "cli-budget",
                        "premium_models": None,
                    }
                ),
            ):
                with patch(
                    "codemie.enterprise.litellm.proxy_router._probe_project_budget_scopes",
                    new=AsyncMock(return_value={BudgetCategory.PLATFORM}),
                ):
                    with patch(
                        "codemie.enterprise.litellm.proxy_router._resolve_project_budget_runtime",
                        new=AsyncMock(return_value=None),
                    ) as mock_resolve_runtime:
                        with patch(
                            "codemie.enterprise.litellm.proxy_router.get_category_budget_id",
                            return_value="cli-budget",
                        ):
                            with patch("codemie.enterprise.litellm.proxy_router.check_user_budget"):
                                with patch(
                                    "codemie.enterprise.litellm.proxy_router._inject_user_into_request_body_from_bytes",
                                    return_value="stream",
                                ):
                                    result = await _create_body_stream_with_optional_injection(
                                        body_bytes=b"{}",
                                        user=user,
                                        request_info=request_info,
                                    )

        assert result == "stream"
        mock_resolve_runtime.assert_awaited_once()
        assert mock_resolve_runtime.await_args.kwargs["category"] == BudgetCategory.PLATFORM

    @pytest.mark.asyncio
    async def test_resolved_user_credentials_skip_budget_injection(self):
        """Resolved user credentials are treated as own credentials and skip budget injection."""
        from codemie.enterprise.litellm.proxy_router import _create_body_stream_with_optional_injection

        user = MagicMock()
        user.id = "user-1"
        user.username = "user@example.com"
        request_info = {"llm_model": "gpt-4.1-mini", "session_id": "session-123", "request_id": "request-456"}
        user_credentials = ResolvedLiteLLMUserCredentials(
            credentials=LiteLLMCredentials(api_key="sk-user", url=""),
            setting_id="setting-1",
            alias="personal-key",
        )

        with patch("codemie.enterprise.litellm.proxy_router._inject_user_into_request_body_from_bytes") as mock_inject:
            result = await _create_body_stream_with_optional_injection(
                body_bytes=b"{}",
                user=user,
                request_info=request_info,
                user_credentials=user_credentials,
            )

        collected = []
        async for chunk in result:
            collected.append(chunk)

        assert collected == [b"{}"]
        mock_inject.assert_not_called()


class TestParseUsageWithCost:
    """Test _parse_usage_with_cost function."""

    @pytest.mark.asyncio
    async def test_parse_usage_with_cost_success(self):
        """Test parsing usage with cost calculation."""
        from codemie.enterprise.litellm.proxy_router import _parse_usage_with_cost

        response_content = b'{"usage": {"prompt_tokens": 100, "completion_tokens": 50}}'

        mock_cost_config = {
            "input": 0.01,
            "output": 0.02,
        }

        with patch("codemie.enterprise.litellm.proxy_router.llm_service") as mock_llm_service:
            mock_llm_service.get_model_cost.return_value = mock_cost_config
            with patch("codemie.enterprise.litellm.proxy_router.parse_usage_from_response") as mock_parse:
                mock_parse.return_value = {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cached_tokens": 0,
                    "money_spent": 2.0,
                    "cached_tokens_money_spent": 0.0,
                }

                result = await _parse_usage_with_cost(
                    response_content=response_content,
                    llm_model="gpt-4",
                    is_streaming=False,
                )

        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50
        assert result["money_spent"] == 2.0
        mock_llm_service.get_model_cost.assert_called_once_with("gpt-4")

    @pytest.mark.asyncio
    async def test_parse_usage_with_cost_error(self):
        """Test parsing usage when cost config fails."""
        from codemie.enterprise.litellm.proxy_router import _parse_usage_with_cost

        response_content = b'{"usage": {"prompt_tokens": 100}}'

        with patch("codemie.enterprise.litellm.proxy_router.llm_service") as mock_llm_service:
            mock_llm_service.get_model_cost.side_effect = Exception("Model not found")
            with patch("codemie.enterprise.litellm.proxy_router.parse_usage_from_response") as mock_parse:
                mock_parse.return_value = {
                    "input_tokens": 100,
                    "output_tokens": 0,
                    "cached_tokens": 0,
                    "money_spent": 0.0,
                    "cached_tokens_money_spent": 0.0,
                }

                result = await _parse_usage_with_cost(
                    response_content=response_content,
                    llm_model="unknown-model",
                    is_streaming=False,
                )

        # Should still return usage data even if cost config fails
        assert result["input_tokens"] == 100


class TestStreamingResponseWithUsageTracking:
    """Test _streaming_response_with_usage_tracking function."""

    @pytest.mark.asyncio
    async def test_streaming_with_usage_tracking(self):
        """Test streaming response with usage tracking."""
        from codemie.enterprise.litellm.proxy_router import _streaming_response_with_usage_tracking

        # Create mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = httpx.Headers({"content-type": "text/event-stream"})

        # Mock streaming chunks
        async def mock_iter():
            yield b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n'
            yield b'data: {"choices": [{"delta": {"content": " World"}}]}\n\n'
            yield b'data: [DONE]\n\n'

        mock_response.aiter_raw = mock_iter
        mock_response.aclose = AsyncMock()

        mock_user = MagicMock()
        mock_user.id = "user-123"

        request_info = {
            "session_id": "session-123",
            "request_id": "request-456",
        }

        mock_background_tasks = MagicMock()

        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
            mock_config.LLM_PROXY_TRACK_USAGE = True
            with patch("codemie.enterprise.litellm.proxy_router._parse_usage_with_cost") as mock_parse:
                mock_parse.return_value = {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cached_tokens": 0,
                    "money_spent": 0.5,
                    "cached_tokens_money_spent": 0.0,
                }

                chunks = []
                async for chunk in _streaming_response_with_usage_tracking(
                    downstream_response=mock_response,
                    user=mock_user,
                    endpoint="/v1/chat/completions",
                    request_info=request_info,
                    llm_model="gpt-4",
                    background_tasks=mock_background_tasks,
                ):
                    chunks.append(chunk)

        # Verify all chunks were yielded
        assert len(chunks) == 3
        # Verify usage tracking was added as background task
        mock_background_tasks.add_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_streaming_no_usage_when_zero_tokens(self):
        """Test streaming doesn't track usage when no tokens used."""
        from codemie.enterprise.litellm.proxy_router import _streaming_response_with_usage_tracking

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = httpx.Headers({"content-type": "text/event-stream"})

        async def mock_iter():
            yield b'data: [DONE]\n\n'

        mock_response.aiter_raw = mock_iter
        mock_response.aclose = AsyncMock()

        mock_user = MagicMock()
        request_info = {}
        mock_background_tasks = MagicMock()

        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
            mock_config.LLM_PROXY_TRACK_USAGE = True
            with patch("codemie.enterprise.litellm.proxy_router._parse_usage_with_cost") as mock_parse:
                mock_parse.return_value = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_tokens": 0,
                    "money_spent": 0.0,
                    "cached_tokens_money_spent": 0.0,
                }

                chunks = []
                async for chunk in _streaming_response_with_usage_tracking(
                    downstream_response=mock_response,
                    user=mock_user,
                    endpoint="/v1/chat/completions",
                    request_info=request_info,
                    llm_model="gpt-4",
                    background_tasks=mock_background_tasks,
                ):
                    chunks.append(chunk)

        # Should not track usage when no tokens
        mock_background_tasks.add_task.assert_not_called()


class TestProxyToLLMProxy:
    """Test _proxy_to_llm_proxy main orchestrator."""

    @pytest.mark.asyncio
    async def test_proxy_success_streaming(self):
        """Test successful proxy with streaming response."""
        from codemie.enterprise.litellm.proxy_router import _proxy_to_llm_proxy

        # Create mock request
        mock_request = MagicMock()
        mock_request.method = "POST"
        mock_request.headers = Headers(
            {
                HEADER_CODEMIE_CLIENT: "cli",
                HEADER_CODEMIE_CLI_MODEL: "gpt-4",
            }
        )
        mock_request.body = AsyncMock(return_value=b'{"messages": [], "model": "gpt-4"}')

        async def mock_stream():
            yield b'{"messages": []}'

        # Create mock user
        mock_user = MagicMock()
        mock_user.id = "user-123"
        mock_user.username = "testuser"

        # Create mock background tasks
        mock_background_tasks = MagicMock()

        # Create mock downstream response
        mock_downstream_response = MagicMock()
        mock_downstream_response.status_code = 200
        mock_downstream_response.headers = httpx.Headers({"content-type": "application/json"})

        async def mock_iter():
            yield b'{"choices": []}'

        mock_downstream_response.aiter_raw = mock_iter

        # Mock dependencies
        with patch("codemie.enterprise.litellm.proxy_router.is_litellm_enabled", return_value=True):
            with patch(
                "codemie.enterprise.litellm.proxy_router._create_body_stream_with_optional_injection",
                new_callable=AsyncMock,
            ) as mock_inject:
                mock_inject.return_value = mock_stream()
                with patch("codemie.enterprise.litellm.proxy_router._prepare_proxy_headers") as mock_prepare:
                    mock_prepare.return_value = {"Authorization": "Bearer test"}
                    with patch("codemie.enterprise.litellm.proxy_router.get_llm_proxy_client") as mock_get_client:
                        mock_client = MagicMock()
                        mock_client.build_request.return_value = MagicMock()
                        mock_client.send = AsyncMock(return_value=mock_downstream_response)
                        mock_get_client.return_value = mock_client
                        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
                            mock_config.LLM_PROXY_TIMEOUT = 300
                            mock_config.LLM_PROXY_TRACK_USAGE = False

                            result = await _proxy_to_llm_proxy(
                                request=mock_request,
                                user=mock_user,
                                endpoint="/v1/chat/completions",
                                background_tasks=mock_background_tasks,
                            )

        # Verify response
        assert result is not None
        # Verify metrics were tracked
        assert mock_background_tasks.add_task.called

    @pytest.mark.asyncio
    async def test_proxy_resolves_user_credentials_without_header(self):
        """Proxy should resolve user credentials from backend settings without an integration header."""
        from codemie.enterprise.litellm.proxy_router import _proxy_to_llm_proxy

        mock_request = MagicMock()
        mock_request.method = "POST"
        mock_request.headers = Headers({HEADER_CODEMIE_CLIENT: "cli", HEADER_CODEMIE_CLI_MODEL: "gpt-4"})
        mock_request.body = AsyncMock(return_value=b'{"messages": [], "model": "gpt-4"}')
        mock_user = MagicMock()
        mock_user.id = "user-123"
        mock_user.username = "user@example.com"
        background_tasks = MagicMock()
        user_credentials = ResolvedLiteLLMUserCredentials(
            credentials=LiteLLMCredentials(api_key="sk-user", url=""),
            setting_id="setting-1",
            alias="personal-key",
        )
        downstream = MagicMock()
        downstream.status_code = 200
        downstream.headers = httpx.Headers({"content-type": "application/json"})

        async def mock_iter():
            yield b'{"choices": []}'

        downstream.aiter_raw = mock_iter

        with (
            patch("codemie.enterprise.litellm.proxy_router.is_litellm_enabled", return_value=True),
            patch(
                "codemie.enterprise.litellm.proxy_router.resolve_litellm_user_credentials",
                return_value=user_credentials,
            ) as mock_resolve,
            patch(
                "codemie.enterprise.litellm.proxy_router._create_body_stream_with_optional_injection",
                new_callable=AsyncMock,
            ) as mock_body,
            patch("codemie.enterprise.litellm.proxy_router._prepare_proxy_headers") as mock_headers,
            patch("codemie.enterprise.litellm.proxy_router.get_llm_proxy_client") as mock_client_factory,
            patch("codemie.enterprise.litellm.proxy_router.config") as mock_config,
        ):

            async def body_stream():
                yield b"{}"

            mock_body.return_value = body_stream()
            mock_headers.return_value = {"Authorization": "Bearer sk-user"}
            mock_client = MagicMock()
            mock_client.build_request.return_value = MagicMock()
            mock_client.send = AsyncMock(return_value=downstream)
            mock_client_factory.return_value = mock_client
            mock_config.LLM_PROXY_TIMEOUT = 300
            mock_config.LLM_PROXY_TRACK_USAGE = False

            await _proxy_to_llm_proxy(
                request=mock_request,
                user=mock_user,
                endpoint="/v1/chat/completions",
                background_tasks=background_tasks,
            )

        mock_resolve.assert_called_once_with(
            user_id="user-123",
            username="user@example.com",
            project_name="",
        )
        mock_body.assert_called_once()
        assert mock_body.call_args.kwargs["user_credentials"] is user_credentials
        mock_headers.assert_called_once()
        assert mock_headers.call_args.kwargs["user_credentials"] is user_credentials

    @pytest.mark.asyncio
    async def test_proxy_disabled(self):
        """Test proxy when LiteLLM is disabled."""
        from codemie.enterprise.litellm.proxy_router import _proxy_to_llm_proxy

        mock_request = MagicMock()
        mock_request.headers = Headers({})
        mock_request.body = AsyncMock(return_value=b"{}")
        mock_user = MagicMock()
        mock_background_tasks = MagicMock()

        with patch("codemie.enterprise.litellm.proxy_router.is_litellm_enabled", return_value=False):
            with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
                mock_config.LLM_PROXY_ENABLED = False

                with pytest.raises(HTTPException) as exc_info:
                    await _proxy_to_llm_proxy(
                        request=mock_request,
                        user=mock_user,
                        endpoint="/v1/chat/completions",
                        background_tasks=mock_background_tasks,
                    )

        assert exc_info.value.status_code == 400
        assert "not available" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_proxy_connection_error(self):
        """Test proxy when connection to LiteLLM fails."""
        from codemie.enterprise.litellm.proxy_router import _proxy_to_llm_proxy

        mock_request = MagicMock()
        mock_request.method = "POST"
        mock_request.headers = Headers(
            {
                HEADER_CODEMIE_CLI_MODEL: "gpt-4",
            }
        )
        mock_request.body = AsyncMock(return_value=b'{"messages": [], "model": "gpt-4"}')

        async def mock_stream():
            yield b'{"messages": []}'

        mock_user = MagicMock()
        mock_user.id = "user-123"
        mock_user.username = "testuser"

        mock_background_tasks = MagicMock()

        with patch("codemie.enterprise.litellm.proxy_router.is_litellm_enabled", return_value=True):
            with patch(
                "codemie.enterprise.litellm.proxy_router._create_body_stream_with_optional_injection",
                new_callable=AsyncMock,
            ) as mock_inject:
                mock_inject.return_value = mock_stream()
                with patch("codemie.enterprise.litellm.proxy_router._prepare_proxy_headers") as mock_prepare:
                    mock_prepare.return_value = {"Authorization": "Bearer test"}
                    with patch("codemie.enterprise.litellm.proxy_router.get_llm_proxy_client") as mock_get_client:
                        mock_client = MagicMock()
                        mock_client.build_request.return_value = MagicMock()
                        mock_client.send = AsyncMock(side_effect=httpx.RequestError("Connection failed"))
                        mock_get_client.return_value = mock_client
                        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
                            mock_config.LLM_PROXY_TIMEOUT = 300

                            result = await _proxy_to_llm_proxy(
                                request=mock_request,
                                user=mock_user,
                                endpoint="/v1/chat/completions",
                                background_tasks=mock_background_tasks,
                            )

        # Should return error response
        assert result.status_code == 503
        # Should still track error metrics
        assert mock_background_tasks.add_task.called


class TestCreateProxyEndpoint:
    """Test _create_proxy_endpoint factory function."""

    @pytest.mark.asyncio
    async def test_create_simple_endpoint(self):
        """Test creating endpoint without path parameters."""
        from codemie.enterprise.litellm.proxy_router import _create_proxy_endpoint

        endpoint = "/v1/chat/completions"
        handler = _create_proxy_endpoint(endpoint)

        # Verify handler is a function
        assert callable(handler)

        # Mock dependencies
        mock_request = MagicMock()
        mock_background_tasks = MagicMock()
        mock_user = MagicMock()

        with patch("codemie.enterprise.litellm.proxy_router._proxy_to_llm_proxy") as mock_proxy:
            mock_proxy.return_value = MagicMock()

            await handler(
                request=mock_request,
                background_tasks=mock_background_tasks,
                user=mock_user,
            )

        # Verify proxy was called with correct endpoint
        mock_proxy.assert_called_once()
        assert mock_proxy.call_args.kwargs["endpoint"] == endpoint

    def test_create_endpoint_with_path_params(self):
        """Test creating endpoint with path parameters extracts params correctly."""
        from codemie.enterprise.litellm.proxy_router import _create_proxy_endpoint
        import inspect

        # Test with single path parameter
        endpoint = "/v1/models/{model_name}:generateContent"
        handler = _create_proxy_endpoint(endpoint)

        # Verify handler is a function
        assert callable(handler)

        # Verify function signature includes the path parameter
        sig = inspect.signature(handler)
        params = list(sig.parameters.keys())
        assert "request" in params
        assert "background_tasks" in params
        assert "model_name" in params  # Path parameter should be in signature
        assert "user" in params

    def test_create_endpoint_with_multiple_path_params(self):
        """Test creating endpoint with multiple path parameters."""
        from codemie.enterprise.litellm.proxy_router import _create_proxy_endpoint
        import inspect

        # Test with multiple path parameters
        endpoint = "/v1beta/models/{model_name}:streamGenerateContent"
        handler = _create_proxy_endpoint(endpoint)

        # Verify handler is a function
        assert callable(handler)

        # Verify function signature
        sig = inspect.signature(handler)
        params = list(sig.parameters.keys())
        assert "model_name" in params


class TestRegisterProxyEndpoints:
    """Test register_proxy_endpoints function."""

    def test_register_when_disabled(self):
        """Test registration skipped when LiteLLM disabled."""
        with patch("codemie.enterprise.litellm.proxy_router.is_litellm_enabled", return_value=False):
            with patch("codemie.enterprise.litellm.proxy_router.proxy_router") as mock_router:
                register_proxy_endpoints()

                # Should not register any endpoints
                mock_router.add_api_route.assert_not_called()

    def test_register_when_enabled(self):
        """Test registration when LiteLLM enabled."""
        mock_endpoints = [
            {"path": "/v1/chat/completions", "methods": ["POST"]},
            {"path": "/v1/embeddings", "methods": ["POST"]},
            {"path": "/v1/models", "methods": ["GET"]},
        ]

        with patch("codemie.enterprise.litellm.proxy_router.is_litellm_enabled", return_value=True):
            with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
                mock_config.LITE_LLM_PROXY_ENDPOINTS = mock_endpoints
                with patch("codemie.enterprise.litellm.proxy_router.proxy_router") as mock_router:
                    register_proxy_endpoints()

                    # Should register all endpoints
                    assert mock_router.add_api_route.call_count == 3

    def test_register_with_invalid_config(self):
        """Test registration with invalid endpoint config."""
        mock_endpoints = [
            {"path": "/v1/chat/completions", "methods": ["POST"]},
            {"methods": ["POST"]},  # Missing path
            "invalid",  # Not a dict
        ]

        with patch("codemie.enterprise.litellm.proxy_router.is_litellm_enabled", return_value=True):
            with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
                mock_config.LITE_LLM_PROXY_ENDPOINTS = mock_endpoints
                with patch("codemie.enterprise.litellm.proxy_router.proxy_router") as mock_router:
                    register_proxy_endpoints()

                    # Should only register valid endpoint
                    assert mock_router.add_api_route.call_count == 1

    def test_register_with_error(self):
        """Test registration handles errors gracefully."""
        mock_endpoints = [
            {"path": "/v1/chat/completions", "methods": ["POST"]},
        ]

        with patch("codemie.enterprise.litellm.proxy_router.is_litellm_enabled", return_value=True):
            with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
                mock_config.LITE_LLM_PROXY_ENDPOINTS = mock_endpoints
                with patch("codemie.enterprise.litellm.proxy_router.proxy_router") as mock_router:
                    mock_router.add_api_route.side_effect = Exception("Registration failed")

                    # Should not raise exception
                    register_proxy_endpoints()

                    # Should have attempted registration
                    mock_router.add_api_route.assert_called_once()


class TestBuildPremiumBudgetErrorBody:
    """Tests for _build_premium_budget_error_body."""

    _BUDGET_EXCEEDED_BODY = (
        b'{"error":{"message":"ExceededBudget: End User=user@example.com_premium_models over budget.'
        b' Spend=300.16, Budget=300.0","type":"budget_exceeded","param":null,"code":"400"}}'
    )

    def _patch_config(self, budget_name: str = "premium_models", aliases: list | None = None):
        """Patch config for premium budget tests."""
        from codemie.enterprise.litellm import proxy_router

        return patch.object(
            proxy_router.config,
            "__class__",
            proxy_router.config.__class__,
        )

    def test_returns_none_when_budget_name_empty(self):
        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_cfg:
            mock_cfg.LITELLM_PREMIUM_MODELS_BUDGET_NAME = ""
            mock_cfg.LITELLM_PREMIUM_MODELS_ALIASES = ["opus"]

            result = _build_premium_budget_error_body(self._BUDGET_EXCEEDED_BODY)

        assert result is None

    def test_returns_none_for_non_json_body(self):
        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_cfg:
            mock_cfg.LITELLM_PREMIUM_MODELS_BUDGET_NAME = "premium_models"
            mock_cfg.LITELLM_PREMIUM_MODELS_ALIASES = ["opus"]

            result = _build_premium_budget_error_body(b"not json at all")

        assert result is None

    def test_returns_none_when_error_type_not_budget_exceeded(self):
        body = b'{"error":{"message":"rate limit hit","type":"rate_limit_error","code":"429"}}'
        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_cfg:
            mock_cfg.LITELLM_PREMIUM_MODELS_BUDGET_NAME = "premium_models"
            mock_cfg.LITELLM_PREMIUM_MODELS_ALIASES = ["opus"]

            result = _build_premium_budget_error_body(body)

        assert result is None

    def test_returns_none_when_end_user_is_regular_not_premium(self):
        # end_user is plain email, not the premium "{email}_{budget_name}" identity
        body = (
            b'{"error":{"message":"ExceededBudget: End User=user@example.com over budget.'
            b' Spend=10.0, Budget=5.0","type":"budget_exceeded","code":"400"}}'
        )
        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_cfg:
            mock_cfg.LITELLM_PREMIUM_MODELS_BUDGET_NAME = "premium_models"
            mock_cfg.LITELLM_PREMIUM_MODELS_ALIASES = ["opus"]

            result = _build_premium_budget_error_body(body)

        assert result is None

    def test_returns_friendly_message_for_premium_budget_exceeded(self):
        with patch(
            "codemie.enterprise.litellm.proxy_router.get_category_budget_id",
            return_value="premium_models",
        ):
            with patch("codemie.enterprise.litellm.proxy_router.config") as mock_cfg:
                mock_cfg.LITELLM_PREMIUM_MODELS_ALIASES = ["opus", "claude-opus-4"]

                result = _build_premium_budget_error_body(self._BUDGET_EXCEEDED_BODY)

        assert result is not None
        data = json.loads(result)
        assert data["error"]["type"] == "budget_exceeded"
        assert data["error"]["code"] == "400"
        msg = data["error"]["message"]
        assert "opus" in msg
        assert "claude-opus-4" in msg
        assert "regular models" in msg
        assert "codemie setup" in msg
        assert "--model" in msg
        assert "https://docs.codemie.ai/user-guide/codemie-cli/" in msg

    def test_friendly_message_lists_all_premium_aliases(self):
        with patch(
            "codemie.enterprise.litellm.proxy_router.get_category_budget_id",
            return_value="premium_models",
        ):
            with patch("codemie.enterprise.litellm.proxy_router.config") as mock_cfg:
                mock_cfg.LITELLM_PREMIUM_MODELS_ALIASES = ["model-a", "model-b", "model-c"]

                result = _build_premium_budget_error_body(self._BUDGET_EXCEEDED_BODY)

        data = json.loads(result)
        msg = data["error"]["message"]
        assert "model-a" in msg
        assert "model-b" in msg
        assert "model-c" in msg

    def test_friendly_message_when_aliases_empty(self):
        with patch(
            "codemie.enterprise.litellm.proxy_router.get_category_budget_id",
            return_value="premium_models",
        ):
            with patch("codemie.enterprise.litellm.proxy_router.config") as mock_cfg:
                mock_cfg.LITELLM_PREMIUM_MODELS_ALIASES = []

                result = _build_premium_budget_error_body(self._BUDGET_EXCEEDED_BODY)

        data = json.loads(result)
        assert "premium models" in data["error"]["message"]

    def test_returns_none_when_error_field_is_not_dict(self):
        body = b'{"error":"something went wrong"}'
        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_cfg:
            mock_cfg.LITELLM_PREMIUM_MODELS_BUDGET_NAME = "premium_models"
            mock_cfg.LITELLM_PREMIUM_MODELS_ALIASES = ["opus"]

            result = _build_premium_budget_error_body(body)

        assert result is None


class TestHandleErrorResponse:
    """Tests for _handle_error_response."""

    @pytest.mark.asyncio
    async def test_passthrough_non_budget_error(self):
        """Non-budget error bodies are forwarded unchanged."""
        original_body = b'{"error":{"message":"rate limit","type":"rate_limit_error","code":"429"}}'
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = httpx.Headers({"content-type": "application/json"})
        mock_response.aread = AsyncMock(return_value=original_body)
        mock_response.aclose = AsyncMock()

        with patch("codemie.enterprise.litellm.proxy_router.is_premium_models_enabled", return_value=True):
            with patch("codemie.enterprise.litellm.proxy_router.config") as mock_cfg:
                mock_cfg.LITELLM_PREMIUM_MODELS_BUDGET_NAME = "premium_models"
                mock_cfg.LITELLM_PREMIUM_MODELS_ALIASES = ["opus"]

                result = await _handle_error_response(mock_response, {})

        assert result.status_code == 429
        assert result.body == original_body

    @pytest.mark.asyncio
    async def test_replaces_premium_budget_exceeded_error(self):
        """Premium budget exceeded error body is replaced with a friendly message."""
        original_body = (
            b'{"error":{"message":"ExceededBudget: End User=user@example.com_premium_models over budget.'
            b' Spend=300.16, Budget=300.0","type":"budget_exceeded","param":null,"code":"400"}}'
        )
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.headers = httpx.Headers({"content-type": "application/json"})
        mock_response.aread = AsyncMock(return_value=original_body)
        mock_response.aclose = AsyncMock()

        with patch("codemie.enterprise.litellm.proxy_router.is_premium_models_enabled", return_value=True):
            with patch(
                "codemie.enterprise.litellm.proxy_router.get_category_budget_id",
                return_value="premium_models",
            ):
                with patch("codemie.enterprise.litellm.proxy_router.config") as mock_cfg:
                    mock_cfg.LITELLM_PREMIUM_MODELS_ALIASES = ["claude-opus-4", "opus"]

                    result = await _handle_error_response(mock_response, {})

        assert result.status_code == 400
        data = json.loads(result.body)
        msg = data["error"]["message"]
        assert "claude-opus-4" in msg
        assert "regular models" in msg
        assert "codemie setup" in msg
        assert "--model" in msg
        assert "https://docs.codemie.ai/user-guide/codemie-cli/" in msg
        # Must not expose the raw LiteLLM internal message
        assert "ExceededBudget" not in msg

    @pytest.mark.asyncio
    async def test_rewritten_error_drops_stale_content_headers(self):
        """Locally rewritten bodies must not reuse upstream Content-Length/Encoding headers."""
        original_body = (
            b'{"error":{"message":"ExceededBudget: End User=user@example.com_premium_models over budget.'
            b' Spend=300.16, Budget=300.0","type":"budget_exceeded","param":null,"code":"400"}}'
        )
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.headers = httpx.Headers({"content-type": "application/json"})
        mock_response.aread = AsyncMock(return_value=original_body)
        mock_response.aclose = AsyncMock()
        response_headers = {
            "content-type": "application/json",
            "content-length": "17",
            "content-encoding": "gzip",
            "x-test-header": "kept",
        }

        with patch("codemie.enterprise.litellm.proxy_router.is_premium_models_enabled", return_value=True):
            with patch("codemie.enterprise.litellm.proxy_router.config") as mock_cfg:
                mock_cfg.LITELLM_PREMIUM_MODELS_BUDGET_NAME = "premium_models"
                mock_cfg.LITELLM_PREMIUM_MODELS_ALIASES = ["claude-opus-4", "opus"]

                result = await _handle_error_response(mock_response, response_headers)

        assert result.headers.get("content-length") != "17"
        assert "content-encoding" not in result.headers
        assert result.headers["x-test-header"] == "kept"

    @pytest.mark.asyncio
    async def test_passthrough_when_premium_disabled(self):
        """When premium feature is disabled, error body is never replaced."""
        original_body = (
            b'{"error":{"message":"ExceededBudget: End User=user@example.com_premium_models over budget.'
            b' Spend=300.16, Budget=300.0","type":"budget_exceeded","param":null,"code":"400"}}'
        )
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.headers = httpx.Headers({"content-type": "application/json"})
        mock_response.aread = AsyncMock(return_value=original_body)
        mock_response.aclose = AsyncMock()

        with patch("codemie.enterprise.litellm.proxy_router.is_premium_models_enabled", return_value=False):
            result = await _handle_error_response(mock_response, {})

        assert result.body == original_body

    @pytest.mark.asyncio
    async def test_passthrough_error_drops_stale_content_headers(self):
        """Even passthrough error bodies are rebuilt locally and need fresh framing headers."""
        original_body = b'{"error":{"message":"rate limit","type":"rate_limit_error","code":"429"}}'
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = httpx.Headers({"content-type": "application/json"})
        mock_response.aread = AsyncMock(return_value=original_body)
        mock_response.aclose = AsyncMock()
        response_headers = {
            "content-type": "application/json",
            "content-length": "7",
            "content-encoding": "gzip",
            "x-test-header": "kept",
        }

        with patch("codemie.enterprise.litellm.proxy_router.is_premium_models_enabled", return_value=False):
            result = await _handle_error_response(mock_response, response_headers)

        assert result.body == original_body
        assert result.headers.get("content-length") != "7"
        assert "content-encoding" not in result.headers
        assert result.headers["x-test-header"] == "kept"

    @pytest.mark.asyncio
    async def test_closes_downstream_on_read_error(self):
        """Downstream response is closed even when body read fails."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.headers = httpx.Headers({"content-type": "application/json"})
        mock_response.aread = AsyncMock(side_effect=Exception("read error"))
        mock_response.aclose = AsyncMock()

        with patch("codemie.enterprise.litellm.proxy_router.is_premium_models_enabled", return_value=False):
            result = await _handle_error_response(mock_response, {})

        mock_response.aclose.assert_called_once()
        assert result.status_code == 500


class TestCheckCliVersion:
    def _make_request(self, headers: dict) -> MagicMock:
        """Return a minimal mock Request with the given headers."""
        mock_request = MagicMock()
        mock_request.headers = Headers(headers)
        return mock_request

    def test_check_disabled_when_min_version_empty(self):
        """No exception when CODEMIE_MIN_CLI_VERSION is empty (feature disabled)."""
        request = self._make_request({})
        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
            mock_config.CODEMIE_MIN_CLI_VERSION = ""
            _check_cli_version(request)  # must not raise

    def test_lower_version_is_rejected(self):
        """CLI version below minimum → HTTP 426."""
        request = self._make_request({HEADER_CODEMIE_CLI: "0.9.0"})
        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
            mock_config.CODEMIE_MIN_CLI_VERSION = "1.0.0"
            with pytest.raises(HTTPException) as exc_info:
                _check_cli_version(request)
        assert exc_info.value.status_code == 426
        assert "0.9.0" in exc_info.value.detail
        assert "1.0.0" in exc_info.value.detail

    def test_equal_version_is_allowed(self):
        """CLI version equal to minimum → allowed (no exception)."""
        request = self._make_request({HEADER_CODEMIE_CLI: "1.0.0"})
        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
            mock_config.CODEMIE_MIN_CLI_VERSION = "1.0.0"
            _check_cli_version(request)  # must not raise

    def test_higher_version_is_allowed(self):
        """CLI version above minimum → allowed (no exception)."""
        request = self._make_request({HEADER_CODEMIE_CLI: "2.3.1"})
        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
            mock_config.CODEMIE_MIN_CLI_VERSION = "1.0.0"
            _check_cli_version(request)  # must not raise

    def test_missing_header_is_allowed(self):
        """Missing X-CodeMie-CLI header → non-CLI request, allowed even when min version configured."""
        request = self._make_request({})
        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
            mock_config.CODEMIE_MIN_CLI_VERSION = "1.0.0"
            _check_cli_version(request)  # must not raise — non-CLI client

    def test_invalid_version_format_is_rejected(self):
        """Invalid/unparseable version string → HTTP 426."""
        request = self._make_request({HEADER_CODEMIE_CLI: "not-a-version"})
        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
            mock_config.CODEMIE_MIN_CLI_VERSION = "1.0.0"
            with pytest.raises(HTTPException) as exc_info:
                _check_cli_version(request)
        assert exc_info.value.status_code == 426

    def test_misconfigured_min_version_raises_server_error(self):
        """Invalid CODEMIE_MIN_CLI_VERSION in config → server error (not a 426 aimed at the client)."""
        from packaging.version import InvalidVersion

        request = self._make_request({HEADER_CODEMIE_CLI: "1.0.0"})
        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
            mock_config.CODEMIE_MIN_CLI_VERSION = "not-a-version"
            with pytest.raises(InvalidVersion):
                _check_cli_version(request)

    def test_slash_format_valid_version_is_allowed(self):
        """Header in 'codemie-cli/X.Y.Z' format with sufficient version → allowed."""
        request = self._make_request({HEADER_CODEMIE_CLI: "codemie-cli/1.5.0"})
        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
            mock_config.CODEMIE_MIN_CLI_VERSION = "1.0.0"
            _check_cli_version(request)  # must not raise

    def test_slash_format_low_version_is_rejected(self):
        """Header in 'codemie-cli/X.Y.Z' format with version below minimum → HTTP 426."""
        request = self._make_request({HEADER_CODEMIE_CLI: "codemie-cli/0.5.0"})
        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
            mock_config.CODEMIE_MIN_CLI_VERSION = "1.0.0"
            with pytest.raises(HTTPException) as exc_info:
                _check_cli_version(request)
        assert exc_info.value.status_code == 426

    def test_slash_format_empty_version_is_rejected(self):
        """Header 'codemie-cli/' (slash with empty version part) → HTTP 426, not 500."""
        request = self._make_request({HEADER_CODEMIE_CLI: "codemie-cli/"})
        with patch("codemie.enterprise.litellm.proxy_router.config") as mock_config:
            mock_config.CODEMIE_MIN_CLI_VERSION = "1.0.0"
            with pytest.raises(HTTPException) as exc_info:
                _check_cli_version(request)
        assert exc_info.value.status_code == 426
