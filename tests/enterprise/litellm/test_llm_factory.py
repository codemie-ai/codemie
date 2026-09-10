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

"""Tests for LiteLLM LLM factory (codemie.enterprise.litellm.llm_factory)."""

import pytest

from unittest.mock import MagicMock, patch


class TestGenerateLiteLLMHeadersFromContext:
    """Test generate_litellm_headers_from_context() function."""

    def test_returns_default_when_no_context(self):
        """Test returns default tag when context is None."""
        from codemie.configs.config import config

        with patch.object(config, "LITE_LLM_TAGS_HEADER_VALUE", "default-tag"):
            from codemie.enterprise.litellm.llm_factory import generate_litellm_headers_from_context

            result = generate_litellm_headers_from_context(None)

            assert result == {"x-litellm-tags": "default-tag"}

    def test_returns_project_name_when_in_allowed_list(self):
        """Test returns project name when it's in allowed list."""
        from codemie.configs.config import config
        from codemie.rest_api.models.settings import LiteLLMContext, LiteLLMCredentials

        context = LiteLLMContext(
            credentials=LiteLLMCredentials(api_key="test", url="http://test"),
            current_project="project-1",
        )

        with patch.object(config, "LITE_LLM_PROJECTS_TO_TAGS_LIST", "project-1,project-2"):
            with patch.object(config, "LITE_LLM_TAGS_HEADER_VALUE", "default"):
                from codemie.enterprise.litellm.llm_factory import generate_litellm_headers_from_context

                result = generate_litellm_headers_from_context(context)

                assert result == {"x-litellm-tags": "project-1"}

    def test_returns_default_when_project_not_in_allowed_list(self):
        """Test returns default when project not in allowed list."""
        from codemie.configs.config import config
        from codemie.rest_api.models.settings import LiteLLMContext, LiteLLMCredentials

        context = LiteLLMContext(
            credentials=LiteLLMCredentials(api_key="test", url="http://test"),
            current_project="project-3",
        )

        with patch.object(config, "LITE_LLM_PROJECTS_TO_TAGS_LIST", "project-1,project-2"):
            with patch.object(config, "LITE_LLM_TAGS_HEADER_VALUE", "default"):
                from codemie.enterprise.litellm.llm_factory import generate_litellm_headers_from_context

                result = generate_litellm_headers_from_context(context)

                assert result == {"x-litellm-tags": "default"}

    def test_no_codemie_tagging_headers_in_litellm_path(self):
        """_generate_litellm_headers must produce x-litellm-tags but not X-CodeMie-* tags."""
        from codemie.configs.config import config
        from codemie.rest_api.models.settings import LiteLLMContext, LiteLLMCredentials
        from codemie.enterprise.litellm.llm_factory import _generate_litellm_headers

        ctx = LiteLLMContext(
            credentials=LiteLLMCredentials(api_key="k", url="http://test"),
            current_project="test-project",
        )
        mock_model = MagicMock()
        mock_model.configuration = None

        with (
            patch.object(config, "LITE_LLM_TAGS_HEADER_VALUE", "default"),
            patch.object(config, "LITE_LLM_PROJECTS_TO_TAGS_LIST", ""),
        ):
            headers = _generate_litellm_headers(mock_model, ctx)

        assert "x-litellm-tags" in headers
        assert "X-CodeMie-Version" not in headers
        assert "X-CodeMie-Project" not in headers


