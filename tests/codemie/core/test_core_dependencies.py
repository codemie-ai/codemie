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

import json
import pytest
from unittest.mock import Mock, patch

from codemie.core.dependecies import (
    LLMClientWrapper,
    filter_params,
    get_anthropic_llm,
    get_bedrock_llm,
    get_llm_by_credentials,
    get_vertex_llm,
)


# Mock logger for testing
@pytest.fixture
def mock_logger():
    with patch('codemie.core.dependecies.logger') as mock_log:
        yield mock_log


class TestLLMClientWrapper:
    @pytest.fixture
    def wrapped_class(self):
        return Mock()

    @pytest.fixture
    def wrapper(self, wrapped_class):
        return LLMClientWrapper(wrapped_class)

    def test_getattr_returns_wrapper(self, wrapper, wrapped_class):
        wrapped_class.some_method = Mock()
        assert callable(wrapper.some_method)
        assert wrapper.some_method is not wrapped_class.some_method

    def test_wrapper_calls_original_function(self, wrapper, wrapped_class):
        wrapped_class.some_method = Mock(return_value="test_result")
        result = wrapper.some_method()
        assert result == "test_result"
        wrapped_class.some_method.assert_called_once()

    def test_wrapper_logs_body_when_present(self, wrapper, wrapped_class, mock_logger):
        wrapped_class.some_method = Mock()
        wrapper.some_method(body="test_body")
        mock_logger.debug.assert_called_once_with("Call LLM with the following body:\ntest_body")

    def test_wrapper_logs_kwargs_when_no_body(self, wrapper, wrapped_class, mock_logger):
        wrapped_class.some_method = Mock()
        wrapper.some_method(arg1="value1", arg2="value2")
        expected_log = "Call LLM with the following body:\n" + json.dumps({"arg1": "value1", "arg2": "value2"})
        mock_logger.debug.assert_called_once_with(expected_log)

    def test_wrapper_handles_logging_exception(self, wrapper, wrapped_class, mock_logger):
        wrapped_class.some_method = Mock()
        mock_logger.debug.side_effect = Exception("Test exception")
        wrapper.some_method(body="test_body")
        mock_logger.warning.assert_called_once_with(
            "Exception has been occurred during the logging request to LLM: \nTest exception"
        )

    def test_wrapper_passes_args_and_kwargs(self, wrapper, wrapped_class):
        wrapped_class.some_method = Mock()
        wrapper.some_method("arg1", "arg2", kwarg1="value1", kwarg2="value2")
        wrapped_class.some_method.assert_called_once_with("arg1", "arg2", kwarg1="value1", kwarg2="value2")


