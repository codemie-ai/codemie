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

"""Tests for LiteLLM credentials retrieval (codemie.enterprise.litellm.credentials)."""

from unittest.mock import patch


class TestGetLiteLLMCredentialsForUser:
    """Test get_litellm_credentials_for_user() function."""

    def test_returns_user_level_credentials_when_found(self):
        """Test returns user-level credentials when found."""
        from codemie.rest_api.models.settings import LiteLLMCredentials

        mock_credentials = LiteLLMCredentials(api_key="sk-user-key", url="http://localhost:4000")

        with patch(
            "codemie.service.settings.settings.SettingsService.get_litellm_creds", return_value=mock_credentials
        ) as mock_get_creds:
            from codemie.enterprise.litellm.credentials import get_litellm_credentials_for_user

            result = get_litellm_credentials_for_user(user_id="test-user", user_applications=["app1", "app2"])

            # Should return user-level credentials
            assert result is mock_credentials
            assert result.api_key == "sk-user-key"

            # Should have checked user-level first (project_name=None)
            mock_get_creds.assert_called_once_with(project_name=None, user_id="test-user")

    def test_returns_application_level_credentials_when_user_level_not_found(self):
        """Test returns app-level credentials when user-level not found."""
        from codemie.rest_api.models.settings import LiteLLMCredentials

        mock_app_credentials = LiteLLMCredentials(api_key="sk-app-key", url="http://localhost:4000")

        def get_creds_side_effect(project_name, user_id):
            if project_name is None:
                # User-level not found
                raise Exception("Not found")
            elif project_name == "app1":
                # App-level found
                return mock_app_credentials
            else:
                raise Exception("Not found")

        with patch(
            "codemie.service.settings.settings.SettingsService.get_litellm_creds", side_effect=get_creds_side_effect
        ) as mock_get_creds:
            from codemie.enterprise.litellm.credentials import get_litellm_credentials_for_user

            result = get_litellm_credentials_for_user(user_id="test-user", user_applications=["app1", "app2"])

            # Should return app-level credentials
            assert result is mock_app_credentials
            assert result.api_key == "sk-app-key"

            # Should have checked user-level first, then app1
            assert mock_get_creds.call_count == 2

    def test_checks_all_applications_in_order(self):
        """Test checks all applications in order until credentials found."""
        from codemie.rest_api.models.settings import LiteLLMCredentials

        mock_app2_credentials = LiteLLMCredentials(api_key="sk-app2-key", url="http://localhost:4000")

        def get_creds_side_effect(project_name, user_id):
            if project_name is None:
                # User-level not found
                raise Exception("Not found")
            elif project_name == "app1":
                # App1-level not found
                raise Exception("Not found")
            elif project_name == "app2":
                # App2-level found
                return mock_app2_credentials
            else:
                raise Exception("Not found")

        with patch(
            "codemie.service.settings.settings.SettingsService.get_litellm_creds", side_effect=get_creds_side_effect
        ) as mock_get_creds:
            from codemie.enterprise.litellm.credentials import get_litellm_credentials_for_user

            result = get_litellm_credentials_for_user(user_id="test-user", user_applications=["app1", "app2", "app3"])

            # Should return app2-level credentials
            assert result is mock_app2_credentials
            assert result.api_key == "sk-app2-key"

            # Should have checked user-level, app1, and app2 (stops at app2)
            assert mock_get_creds.call_count == 3

    def test_returns_none_when_no_credentials_found(self):
        """Test returns None when no credentials found anywhere."""
        with patch(
            "codemie.service.settings.settings.SettingsService.get_litellm_creds", side_effect=Exception("Not found")
        ) as mock_get_creds:
            from codemie.enterprise.litellm.credentials import get_litellm_credentials_for_user

            result = get_litellm_credentials_for_user(user_id="test-user", user_applications=["app1", "app2"])

            # Should return None
            assert result is None

            # Should have checked user-level and all apps
            assert mock_get_creds.call_count == 3  # user + app1 + app2

    def test_returns_none_when_no_applications(self):
        """Test returns None when user has no applications and no user-level creds."""
        with patch(
            "codemie.service.settings.settings.SettingsService.get_litellm_creds", side_effect=Exception("Not found")
        ) as mock_get_creds:
            from codemie.enterprise.litellm.credentials import get_litellm_credentials_for_user

            result = get_litellm_credentials_for_user(user_id="test-user", user_applications=[])

            # Should return None
            assert result is None

            # Should have checked only user-level
            assert mock_get_creds.call_count == 1

    def test_logs_debug_messages_on_success(self):
        """Test logs debug messages when credentials found."""
        from codemie.rest_api.models.settings import LiteLLMCredentials

        mock_credentials = LiteLLMCredentials(api_key="sk-user-key", url="http://localhost:4000")

        with (
            patch("codemie.service.settings.settings.SettingsService.get_litellm_creds", return_value=mock_credentials),
            patch("codemie.configs.logger") as mock_logger,
        ):
            from codemie.enterprise.litellm.credentials import get_litellm_credentials_for_user

            get_litellm_credentials_for_user(user_id="test-user", user_applications=["app1"])

            # Should have logged success
            mock_logger.debug.assert_called_once()
            call_args = mock_logger.debug.call_args[0][0]
            assert "Found user-level LiteLLM credentials" in call_args
            assert "test-user" in call_args

    def test_logs_debug_messages_on_failure(self):
        """Test logs debug/warning messages when credentials not found."""
        with (
            patch(
                "codemie.service.settings.settings.SettingsService.get_litellm_creds",
                side_effect=Exception("Not found"),
            ),
            patch("codemie.configs.logger") as mock_logger,
        ):
            from codemie.enterprise.litellm.credentials import get_litellm_credentials_for_user

            get_litellm_credentials_for_user(user_id="test-user", user_applications=["app1"])

            # Should have logged failures (warning for unexpected exceptions)
            # Since we raise generic Exception, it's logged as warning, not debug
            assert mock_logger.warning.call_count >= 1
            # At least one call should mention error retrieving credentials
            warning_calls = [call[0][0] for call in mock_logger.warning.call_args_list]
            assert any("error" in msg.lower() and "credentials" in msg.lower() for msg in warning_calls)

    def test_logs_debug_for_expected_exceptions(self):
        """Test logs debug (not warning) for expected exceptions like ValueError."""
        with (
            patch(
                "codemie.service.settings.settings.SettingsService.get_litellm_creds",
                side_effect=ValueError("Invalid credentials"),
            ),
            patch("codemie.configs.logger") as mock_logger,
        ):
            from codemie.enterprise.litellm.credentials import get_litellm_credentials_for_user

            get_litellm_credentials_for_user(user_id="test-user", user_applications=["app1"])

            # Should have logged with debug (expected exception)
            assert mock_logger.debug.call_count >= 1
            # Should NOT have logged warnings for expected exceptions
            assert mock_logger.warning.call_count == 0
            # At least one debug call should mention no credentials
            debug_calls = [call[0][0] for call in mock_logger.debug.call_args_list]
            assert any("No" in msg and "credentials" in msg for msg in debug_calls)

    def test_stops_at_first_valid_credentials(self):
        """Test stops searching when first valid credentials found (doesn't check remaining apps)."""
        from codemie.rest_api.models.settings import LiteLLMCredentials

        mock_credentials = LiteLLMCredentials(api_key="sk-app1-key", url="http://localhost:4000")

        def get_creds_side_effect(project_name, user_id):
            if project_name is None:
                # User-level not found
                raise Exception("Not found")
            elif project_name == "app1":
                # App1-level found
                return mock_credentials
            else:
                # Should never be called for app2
                raise AssertionError("Should not check app2")

        with patch(
            "codemie.service.settings.settings.SettingsService.get_litellm_creds", side_effect=get_creds_side_effect
        ) as mock_get_creds:
            from codemie.enterprise.litellm.credentials import get_litellm_credentials_for_user

            result = get_litellm_credentials_for_user(user_id="test-user", user_applications=["app1", "app2"])

            # Should return app1 credentials
            assert result is mock_credentials

            # Should have checked user-level and app1 only (not app2)
            assert mock_get_creds.call_count == 2