class TestCreateLiteLLMChatModel:
    """Test create_litellm_chat_model() function."""

    def test_checks_budget_when_no_credentials(self):
        """Test checks user budget when user doesn't have own credentials."""
        from codemie.configs.config import config

        mock_model_details = MagicMock()
        mock_model_details.base_name = "gpt-4"
        mock_model_details.configuration = None
        mock_model_details.features.streaming = True
        mock_model_details.features.temperature = True
        mock_model_details.features.parallel_tool_calls = True
        mock_model_details.features.max_tokens = True
        mock_model_details.features.top_p = True
        mock_model_details.api_version = None

        with patch.object(config, "LITE_LLM_URL", "http://test:4000"):
            with patch.object(config, "LITE_LLM_APP_KEY", "test-key"):
                with patch.object(config, "OPENAI_API_VERSION", "2025-04-01-preview"):
                    with patch.object(config, "OPENAI_API_TYPE", "azure"):
                        with patch.object(config, "AZURE_OPENAI_MAX_RETRIES", 3):
                            with patch("codemie.enterprise.litellm.llm_factory.LiteLLMChatOpenAI"):
                                with patch(
                                    "codemie.enterprise.litellm.dependencies.check_user_budget"
                                ) as mock_check_budget:
                                    from codemie.enterprise.litellm.llm_factory import create_litellm_chat_model

                                    create_litellm_chat_model(
                                        llm_model_details=mock_model_details,
                                        litellm_context=None,  # No credentials = use budget check
                                        user_email="test@example.com",
                                    )

                                    # Should have checked budget
                                    mock_check_budget.assert_called_once_with(
                                        user_email="test@example.com", user_id=None, budget_id="default"
                                    )

    def test_skips_budget_check_when_has_credentials(self):
        """Test skips budget check when user has own credentials."""
        from codemie.configs.config import config
        from codemie.rest_api.models.settings import LiteLLMCredentials, LiteLLMContext

        creds = LiteLLMCredentials(api_key="user-key", url="http://test:4000")
        litellm_context = LiteLLMContext(credentials=creds, current_project="test-project")

        mock_model_details = MagicMock()
        mock_model_details.base_name = "gpt-4"
        mock_model_details.configuration = None
        mock_model_details.features.streaming = True
        mock_model_details.features.temperature = True
        mock_model_details.features.parallel_tool_calls = True
        mock_model_details.features.max_tokens = True
        mock_model_details.features.top_p = True
        mock_model_details.api_version = None

        with patch.object(config, "LITE_LLM_URL", "http://test:4000"):
            with patch.object(config, "OPENAI_API_VERSION", "2025-04-01-preview"):
                with patch.object(config, "OPENAI_API_TYPE", "azure"):
                    with patch.object(config, "AZURE_OPENAI_MAX_RETRIES", 3):
                        with patch.object(config, "LITE_LLM_TAGS_HEADER_VALUE", "default"):
                            with patch.object(config, "LITE_LLM_PROJECTS_TO_TAGS_LIST", ""):
                                with patch("codemie.enterprise.litellm.llm_factory.LiteLLMChatOpenAI"):
                                    with patch(
                                        "codemie.enterprise.litellm.dependencies.check_user_budget"
                                    ) as mock_check_budget:
                                        from codemie.enterprise.litellm.llm_factory import create_litellm_chat_model

                                        create_litellm_chat_model(
                                            llm_model_details=mock_model_details,
                                            litellm_context=litellm_context,  # Has credentials = skip budget check
                                            user_email="test@example.com",
                                        )

                                        # Should NOT have checked budget
                                        mock_check_budget.assert_not_called()

    def test_skips_user_injection_when_has_personal_credentials(self):
        """Personal-key bypass must not inject model_kwargs['user'] even when user_id and project are present."""
        from codemie.configs.config import config
        from codemie.rest_api.models.settings import LiteLLMCredentials, LiteLLMContext

        creds = LiteLLMCredentials(api_key="personal-key", url="http://personal:4000")
        litellm_context = LiteLLMContext(credentials=creds, current_project="test-project")

        mock_model_details = MagicMock()
        mock_model_details.base_name = "gpt-4"
        mock_model_details.configuration = None
        mock_model_details.features.streaming = True
        mock_model_details.features.temperature = True
        mock_model_details.features.parallel_tool_calls = True
        mock_model_details.features.max_tokens = True
        mock_model_details.features.top_p = True
        mock_model_details.api_version = None

        with patch.object(config, "LITE_LLM_URL", "http://test:4000"):
            with patch.object(config, "OPENAI_API_VERSION", "2025-04-01-preview"):
                with patch.object(config, "OPENAI_API_TYPE", "azure"):
                    with patch.object(config, "AZURE_OPENAI_MAX_RETRIES", 3):
                        with patch.object(config, "LITE_LLM_TAGS_HEADER_VALUE", "default"):
                            with patch.object(config, "LITE_LLM_PROJECTS_TO_TAGS_LIST", ""):
                                with patch(
                                    "codemie.enterprise.litellm.llm_factory._resolve_direct_project_budget_runtime"
                                ) as mock_resolver:
                                    # Return a non-None user so the bug would inject it before the fix
                                    mock_resolver.return_value = ("project-member-123", {}, None, None, True)
                                    with patch(
                                        "codemie.enterprise.litellm.llm_factory.LiteLLMChatOpenAI"
                                    ) as mock_model_cls:
                                        from codemie.enterprise.litellm.llm_factory import create_litellm_chat_model

                                        create_litellm_chat_model(
                                            llm_model_details=mock_model_details,
                                            litellm_context=litellm_context,
                                            user_email="test@example.com",
                                            user_id="user-123",
                                        )

                                        # Primary: resolver must not be called in personal-key bypass mode
                                        mock_resolver.assert_not_called()
                                        # Secondary: model_kwargs must not carry a "user" value
                                        call_kwargs = mock_model_cls.call_args.kwargs
                                        assert call_kwargs.get("model_kwargs", {}).get("user") is None

    def test_sets_customer_header_without_user_when_no_personal_credentials(self):
        """Member tracking must use the customer header without setting the provider JSON user."""
        from codemie.configs.config import config
        from codemie.enterprise.litellm.constants import LITELLM_CUSTOMER_ID_HEADER
        from codemie.rest_api.models.settings import LiteLLMContext

        # No personal key (credentials=None) but project context present so member-tracking runs
        litellm_context = LiteLLMContext(credentials=None, current_project="test-project")

        mock_model_details = MagicMock()
        mock_model_details.base_name = "gpt-4"
        mock_model_details.configuration = None
        mock_model_details.features.streaming = True
        mock_model_details.features.temperature = True
        mock_model_details.features.parallel_tool_calls = True
        mock_model_details.features.max_tokens = True
        mock_model_details.features.top_p = True
        mock_model_details.api_version = None
        provider_member_ref = (
            "codemie:project:a-project-name-that-makes-the-id-too-long:category:platform:user:"
            "12345678-1234-1234-1234-123456789012"
        )

        with patch.object(config, "LITE_LLM_URL", "http://test:4000"):
            with patch.object(config, "LITE_LLM_APP_KEY", "test-key"):
                with patch.object(config, "OPENAI_API_VERSION", "2025-04-01-preview"):
                    with patch.object(config, "OPENAI_API_TYPE", "azure"):
                        with patch.object(config, "AZURE_OPENAI_MAX_RETRIES", 3):
                            with patch.object(config, "LITE_LLM_TAGS_HEADER_VALUE", "default"):
                                with patch.object(config, "LITE_LLM_PROJECTS_TO_TAGS_LIST", ""):
                                    with patch(
                                        "codemie.enterprise.litellm.llm_factory._resolve_direct_project_budget_runtime",
                                        return_value=(
                                            None,
                                            {LITELLM_CUSTOMER_ID_HEADER: provider_member_ref},
                                            None,
                                            None,
                                            True,
                                        ),
                                    ):
                                        with patch(
                                            "codemie.enterprise.litellm.llm_factory.LiteLLMChatOpenAI"
                                        ) as mock_model_cls:
                                            from codemie.enterprise.litellm.llm_factory import create_litellm_chat_model

                                            create_litellm_chat_model(
                                                llm_model_details=mock_model_details,
                                                litellm_context=litellm_context,
                                                user_email="test@example.com",
                                                user_id="user-123",
                                            )

                                            call_kwargs = mock_model_cls.call_args.kwargs
                                            assert len(provider_member_ref) > 64
                                            assert call_kwargs.get("model_kwargs", {}).get("user") is None
                                            assert call_kwargs["default_headers"][LITELLM_CUSTOMER_ID_HEADER] == (
                                                provider_member_ref
                                            )


