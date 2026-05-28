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

from codemie.service.llm_service.utils import _resolve_effective_project, set_llm_context
from codemie.rest_api.models.settings import LiteLLMCredentials, LiteLLMContext


def _make_user(email: str = "user@test.com") -> MagicMock:
    user = MagicMock()
    user.email = email
    user.project_names = []
    user.admin_project_names = []
    user.id = "user-123"
    user.username = email
    return user


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
        mock_user = MagicMock()
        mock_user.id = user_id

        # Mock LiteLLM credentials
        litellm_creds = LiteLLMCredentials(api_key="litellm-key", url="https://litellm.test.com")
        mock_settings_service.get_litellm_creds.return_value = litellm_creds

        # Mock DIAL credentials
        dial_creds = MagicMock()
        dial_creds.api_key = "dial-key"
        dial_creds.url = "https://dial.test.com"
        mock_settings_service.get_dial_creds.return_value = dial_creds

        # Act
        set_llm_context(None, project_name, mock_user)

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
        mock_user = MagicMock()
        mock_user.id = user_id

        # Mock None LiteLLM credentials
        mock_settings_service.get_litellm_creds.return_value = None

        # Mock DIAL credentials
        dial_creds = MagicMock()
        mock_settings_service.get_dial_creds.return_value = dial_creds

        # Act
        set_llm_context(None, project_name, mock_user)

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
        mock_user = MagicMock()
        mock_user.id = user_id

        # Mock LiteLLM credentials
        litellm_creds = LiteLLMCredentials(api_key="litellm-key", url="https://litellm.test.com")
        mock_settings_service.get_litellm_creds.return_value = litellm_creds

        # Mock None DIAL credentials
        mock_settings_service.get_dial_creds.return_value = None

        # Act
        set_llm_context(None, project_name, mock_user)

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
        mock_user = MagicMock()
        mock_user.id = user_id

        # Mock exception in get_litellm_creds
        mock_settings_service.get_litellm_creds.side_effect = Exception("LiteLLM service error")

        # Act
        set_llm_context(None, project_name, mock_user)

        # Assert
        # Verify SettingsService was called
        mock_settings_service.get_litellm_creds.assert_called_once_with(project_name=project_name, user_id=user_id)

        # Verify dependencies functions were not called due to exception
        mock_set_litellm_context.assert_not_called()
        mock_set_dial_creds.assert_not_called()
        mock_settings_service.get_dial_creds.assert_not_called()

        # Verify warning logging (context loss is non-fatal; falls back to platform budget)
        mock_logger.warning.assert_called_once()
        warning_message = mock_logger.warning.call_args[0][0]
        assert f"project={project_name!r}" in warning_message
        assert user_id in warning_message
        assert "LiteLLM service error" in warning_message

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
        mock_user = MagicMock()
        mock_user.id = user_id

        # Mock successful LiteLLM credentials
        litellm_creds = LiteLLMCredentials(api_key="litellm-key", url="https://litellm.test.com")
        mock_settings_service.get_litellm_creds.return_value = litellm_creds

        # Mock exception in get_dial_creds
        mock_settings_service.get_dial_creds.side_effect = Exception("DIAL service error")

        # Act
        set_llm_context(None, project_name, mock_user)

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

        # Verify warning logging (context loss is non-fatal; falls back to platform budget)
        mock_logger.warning.assert_called_once()
        warning_message = mock_logger.warning.call_args[0][0]
        assert f"project={project_name!r}" in warning_message
        assert user_id in warning_message
        assert "DIAL service error" in warning_message

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
        mock_user = MagicMock()
        mock_user.id = user_id

        # Mock successful credential retrieval
        litellm_creds = LiteLLMCredentials(api_key="litellm-key", url="https://litellm.test.com")
        mock_settings_service.get_litellm_creds.return_value = litellm_creds

        dial_creds = MagicMock()
        mock_settings_service.get_dial_creds.return_value = dial_creds

        # Mock exception in set_litellm_context
        mock_set_litellm_context.side_effect = Exception("Context setting error")

        # Act
        set_llm_context(None, project_name, mock_user)

        # Assert
        # Verify SettingsService was called for LiteLLM creds
        mock_settings_service.get_litellm_creds.assert_called_once()

        # Verify get_dial_creds was NOT called due to early exception in set_litellm_context
        mock_settings_service.get_dial_creds.assert_not_called()

        # Verify set_litellm_context was called and failed
        mock_set_litellm_context.assert_called_once()

        # Verify set_dial_credentials was not called due to early exception
        mock_set_dial_creds.assert_not_called()

        # Verify warning logging (context loss is non-fatal; falls back to platform budget)
        mock_logger.warning.assert_called_once()
        warning_message = mock_logger.warning.call_args[0][0]
        assert f"project={project_name!r}" in warning_message
        assert user_id in warning_message
        assert "Context setting error" in warning_message


