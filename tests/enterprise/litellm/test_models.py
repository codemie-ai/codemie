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

"""Tests for LiteLLM model mapping (codemie.enterprise.litellm.models)."""

from unittest.mock import MagicMock, patch


class TestMapLiteLLMToLLMModel:
    """Test map_litellm_to_llm_model() function."""

    def test_maps_basic_model_info(self):
        """Test maps basic model information from LiteLLM dict."""
        litellm_model = {
            "model_name": "azure/gpt-4",
            "model_info": {
                "litellm_provider": "azure",
                "id": "gpt-4",
                "label": "GPT-4",
                "enabled": True,
            },
        }

        from codemie.enterprise.litellm.models import map_litellm_to_llm_model

        result = map_litellm_to_llm_model(litellm_model)

        # Should map basic fields
        assert result.base_name == "azure/gpt-4"
        assert result.deployment_name == "azure/gpt-4"
        assert result.label == "GPT-4"
        assert result.enabled is True

    def test_maps_api_version(self):
        litellm_model = {
            "model_name": "azure/gpt-4",
            "litellm_params": {
                "api_version": "2025-04-01-preview",
            },
            "model_info": {
                "litellm_provider": "azure",
                "id": "gpt-4",
                "label": "GPT-4",
                "enabled": True,
            },
        }

        from codemie.enterprise.litellm.models import map_litellm_to_llm_model

        result = map_litellm_to_llm_model(litellm_model)

        assert result.api_version == "2025-04-01-preview"

    def test_handles_empty_api_version_correctly(self):
        litellm_model = {
            "model_name": "azure/gpt-4",
            "model_info": {
                "litellm_provider": "azure",
                "id": "gpt-4",
                "label": "GPT-4",
                "enabled": True,
            },
        }

        from codemie.enterprise.litellm.models import map_litellm_to_llm_model

        result = map_litellm_to_llm_model(litellm_model)

        assert result.api_version is None

    def test_maps_provider_strings_correctly(self):
        """Test maps various provider strings to LLMProvider enum."""
        from codemie.configs.llm_config import LLMProvider
        from codemie.enterprise.litellm.models import map_litellm_to_llm_model

        test_cases = [
            ("openai", LLMProvider.AZURE_OPENAI),
            ("azure", LLMProvider.AZURE_OPENAI),
            ("bedrock", LLMProvider.AWS_BEDROCK),
            ("vertex_ai", LLMProvider.GOOGLE_VERTEX_AI),
            ("anthropic", LLMProvider.ANTHROPIC),
            ("unknown", LLMProvider.AZURE_OPENAI),  # Falls back to AZURE_OPENAI
        ]

        for provider_str, expected_provider in test_cases:
            litellm_model = {
                "model_name": f"{provider_str}/test-model",
                "model_info": {
                    "litellm_provider": provider_str,
                },
            }

            result = map_litellm_to_llm_model(litellm_model)
            assert result.provider == expected_provider, f"Failed for provider: {provider_str}"

    def test_maps_features_correctly(self):
        """Test maps model features to LLMFeatures."""
        litellm_model = {
            "model_name": "azure/gpt-4",
            "model_info": {
                "supports_native_streaming": True,
                "supports_function_calling": True,
                "supports_system_messages": True,
                "top_p": True,
                "supported_openai_params": [
                    "temperature",
                    "max_tokens",
                    "parallel_tool_calls",
                ],
            },
        }

        from codemie.enterprise.litellm.models import map_litellm_to_llm_model

        result = map_litellm_to_llm_model(litellm_model)

        # Should map features
        assert result.features.streaming is True
        assert result.features.tools is True
        assert result.features.system_prompt is True
        assert result.features.parallel_tool_calls is True
        assert result.features.temperature is True
        assert result.features.max_tokens is True
        assert result.features.top_p is True

    def test_handles_missing_features(self):
        """Test handles missing or false feature flags."""
        litellm_model = {
            "model_name": "azure/test-model",
            "model_info": {
                "supports_native_streaming": False,
                "supports_function_calling": False,
                "top_p": False,
                "supported_openai_params": [],  # No params
            },
        }

        from codemie.enterprise.litellm.models import map_litellm_to_llm_model

        result = map_litellm_to_llm_model(litellm_model)

        # Should handle missing features
        assert result.features.streaming is False
        assert result.features.tools is False
        assert result.features.parallel_tool_calls is False
        assert result.features.temperature is False
        assert result.features.max_tokens is False
        assert result.features.top_p is False

    def test_maps_cost_information(self):
        """Test maps cost information when available."""
        litellm_model = {
            "model_name": "azure/gpt-4",
            "model_info": {
                "input_cost_per_token": 0.00003,
                "output_cost_per_token": 0.00006,
                "cache_read_input_token_cost": 0.000015,
            },
        }

        from codemie.enterprise.litellm.models import map_litellm_to_llm_model

        result = map_litellm_to_llm_model(litellm_model)

        # Should map cost information
        assert result.cost is not None
        assert result.cost.input == 0.00003
        assert result.cost.output == 0.00006
        assert result.cost.cache_read_input_token_cost == 0.000015

    def test_handles_missing_cost_information(self):
        """Test handles missing cost information (returns None)."""
        litellm_model = {
            "model_name": "azure/test-model",
            "model_info": {},  # No cost info
        }

        from codemie.enterprise.litellm.models import map_litellm_to_llm_model

        result = map_litellm_to_llm_model(litellm_model)

        # Should have None cost
        assert result.cost is None

    def test_maps_default_categories(self):
        """Test maps default_for_categories correctly."""
        from codemie.configs.llm_config import ModelCategory

        litellm_model = {
            "model_name": "azure/gpt-4",
            "model_info": {
                "default_for_categories": ["global", "chat", "code"],
            },
        }

        from codemie.enterprise.litellm.models import map_litellm_to_llm_model

        result = map_litellm_to_llm_model(litellm_model)

        # Should map categories
        assert ModelCategory.GLOBAL in result.default_for_categories
        assert ModelCategory.CHAT in result.default_for_categories
        assert ModelCategory.CODE in result.default_for_categories

    def test_handles_invalid_categories_gracefully(self):
        """Test handles invalid category strings (logs warning, skips)."""
        litellm_model = {
            "model_name": "azure/gpt-4",
            "model_info": {
                "default_for_categories": ["global", "invalid_category", "chat"],
            },
        }

        with patch("codemie.configs.logger") as mock_logger:
            from codemie.enterprise.litellm.models import map_litellm_to_llm_model

            result = map_litellm_to_llm_model(litellm_model)

            # Should have logged warning for invalid category
            mock_logger.warning.assert_called_once()
            warning_msg = mock_logger.warning.call_args[0][0]
            assert "invalid_category" in warning_msg

            # Should have skipped invalid category
            from codemie.configs.llm_config import ModelCategory

            assert ModelCategory.GLOBAL in result.default_for_categories
            # Invalid category should not be present
            category_values = [cat.value for cat in result.default_for_categories]
            assert "invalid_category" not in category_values

    def test_sets_default_flag_when_global_category_present(self):
        """Test sets default=True when GLOBAL category present."""

        litellm_model = {
            "model_name": "azure/gpt-4",
            "model_info": {
                "default_for_categories": ["global"],
            },
        }

        from codemie.enterprise.litellm.models import map_litellm_to_llm_model

        result = map_litellm_to_llm_model(litellm_model)

        # Should set default flag
        assert result.default is True

    def test_maps_multimodal_flag(self):
        """Test maps supports_vision to multimodal."""
        litellm_model = {
            "model_name": "azure/gpt-4-vision",
            "model_info": {
                "supports_vision": True,
            },
        }

        from codemie.enterprise.litellm.models import map_litellm_to_llm_model

        result = map_litellm_to_llm_model(litellm_model)

        # Should map multimodal
        assert result.multimodal is True

    def test_maps_react_agent_from_function_calling(self):
        """Test sets react_agent based on function calling support."""
        # Model WITH function calling should have react_agent=False
        litellm_model_with_tools = {
            "model_name": "azure/gpt-4",
            "model_info": {
                "supports_function_calling": True,
            },
        }

        from codemie.enterprise.litellm.models import map_litellm_to_llm_model

        result_with_tools = map_litellm_to_llm_model(litellm_model_with_tools)
        assert result_with_tools.react_agent is False

        # Model WITHOUT function calling should have react_agent=True
        litellm_model_without_tools = {
            "model_name": "azure/test-model",
            "model_info": {
                "supports_function_calling": False,
            },
        }

        result_without_tools = map_litellm_to_llm_model(litellm_model_without_tools)
        assert result_without_tools.react_agent is True

    def test_handles_max_completion_tokens_param(self):
        """Test recognizes max_completion_tokens as max_tokens support."""
        litellm_model = {
            "model_name": "azure/gpt-4",
            "model_info": {
                "supported_openai_params": ["max_completion_tokens"],
            },
        }

        from codemie.enterprise.litellm.models import map_litellm_to_llm_model

        result = map_litellm_to_llm_model(litellm_model)

        # Should recognize max_completion_tokens as max_tokens support
        assert result.features.max_tokens is True