class TestGetLiteLLMChatModel:
    """Test get_litellm_chat_model() wrapper function."""

    def test_returns_none_when_litellm_not_enabled(self):
        """Test returns None when LiteLLM not enabled."""

        mock_model_details = MagicMock()

        with patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=False):
            from codemie.enterprise.litellm.llm_factory import get_litellm_chat_model

            result = get_litellm_chat_model(
                llm_model_details=mock_model_details,
                litellm_context=None,
                user_email="test@example.com",
            )

            assert result is None

    def test_calls_create_function_when_enabled(self):
        """Test calls create_litellm_chat_model when enabled and proxy mode is lite_llm."""
        from codemie.configs.config import config

        mock_model = MagicMock()
        mock_model_details = MagicMock()

        with patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=True):
            with patch.object(config, "LLM_PROXY_MODE", "lite_llm"):
                with patch(
                    "codemie.enterprise.litellm.llm_factory.create_litellm_chat_model", return_value=mock_model
                ) as mock_create:
                    from codemie.enterprise.litellm.llm_factory import get_litellm_chat_model

                    result = get_litellm_chat_model(
                        llm_model_details=mock_model_details,
                        litellm_context=None,
                        user_email="test@example.com",
                    )

                    assert result is mock_model
                    mock_create.assert_called_once()


class TestCreateLiteLLMEmbeddingModel:
    """Test create_litellm_embedding_model() function."""

    def test_checks_budget_for_embedding_model(self):
        """Test checks user budget for embedding model when no credentials."""
        from codemie.configs.config import config

        mock_model_details = MagicMock()
        mock_model_details.base_name = "text-embedding-ada-002"
        mock_model_details.configuration = None

        with patch.object(config, "LITE_LLM_URL", "http://test:4000"):
            with patch.object(config, "LITE_LLM_APP_KEY", "test-key"):
                with patch.object(config, "OPENAI_API_TYPE", "azure"):
                    with patch.object(config, "OPENAI_API_VERSION", "2025-04-01-preview"):
                        with patch.object(config, "LITE_LLM_TAGS_HEADER_VALUE", "default"):
                            with patch.object(config, "LITE_LLM_PROJECTS_TO_TAGS_LIST", ""):
                                with patch("langchain_openai.AzureOpenAIEmbeddings"):
                                    with patch(
                                        "codemie.enterprise.litellm.dependencies.check_user_budget"
                                    ) as mock_check_budget:
                                        from codemie.enterprise.litellm.llm_factory import (
                                            create_litellm_embedding_model,
                                        )

                                        create_litellm_embedding_model(
                                            embedding_model="text-embedding-ada-002",
                                            llm_model_details=mock_model_details,
                                            litellm_context=None,  # No credentials = use budget check
                                            user_email="test@example.com",
                                        )

                                        # Should have checked budget
                                        mock_check_budget.assert_called_once_with(
                                            user_email="test@example.com", user_id=None, budget_id="default"
                                        )

    def test_project_member_uses_customer_header_without_user(self):
        """Embedding project attribution must not set the provider JSON user."""
        from codemie.configs.config import config
        from codemie.enterprise.litellm.constants import LITELLM_CUSTOMER_ID_HEADER
        from codemie.rest_api.models.settings import LiteLLMContext

        litellm_context = LiteLLMContext(credentials=None, current_project="test-project")
        mock_model_details = MagicMock()
        mock_model_details.base_name = "text-embedding-ada-002"
        mock_model_details.configuration = None
        provider_member_ref = (
            "codemie:project:a-project-name-that-makes-the-id-too-long:category:platform:user:"
            "12345678-1234-1234-1234-123456789012"
        )

        with (
            patch.object(config, "LITE_LLM_URL", "http://test:4000"),
            patch.object(config, "LITE_LLM_APP_KEY", "test-key"),
            patch.object(config, "OPENAI_API_TYPE", "azure"),
            patch.object(config, "OPENAI_API_VERSION", "2025-04-01-preview"),
            patch.object(config, "LITE_LLM_TAGS_HEADER_VALUE", "default"),
            patch.object(config, "LITE_LLM_PROJECTS_TO_TAGS_LIST", ""),
            patch(
                "codemie.enterprise.litellm.llm_factory._resolve_direct_project_budget_runtime",
                return_value=(
                    None,
                    {LITELLM_CUSTOMER_ID_HEADER: provider_member_ref},
                    None,
                    None,
                    True,
                ),
            ),
            patch("codemie.enterprise.litellm.llm_factory.LiteLLMAzureOpenAIEmbeddings") as mock_model_cls,
        ):
            from codemie.enterprise.litellm.llm_factory import create_litellm_embedding_model

            create_litellm_embedding_model(
                embedding_model="text-embedding-ada-002",
                llm_model_details=mock_model_details,
                litellm_context=litellm_context,
                user_email="test@example.com",
                user_id="12345678-1234-1234-1234-123456789012",
            )

        call_kwargs = mock_model_cls.call_args.kwargs
        assert len(provider_member_ref) > 64
        assert call_kwargs.get("model_kwargs", {}).get("user") is None
        assert call_kwargs["default_headers"][LITELLM_CUSTOMER_ID_HEADER] == provider_member_ref


