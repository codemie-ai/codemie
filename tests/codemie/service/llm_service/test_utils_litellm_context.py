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
from codemie.rest_api.security.user import User


def _make_user(email: str = "user@test.com") -> MagicMock:
    user = MagicMock()
    user.email = email
    user.project_names = []
    user.admin_project_names = []
    user.default_project = None
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
        mock_user.email = 'user@example.com'

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
        mock_user.email = 'user@example.com'

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
        mock_user.email = 'user@example.com'

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
        mock_user.email = 'user@example.com'

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
        mock_user.email = 'user@example.com'

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
        mock_user.email = 'user@example.com'

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
    def test_global_asset_not_a_member_uses_default_project(self):
        """AC2: not a member, has default project -> charged to default project."""
        asset = MagicMock(spec=['project', 'is_global'])
        asset.project = "assistant-project"
        asset.is_global = True
        user = _make_user()
        user.default_project = "my-default-project"

        result = _resolve_effective_project(asset, None, user)

        assert result == "my-default-project"

    def test_global_asset_not_a_member_no_default_falls_back_to_email(self):
        """AC3: not a member, no default -> personal fallback unchanged."""
        asset = MagicMock(spec=['project', 'is_global'])
        asset.project = "assistant-project"
        asset.is_global = True
        user = _make_user()
        user.default_project = None

        result = _resolve_effective_project(asset, None, user)

        assert result == user.email

    def test_global_asset_membership_wins_over_different_default(self):
        """AC7: member of asset's project AND has a different default -> asset's project wins."""
        asset = MagicMock(spec=['project', 'is_global'])
        asset.project = "assistant-project"
        asset.is_global = True
        user = _make_user()
        user.project_names = ["assistant-project"]
        user.default_project = "some-other-project"

        result = _resolve_effective_project(asset, None, user)

        assert result == "assistant-project"

    def test_shared_assistant_returns_project(self):
        asset = MagicMock()
        asset.project = "proj-a"
        asset.shared = True
        asset.is_global = False
        user = _make_user()

        result = _resolve_effective_project(asset, None, user)

        assert result == "proj-a"

    def test_private_assistant_returns_project(self):
        asset = MagicMock()
        asset.project = "proj-a"
        asset.shared = False
        asset.is_global = False
        user = _make_user()

        result = _resolve_effective_project(asset, None, user)

        assert result == "proj-a"

    def test_shared_workflow_config_returns_project(self):
        asset = MagicMock()
        asset.project = "proj-b"
        asset.shared = True
        asset.is_global = False
        user = _make_user()

        result = _resolve_effective_project(asset, None, user)

        assert result == "proj-b"

    def test_private_workflow_config_returns_project(self):
        asset = MagicMock()
        asset.project = "proj-b"
        asset.shared = False
        asset.is_global = False
        user = _make_user()

        result = _resolve_effective_project(asset, None, user)

        assert result == "proj-b"

    def test_shared_index_info_returns_project_name(self):
        # IndexInfo has project_name (not project) and project_space_visible (not shared).
        # Use spec to ensure getattr(asset, 'shared', None) returns None (not a MagicMock).
        asset = MagicMock(spec=['id', 'project_name', 'project_space_visible'])
        asset.project_name = "proj-c"
        asset.project_space_visible = True
        user = _make_user()

        result = _resolve_effective_project(asset, None, user)

        assert result == "proj-c"

    def test_private_index_info_returns_project_name(self):
        asset = MagicMock(spec=['id', 'project_name', 'project_space_visible'])
        asset.project_name = "proj-c"
        asset.project_space_visible = False
        user = _make_user()

        result = _resolve_effective_project(asset, None, user)

        assert result == "proj-c"

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
    def test_set_llm_context_private_asset_current_project_is_set(self, mock_settings, mock_dial, mock_set_litellm):
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
        assert ctx.current_project == "proj-a"

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    def test_set_llm_context_shared_asset_creds_are_nulled(self, mock_settings, mock_dial, mock_set_litellm):
        """Shared asset + personal key → creds nulled; project budget wins."""
        personal_creds = LiteLLMCredentials(api_key="personal-key", url="https://litellm.test.com")
        mock_settings.get_litellm_creds.return_value = personal_creds

        mock_setting = MagicMock()
        mock_setting.setting_type = "user"  # not PROJECT — would normally allow bypass
        mock_setting.is_global = False  # non-global key — shared-asset routing must still null it
        mock_settings.retrieve_setting.return_value = mock_setting
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
        assert ctx.credentials is None  # shared asset → key must not bypass project budget
        assert ctx.current_project == "proj-a"

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    def test_set_llm_context_global_integration_shared_asset_creds_preserved(
        self, mock_settings, mock_dial, mock_set_litellm
    ):
        """Global USER integration (is_global=True) + shared asset → creds preserved; bypasses project budget."""
        personal_creds = LiteLLMCredentials(api_key="global-user-key", url="https://litellm.test.com")
        mock_settings.get_litellm_creds.return_value = personal_creds

        mock_setting = MagicMock()
        mock_setting.setting_type = "user"
        mock_setting.is_global = True  # global USER integration
        mock_settings.retrieve_setting.return_value = mock_setting
        mock_settings.get_dial_creds.return_value = None

        asset = MagicMock()
        asset.project = "other-project"
        asset.shared = True  # shared asset — normally nulls creds
        asset.is_global = False  # not a marketplace assistant
        user = _make_user()

        set_llm_context(asset, None, user)

        mock_set_litellm.assert_called_once()
        ctx = mock_set_litellm.call_args[0][0]
        assert isinstance(ctx, LiteLLMContext)
        assert ctx.credentials == personal_creds  # global integration must NOT be nulled
        assert ctx.current_project == "other-project"

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    def test_set_llm_context_private_asset_creds_are_kept(self, mock_settings, mock_dial, mock_set_litellm):
        """Private asset + personal key → creds kept; USER_CREDENTIALS_BYPASS applies."""
        personal_creds = LiteLLMCredentials(api_key="personal-key", url="https://litellm.test.com")
        mock_settings.get_litellm_creds.return_value = personal_creds

        mock_setting = MagicMock()
        mock_setting.setting_type = "user"  # not PROJECT
        mock_settings.retrieve_setting.return_value = mock_setting
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
        assert ctx.credentials == personal_creds  # private asset → key triggers bypass
        assert ctx.current_project == "proj-a"

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

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    def test_set_llm_context_asset_none_preserves_personal_creds(self, mock_settings, mock_dial, mock_set_litellm):
        """asset=None + personal USER key → shared-asset branch skipped, credentials preserved."""
        personal_creds = LiteLLMCredentials(api_key="global-key", url="https://litellm.test.com")
        mock_settings.get_litellm_creds.return_value = personal_creds

        mock_setting = MagicMock()
        mock_setting.setting_type = "user"
        mock_settings.retrieve_setting.return_value = mock_setting
        mock_settings.get_dial_creds.return_value = None

        user = _make_user()

        set_llm_context(None, "fallback-project", user)

        ctx = mock_set_litellm.call_args[0][0]
        assert ctx.credentials == personal_creds

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    def test_set_llm_context_project_scoped_setting_with_is_global_true_nulls_creds(
        self, mock_settings, mock_dial, mock_set_litellm
    ):
        """PROJECT-scoped setting with is_global=True → credentials nulled, is_global remains False."""
        personal_creds = LiteLLMCredentials(api_key="some-key", url="https://litellm.test.com")
        mock_settings.get_litellm_creds.return_value = personal_creds

        mock_setting = MagicMock()
        mock_setting.setting_type = "project"  # PROJECT-scoped — must be nulled
        mock_setting.is_global = True  # anomalous but should not override the PROJECT nulling
        mock_settings.retrieve_setting.return_value = mock_setting
        mock_settings.get_dial_creds.return_value = None

        asset = MagicMock()
        asset.project = "some-project"
        asset.is_global = False
        user = _make_user()

        set_llm_context(asset, None, user)

        ctx = mock_set_litellm.call_args[0][0]
        assert ctx.credentials is None


