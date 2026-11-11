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

from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from codemie.service.llm_service.utils import _litellm_budgets_active as _real_budgets_active
from codemie.service.llm_service.utils import set_llm_context
from codemie.rest_api.models.settings import LiteLLMCredentials, LiteLLMContext
from codemie.rest_api.security.user import User


@pytest.fixture(autouse=True)
def _litellm_budgets_active():
    """Budget attribution runs only in LiteLLM mode; tests that need the other mode patch it back."""
    with patch('codemie.service.llm_service.utils._litellm_budgets_active', return_value=True) as active:
        yield active


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
        mock_user.email = "user@test.com"
        mock_user.project_names = [project_name]
        mock_user.admin_project_names = []
        mock_user.default_project = None

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
        mock_user.email = "user@test.com"
        mock_user.project_names = [project_name]
        mock_user.admin_project_names = []
        mock_user.default_project = None

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
        mock_user.email = "user@test.com"
        mock_user.project_names = [project_name]
        mock_user.admin_project_names = []
        mock_user.default_project = None

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
        mock_user.email = "user@test.com"
        mock_user.project_names = [project_name]
        mock_user.admin_project_names = []
        mock_user.default_project = None

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
        mock_user.email = "user@test.com"
        mock_user.project_names = [project_name]
        mock_user.admin_project_names = []
        mock_user.default_project = None

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
        mock_user.email = "user@test.com"
        mock_user.project_names = [project_name]
        mock_user.admin_project_names = []
        mock_user.default_project = None

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


class TestSetLLMContextProjectSelection:
    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    def test_set_llm_context_records_the_principal_email(self, mock_settings, mock_dial, mock_set_litellm):
        """The email names the personal project; the spend header compares the project with it."""
        mock_settings.get_litellm_creds.return_value = None
        mock_settings.get_dial_creds.return_value = None
        user = _make_user("alice@example.com")
        user.username = "alice"

        set_llm_context(None, "alice@example.com", user)

        ctx = mock_set_litellm.call_args[0][0]
        assert ctx.principal_email == "alice@example.com"
        assert ctx.current_project == "alice@example.com"

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
        user.project_names = ["proj-a"]

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
        user.project_names = ["proj-a"]

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
        user.project_names = ["other-project"]

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
        user.project_names = ["proj-a"]

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
        user.project_names = ["proj-a"]

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

    def test_probe_failure_is_reported_as_unknown_funding(self):
        """A failed funded check propagates, so the resolver walks on exactly as on the CLI (EPMCDME-16625 AR-1)."""
        from codemie.service.llm_service.utils import _unfunded_project

        with (
            patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=True),
            patch(
                "codemie.enterprise.litellm.llm_factory._probe_direct_project_budget_scopes",
                side_effect=RuntimeError("db down"),
            ),
            patch("codemie.service.llm_service.utils.config.LLM_PROXY_MODE", "lite_llm"),
            pytest.raises(RuntimeError, match="db down"),
        ):
            _unfunded_project("proj-c", None, self._user(), "gpt-4.1")

    def test_probe_failure_walks_to_the_default_project(self):
        from codemie.service.budget.attribution_resolver import (
            AssetKind,
            FallbackKind,
            attribution_input_for_user,
            resolve_attribution,
        )

        user = SimpleNamespace(
            id="u-1",
            email="carol@example.com",
            username="carol",
            project_names=["proj-c", "proj-d"],
            admin_project_names=[],
            default_project="proj-d",
        )

        def is_funded(project: str) -> bool:
            if project == "proj-c":
                raise RuntimeError("db down")
            return True

        inp = attribution_input_for_user(user, kind=AssetKind.PROJECT_ASSET, asset_project="proj-c")
        decision = resolve_attribution(inp, is_funded)

        assert decision.charged_project == "proj-d"
        assert decision.fallback_kind is FallbackKind.DEFAULT_PROJECT
        assert decision.funding_unknown is True
        assert decision.candidates == ("proj-c", "proj-d")

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