class TestResolveDirectProjectBudgetRuntime:
    """Test _resolve_direct_project_budget_runtime() direct budget path behavior."""

    def test_project_platform_only_suppresses_global_premium_selection(self):
        from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory
        from codemie.enterprise.litellm.llm_factory import DirectBudgetAvailability, _resolve_direct_budget_category

        availability = DirectBudgetAvailability(
            user_budget_ids={
                CoreBudgetCategory.PLATFORM.value: None,
                CoreBudgetCategory.PREMIUM_MODELS.value: None,
            },
            project_scopes={CoreBudgetCategory.PLATFORM},
        )

        with patch(
            "codemie.enterprise.litellm.dependencies.get_premium_username",
            return_value="user@example.com_codemie_premium_models",
        ):
            with patch(
                "codemie.enterprise.litellm.dependencies.get_category_budget_id",
                return_value="global-premium-budget",
            ):
                category = _resolve_direct_budget_category(
                    user_email="user@example.com",
                    llm_model="claude-opus-4-6-20260205",
                    availability=availability,
                )

        assert category == CoreBudgetCategory.PLATFORM

    def test_project_premium_scope_selects_premium_category(self):
        from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory
        from codemie.enterprise.litellm.llm_factory import DirectBudgetAvailability, _resolve_direct_budget_category

        availability = DirectBudgetAvailability(
            user_budget_ids={
                CoreBudgetCategory.PLATFORM.value: None,
                CoreBudgetCategory.PREMIUM_MODELS.value: None,
            },
            project_scopes={CoreBudgetCategory.PLATFORM, CoreBudgetCategory.PREMIUM_MODELS},
        )

        with patch(
            "codemie.enterprise.litellm.dependencies.get_premium_username",
            return_value="user@example.com_codemie_premium_models",
        ):
            category = _resolve_direct_budget_category(
                user_email="user@example.com",
                llm_model="claude-opus-4-6-20260205",
                availability=availability,
            )

        assert category == CoreBudgetCategory.PREMIUM_MODELS

    def test_project_scope_probe_uses_resolution_cache_when_warm(self):
        from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory
        from codemie.service.budget.budget_resolution_service import (
            BudgetScope,
            ResolvedBudgetContext,
            _resolution_cache,
            clear_budget_resolution_cache,
        )
        from codemie.enterprise.litellm.llm_factory import _probe_direct_project_budget_scopes

        clear_budget_resolution_cache()
        _resolution_cache[("project-1", CoreBudgetCategory.PLATFORM.value, "user-1")] = ResolvedBudgetContext(
            scope=BudgetScope.PROJECT,
            project_name="project-1",
            budget_category=CoreBudgetCategory.PLATFORM,
            budget_id="platform-budget",
        )
        _resolution_cache[("project-1", CoreBudgetCategory.CLI.value, "user-1")] = None
        _resolution_cache[("project-1", CoreBudgetCategory.PREMIUM_MODELS.value, "user-1")] = None

        with patch("codemie.clients.postgres.PostgresClient.get_engine") as mock_get_engine:
            scopes = _probe_direct_project_budget_scopes("project-1", "user-1")

        assert scopes == {CoreBudgetCategory.PLATFORM}
        mock_get_engine.assert_not_called()

    def test_calls_sync_before_resolve_and_dispatch_and_returns_provider_runtime(self):
        """Sync helper must run before budget resolve/dispatch and preserve provider values."""
        from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory
        from codemie.enterprise.litellm.llm_factory import (
            DirectBudgetAvailability,
            _resolve_direct_project_budget_runtime,
        )

        model_details = MagicMock()
        model_details.base_name = "gpt-4.1"

        litellm_context = MagicMock()
        litellm_context.current_project = "project-1"

        resolved = MagicMock()
        provider_result = MagicMock()
        provider_result.body_overrides = {}
        provider_result.headers = {
            "x-budget-runtime": "true",
            "x-litellm-customer-id": "runtime-user",
        }
        provider_result.api_key = "runtime-api-key"
        provider_result.base_url = "https://runtime.example"

        execution_order: list[str] = []

        def _helper_side_effect(**_: str) -> None:
            execution_order.append("sync")

        def _resolve_side_effect(**_: str) -> MagicMock:
            execution_order.append("resolve")
            return resolved

        def _dispatch_side_effect(*args: object, **kwargs: str) -> MagicMock:
            assert args[0] is resolved
            execution_order.append("dispatch")
            return provider_result

        with patch(
            "codemie.enterprise.litellm.llm_factory._resolve_direct_budget_availability",
            return_value=DirectBudgetAvailability(
                user_budget_ids={CoreBudgetCategory.PLATFORM.value: None},
                project_scopes={CoreBudgetCategory.PLATFORM},
            ),
        ):
            with patch(
                "codemie.enterprise.litellm.llm_factory.ensure_project_member_runtime_ready_sync",
                side_effect=_helper_side_effect,
            ) as mock_sync:
                with patch(
                    "codemie.service.budget.budget_resolution_service.budget_resolution_service.resolve_sync",
                    side_effect=_resolve_side_effect,
                ) as mock_resolve:
                    with patch(
                        "codemie.service.budget.budget_resolution_service.budget_resolution_service.dispatch_runtime_sync",
                        side_effect=_dispatch_side_effect,
                    ) as mock_dispatch:
                        result = _resolve_direct_project_budget_runtime(
                            llm_model_details=model_details,
                            litellm_context=litellm_context,
                            user_id="user-1",
                            user_email="user@example.com",
                        )

        assert execution_order == ["sync", "resolve", "dispatch"]
        assert result == (
            None,
            {"x-budget-runtime": "true", "x-litellm-customer-id": "runtime-user"},
            "runtime-api-key",
            "https://runtime.example",
            True,
        )
        mock_sync.assert_called_once_with(
            user_id="user-1",
            user_email="user@example.com",
            project_name="project-1",
            budget_category=CoreBudgetCategory.PLATFORM,
        )
        mock_resolve.assert_called_once_with(
            user_id="user-1",
            project_name="project-1",
            budget_category=CoreBudgetCategory.PLATFORM,
        )
        mock_dispatch.assert_called_once_with(
            resolved,
            user_id="user-1",
            user_email="user@example.com",
            model="gpt-4.1",
        )

    def test_helper_failure_bubbles_up(self):
        """Sync helper failures must not be swallowed."""
        from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory
        from codemie.enterprise.litellm.llm_factory import (
            DirectBudgetAvailability,
            _resolve_direct_project_budget_runtime,
        )

        model_details = MagicMock()
        model_details.base_name = "gpt-4.1"

        litellm_context = MagicMock()
        litellm_context.current_project = "project-1"

        with patch(
            "codemie.enterprise.litellm.llm_factory._resolve_direct_budget_availability",
            return_value=DirectBudgetAvailability(
                user_budget_ids={CoreBudgetCategory.PLATFORM.value: None},
                project_scopes={CoreBudgetCategory.PLATFORM},
            ),
        ):
            with patch(
                "codemie.enterprise.litellm.llm_factory.ensure_project_member_runtime_ready_sync",
                side_effect=RuntimeError("sync failed"),
            ):
                with patch(
                    "codemie.service.budget.budget_resolution_service.budget_resolution_service.resolve_sync"
                ) as mock_resolve:
                    with patch(
                        "codemie.service.budget.budget_resolution_service.budget_resolution_service.dispatch_runtime_sync"
                    ) as mock_dispatch:
                        with pytest.raises(RuntimeError, match="sync failed"):
                            _resolve_direct_project_budget_runtime(
                                llm_model_details=model_details,
                                litellm_context=litellm_context,
                                user_id="user-1",
                                user_email="user@example.com",
                            )

        mock_resolve.assert_not_called()
        mock_dispatch.assert_not_called()

    @pytest.mark.parametrize("missing_field", ["context", "project", "user_id", "user_email"])
    def test_early_return_when_required_inputs_missing(self, missing_field: str):
        """Missing context/project/user inputs must keep the existing early return behavior."""
        from codemie.enterprise.litellm.llm_factory import _resolve_direct_project_budget_runtime

        model_details = MagicMock()
        model_details.base_name = "gpt-4.1"

        litellm_context = MagicMock()
        litellm_context.current_project = "project-1"
        user_id: str | None = "user-1"
        user_email: str | None = "user@example.com"

        if missing_field == "context":
            litellm_context = None
        if missing_field == "project":
            litellm_context.current_project = None
        if missing_field == "user_id":
            user_id = None
        if missing_field == "user_email":
            user_email = None

        with patch("codemie.enterprise.litellm.llm_factory.ensure_project_member_runtime_ready_sync") as mock_sync:
            with patch(
                "codemie.service.budget.budget_resolution_service.budget_resolution_service.resolve_sync"
            ) as mock_resolve:
                with patch(
                    "codemie.service.budget.budget_resolution_service.budget_resolution_service.dispatch_runtime_sync"
                ) as mock_dispatch:
                    result = _resolve_direct_project_budget_runtime(
                        llm_model_details=model_details,
                        litellm_context=litellm_context,
                        user_id=user_id,
                        user_email=user_email,
                    )

        assert result == (None, {}, None, None, False)
        mock_sync.assert_not_called()
        mock_resolve.assert_not_called()
        mock_dispatch.assert_not_called()

    def test_premium_model_uses_premium_category_when_runtime_budget_assigned(self):
        from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory
        from codemie.enterprise.litellm.llm_factory import (
            DirectBudgetAvailability,
            _resolve_direct_project_budget_runtime,
        )

        model_details = MagicMock()
        model_details.base_name = "claude-opus-4-6-20260205"

        litellm_context = MagicMock()
        litellm_context.current_project = "project-1"

        resolved = MagicMock()
        provider_result = MagicMock()
        provider_result.body_overrides = {}
        provider_result.headers = {
            "x-budget-runtime": "true",
            "x-litellm-customer-id": "premium-runtime-user",
        }
        provider_result.api_key = "runtime-api-key"
        provider_result.base_url = "https://runtime.example"

        with patch(
            "codemie.enterprise.litellm.llm_factory._resolve_direct_budget_availability",
            return_value=DirectBudgetAvailability(
                user_budget_ids={CoreBudgetCategory.PREMIUM_MODELS.value: "premium-runtime-budget"},
                project_scopes={CoreBudgetCategory.PLATFORM, CoreBudgetCategory.PREMIUM_MODELS},
            ),
        ):
            with patch("codemie.enterprise.litellm.llm_factory.ensure_project_member_runtime_ready_sync") as mock_sync:
                with patch(
                    "codemie.service.budget.budget_resolution_service.budget_resolution_service.resolve_sync",
                    return_value=resolved,
                ) as mock_resolve:
                    with patch(
                        "codemie.service.budget.budget_resolution_service.budget_resolution_service.dispatch_runtime_sync",
                        return_value=provider_result,
                    ) as mock_dispatch:
                        result = _resolve_direct_project_budget_runtime(
                            llm_model_details=model_details,
                            litellm_context=litellm_context,
                            user_id="user-1",
                            user_email="user@example.com",
                        )

        assert result == (
            None,
            {"x-budget-runtime": "true", "x-litellm-customer-id": "premium-runtime-user"},
            "runtime-api-key",
            "https://runtime.example",
            True,
        )
        mock_sync.assert_called_once_with(
            user_id="user-1",
            user_email="user@example.com",
            project_name="project-1",
            budget_category=CoreBudgetCategory.PREMIUM_MODELS,
        )
        mock_resolve.assert_called_once_with(
            user_id="user-1",
            project_name="project-1",
            budget_category=CoreBudgetCategory.PREMIUM_MODELS,
        )
        mock_dispatch.assert_called_once_with(
            resolved,
            user_id="user-1",
            user_email="user@example.com",
            model="claude-opus-4-6-20260205",
        )

    def test_project_with_no_budget_scopes_early_returns_with_false(self):
        """Project with no budget scopes must return early with has_project_budget_scopes=False
        and skip resolve/dispatch entirely — billing falls through to personal/default budget."""
        from codemie.enterprise.litellm.llm_factory import (
            DirectBudgetAvailability,
            _resolve_direct_project_budget_runtime,
        )

        model_details = MagicMock()
        model_details.base_name = "claude-opus-4"

        litellm_context = MagicMock()
        litellm_context.current_project = "project-without-budgets"

        with patch(
            "codemie.enterprise.litellm.llm_factory._resolve_direct_budget_availability",
            return_value=DirectBudgetAvailability(user_budget_ids={}, project_scopes=set()),
        ):
            with patch("codemie.enterprise.litellm.llm_factory.ensure_project_member_runtime_ready_sync") as mock_sync:
                with patch(
                    "codemie.service.budget.budget_resolution_service.budget_resolution_service.resolve_sync"
                ) as mock_resolve:
                    with patch(
                        "codemie.service.budget.budget_resolution_service.budget_resolution_service.dispatch_runtime_sync"
                    ) as mock_dispatch:
                        result = _resolve_direct_project_budget_runtime(
                            llm_model_details=model_details,
                            litellm_context=litellm_context,
                            user_id="user-1",
                            user_email="user@example.com",
                        )

        assert result == (None, {}, None, None, False)
        mock_sync.assert_not_called()
        mock_resolve.assert_not_called()
        mock_dispatch.assert_not_called()