class TestUnfundedProjectAtContextCreation:
    """EPMCDME-15111 AC6/AC10: the fallback is decided with the context, so agent threads that
    re-create the context agree with the analytics emitted in the request thread."""

    def _user(self):
        return User(id="u-1", username="carol", email="carol@example.com", project_names=["proj-c"])

    def _run(self, scopes, creds=None, project="proj-c", enabled=True, mode="lite_llm"):
        from codemie.service.llm_service.utils import _unfunded_project

        with (
            patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=enabled),
            patch(
                "codemie.enterprise.litellm.llm_factory._probe_direct_project_budget_scopes", return_value=scopes
            ) as probe,
            patch("codemie.service.llm_service.utils.config.LLM_PROXY_MODE", mode),
        ):
            return _unfunded_project(project, creds, self._user()), probe

    def test_project_without_budget_is_unfunded(self):
        result, _ = self._run(scopes=set())
        assert result == "proj-c"

    def test_project_with_budget_is_funded(self):
        result, _ = self._run(scopes={"platform"})
        assert result is None

    def test_own_credentials_are_never_a_fallback(self):
        result, probe = self._run(scopes=set(), creds=MagicMock())
        assert result is None
        probe.assert_not_called()

    def test_personal_project_is_never_a_fallback(self):
        result, probe = self._run(scopes=set(), project="carol@example.com")
        assert result is None
        probe.assert_not_called()

    def test_internal_proxy_mode_skips_probe(self):
        result, probe = self._run(scopes=set(), mode="internal")
        assert result is None
        probe.assert_not_called()

    def test_probe_failure_keeps_original_attribution(self):
        from codemie.service.llm_service.utils import _unfunded_project

        with (
            patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=True),
            patch(
                "codemie.enterprise.litellm.llm_factory._probe_direct_project_budget_scopes",
                side_effect=RuntimeError("db down"),
            ),
            patch("codemie.service.llm_service.utils.config.LLM_PROXY_MODE", "lite_llm"),
        ):
            assert _unfunded_project("proj-c", None, self._user()) is None

    @patch("codemie.service.llm_service.utils.set_dial_credentials")
    @patch("codemie.service.llm_service.utils.set_litellm_context")
    @patch("codemie.service.llm_service.utils.SettingsService")
    @patch("codemie.service.llm_service.utils._unfunded_project", return_value="proj-c")
    def test_context_is_created_already_reattributed(self, _unfunded, mock_settings, mock_set_ctx, _dial):
        mock_settings.get_litellm_creds.return_value = None

        set_llm_context(None, "proj-c", self._user())

        ctx = mock_set_ctx.call_args[0][0]
        assert ctx.current_project == "carol@example.com"
        assert ctx.budget_fallback_from == "proj-c"