class TestAttributionMatrixWiring:
    """EPMCDME-16625: set_llm_context charges target -> default project -> personal via the shared resolver."""

    @staticmethod
    def _user(default="team-d", members=("team-a", "team-d")):
        return User(
            id="u-1",
            username="carol",
            email="carol@example.com",
            project_names=list(members),
            default_project=default,
        )

    @staticmethod
    def _asset(project="team-a", is_global=False, shared=False):
        asset = MagicMock(spec=['project', 'is_global', 'shared', 'llm_model_type', 'id'])
        asset.project = project
        asset.is_global = is_global
        asset.shared = shared
        asset.llm_model_type = None
        return asset

    @staticmethod
    def _ctx(asset, user, funded, fallback=None):
        def fake_unfunded(project, creds, u, model=None):
            return None if project in funded else project

        with (
            patch('codemie.service.llm_service.utils.set_litellm_context') as set_ctx,
            patch('codemie.service.llm_service.utils.set_dial_credentials'),
            patch('codemie.service.llm_service.utils.SettingsService') as settings,
            patch('codemie.service.llm_service.utils._unfunded_project', side_effect=fake_unfunded) as unfunded,
        ):
            settings.get_litellm_creds.return_value = None
            set_llm_context(asset, fallback, user)
        return set_ctx.call_args[0][0], unfunded

    def test_marketplace_assistant_member_charges_default_project(self):
        ctx, _ = self._ctx(self._asset(is_global=True), self._user(), {"team-a", "team-d"})

        assert ctx.current_project == "team-d"
        assert ctx.budget_fallback_from is None
        assert ctx.budget_fallback_kind is None

    def test_marketplace_assistant_nonmember_charges_default_project(self):
        user = self._user(members=("team-d",))

        ctx, _ = self._ctx(self._asset(project="foreign", is_global=True), user, {"foreign", "team-d"})

        assert ctx.current_project == "team-d"

    def test_marketplace_default_unfunded_falls_to_personal_recorded_from_default(self):
        ctx, _ = self._ctx(self._asset(is_global=True), self._user(), set())

        assert ctx.current_project == "carol@example.com"
        assert ctx.budget_fallback_from == "team-d"
        assert ctx.budget_fallback_kind == "personal"

    def test_shared_assistant_nonmember_never_charges_asset_project(self):
        user = self._user(members=("team-d",))

        ctx, unfunded = self._ctx(self._asset(project="foreign", shared=True), user, {"foreign", "team-d"})

        assert ctx.current_project == "team-d"
        assert "foreign" not in [call.args[0] for call in unfunded.call_args_list]

    def test_personal_assistant_owner_with_team_default_charges_default(self):
        user = self._user(members=("team-d", "carol@example.com"))

        ctx, _ = self._ctx(self._asset(project="carol@example.com"), user, {"team-d"})

        assert ctx.current_project == "team-d"

    def test_personal_assistant_without_assigned_default_stays_personal(self):
        user = self._user(default="carol@example.com", members=("carol@example.com",))

        ctx, _ = self._ctx(self._asset(project="carol@example.com"), user, set())

        assert ctx.current_project == "carol@example.com"
        assert ctx.budget_fallback_from is None
        assert ctx.budget_fallback_kind is None

    def test_member_unfunded_project_falls_to_funded_default(self):
        ctx, _ = self._ctx(self._asset(), self._user(), {"team-d"})

        assert ctx.current_project == "team-d"
        assert ctx.budget_fallback_from == "team-a"
        assert ctx.budget_fallback_kind == "default_project"

    def test_member_unfunded_project_and_default_fall_to_personal(self):
        ctx, _ = self._ctx(self._asset(), self._user(), set())

        assert ctx.current_project == "carol@example.com"
        assert ctx.budget_fallback_from == "team-a"
        assert ctx.budget_fallback_kind == "personal"

    def test_member_funded_project_is_charged(self):
        ctx, _ = self._ctx(self._asset(), self._user(), {"team-a", "team-d"})

        assert ctx.current_project == "team-a"
        assert ctx.budget_fallback_kind is None

    def test_asset_less_flow_with_unfunded_default_falls_to_personal(self):
        ctx, _ = self._ctx(None, self._user(), set(), fallback="request-project")

        assert ctx.current_project == "carol@example.com"
        assert ctx.budget_fallback_from == "team-d"
        assert ctx.budget_fallback_kind == "personal"

    def test_deployment_tags_keep_the_asset_project_when_the_default_pays(self):
        """Review AR-5: attribution moves the charge, not the request, to the default project's deployments."""
        ctx, _ = self._ctx(self._asset(), self._user(), {"team-d"})

        assert ctx.current_project == "team-d"
        assert ctx.routing_project == "team-a"

    def test_user_without_email_is_charged_personal_under_their_username(self):
        """Review AR-1: a stand-in owner is reported as a personal charge, not under the asset's project."""
        user = User(id="u-1", username="u-1", email="", project_names=[], default_project=None)

        ctx, unfunded = self._ctx(self._asset(), user, set())

        assert ctx.current_project == "u-1"
        assert ctx.personal_project == "u-1"
        assert ctx.budget_candidates == []
        unfunded.assert_not_called()