class TestLiteLLMChatOpenAI:
    """Test LiteLLMChatOpenAI.with_structured_output behavior."""

    def _make_instance(self):
        from codemie.enterprise.litellm.llm_factory import LiteLLMChatOpenAI

        mock_model_details = MagicMock()
        mock_model_details.base_name = "gpt-4"

        instance = LiteLLMChatOpenAI.__new__(LiteLLMChatOpenAI)
        object.__setattr__(instance, "llm_model_details", mock_model_details)
        return instance

    def test_with_structured_output_forces_function_calling(self):
        """with_structured_output always delegates to parent with method='function_calling'."""
        from pydantic import BaseModel
        from langchain_openai import AzureChatOpenAI

        class OutputSchema(BaseModel):
            answer: str

        instance = self._make_instance()

        with patch.object(AzureChatOpenAI, "with_structured_output") as mock_super:
            instance.with_structured_output(OutputSchema)

        mock_super.assert_called_once_with(OutputSchema, method="function_calling", include_raw=False, strict=None)

    def test_with_structured_output_raises_on_tools_kwarg(self):
        """with_structured_output raises ValueError when 'tools' kwarg is passed."""
        import pytest
        from pydantic import BaseModel

        class OutputSchema(BaseModel):
            answer: str

        instance = self._make_instance()

        with pytest.raises(ValueError, match="tools"):
            instance.with_structured_output(OutputSchema, tools=["some_tool"])

    def test_convert_chunk_injects_litellm_cost(self):
        """_convert_chunk_to_generation_chunk injects cost from streaming usage into generation_info."""
        from langchain_core.messages import AIMessageChunk
        from codemie.enterprise.litellm.llm_factory import LiteLLMChatOpenAI

        instance = self._make_instance()

        # Simulate a final streaming chunk with cost in usage (no choices)
        chunk = {
            "choices": [],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, "cost": 0.0042},
        }

        with patch.object(LiteLLMChatOpenAI.__bases__[0], "_convert_chunk_to_generation_chunk") as mock_super:
            from langchain_core.outputs import ChatGenerationChunk

            mock_super.return_value = ChatGenerationChunk(
                message=AIMessageChunk(content=""),
                generation_info=None,
            )
            result = instance._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, {})

        assert result is not None
        assert result.generation_info is not None
        assert result.generation_info.get("litellm_cost") == 0.0042

    def test_convert_chunk_skips_inject_when_no_cost(self):
        """_convert_chunk_to_generation_chunk does not add litellm_cost when usage has no cost."""
        from langchain_core.messages import AIMessageChunk
        from codemie.enterprise.litellm.llm_factory import LiteLLMChatOpenAI

        instance = self._make_instance()

        chunk = {
            "choices": [],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }

        with patch.object(LiteLLMChatOpenAI.__bases__[0], "_convert_chunk_to_generation_chunk") as mock_super:
            from langchain_core.outputs import ChatGenerationChunk

            mock_super.return_value = ChatGenerationChunk(
                message=AIMessageChunk(content=""),
                generation_info=None,
            )
            result = instance._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, {})

        assert result is not None
        assert (result.generation_info or {}).get("litellm_cost") is None


