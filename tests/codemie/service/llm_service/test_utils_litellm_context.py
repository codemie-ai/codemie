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

"""
Tests for LiteLLM context functionality in llm_service utils module.
"""

from unittest.mock import patch, MagicMock

from codemie.service.llm_service.utils import set_llm_context
from codemie.rest_api.models.settings import LiteLLMCredentials, LiteLLMContext


class TestSetLLMContext:
    """Test suite for set_llm_context function."""

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    @patch('codemie.service.llm_service.utils.logger')
    def test_set_llm_context_success_with_litellm_and_dial_creds(
        self, mock_logger, mock_settings_service, mock_set_dial_creds, mock_set_litellm_context
    ):
        """Test successful set_llm_context with both LiteLLM and DIAL credentials."""
        # Arrange
        project_name = "test-project"
        user_id = "test-user-123"

        # Mock LiteLLM credentials
        litellm_creds = LiteLLMCredentials(api_key="litellm-key", url="https://litellm.test.com")
        mock_settings_service.get_litellm_creds.return_value = litellm_creds

        # Mock DIAL credentials
        dial_creds = MagicMock()
        dial_creds.api_key = "dial-key"
        dial_creds.url = "https://dial.test.com"
        mock_settings_service.get_dial_creds.return_value = dial_creds

        # Act
        set_llm_context(project_name, user_id)

        # Assert
        # Verify SettingsService calls
        mock_settings_service.get_litellm_creds.assert_called_once_with(project_name=project_name, user_id=user_id)
        mock_settings_service.get_dial_creds.assert_called_once_with(project_name)

        # Verify set_litellm_context was called with correct LiteLLMContext
        mock_set_litellm_context.assert_called_once()
        call_args = mock_set_litellm_context.call_args[0][0]
        assert isinstance(call_args, LiteLLMContext)
        assert call_args.credentials == litellm_creds
        assert call_args.current_project == project_name

        # Verify set_dial_credentials was called
        mock_set_dial_creds.assert_called_once_with(dial_creds)

        # Verify no error logging
        mock_logger.error.assert_not_called()

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    @patch('codemie.service.llm_service.utils.logger')
    def test_set_llm_context_success_with_none_litellm_creds(
        self, mock_logger, mock_settings_service, mock_set_dial_creds, mock_set_litellm_context
    ):
        """Test successful set_llm_context with None LiteLLM credentials."""
        # Arrange
        project_name = "test-project"
        user_id = "test-user-123"

        # Mock None LiteLLM credentials
        mock_settings_service.get_litellm_creds.return_value = None

        # Mock DIAL credentials
        dial_creds = MagicMock()
        mock_settings_service.get_dial_creds.return_value = dial_creds

        # Act
        set_llm_context(project_name, user_id)

        # Assert
        # Verify SettingsService calls
        mock_settings_service.get_litellm_creds.assert_called_once_with(project_name=project_name, user_id=user_id)
        mock_settings_service.get_dial_creds.assert_called_once_with(project_name)

        # Verify set_litellm_context was called with None credentials
        mock_set_litellm_context.assert_called_once()
        call_args = mock_set_litellm_context.call_args[0][0]
        assert isinstance(call_args, LiteLLMContext)
        assert call_args.credentials is None
        assert call_args.current_project == project_name

        # Verify set_dial_credentials was called
        mock_set_dial_creds.assert_called_once_with(dial_creds)

        # Verify no error logging
        mock_logger.error.assert_not_called()

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    @patch('codemie.service.llm_service.utils.logger')
    def test_set_llm_context_success_with_none_dial_creds(
        self, mock_logger, mock_settings_service, mock_set_dial_creds, mock_set_litellm_context
    ):
        """Test successful set_llm_context with None DIAL credentials."""
        # Arrange
        project_name = "test-project"
        user_id = "test-user-123"

        # Mock LiteLLM credentials
        litellm_creds = LiteLLMCredentials(api_key="litellm-key", url="https://litellm.test.com")
        mock_settings_service.get_litellm_creds.return_value = litellm_creds

        # Mock None DIAL credentials
        mock_settings_service.get_dial_creds.return_value = None

        # Act
        set_llm_context(project_name, user_id)

        # Assert
        # Verify SettingsService calls
        mock_settings_service.get_litellm_creds.assert_called_once_with(project_name=project_name, user_id=user_id)
        mock_settings_service.get_dial_creds.assert_called_once_with(project_name)

        # Verify set_litellm_context was called
        mock_set_litellm_context.assert_called_once()
        call_args = mock_set_litellm_context.call_args[0][0]
        assert isinstance(call_args, LiteLLMContext)
        assert call_args.credentials == litellm_creds
        assert call_args.current_project == project_name

        # Verify set_dial_credentials was called with None
        mock_set_dial_creds.assert_called_once_with(None)

        # Verify no error logging
        mock_logger.error.assert_not_called()

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    @patch('codemie.service.llm_service.utils.logger')
    def test_set_llm_context_exception_in_get_litellm_creds(
        self, mock_logger, mock_settings_service, mock_set_dial_creds, mock_set_litellm_context
    ):
        """Test set_llm_context when get_litellm_creds raises an exception."""
        # Arrange
        project_name = "test-project"
        user_id = "test-user-123"

        # Mock exception in get_litellm_creds
        mock_settings_service.get_litellm_creds.side_effect = Exception("LiteLLM service error")

        # Act
        set_llm_context(project_name, user_id)

        # Assert
        # Verify SettingsService was called
        mock_settings_service.get_litellm_creds.assert_called_once_with(project_name=project_name, user_id=user_id)

        # Verify dependencies functions were not called due to exception
        mock_set_litellm_context.assert_not_called()
        mock_set_dial_creds.assert_not_called()
        mock_settings_service.get_dial_creds.assert_not_called()

        # Verify error logging
        mock_logger.error.assert_called_once()
        error_message = mock_logger.error.call_args[0][0]
        assert f"Cannot get/set current llm credentials for project: {project_name}" in error_message
        assert f"user: {user_id}" in error_message
        assert "LiteLLM service error" in error_message

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    @patch('codemie.service.llm_service.utils.logger')
    def test_set_llm_context_exception_in_get_dial_creds(
        self, mock_logger, mock_settings_service, mock_set_dial_creds, mock_set_litellm_context
    ):
        """Test set_llm_context when get_dial_creds raises an exception."""
        # Arrange
        project_name = "test-project"
        user_id = "test-user-123"

        # Mock successful LiteLLM credentials
        litellm_creds = LiteLLMCredentials(api_key="litellm-key", url="https://litellm.test.com")
        mock_settings_service.get_litellm_creds.return_value = litellm_creds

        # Mock exception in get_dial_creds
        mock_settings_service.get_dial_creds.side_effect = Exception("DIAL service error")

        # Act
        set_llm_context(project_name, user_id)

        # Assert
        # Verify both SettingsService methods were called
        mock_settings_service.get_litellm_creds.assert_called_once_with(project_name=project_name, user_id=user_id)
        mock_settings_service.get_dial_creds.assert_called_once_with(project_name)

        # Verify set_litellm_context was called (it succeeds before the DIAL error)
        mock_set_litellm_context.assert_called_once()
        call_args = mock_set_litellm_context.call_args[0][0]
        assert isinstance(call_args, LiteLLMContext)
        assert call_args.credentials == litellm_creds
        assert call_args.current_project == project_name

        # Verify set_dial_credentials was not called due to exception
        mock_set_dial_creds.assert_not_called()

        # Verify error logging
        mock_logger.error.assert_called_once()
        error_message = mock_logger.error.call_args[0][0]
        assert f"Cannot get/set current llm credentials for project: {project_name}" in error_message
        assert f"user: {user_id}" in error_message
        assert "DIAL service error" in error_message

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    @patch('codemie.service.llm_service.utils.logger')
    def test_set_llm_context_exception_in_set_context_functions(
        self, mock_logger, mock_settings_service, mock_set_dial_creds, mock_set_litellm_context
    ):
        """Test set_llm_context when set context functions raise exceptions."""
        # Arrange
        project_name = "test-project"
        user_id = "test-user-123"

        # Mock successful credential retrieval
        litellm_creds = LiteLLMCredentials(api_key="litellm-key", url="https://litellm.test.com")
        mock_settings_service.get_litellm_creds.return_value = litellm_creds

        dial_creds = MagicMock()
        mock_settings_service.get_dial_creds.return_value = dial_creds

        # Mock exception in set_litellm_context
        mock_set_litellm_context.side_effect = Exception("Context setting error")

        # Act
        set_llm_context(project_name, user_id)

        # Assert
        # Verify SettingsService was called for LiteLLM creds
        mock_settings_service.get_litellm_creds.assert_called_once()

        # Verify get_dial_creds was NOT called due to early exception in set_litellm_context
        mock_settings_service.get_dial_creds.assert_not_called()

        # Verify set_litellm_context was called and failed
        mock_set_litellm_context.assert_called_once()

        # Verify set_dial_credentials was not called due to early exception
        mock_set_dial_creds.assert_not_called()

        # Verify error logging
        mock_logger.error.assert_called_once()
        error_message = mock_logger.error.call_args[0][0]
        assert f"Cannot get/set current llm credentials for project: {project_name}" in error_message
        assert f"user: {user_id}" in error_message
        assert "Context setting error" in error_message