class TestAReDecisionWithUnknownFundingKeepsTheBuiltOutcome:
    """Final review AR-2: the agent's model is built once; a later re-decision whose funded check fails must not
    replace the payer that model actually charges."""

    @staticmethod
    def _decide(funded_or_error, previous):
        from codemie.core.dependecies import litellm_context

        def fake_unfunded(project, creds, u, model=None):
            if funded_or_error == "error":
                raise RuntimeError("db blip")
            return None if project in funded_or_error else project

        token = litellm_context.set(previous)
        try:
            with (
                patch('codemie.service.llm_service.utils.set_litellm_context') as set_ctx,
                patch('codemie.service.llm_service.utils.set_dial_credentials'),
                patch('codemie.service.llm_service.utils.SettingsService') as settings,
                patch('codemie.service.llm_service.utils._unfunded_project', side_effect=fake_unfunded),
            ):
                settings.get_litellm_creds.return_value = None
                set_llm_context(TestAttributionMatrixWiring._asset(), None, TestAttributionMatrixWiring._user())
        finally:
            litellm_context.reset(token)
        return set_ctx.call_args[0][0]

    @staticmethod
    def _built(**overrides):
        values = {
            "credentials": None,
            "current_project": "team-a",
            "principal_email": "carol@example.com",
            "personal_project": "carol@example.com",
            "budget_candidates": ["team-a", "team-d"],
            "routing_project": "team-a",
            "budget_outcome_built": True,
        }
        values.update(overrides)
        return LiteLLMContext(**values)

    def test_the_built_payer_is_kept(self):
        ctx = self._decide("error", self._built())

        assert ctx.current_project == "team-a"
        assert ctx.budget_fallback_kind is None
        assert ctx.budget_outcome_built is True

    def test_an_agreeing_readable_re_decision_keeps_the_built_mark(self):
        """Between the build and a failing re-decision the agent re-decides with readable funding on every call."""
        agreeing = self._decide({"team-a"}, self._built())
        assert agreeing.budget_outcome_built is True

        ctx = self._decide("error", agreeing)

        assert ctx.current_project == "team-a"
        assert ctx.budget_fallback_kind is None

    def test_a_readable_re_decision_still_replaces_it(self):
        ctx = self._decide({"team-d"}, self._built())

        assert ctx.current_project == "team-d"
        assert ctx.budget_fallback_kind == "default_project"

    def test_a_context_of_another_request_is_not_kept(self):
        ctx = self._decide("error", self._built(routing_project="other"))

        assert ctx.current_project == "carol@example.com"
        assert ctx.budget_fallback_kind == "personal"
        assert ctx.budget_funding_unknown is True

    def test_funding_unknown_reaches_metrics_with_the_fallback(self):
        from codemie.core.dependecies import litellm_context
        from codemie.service.monitoring.base_monitoring_service import _add_budget_fallback_attribute

        ctx = self._decide("error", None)
        token = litellm_context.set(ctx)
        try:
            attributes: dict = {}
            _add_budget_fallback_attribute(attributes)
        finally:
            litellm_context.reset(token)

        assert attributes["budget_fallback_kind"] == "personal"
        assert attributes["budget_funding_unknown"] is True


