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
                with patch.object(config, "OPENAI_API_VERSION", "2024-12-01-preview"):
                    with patch.object(config, "OPENAI_API_TYPE", "azure"):
                        with patch.object(config, "AZURE_OPENAI_MAX_RETRIES", 3):
                            with patch("langchain_openai.AzureChatOpenAI"):
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
                                        user_email="test@example.com", user_id=None
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
            with patch.object(config, "OPENAI_API_VERSION", "2024-12-01-preview"):
                with patch.object(config, "OPENAI_API_TYPE", "azure"):
                    with patch.object(config, "AZURE_OPENAI_MAX_RETRIES", 3):
                        with patch.object(config, "LITE_LLM_TAGS_HEADER_VALUE", "default"):
                            with patch.object(config, "LITE_LLM_PROJECTS_TO_TAGS_LIST", ""):
                                with patch("langchain_openai.AzureChatOpenAI"):
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
                    with patch.object(config, "OPENAI_API_VERSION", "2024-12-01-preview"):
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
                                            user_email="test@example.com", user_id=None
                                        )


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
        provider_result.body_overrides = {"user": "runtime-user"}
        provider_result.headers = {"x-budget-runtime": "true"}
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
                "codemie.service.settings.settings.SettingsService.get_project_member_budget_tracking_enabled",
                return_value=True,
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
            "runtime-user",
            {"x-budget-runtime": "true"},
            "runtime-api-key",
            "https://runtime.example",
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
                "codemie.service.settings.settings.SettingsService.get_project_member_budget_tracking_enabled",
                return_value=True,
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

        assert result == (None, {}, None, None)
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
        provider_result.body_overrides = {"user": "premium-runtime-user"}
        provider_result.headers = {"x-budget-runtime": "true"}
        provider_result.api_key = "runtime-api-key"
        provider_result.base_url = "https://runtime.example"

        with patch(
            "codemie.enterprise.litellm.llm_factory._resolve_direct_budget_availability",
            return_value=DirectBudgetAvailability(
                user_budget_ids={CoreBudgetCategory.PREMIUM_MODELS.value: "premium-runtime-budget"},
                project_scopes={CoreBudgetCategory.PLATFORM, CoreBudgetCategory.PREMIUM_MODELS},
            ),
        ):
            with patch(
                "codemie.service.settings.settings.SettingsService.get_project_member_budget_tracking_enabled",
                return_value=True,
            ):
                with patch(
                    "codemie.enterprise.litellm.llm_factory.ensure_project_member_runtime_ready_sync"
                ) as mock_sync:
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
            "premium-runtime-user",
            {"x-budget-runtime": "true"},
            "runtime-api-key",
            "https://runtime.example",
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