class TestUnfundedProjectIsCategoryAware:
    """R01: an allocation in some other category must not hide a fallback for the category this
    request will actually use (e.g. CLI-only project budget, web request charged to platform)."""

    def _run(self, scopes, llm_model="gpt-4.1"):
        from codemie.service.llm_service.utils import _unfunded_project

        user = User(id="u-1", username="carol", email="carol@example.com", project_names=["proj-c"])
        details = MagicMock()
        details.base_name = llm_model
        with (
            patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=True),
            patch("codemie.enterprise.litellm.llm_factory._probe_direct_project_budget_scopes", return_value=scopes),
            patch("codemie.enterprise.litellm.dependencies.get_premium_username", return_value=None),
            patch("codemie.service.llm_service.llm_service.llm_service.get_model_details", return_value=details),
            patch("codemie.service.llm_service.utils.config.LLM_PROXY_MODE", "lite_llm"),
        ):
            return _unfunded_project("proj-c", None, user, llm_model)

    def test_cli_only_project_falls_back_for_a_platform_request(self):
        from codemie.service.budget.budget_enums import BudgetCategory

        assert self._run({BudgetCategory.CLI}) == "proj-c"

    def test_platform_funded_project_is_not_a_fallback(self):
        from codemie.service.budget.budget_enums import BudgetCategory

        assert self._run({BudgetCategory.PLATFORM}) is None

    def test_unknown_model_with_some_funding_defers_to_runtime(self):
        from codemie.service.budget.budget_enums import BudgetCategory

        assert self._run({BudgetCategory.CLI}, llm_model=None) is None