class TestVirtualAssistantWithoutANamedProject:
    """Final review AR-4: the virtual assistant's project only selects credentials; the request is billed as
    asset-less, to the default the profile shows, on the IDE and the inline endpoint alike."""

    @staticmethod
    def _virtual(project="team-a", asset_less=True):
        from codemie.rest_api.models.assistant import VirtualIdeAssistant

        assistant = VirtualIdeAssistant(
            id="ide-1", name="Virtual", description="N/A", system_prompt="s", project=project, llm_model_type="m"
        )
        assistant._runtime_bills_as_asset_less = asset_less
        return assistant

    def test_the_default_project_pays_although_the_assistant_names_a_member_project(self):
        ctx, _ = TestAttributionMatrixWiring._ctx(
            self._virtual(), TestAttributionMatrixWiring._user(), {"team-a", "team-d"}
        )

        assert ctx.current_project == "team-d"
        assert ctx.budget_fallback_kind is None
        assert ctx.routing_project == "team-a"

    def test_credentials_are_still_looked_up_under_the_assistant_project(self):
        creds_project, dial_project = TestCredentialLookupProjectUnchanged._lookup_project(
            self._virtual(), TestAttributionMatrixWiring._user()
        )

        assert creds_project == "team-a"
        assert dial_project == "team-a"

    def test_a_named_project_is_billed_as_the_target(self):
        ctx, _ = TestAttributionMatrixWiring._ctx(
            self._virtual(asset_less=False), TestAttributionMatrixWiring._user(), {"team-a", "team-d"}
        )

        assert ctx.current_project == "team-a"


class TestCredentialLookupProjectUnchanged:
    """EPMCDME-16625 row 18: own-key lookup keeps using the asset's project; only budget attribution moved."""

    @staticmethod
    def _lookup_project(asset, user):
        with (
            patch('codemie.service.llm_service.utils.set_litellm_context'),
            patch('codemie.service.llm_service.utils.set_dial_credentials'),
            patch('codemie.service.llm_service.utils.SettingsService') as settings,
            patch('codemie.service.llm_service.utils._unfunded_project', return_value=None),
        ):
            settings.get_litellm_creds.return_value = None
            set_llm_context(asset, None, user)
        return settings.get_litellm_creds.call_args.kwargs["project_name"], settings.get_dial_creds.call_args.args[0]

    def test_marketplace_member_looks_up_own_key_in_asset_project(self):
        user = TestAttributionMatrixWiring._user()

        creds_project, dial_project = self._lookup_project(TestAttributionMatrixWiring._asset(is_global=True), user)

        assert creds_project == "team-a"
        assert dial_project == "team-a"

    def test_shared_nonmember_looks_up_own_key_in_asset_project(self):
        user = TestAttributionMatrixWiring._user(members=("team-d",))

        creds_project, _ = self._lookup_project(TestAttributionMatrixWiring._asset(project="foreign"), user)

        assert creds_project == "foreign"


