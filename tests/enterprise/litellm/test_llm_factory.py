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
                                    mock_check_budget.assert_called_once_with(user_id="test@example.com")

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
                                        mock_check_budget.assert_called_once_with(user_id="test@example.com")