class TestConfigureDirectRuntimeOverrides:
    """Tests for _configure_direct_runtime_overrides — bypass-mode user injection behaviour."""

    def test_global_integration_bypass_does_not_inject_user(self):
        """Global LiteLLM integration in bypass mode must NOT set model_kwargs[user]."""
        from codemie.enterprise.litellm.llm_factory import _configure_direct_runtime_overrides
        from codemie.rest_api.models.settings import LiteLLMContext, LiteLLMCredentials

        creds = LiteLLMCredentials(api_key="global-key", url="http://litellm:4000")
        context = LiteLLMContext(credentials=creds, current_project="test-project")
        request_params: dict = {}

        with patch("codemie.enterprise.litellm.llm_factory._resolve_direct_project_budget_runtime") as mock_resolve:
            mock_resolve.return_value = ("member-ref-123", {}, None, None)

            _configure_direct_runtime_overrides(
                llm_model_details=MagicMock(),
                litellm_context=context,
                user_email="user@test.com",
                user_id="uid-1",
                creds=creds,
                merged_headers={},
                request_params=request_params,
            )

        assert "model_kwargs" not in request_params

    def test_non_global_bypass_does_not_inject_user(self):
        """Non-global personal key in bypass mode must NOT set model_kwargs[user] (EPMCDME-13264)."""
        from codemie.enterprise.litellm.llm_factory import _configure_direct_runtime_overrides
        from codemie.rest_api.models.settings import LiteLLMContext, LiteLLMCredentials

        creds = LiteLLMCredentials(api_key="personal-key", url="http://litellm:4000")
        context = LiteLLMContext(credentials=creds, current_project="test-project")
        request_params: dict = {}

        with patch("codemie.enterprise.litellm.llm_factory._resolve_direct_project_budget_runtime") as mock_resolve:
            mock_resolve.return_value = ("member-ref-123", {}, None, None)

            _configure_direct_runtime_overrides(
                llm_model_details=MagicMock(),
                litellm_context=context,
                user_email="user@test.com",
                user_id="uid-1",
                creds=creds,
                merged_headers={},
                request_params=request_params,
            )

        mock_resolve.assert_not_called()
        assert "model_kwargs" not in request_params