class TestGetLLMByCredentialsUserFallback:
    """
    Fallback to get_current_user() when current_user_email context variable is not set.

    This ensures that tools creating internal LLM instances use the correct user's budget
    for LiteLLM tracking, even when the current_user_email context variable is not set
    (e.g., during toolkit initialization before agent sets logging context).
    """

    @pytest.fixture
    def mock_dependencies(self):
        """Mock all dependencies for get_llm_by_credentials"""
        with (
            patch('codemie.core.dependecies.llm_service') as mock_llm_service,
            patch('codemie.core.dependecies.litellm_context') as mock_litellm_context,
            patch('codemie.core.dependecies.current_user_email') as mock_current_user_email,
            patch('codemie.core.dependecies.logging_user_id') as mock_logging_user_id,
            patch('codemie.enterprise.litellm.get_litellm_chat_model') as mock_get_litellm,
            patch('codemie.core.dependecies.logger') as mock_logger,
            patch('codemie.rest_api.security.user_context.get_current_user') as mock_get_current_user,
        ):
            # Setup mock LLM model details
            mock_model_details = Mock()
            mock_model_details.base_name = "gpt-4"
            mock_llm_service.get_model_details.return_value = mock_model_details

            # Setup mock LiteLLM context
            mock_litellm_context.get.return_value = None

            # Setup mock logging_user_id (not consulted for email resolution)
            mock_logging_user_id.get.return_value = None

            # Default: no current user in request state
            mock_get_current_user.return_value = None

            # Setup mock LiteLLM chat model
            mock_llm = Mock()
            mock_get_litellm.return_value = mock_llm

            yield {
                'llm_service': mock_llm_service,
                'litellm_context': mock_litellm_context,
                'current_user_email': mock_current_user_email,
                'logging_user_id': mock_logging_user_id,
                'get_litellm_chat_model': mock_get_litellm,
                'logger': mock_logger,
                'mock_llm': mock_llm,
                'get_current_user': mock_get_current_user,
            }

    def _make_current_user(self, email: str | None = None, username: str | None = None) -> Mock:
        user = Mock()
        user.email = email
        user.username = username
        return user

    def test_normal_flow_with_valid_user_email(self, mock_dependencies):
        """Test normal flow when current_user_email context variable is set correctly"""
        # Setup: Context variable has valid user email
        mock_dependencies['current_user_email'].get.return_value = "john@example.com"

        # Execute
        get_llm_by_credentials(llm_model="gpt-4")

        # Verify: LiteLLM called with correct user email (no fallback needed)
        mock_dependencies['get_litellm_chat_model'].assert_called_once()
        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "john@example.com"

    def test_fallback_when_context_is_unknown(self, mock_dependencies):
        """Test fallback to get_current_user() when current_user_email returns 'unknown'"""
        mock_dependencies['current_user_email'].get.return_value = "unknown"
        mock_dependencies['get_current_user'].return_value = self._make_current_user(email="user@example.com")

        get_llm_by_credentials(llm_model="gpt-4")

        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "user@example.com"

    def test_fallback_when_context_is_dash(self, mock_dependencies):
        """Test fallback to get_current_user() when current_user_email returns '-'"""
        mock_dependencies['current_user_email'].get.return_value = "-"
        mock_dependencies['get_current_user'].return_value = self._make_current_user(email="user@example.com")

        get_llm_by_credentials(llm_model="gpt-4")

        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "user@example.com"

    def test_fallback_when_context_is_empty(self, mock_dependencies):
        """Test fallback to get_current_user() when current_user_email returns empty string"""
        mock_dependencies['current_user_email'].get.return_value = ""
        mock_dependencies['get_current_user'].return_value = self._make_current_user(email="user@example.com")

        get_llm_by_credentials(llm_model="gpt-4")

        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "user@example.com"

    def test_fallback_when_context_is_none(self, mock_dependencies):
        """Test fallback to get_current_user() when current_user_email returns None"""
        mock_dependencies['current_user_email'].get.return_value = None
        mock_dependencies['get_current_user'].return_value = self._make_current_user(email="user@example.com")

        get_llm_by_credentials(llm_model="gpt-4")

        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "user@example.com"

    def test_fallback_uses_username_when_email_absent(self, mock_dependencies):
        """Test fallback uses username when current user has no email"""
        mock_dependencies['current_user_email'].get.return_value = "unknown"
        mock_dependencies['get_current_user'].return_value = self._make_current_user(
            email=None, username="user@example.com"
        )

        get_llm_by_credentials(llm_model="gpt-4")

        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "user@example.com"

    def test_no_fallback_when_no_current_user(self, mock_dependencies):
        """Test graceful handling when current_user_email is invalid and no request user (system operations)"""
        mock_dependencies['current_user_email'].get.return_value = "unknown"
        mock_dependencies['get_current_user'].return_value = None

        get_llm_by_credentials(llm_model="gpt-4")

        # No valid user: original "unknown" value is passed through unchanged
        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "unknown"

    def test_no_fallback_when_no_current_user_dash(self, mock_dependencies):
        """Test graceful handling when current_user_email is '-' and no request user"""
        mock_dependencies['current_user_email'].get.return_value = "-"
        mock_dependencies['get_current_user'].return_value = None

        get_llm_by_credentials(llm_model="gpt-4")

        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "-"

    def test_fallback_with_different_llm_models(self, mock_dependencies):
        """Test fallback works with different LLM models"""
        mock_dependencies['current_user_email'].get.return_value = "unknown"
        mock_dependencies['get_current_user'].return_value = self._make_current_user(email="user@example.com")

        for model_name in ["gpt-4", "gpt-3.5-turbo", "claude-3-opus"]:
            mock_dependencies['get_litellm_chat_model'].reset_mock()

            get_llm_by_credentials(llm_model=model_name)

            call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
            assert call_kwargs['user_email'] == "user@example.com"

    def test_fallback_preserves_other_parameters(self, mock_dependencies):
        """Test that fallback doesn't affect other parameters"""
        mock_dependencies['current_user_email'].get.return_value = "unknown"
        mock_dependencies['get_current_user'].return_value = self._make_current_user(email="user@example.com")

        get_llm_by_credentials(
            llm_model="gpt-4", temperature=0.7, top_p=0.9, streaming=False, request_id="test-request-123"
        )

        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "user@example.com"
        assert call_kwargs['temperature'] == 0.7
        assert call_kwargs['top_p'] == 0.9
        assert not call_kwargs['streaming']

    def test_integration_scenario_toolkit_initialization(self, mock_dependencies):
        """
        Integration test: toolkit initialization happens before agent sets logging context.
        get_current_user() must resolve the email from the active request state.
        """
        # Context variable still holds the middleware default — agent hasn't set it yet
        mock_dependencies['current_user_email'].get.return_value = "-"

        # Request state has the authenticated user
        mock_dependencies['get_current_user'].return_value = self._make_current_user(email="user@example.com")

        get_llm_by_credentials(llm_model="gpt-4")

        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "user@example.com"

        # Verify: UUID is NOT used (that was the original bug)
        assert call_kwargs['user_email'] != "-"
        assert call_kwargs['user_email'] != "unknown"


