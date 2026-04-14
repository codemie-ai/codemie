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

from codemie.core.dependecies import LLMClientWrapper, get_llm_by_credentials
from codemie.rest_api.security.user import User


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
    Fallback to authenticated user when context variable is not set.

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
            patch('codemie.enterprise.litellm.get_litellm_chat_model') as mock_get_litellm,
            patch('codemie.core.dependecies.logger') as mock_logger,
        ):
            # Setup mock LLM model details
            mock_model_details = Mock()
            mock_model_details.base_name = "gpt-4"
            mock_llm_service.get_model_details.return_value = mock_model_details

            # Setup mock LiteLLM context
            mock_litellm_context.get.return_value = None

            # Setup mock LiteLLM chat model
            mock_llm = Mock()
            mock_get_litellm.return_value = mock_llm

            yield {
                'llm_service': mock_llm_service,
                'litellm_context': mock_litellm_context,
                'current_user_email': mock_current_user_email,
                'get_litellm_chat_model': mock_get_litellm,
                'logger': mock_logger,
                'mock_llm': mock_llm,
            }

    @pytest.fixture
    def mock_user(self):
        """Create a mock authenticated user"""
        user = Mock(spec=User)
        user.username = "test@example.com"
        user.id = "user-123"
        return user

    def test_normal_flow_with_valid_user_email(self, mock_dependencies):
        """Test normal flow when current_user_email context variable is set correctly"""
        # Setup: Context variable has valid user email
        mock_dependencies['current_user_email'].get.return_value = "john@example.com"

        # Execute
        get_llm_by_credentials(llm_model="gpt-4")

        # Verify: LiteLLM called with correct user email
        mock_dependencies['get_litellm_chat_model'].assert_called_once()
        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "john@example.com"

    def test_fallback_when_context_is_unknown(self, mock_dependencies, mock_user):
        """Test fallback when current_user_email returns 'unknown' (ContextVar default)"""
        # Setup: Context variable returns "unknown"
        mock_dependencies['current_user_email'].get.return_value = "unknown"

        # Setup: get_current_user returns authenticated user
        with patch('codemie.rest_api.security.user_context.get_current_user', return_value=mock_user):
            # Execute
            get_llm_by_credentials(llm_model="gpt-4")

        # Verify: LiteLLM called with fallback user email
        mock_dependencies['get_litellm_chat_model'].assert_called_once()
        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "test@example.com"

    def test_fallback_when_context_is_dash(self, mock_dependencies, mock_user):
        """Test fallback when current_user_email returns '-' (function default)"""
        # Setup: Context variable returns "-"
        mock_dependencies['current_user_email'].get.return_value = "-"

        # Setup: get_current_user returns authenticated user
        with patch('codemie.rest_api.security.user_context.get_current_user', return_value=mock_user):
            # Execute
            get_llm_by_credentials(llm_model="gpt-4")

        # Verify: LiteLLM called with fallback user email
        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "test@example.com"

    def test_fallback_when_context_is_empty(self, mock_dependencies, mock_user):
        """Test fallback when current_user_email returns empty string"""
        # Setup: Context variable returns empty string
        mock_dependencies['current_user_email'].get.return_value = ""

        # Setup: get_current_user returns authenticated user
        with patch('codemie.rest_api.security.user_context.get_current_user', return_value=mock_user):
            # Execute
            get_llm_by_credentials(llm_model="gpt-4")

        # Verify: LiteLLM called with fallback user email
        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "test@example.com"

    def test_fallback_when_context_is_none(self, mock_dependencies, mock_user):
        """Test fallback when current_user_email returns None"""
        # Setup: Context variable returns None
        mock_dependencies['current_user_email'].get.return_value = None

        # Setup: get_current_user returns authenticated user
        with patch('codemie.rest_api.security.user_context.get_current_user', return_value=mock_user):
            # Execute
            get_llm_by_credentials(llm_model="gpt-4")

        # Verify: LiteLLM called with fallback user email
        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "test@example.com"

    def test_fallback_prefers_username_over_id(self, mock_dependencies):
        """Test that fallback prefers username over id when both are available"""
        # Setup: Context variable returns "unknown"
        mock_dependencies['current_user_email'].get.return_value = "unknown"

        # Setup: User with both username and id
        user = Mock(spec=User)
        user.username = "preferred@example.com"
        user.id = "user-456"

        with patch('codemie.rest_api.security.user_context.get_current_user', return_value=user):
            # Execute
            get_llm_by_credentials(llm_model="gpt-4")

        # Verify: Username is used, not id
        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "preferred@example.com"

    def test_fallback_uses_id_when_username_is_none(self, mock_dependencies):
        """Test that fallback uses id when username is None"""
        # Setup: Context variable returns "unknown"
        mock_dependencies['current_user_email'].get.return_value = "unknown"

        # Setup: User with None username but valid id
        user = Mock(spec=User)
        user.username = None
        user.id = "user-789"

        with patch('codemie.rest_api.security.user_context.get_current_user', return_value=user):
            # Execute
            get_llm_by_credentials(llm_model="gpt-4")

        # Verify: ID is used
        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "user-789"

    def test_no_fallback_when_get_current_user_returns_none(self, mock_dependencies):
        """Test graceful handling when both context and get_current_user are empty (system operations)"""
        # Setup: Context variable returns "unknown"
        mock_dependencies['current_user_email'].get.return_value = "unknown"

        # Setup: get_current_user returns None (no authenticated user - system operation)
        with patch('codemie.rest_api.security.user_context.get_current_user', return_value=None):
            # Execute
            get_llm_by_credentials(llm_model="gpt-4")

        # Verify: LiteLLM called with original "unknown" value (no fallback possible)
        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "unknown"

    def test_fallback_with_different_llm_models(self, mock_dependencies, mock_user):
        """Test fallback works with different LLM models"""
        # Setup: Context variable returns "unknown"
        mock_dependencies['current_user_email'].get.return_value = "unknown"

        with patch('codemie.rest_api.security.user_context.get_current_user', return_value=mock_user):
            # Test with different model names
            for model_name in ["gpt-4", "gpt-3.5-turbo", "claude-3-opus"]:
                mock_dependencies['get_litellm_chat_model'].reset_mock()

                # Execute
                get_llm_by_credentials(llm_model=model_name)

                # Verify: Correct user email used regardless of model
                call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
                assert call_kwargs['user_email'] == "test@example.com"

    def test_fallback_preserves_other_parameters(self, mock_dependencies, mock_user):
        """Test that fallback doesn't affect other parameters"""
        # Setup: Context variable returns "unknown"
        mock_dependencies['current_user_email'].get.return_value = "unknown"

        with patch('codemie.rest_api.security.user_context.get_current_user', return_value=mock_user):
            # Execute with various parameters
            get_llm_by_credentials(
                llm_model="gpt-4", temperature=0.7, top_p=0.9, streaming=False, request_id="test-request-123"
            )

        # Verify: All parameters passed correctly
        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "test@example.com"
        assert call_kwargs['temperature'] == 0.7
        assert call_kwargs['top_p'] == 0.9
        assert not call_kwargs['streaming']

    def test_integration_scenario_toolkit_initialization(self, mock_dependencies, mock_user):
        """
        Integration test simulating the real bug scenario:
        Toolkit initialization happens before agent sets logging context
        """
        # Scenario: Toolkit being initialized, agent hasn't set logging context yet
        # Context variable still has default value from middleware
        mock_dependencies['current_user_email'].get.return_value = "-"

        # But user IS authenticated (request.state.user is set)
        with patch('codemie.rest_api.security.user_context.get_current_user', return_value=mock_user):
            # Toolkit calls get_llm_by_credentials to create LLM for tools
            get_llm_by_credentials(llm_model="gpt-4")

        # Verify: Tool will use correct user's budget
        call_kwargs = mock_dependencies['get_litellm_chat_model'].call_args[1]
        assert call_kwargs['user_email'] == "test@example.com"

        # Verify: This fixes the bug where tools were charged to "-" user
        assert call_kwargs['user_email'] != "-"
        assert call_kwargs['user_email'] != "unknown"