class TestResolveEffectiveProjectSharing:
    def test_shared_assistant_returns_project(self):
        asset = MagicMock()
        asset.project = "proj-a"
        asset.shared = True
        asset.is_global = False
        user = _make_user()

        result = _resolve_effective_project(asset, None, user)

        assert result == "proj-a"

    def test_private_assistant_returns_none(self):
        asset = MagicMock()
        asset.project = "proj-a"
        asset.shared = False
        asset.is_global = False
        user = _make_user()

        result = _resolve_effective_project(asset, None, user)

        assert result is None

    def test_shared_workflow_config_returns_project(self):
        asset = MagicMock()
        asset.project = "proj-b"
        asset.shared = True
        asset.is_global = False
        user = _make_user()

        result = _resolve_effective_project(asset, None, user)

        assert result == "proj-b"

    def test_private_workflow_config_returns_none(self):
        asset = MagicMock()
        asset.project = "proj-b"
        asset.shared = False
        asset.is_global = False
        user = _make_user()

        result = _resolve_effective_project(asset, None, user)

        assert result is None

    def test_shared_index_info_returns_project_name(self):
        # IndexInfo has project_name (not project) and project_space_visible (not shared).
        # Use spec to ensure getattr(asset, 'shared', None) returns None (not a MagicMock).
        asset = MagicMock(spec=['id', 'project_name', 'project_space_visible'])
        asset.project_name = "proj-c"
        asset.project_space_visible = True
        user = _make_user()

        result = _resolve_effective_project(asset, None, user)

        assert result == "proj-c"

    def test_private_index_info_returns_none(self):
        asset = MagicMock(spec=['id', 'project_name', 'project_space_visible'])
        asset.project_name = "proj-c"
        asset.project_space_visible = False
        user = _make_user()

        result = _resolve_effective_project(asset, None, user)

        assert result is None

    def test_flag_disabled_private_asset_still_returns_project(self):
        asset = MagicMock()
        asset.project = "proj-d"
        asset.shared = False
        asset.is_global = False
        user = _make_user()

        with patch('codemie.service.llm_service.utils.config') as mock_config:
            mock_config.LLM_PROXY_SHARED_ASSET_PROJECT_BUDGET_ROUTING_ENABLED = False
            result = _resolve_effective_project(asset, None, user)

        assert result == "proj-d"

    def test_none_asset_returns_fallback_project_name(self):
        user = _make_user()

        result = _resolve_effective_project(None, "fallback-proj", user)

        assert result == "fallback-proj"

    def test_none_asset_none_fallback_returns_none(self):
        user = _make_user()

        result = _resolve_effective_project(None, None, user)

        assert result is None

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    def test_set_llm_context_private_asset_current_project_is_none(self, mock_settings, mock_dial, mock_set_litellm):
        mock_settings.get_litellm_creds.return_value = None
        mock_settings.get_dial_creds.return_value = None

        asset = MagicMock()
        asset.project = "proj-a"
        asset.shared = False
        asset.is_global = False
        user = _make_user()

        set_llm_context(asset, None, user)

        mock_set_litellm.assert_called_once()
        ctx = mock_set_litellm.call_args[0][0]
        assert isinstance(ctx, LiteLLMContext)
        assert ctx.current_project is None

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    def test_set_llm_context_shared_asset_current_project_is_set(self, mock_settings, mock_dial, mock_set_litellm):
        mock_settings.get_litellm_creds.return_value = None
        mock_settings.get_dial_creds.return_value = None

        asset = MagicMock()
        asset.project = "proj-a"
        asset.shared = True
        asset.is_global = False
        user = _make_user()

        set_llm_context(asset, None, user)

        mock_set_litellm.assert_called_once()
        ctx = mock_set_litellm.call_args[0][0]
        assert isinstance(ctx, LiteLLMContext)
        assert ctx.current_project == "proj-a"