class TestPartialUserObjects:
    """Attribution must not break a request for user objects missing membership data (e.g. internal callers)."""

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    def test_user_without_membership_fields_still_gets_context(self, mock_settings, _dial, mock_set_litellm):
        from types import SimpleNamespace

        mock_settings.get_litellm_creds.return_value = None
        user = SimpleNamespace(id="u-1", username="carol", email="carol@example.com")
        asset = MagicMock(spec=['project', 'is_global', 'llm_model_type'])
        asset.project = "team-a"
        asset.is_global = False
        asset.llm_model_type = None

        set_llm_context(asset, None, user)

        assert mock_set_litellm.call_args[0][0].current_project == "carol@example.com"

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    def test_mock_user_with_non_list_memberships_still_gets_context(self, mock_settings, _dial, mock_set_litellm):
        from unittest.mock import Mock

        mock_settings.get_litellm_creds.return_value = None
        user = Mock()
        user.id = "u-1"
        user.email = "carol@example.com"

        set_llm_context(None, "team-a", user)

        mock_set_litellm.assert_called_once()


class TestFundedCheckUsesARealModel:
    """CR-011: with no model on the asset the funded check uses the platform default model, so it is category-aware."""

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    def test_default_model_is_used_when_the_asset_names_none(self, mock_settings, _dial, _set_ctx):
        mock_settings.get_litellm_creds.return_value = None
        user = TestAttributionMatrixWiring._user()

        from unittest.mock import PropertyMock

        from codemie.service.llm_service.llm_service import LLMService

        with (
            patch('codemie.service.llm_service.utils._unfunded_project', return_value=None) as unfunded,
            patch.object(LLMService, 'default_llm_model', new_callable=PropertyMock, return_value='platform-default'),
        ):
            set_llm_context(TestAttributionMatrixWiring._asset(), None, user)

        assert unfunded.call_args.args[3] == 'platform-default'

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    def test_indexing_is_checked_against_the_embedding_model(self, mock_settings, _dial, _set_ctx):
        """Indexing calls the datasource's embedding model, so funding is checked in that model's category."""

        mock_settings.get_litellm_creds.return_value = None
        user = TestAttributionMatrixWiring._user()
        index = _index_info(embeddings_model='emb-x')

        with patch('codemie.service.llm_service.utils._unfunded_project', return_value=None) as unfunded:
            set_llm_context(index, None, user)

        assert unfunded.call_args.args[3] == 'emb-x'

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    def test_indexing_without_a_model_uses_the_default_embedding_model(self, mock_settings, _dial, _set_ctx):
        from unittest.mock import PropertyMock

        from codemie.service.llm_service.llm_service import LLMService

        mock_settings.get_litellm_creds.return_value = None
        user = TestAttributionMatrixWiring._user()
        index = _index_info(embeddings_model=None)

        with (
            patch('codemie.service.llm_service.utils._unfunded_project', return_value=None) as unfunded,
            patch.object(LLMService, 'default_embedding_model', new_callable=PropertyMock, return_value='emb-default'),
        ):
            set_llm_context(index, None, user)

        assert unfunded.call_args.args[3] == 'emb-default'

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    def test_indexing_with_native_embeddings_is_checked_against_the_default_chat_model(
        self, mock_settings, _dial, set_ctx
    ):
        """Final review AR-3: only indexing's chat models reach a budget then, so the walk is decided for them."""
        from unittest.mock import PropertyMock

        from codemie.service.llm_service.llm_service import LLMService

        mock_settings.get_litellm_creds.return_value = None
        user = TestAttributionMatrixWiring._user()
        index = _index_info(embeddings_model='emb-x')

        with (
            patch('codemie.service.llm_service.utils.config.LLM_PROXY_EMBEDDINGS_DISABLED', True),
            patch('codemie.service.llm_service.utils._unfunded_project', return_value=None) as unfunded,
            patch.object(LLMService, 'default_llm_model', new_callable=PropertyMock, return_value='chat-default'),
        ):
            set_llm_context(index, None, user)

        assert unfunded.call_args.args[3] == 'chat-default'
        assert set_ctx.call_args.args[0].budget_candidates == ['team-a', 'team-d']


def _index_info(embeddings_model):
    from codemie.rest_api.models.index import IndexInfo

    index = MagicMock(spec=IndexInfo)
    index.project = None
    index.project_name = 'team-a'
    index.is_global = False
    index.embeddings_model = embeddings_model
    return index