def _model_details(temperature_supported, top_p_supported=True, base_name="gemini-2.5-pro"):
    details = Mock()
    details.features.temperature = temperature_supported
    details.features.parallel_tool_calls = True
    details.features.streaming = True
    details.features.max_tokens = True
    details.features.top_p = top_p_supported
    details.deployment_name = "model-deployment"
    details.base_name = base_name
    details.max_output_tokens = 1000
    details.configuration = None
    return details


class TestFilterParamsTemperature:
    """EPMCDME-15208: unsupported temperature is dropped, not forced to 1.

    The only caller (get_llm_by_credentials_raw) never puts temperature in either dict, so these
    cases exercise the defensive pops rather than a reachable production path.
    """

    def test_defensively_drops_temperature_when_unsupported(self):
        result = filter_params({"temperature": 0.3}, {"temperature": 0.3}, _model_details(False))
        assert "temperature" not in result

    def test_real_caller_shape_has_no_temperature_when_unsupported(self):
        result = filter_params({"deployment_name": "d"}, {}, _model_details(False))
        assert "temperature" not in result

    def test_keeps_temperature_when_supported(self):
        result = filter_params({}, {"temperature": 0.3}, _model_details(True))
        assert result["temperature"] == 0.3


class TestGetBedrockLlmTemperature:
    """EPMCDME-15208: temperature is omitted from ChatBedrockConverse when None or unsupported."""

    @staticmethod
    def _call(supported, temperature, top_p=0.9, top_p_supported=True):
        with (
            patch("langchain_aws.ChatBedrockConverse") as mock_cls,
            patch("codemie.core.dependecies.get_bedrock_runtime_client"),
        ):
            get_bedrock_llm(_model_details(supported, top_p_supported), temperature=temperature, top_p=top_p)
        return mock_cls.call_args.kwargs

    def test_omits_temperature_when_none(self):
        assert "temperature" not in self._call(True, None)

    def test_omits_temperature_when_unsupported(self):
        assert "temperature" not in self._call(False, 0.5)

    def test_passes_temperature_when_supported(self):
        kwargs = self._call(True, 0.5)
        assert kwargs["temperature"] == 0.5
        assert kwargs["top_p"] == 0.9

    def test_omits_top_p_when_unsupported(self):
        assert "top_p" not in self._call(True, 0.5, top_p_supported=False)

    def test_omits_top_p_when_none(self):
        assert "top_p" not in self._call(True, 0.5, top_p=None)

    def test_passes_top_p_when_supported(self):
        assert self._call(True, 0.5, top_p_supported=True)["top_p"] == 0.9


class TestGetAnthropicLlmTemperature:
    """EPMCDME-15208: temperature is omitted from ChatAnthropic when None or unsupported."""

    @staticmethod
    def _call(supported, temperature):
        with (
            patch("langchain_anthropic.ChatAnthropic") as mock_cls,
            patch("codemie.core.dependecies._build_codemie_tagging_headers", return_value={}),
        ):
            get_anthropic_llm(_model_details(supported), temperature=temperature, top_p=0.9)
        return mock_cls.call_args.kwargs

    def test_omits_temperature_when_none(self):
        assert "temperature" not in self._call(True, None)

    def test_omits_temperature_when_unsupported(self):
        assert "temperature" not in self._call(False, 0.5)

    def test_passes_temperature_when_supported(self):
        assert self._call(True, 0.5)["temperature"] == 0.5


class TestGetVertexLlmTemperature:
    """EPMCDME-15208: temperature is omitted from the Vertex chat models when None or unsupported."""

    @staticmethod
    def _call(supported, temperature, base_name):
        target = (
            "langchain_google_vertexai.ChatVertexAI"
            if "gemini" in base_name
            else "langchain_google_vertexai.model_garden.ChatAnthropicVertex"
        )
        with (
            patch(target) as mock_cls,
            patch("codemie.core.dependecies._build_codemie_tagging_headers", return_value={}),
            patch("codemie.agents.tools.schema_compatibility.patch_langchain_google_vertexai_schema"),
        ):
            get_vertex_llm(_model_details(supported, base_name=base_name), temperature=temperature, top_p=0.9)
        return mock_cls.call_args.kwargs

    @pytest.mark.parametrize("base_name", ["gemini-2.5-pro", "claude-sonnet-4"])
    def test_omits_temperature_when_none(self, base_name):
        assert "temperature" not in self._call(True, None, base_name)

    @pytest.mark.parametrize("base_name", ["gemini-2.5-pro", "claude-sonnet-4"])
    def test_omits_temperature_when_unsupported(self, base_name):
        assert "temperature" not in self._call(False, 0.5, base_name)

    @pytest.mark.parametrize("base_name", ["gemini-2.5-pro", "claude-sonnet-4"])
    def test_passes_temperature_when_supported(self, base_name):
        assert self._call(True, 0.5, base_name)["temperature"] == 0.5