class TestConfigureDirectRuntimeOverridesPremiumNoProject:
    """Tests for the premium model guard in _configure_direct_runtime_overrides
    when litellm_context.current_project is None (personal agent, no project context).
    """

    @pytest.fixture(autouse=True)
    def clear_caches(self):
        from codemie.enterprise.litellm.dependencies import (
            is_premium_model,
            is_premium_models_enabled,
        )

        is_premium_models_enabled.cache_clear()
        is_premium_model.cache_clear()
        yield
        is_premium_models_enabled.cache_clear()
        is_premium_model.cache_clear()

    def _invoke(self, *, user_email="alice@example.com", user_id="uid-1"):
        """Call _configure_direct_runtime_overrides with no creds and no project overrides."""
        from codemie.enterprise.litellm.llm_factory import _configure_direct_runtime_overrides

        llm_model_details = MagicMock()
        llm_model_details.base_name = "claude-opus-4"
        request_params: dict = {}

        with patch(
            "codemie.enterprise.litellm.llm_factory._resolve_direct_project_budget_runtime",
            return_value=(None, {}, None, None, False),
        ):
            _configure_direct_runtime_overrides(
                llm_model_details=llm_model_details,
                litellm_context=None,
                user_email=user_email,
                user_id=user_id,
                creds=None,
                merged_headers={},
                request_params=request_params,
            )
        return request_params

    def test_premium_model_no_project_context_uses_premium_budget(self):
        """Premium model + no project context → check_user_budget called with premium username and premium budget."""
        mock_customer = MagicMock()

        with (
            patch(
                "codemie.enterprise.litellm.dependencies.get_premium_username",
                return_value="alice@example.com_codemie_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.llm_factory._get_direct_request_category_budget_id",
                return_value=None,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.get_category_budget_id",
                return_value="default_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.check_user_budget",
                return_value=mock_customer,
            ) as mock_check,
            patch("codemie.enterprise.litellm.llm_factory._mirror_budget_assignment"),
        ):
            params = self._invoke()

        mock_check.assert_called_once_with(
            user_email="alice@example.com_codemie_premium_models",
            user_id="uid-1",
            budget_id="default_premium_models",
        )
        assert params["model_kwargs"]["user"] == "alice@example.com_codemie_premium_models"

    def test_non_premium_model_no_project_context_uses_platform_budget(self):
        """Non-premium model → get_premium_username returns None → PLATFORM path unchanged."""
        with (
            patch(
                "codemie.enterprise.litellm.dependencies.get_premium_username",
                return_value=None,
            ),
            patch(
                "codemie.enterprise.litellm.llm_factory._get_direct_request_category_budget_id",
                return_value=None,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.get_category_budget_id",
                return_value="default",
            ),
            patch("codemie.enterprise.litellm.dependencies.check_user_budget", return_value=MagicMock()),
            patch("codemie.enterprise.litellm.llm_factory._mirror_budget_assignment"),
        ):
            params = self._invoke()

        assert params["model_kwargs"]["user"] == "alice@example.com"

    def test_premium_model_no_budget_id_anywhere_falls_through_to_platform(self):
        """Premium model but no personal assignment and no default config → falls through to PLATFORM."""

        def category_budget_id(category):
            if category.value == "premium_models":
                return None
            return "default"

        with (
            patch(
                "codemie.enterprise.litellm.dependencies.get_premium_username",
                return_value="alice@example.com_codemie_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.llm_factory._get_direct_request_category_budget_id",
                return_value=None,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.get_category_budget_id",
                side_effect=category_budget_id,
            ),
            patch("codemie.enterprise.litellm.dependencies.check_user_budget", return_value=MagicMock()),
            patch("codemie.enterprise.litellm.llm_factory._mirror_budget_assignment"),
        ):
            params = self._invoke()

        assert params["model_kwargs"]["user"] == "alice@example.com"

    def test_premium_model_personal_assignment_wins_over_default(self):
        """Personal PREMIUM_MODELS assignment present → used instead of default config budget."""
        with (
            patch(
                "codemie.enterprise.litellm.dependencies.get_premium_username",
                return_value="alice@example.com_codemie_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.llm_factory._get_direct_request_category_budget_id",
                return_value="personal-premium-budget",
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.get_category_budget_id",
                return_value="default_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.check_user_budget",
                return_value=MagicMock(),
            ) as mock_check,
            patch("codemie.enterprise.litellm.llm_factory._mirror_budget_assignment"),
        ):
            self._invoke()

        mock_check.assert_called_once_with(
            user_email="alice@example.com_codemie_premium_models",
            user_id="uid-1",
            budget_id="personal-premium-budget",
        )

    def test_mirror_budget_assignment_called_with_premium_category(self):
        """Premium model path calls _mirror_budget_assignment with category=PREMIUM_MODELS."""
        from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory

        with (
            patch(
                "codemie.enterprise.litellm.dependencies.get_premium_username",
                return_value="alice@example.com_codemie_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.llm_factory._get_direct_request_category_budget_id",
                return_value=None,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.get_category_budget_id",
                return_value="default_premium_models",
            ),
            patch("codemie.enterprise.litellm.dependencies.check_user_budget", return_value=MagicMock()),
            patch(
                "codemie.enterprise.litellm.llm_factory._mirror_budget_assignment",
            ) as mock_mirror,
        ):
            self._invoke()

        call_kwargs = mock_mirror.call_args.kwargs
        assert call_kwargs["category"] == CoreBudgetCategory.PREMIUM_MODELS
        assert call_kwargs["user_id"] == "uid-1"

    def test_premium_model_personal_assignment_enforced_without_default_config(self):
        """Personal PREMIUM_MODELS assignment is enforced even when no default config entry exists."""
        with (
            patch(
                "codemie.enterprise.litellm.dependencies.get_premium_username",
                return_value="alice@example.com_codemie_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.llm_factory._get_direct_request_category_budget_id",
                return_value="personal-premium-budget",
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.get_category_budget_id",
                return_value=None,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.check_user_budget",
                return_value=MagicMock(),
            ) as mock_check,
            patch("codemie.enterprise.litellm.llm_factory._mirror_budget_assignment"),
        ):
            params = self._invoke()

        mock_check.assert_called_once_with(
            user_email="alice@example.com_codemie_premium_models",
            user_id="uid-1",
            budget_id="personal-premium-budget",
        )
        assert params["model_kwargs"]["user"] == "alice@example.com_codemie_premium_models"