class TestGetUserAllowedModels:
    """Test get_user_allowed_models() function."""

    def test_returns_none_when_no_credentials(self):
        """Test returns None when user has no LiteLLM credentials."""
        with patch("codemie.enterprise.litellm.credentials.get_litellm_credentials_for_user", return_value=None):
            from codemie.enterprise.litellm.models import get_user_allowed_models

            result = get_user_allowed_models(user_id="test-user", user_applications=["app1"])

            assert result is None

    def test_returns_none_when_service_unavailable(self):
        """Test returns None when LiteLLM service not available."""
        from codemie.rest_api.models.settings import LiteLLMCredentials

        mock_credentials = LiteLLMCredentials(api_key="sk-test", url="http://localhost:4000")

        with patch(
            "codemie.enterprise.litellm.credentials.get_litellm_credentials_for_user", return_value=mock_credentials
        ):
            with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=None):
                from codemie.enterprise.litellm.models import get_user_allowed_models

                result = get_user_allowed_models(user_id="test-user", user_applications=["app1"])

                assert result is None

    def test_returns_none_when_no_models_available(self):
        """Test returns None when service returns empty model list."""
        from codemie.rest_api.models.settings import LiteLLMCredentials

        mock_credentials = LiteLLMCredentials(api_key="sk-test", url="http://localhost:4000")
        mock_service = MagicMock()
        mock_service.get_available_models.return_value = []

        with patch(
            "codemie.enterprise.litellm.credentials.get_litellm_credentials_for_user", return_value=mock_credentials
        ):
            with patch(
                "codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service
            ):
                from codemie.enterprise.litellm.models import get_user_allowed_models

                result = get_user_allowed_models(user_id="test-user", user_applications=["app1"])

                assert result is None

    def test_maps_and_separates_chat_and_embedding_models(self):
        """Test maps models and separates into chat and embedding lists."""
        from codemie.rest_api.models.settings import LiteLLMCredentials

        mock_credentials = LiteLLMCredentials(api_key="sk-test", url="http://localhost:4000")

        # Mock raw models from service
        raw_models = [
            {
                "model_name": "azure/gpt-4",
                "model_info": {
                    "mode": "chat",
                    "litellm_provider": "azure",
                    "enabled": True,
                },
            },
            {
                "model_name": "azure/text-embedding-ada-002",
                "model_info": {
                    "mode": "embedding",
                    "litellm_provider": "azure",
                    "enabled": True,
                },
            },
            {
                "model_name": "bedrock/claude-3-5-sonnet-20241022",
                "model_info": {
                    "mode": "chat",
                    "litellm_provider": "bedrock",
                    "enabled": True,
                },
            },
        ]

        mock_service = MagicMock()
        mock_service.get_available_models.return_value = raw_models

        with patch(
            "codemie.enterprise.litellm.credentials.get_litellm_credentials_for_user", return_value=mock_credentials
        ):
            with patch(
                "codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service
            ):
                from codemie.enterprise.litellm.models import get_user_allowed_models

                result = get_user_allowed_models(user_id="test-user", user_applications=["app1"])

                # Should have separated models
                assert result is not None
                assert len(result.chat_models) == 2
                assert len(result.embedding_models) == 1

                # Verify chat models
                chat_names = [m.base_name for m in result.chat_models]
                assert "azure/gpt-4" in chat_names
                assert "bedrock/claude-3-5-sonnet-20241022" in chat_names

                # Verify embedding models
                embedding_names = [m.base_name for m in result.embedding_models]
                assert "azure/text-embedding-ada-002" in embedding_names

    def test_skips_disabled_models(self):
        """Test skips models with enabled=False."""
        from codemie.rest_api.models.settings import LiteLLMCredentials

        mock_credentials = LiteLLMCredentials(api_key="sk-test", url="http://localhost:4000")

        raw_models = [
            {
                "model_name": "azure/gpt-4",
                "model_info": {
                    "mode": "chat",
                    "enabled": True,
                },
            },
            {
                "model_name": "azure/gpt-3.5",
                "model_info": {
                    "mode": "chat",
                    "enabled": False,  # Disabled
                },
            },
        ]

        mock_service = MagicMock()
        mock_service.get_available_models.return_value = raw_models

        with patch(
            "codemie.enterprise.litellm.credentials.get_litellm_credentials_for_user", return_value=mock_credentials
        ):
            with patch(
                "codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service
            ):
                from codemie.enterprise.litellm.models import get_user_allowed_models

                result = get_user_allowed_models(user_id="test-user", user_applications=["app1"])

                # Should only have the enabled model
                assert len(result.chat_models) == 1
                assert result.chat_models[0].base_name == "azure/gpt-4"

    def test_deduplicates_models_by_base_name(self):
        """Test deduplicates models with same base_name (keeps last one)."""
        from codemie.rest_api.models.settings import LiteLLMCredentials

        mock_credentials = LiteLLMCredentials(api_key="sk-test", url="http://localhost:4000")

        raw_models = [
            {
                "model_name": "azure/gpt-4",
                "model_info": {
                    "mode": "chat",
                    "enabled": True,
                    "label": "GPT-4 v1",
                },
            },
            {
                "model_name": "azure/gpt-4",  # Duplicate
                "model_info": {
                    "mode": "chat",
                    "enabled": True,
                    "label": "GPT-4 v2",
                },
            },
        ]

        mock_service = MagicMock()
        mock_service.get_available_models.return_value = raw_models

        with patch(
            "codemie.enterprise.litellm.credentials.get_litellm_credentials_for_user", return_value=mock_credentials
        ):
            with patch(
                "codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service
            ):
                from codemie.enterprise.litellm.models import get_user_allowed_models

                result = get_user_allowed_models(user_id="test-user", user_applications=["app1"])

                # Should only have one model (last one wins)
                assert len(result.chat_models) == 1
                assert result.chat_models[0].label == "GPT-4 v2"

    def test_handles_unknown_model_mode(self):
        """Test handles unknown model mode (defaults to CHAT)."""
        from codemie.rest_api.models.settings import LiteLLMCredentials

        mock_credentials = LiteLLMCredentials(api_key="sk-test", url="http://localhost:4000")

        raw_models = [
            {
                "model_name": "azure/test-model",
                "model_info": {
                    "mode": "unknown_mode",  # Invalid mode
                    "enabled": True,
                },
            },
        ]

        mock_service = MagicMock()
        mock_service.get_available_models.return_value = raw_models

        with patch(
            "codemie.enterprise.litellm.credentials.get_litellm_credentials_for_user", return_value=mock_credentials
        ):
            with patch(
                "codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service
            ):
                with patch("codemie.configs.logger") as mock_logger:
                    from codemie.enterprise.litellm.models import get_user_allowed_models

                    result = get_user_allowed_models(user_id="test-user", user_applications=["app1"])

                    # Should default to chat models
                    assert len(result.chat_models) == 1
                    assert result.chat_models[0].base_name == "azure/test-model"

                    # Should log warning
                    mock_logger.warning.assert_called_once()
                    warning_msg = mock_logger.warning.call_args[0][0]
                    assert "unknown_mode" in warning_msg

    def test_handles_model_mapping_errors(self):
        """Test handles errors during model mapping (logs error, skips model)."""
        from codemie.rest_api.models.settings import LiteLLMCredentials

        mock_credentials = LiteLLMCredentials(api_key="sk-test", url="http://localhost:4000")

        raw_models = [
            {
                "model_name": "azure/good-model",
                "model_info": {
                    "mode": "chat",
                    "enabled": True,
                },
            },
            {
                "model_name": None,  # Will cause error during mapping
                "model_info": {},
            },
        ]

        mock_service = MagicMock()
        mock_service.get_available_models.return_value = raw_models

        with patch(
            "codemie.enterprise.litellm.credentials.get_litellm_credentials_for_user", return_value=mock_credentials
        ):
            with patch(
                "codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service
            ):
                with patch("codemie.configs.logger") as mock_logger:
                    from codemie.enterprise.litellm.models import get_user_allowed_models

                    result = get_user_allowed_models(user_id="test-user", user_applications=["app1"])

                    # Should have one valid model (error model skipped)
                    assert len(result.chat_models) == 1
                    assert result.chat_models[0].base_name == "azure/good-model"

                    # Should log error
                    mock_logger.error.assert_called_once()
                    error_msg = mock_logger.error.call_args[0][0]
                    assert "Error mapping model" in error_msg

    def test_logs_info_messages(self):
        """Test logs appropriate info messages."""
        from codemie.rest_api.models.settings import LiteLLMCredentials

        mock_credentials = LiteLLMCredentials(api_key="sk-test", url="http://localhost:4000")

        raw_models = [
            {
                "model_name": "azure/gpt-4",
                "model_info": {
                    "mode": "chat",
                    "enabled": True,
                },
            },
            {
                "model_name": "azure/text-embedding-ada-002",
                "model_info": {
                    "mode": "embedding",
                    "enabled": True,
                },
            },
        ]

        mock_service = MagicMock()
        mock_service.get_available_models.return_value = raw_models

        with (
            patch(
                "codemie.enterprise.litellm.credentials.get_litellm_credentials_for_user", return_value=mock_credentials
            ),
            patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service),
            patch("codemie.configs.logger") as mock_logger,
        ):
            from codemie.enterprise.litellm.models import get_user_allowed_models

            get_user_allowed_models(user_id="test-user", user_applications=["app1"])

            # Should log success with counts
            info_calls = [call[0][0] for call in mock_logger.info.call_args_list]
            assert any("Fetched" in msg and "chat models" in msg and "embedding models" in msg for msg in info_calls)

    def test_passes_user_credentials_to_service(self):
        """Test passes user's API key to service for model fetching."""
        from codemie.rest_api.models.settings import LiteLLMCredentials

        mock_credentials = LiteLLMCredentials(api_key="sk-user-specific", url="http://localhost:4000")

        mock_service = MagicMock()
        mock_service.get_available_models.return_value = [
            {
                "model_name": "azure/gpt-4",
                "model_info": {
                    "mode": "chat",
                    "enabled": True,
                },
            }
        ]

        with (
            patch(
                "codemie.enterprise.litellm.credentials.get_litellm_credentials_for_user", return_value=mock_credentials
            ),
            patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service),
        ):
            from codemie.enterprise.litellm.models import get_user_allowed_models

            get_user_allowed_models(user_id="test-user", user_applications=["app1"])

            # Should have called service with user's API key
            mock_service.get_available_models.assert_called_once_with(
                user_id="test-user",
                api_key="sk-user-specific",
            )