class TestWithoutLiteLLM:
    """Without LiteLLM no budget pays, so the context keeps the project used before EPMCDME-16625."""

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    def test_marketplace_assistant_keeps_its_project(self, mock_settings, _dial, set_ctx, _litellm_budgets_active):
        mock_settings.get_litellm_creds.return_value = None
        _litellm_budgets_active.return_value = False
        user = TestAttributionMatrixWiring._user()

        with patch('codemie.service.llm_service.utils.resolve_attribution') as resolver:
            set_llm_context(TestAttributionMatrixWiring._asset("team-a", is_global=True), None, user)

        resolver.assert_not_called()
        context = set_ctx.call_args.args[0]
        assert context.current_project == "team-a"
        assert context.budget_fallback_from is None
        mock_settings.get_dial_creds.assert_called_once_with("team-a")


class TestCredentialsAssetIsSeparateFromBilling:
    """A marketplace workflow bills the saved assistant it runs but keeps the run's credentials (EPMCDME-16625)."""

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    def test_credentials_follow_the_execution_copy(self, mock_settings, _dial, _set_ctx, _litellm_budgets_active):
        mock_settings.get_litellm_creds.return_value = None
        user = TestAttributionMatrixWiring._user(default=None, members=("team-d",))
        saved = TestAttributionMatrixWiring._asset("team-a", is_global=False)
        execution_copy = TestAttributionMatrixWiring._asset("team-a", is_global=True)

        with patch('codemie.service.llm_service.utils._unfunded_project', return_value=None):
            set_llm_context(saved, None, user, credentials_asset=execution_copy)

        mock_settings.get_litellm_creds.assert_called_once_with(project_name=user.email, user_id=user.id)
        mock_settings.get_dial_creds.assert_called_once_with(user.email)

    @patch('codemie.service.llm_service.utils.set_litellm_context')
    @patch('codemie.service.llm_service.utils.set_dial_credentials')
    @patch('codemie.service.llm_service.utils.SettingsService')
    def test_without_litellm_credentials_still_follow_the_execution_copy(
        self, mock_settings, _dial, set_ctx, _litellm_budgets_active
    ):
        mock_settings.get_litellm_creds.return_value = None
        _litellm_budgets_active.return_value = False
        user = TestAttributionMatrixWiring._user(default=None, members=("team-d",))

        set_llm_context(
            TestAttributionMatrixWiring._asset("team-a"),
            None,
            user,
            credentials_asset=TestAttributionMatrixWiring._asset("team-a", is_global=True),
        )

        mock_settings.get_dial_creds.assert_called_once_with(user.email)
        assert set_ctx.call_args.args[0].current_project == user.email


class TestLiteLLMBudgetsActive:
    """Attribution applies only to requests a LiteLLM budget pays for."""

    @pytest.mark.parametrize(
        "enabled,mode,expected",
        [(True, "lite_llm", True), (True, "internal", False), (False, "lite_llm", False)],
        ids=["lite-llm", "internal-mode", "disabled"],
    )
    def test_gate(self, enabled, mode, expected):
        with (
            patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=enabled),
            patch("codemie.service.llm_service.utils.config.LLM_PROXY_MODE", mode),
        ):
            assert _real_budgets_active() is expected

    @pytest.mark.parametrize("embeddings_disabled", [False, True], ids=["litellm-embeddings", "native-embeddings"])
    def test_indexing_is_attributed_whatever_runs_its_embeddings(self, embeddings_disabled):
        """Final review AR-3: indexing's summary and document chat models still go through LiteLLM."""
        with (
            patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=True),
            patch("codemie.service.llm_service.utils.config.LLM_PROXY_MODE", "lite_llm"),
            patch("codemie.service.llm_service.utils.config.LLM_PROXY_EMBEDDINGS_DISABLED", embeddings_disabled),
        ):
            assert _real_budgets_active() is True