class TestConfigureDirectRuntimeOverridesPremiumWithProjectContext:
    """Tests for premium budget routing when a project context is present.

    Covers two distinct scenarios:
    1. Real project (current_project != user_email): _try_apply_premium_budget must NOT fire;
       request falls through to project platform budget.
    2. Global-assistant personal context (current_project == user_email): treated as no real
       project — _try_apply_premium_budget MUST fire, same as the no-project-context case.

    Root cause of original regression (EPMCDME-13794): adding default_premium_models to
    budgets-config.yaml made get_category_budget_id(PREMIUM_MODELS) return a non-None value,
    activating the not availability.project_scopes branch for real projects with empty scopes
    and routing them to the personal premium budget instead of the project platform budget.

    Root cause of second regression (this fix's predecessor): has_project_context was set from
    bool(litellm_context.current_project) without excluding the email-as-project case that
    _resolve_effective_project produces for global assistants accessed by non-members.
    """

    @pytest.fixture(autouse=True)
    def clear_caches(self):
        from codemie.enterprise.litellm.dependencies import (
            is_premium_model,
            is_premium_models_enabled,
        )

        is_premium_models_enabled.cache_clear()
        is_premium_model.cache_clear()
        yield
        is_premium_models_enabled.cache_clear()
        is_premium_model.cache_clear()

    def _make_litellm_context(self, project_name="my-project"):
        from codemie.rest_api.models.settings import LiteLLMContext

        return LiteLLMContext(credentials=None, current_project=project_name)

    def _invoke(
        self,
        *,
        litellm_context,
        user_email="alice@example.com",
        user_id="uid-1",
        has_project_budget_scopes=True,
    ):
        from codemie.enterprise.litellm.llm_factory import _configure_direct_runtime_overrides

        llm_model_details = MagicMock()
        llm_model_details.base_name = "claude-opus-4"
        request_params: dict = {}

        with patch(
            "codemie.enterprise.litellm.llm_factory._resolve_direct_project_budget_runtime",
            return_value=(None, {}, None, None, has_project_budget_scopes),
        ):
            _configure_direct_runtime_overrides(
                llm_model_details=llm_model_details,
                litellm_context=litellm_context,
                user_email=user_email,
                user_id=user_id,
                creds=None,
                merged_headers={},
                request_params=request_params,
            )
        return request_params

    def test_premium_model_project_context_no_scope_uses_platform_budget(self):
        """Regression: project context + premium model + no project premium scope → PLATFORM budget.

        Before the fix, adding default_premium_models to budgets-config.yaml caused
        _try_apply_premium_budget to fire here, routing the request to the personal premium
        budget instead of the project platform budget.
        """

        def category_budget_id(category):
            return {"premium_models": "default_premium_models", "platform": "default_platform"}.get(category.value)

        with (
            patch(
                "codemie.enterprise.litellm.dependencies.get_premium_username",
                return_value="alice@example.com_codemie_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.llm_factory._get_direct_request_category_budget_id",
                return_value=None,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.get_category_budget_id",
                side_effect=category_budget_id,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.check_user_budget",
                return_value=MagicMock(),
            ) as mock_check,
            patch("codemie.enterprise.litellm.llm_factory._mirror_budget_assignment"),
        ):
            params = self._invoke(litellm_context=self._make_litellm_context())

        assert params["model_kwargs"]["user"] == "alice@example.com"
        mock_check.assert_called_once_with(
            user_email="alice@example.com",
            user_id="uid-1",
            budget_id="default_platform",
        )

    def test_premium_model_project_no_budget_scopes_uses_personal_premium(self):
        """Project exists but has no budget assignments → personal premium budget must apply.

        When _resolve_direct_project_budget_runtime returns has_project_budget_scopes=False
        (project has no budget scopes at all), the request must fall through to personal premium
        budget — same behaviour as an own assistant with no project context.
        """

        def category_budget_id(category):
            return {"premium_models": "default_premium_models", "platform": "default_platform"}.get(category.value)

        with (
            patch(
                "codemie.enterprise.litellm.dependencies.get_premium_username",
                return_value="alice@example.com_codemie_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.llm_factory._get_direct_request_category_budget_id",
                return_value=None,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.get_category_budget_id",
                side_effect=category_budget_id,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.check_user_budget",
                return_value=MagicMock(),
            ) as mock_check,
            patch("codemie.enterprise.litellm.llm_factory._mirror_budget_assignment"),
        ):
            params = self._invoke(
                litellm_context=self._make_litellm_context(project_name="my-project"),
                has_project_budget_scopes=False,
            )

        assert params["model_kwargs"]["user"] == "alice@example.com_codemie_premium_models"
        mock_check.assert_called_once_with(
            user_email="alice@example.com_codemie_premium_models",
            user_id="uid-1",
            budget_id="default_premium_models",
        )

    def test_premium_model_global_assistant_email_as_project_uses_premium_budget(self):
        """Global assistant accessed by non-member: current_project == user_email.

        _resolve_effective_project sets current_project = user.email for personal users
        on a global assistant. This is NOT a real project context — personal premium budget
        must still be enforced, not suppressed by has_project_context guard.
        """

        def category_budget_id(category):
            return {"premium_models": "default_premium_models", "platform": "default_platform"}.get(category.value)

        with (
            patch(
                "codemie.enterprise.litellm.dependencies.get_premium_username",
                return_value="alice@example.com_codemie_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.llm_factory._get_direct_request_category_budget_id",
                return_value=None,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.get_category_budget_id",
                side_effect=category_budget_id,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.check_user_budget",
                return_value=MagicMock(),
            ) as mock_check,
            patch("codemie.enterprise.litellm.llm_factory._mirror_budget_assignment"),
        ):
            # current_project == user_email: global assistant personal context
            params = self._invoke(
                litellm_context=self._make_litellm_context(project_name="alice@example.com"),
                user_email="alice@example.com",
            )

        assert params["model_kwargs"]["user"] == "alice@example.com_codemie_premium_models"
        mock_check.assert_called_once_with(
            user_email="alice@example.com_codemie_premium_models",
            user_id="uid-1",
            budget_id="default_premium_models",
        )

    def test_premium_model_project_context_personal_assignment_still_uses_platform(self):
        """Project context present + personal PREMIUM_MODELS assignment → personal premium NOT applied."""

        def category_budget_id(category):
            return {"premium_models": "default_premium_models", "platform": "default_platform"}.get(category.value)

        def direct_budget_id(user_id, category):
            # Only the personal premium assignment exists; platform has no personal assignment.
            cat_val = category.value if hasattr(category, "value") else category
            return "personal-premium-budget" if cat_val == "premium_models" else None

        with (
            patch(
                "codemie.enterprise.litellm.dependencies.get_premium_username",
                return_value="alice@example.com_codemie_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.llm_factory._get_direct_request_category_budget_id",
                side_effect=direct_budget_id,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.get_category_budget_id",
                side_effect=category_budget_id,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.check_user_budget",
                return_value=MagicMock(),
            ) as mock_check,
            patch("codemie.enterprise.litellm.llm_factory._mirror_budget_assignment"),
        ):
            params = self._invoke(litellm_context=self._make_litellm_context())

        assert params["model_kwargs"]["user"] == "alice@example.com"
        mock_check.assert_called_once_with(
            user_email="alice@example.com",
            user_id="uid-1",
            budget_id="default_platform",
        )
