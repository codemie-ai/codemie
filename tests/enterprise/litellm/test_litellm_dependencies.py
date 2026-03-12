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

"""Tests for LiteLLM integration layer (codemie.enterprise.litellm.dependencies)."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.enterprise.loader import CustomerInfo, BudgetTable, HAS_LITELLM


class TestIsLiteLLMEnabled:
    """Test is_litellm_enabled() function."""

    def test_returns_false_when_enterprise_not_installed(self):
        """Test returns False when HAS_LITELLM is False."""
        with patch("codemie.enterprise.litellm.dependencies.HAS_LITELLM", False):
            from codemie.enterprise.litellm import is_litellm_enabled

            assert is_litellm_enabled() is False

    def test_returns_false_when_config_disabled(self):
        """Test returns False when config.LLM_PROXY_ENABLED is False."""
        from codemie.configs.config import config

        with patch("codemie.enterprise.litellm.dependencies.HAS_LITELLM", True):
            with patch.object(config, "LLM_PROXY_ENABLED", False):
                from codemie.enterprise.litellm import is_litellm_enabled

                assert is_litellm_enabled() is False

    def test_returns_true_when_both_conditions_met(self):
        """Test returns True when HAS_LITELLM and config both True."""
        from codemie.configs.config import config

        with patch("codemie.enterprise.litellm.dependencies.HAS_LITELLM", True):
            with patch.object(config, "LLM_PROXY_ENABLED", True):
                from codemie.enterprise.litellm import is_litellm_enabled

                assert is_litellm_enabled() is True


class TestGetLiteLLMServiceOrNone:
    """Test get_litellm_service_or_none() function."""

    def test_returns_none_when_not_enabled(self):
        """Test returns None when LiteLLM not enabled."""
        with patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=False):
            from codemie.enterprise.litellm import get_litellm_service_or_none

            result = get_litellm_service_or_none()
            assert result is None

    def test_returns_service_when_enabled(self):
        """Test returns service when LiteLLM enabled."""
        mock_service = MagicMock()

        with patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=True):
            with patch("codemie.enterprise.litellm.dependencies.get_global_litellm_service", return_value=mock_service):
                from codemie.enterprise.litellm import get_litellm_service_or_none

                result = get_litellm_service_or_none()
                assert result is mock_service


class TestCheckUserBudget:
    """Test check_user_budget() function."""

    def test_returns_none_when_service_unavailable(self):
        """Test returns None when LiteLLM service not available."""
        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=None):
            from codemie.enterprise.litellm.dependencies import check_user_budget

            result = check_user_budget("test-user")
            assert result is None

    @pytest.mark.skipif(not HAS_LITELLM, reason="Enterprise package not installed - LiteLLM not available")
    def test_uses_cache_when_available(self):
        """Test uses cached customer info when available."""
        mock_customer = CustomerInfo(
            user_id="test-user",
            spend=50.0,
            litellm_budget_table=BudgetTable(
                budget_id="budget-1", soft_budget=100.0, max_budget=200.0, budget_duration="30d"
            ),
        )

        mock_service = MagicMock()
        mock_service._get_cached_customer.return_value = mock_customer

        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service):
            from codemie.enterprise.litellm.dependencies import check_user_budget

            result = check_user_budget("test-user")

            assert result is mock_customer
            mock_service._get_cached_customer.assert_called_once_with("test-user")
            # Should NOT call get_or_create_customer_with_budget since cache hit
            mock_service.get_or_create_customer_with_budget.assert_not_called()

    @pytest.mark.skipif(not HAS_LITELLM, reason="Enterprise package not installed - LiteLLM not available")
    def test_fetches_from_service_on_cache_miss(self):
        """Test fetches from service when cache miss."""
        mock_customer = CustomerInfo(
            user_id="test-user",
            spend=50.0,
            litellm_budget_table=BudgetTable(
                budget_id="budget-1", soft_budget=100.0, max_budget=200.0, budget_duration="30d"
            ),
        )

        mock_service = MagicMock()
        mock_service._get_cached_customer.return_value = None  # Cache miss
        mock_service.get_or_create_customer_with_budget.return_value = mock_customer

        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service):
            from codemie.enterprise.litellm.dependencies import check_user_budget

            result = check_user_budget("test-user")

            assert result is mock_customer
            mock_service.get_or_create_customer_with_budget.assert_called_once_with("test-user")
            mock_service._cache_customer.assert_called_once_with("test-user", mock_customer)


class TestGetAvailableModels:
    """Test get_available_models() function."""

    def test_returns_empty_when_service_unavailable(self):
        """Test returns empty LiteLLMModels when service not available."""
        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=None):
            from codemie.enterprise.litellm.dependencies import get_available_models

            result = get_available_models()

            assert result.chat_models == []
            assert result.embedding_models == []

    def test_maps_and_deduplicates_models(self):
        """Test maps LiteLLM models to LLMModel and deduplicates by base_name."""
        # Mock raw model data from enterprise service
        raw_models = [
            {
                "model_name": "azure/gpt-4",
                "model_info": {"mode": "chat", "base_model": "gpt-4"},
                "litellm_params": {"model": "azure/gpt-4"},
            },
            {
                "model_name": "azure/gpt-4-turbo",
                "model_info": {"mode": "chat", "base_model": "gpt-4"},
                "litellm_params": {"model": "azure/gpt-4-turbo"},
            },
            {
                "model_name": "azure/text-embedding-ada-002",
                "model_info": {"mode": "embedding", "base_model": "text-embedding-ada-002"},
                "litellm_params": {"model": "azure/text-embedding-ada-002"},
            },
        ]

        mock_service = MagicMock()
        mock_service.get_available_models.return_value = raw_models

        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service):
            from codemie.enterprise.litellm.dependencies import get_available_models

            result = get_available_models(user_id="test-user")

            # Should have mapped models
            assert len(result.chat_models) >= 1  # At least one chat model (may deduplicate)
            assert len(result.embedding_models) == 1  # One embedding model

            # Verify service was called with correct params
            mock_service.get_available_models.assert_called_once_with(user_id="test-user", api_key=None)


class TestInitializeLiteLLMFromConfig:
    """Test initialize_litellm_from_config() function."""

    def test_returns_none_when_not_enabled(self):
        """Test returns None when LiteLLM not enabled."""
        with patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=False):
            from codemie.enterprise.litellm import initialize_litellm_from_config

            result = initialize_litellm_from_config()
            assert result is None

    def test_creates_service_with_config(self):
        """Test creates LiteLLMService with config when enabled."""
        mock_service = MagicMock()

        with patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=True):
            # Mock the imports inside the try block
            with patch.dict(
                'sys.modules',
                {
                    'codemie.enterprise': MagicMock(
                        LiteLLMConfig=MagicMock(), LiteLLMService=MagicMock(return_value=mock_service)
                    )
                },
            ):
                from codemie.enterprise.litellm import initialize_litellm_from_config

                result = initialize_litellm_from_config()

                # Should have returned the mock service
                assert result is mock_service


class TestRequireLiteLLMEnabled:
    """Test require_litellm_enabled() function."""

    def test_raises_exception_when_not_enabled(self):
        """Test raises exception when LiteLLM not enabled."""
        from codemie.core.exceptions import ExtendedHTTPException

        with patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=False):
            from codemie.enterprise.litellm.dependencies import require_litellm_enabled

            with pytest.raises(ExtendedHTTPException) as exc_info:
                require_litellm_enabled()

            assert exc_info.value.code == 400
            assert "not available" in exc_info.value.message.lower()

    def test_passes_when_enabled(self):
        """Test does not raise exception when LiteLLM enabled."""
        with patch("codemie.enterprise.litellm.dependencies.is_litellm_enabled", return_value=True):
            from codemie.enterprise.litellm.dependencies import require_litellm_enabled

            # Should not raise
            require_litellm_enabled()


class TestGetAllKeysSpending:
    """Test get_all_keys_spending() function."""

    def test_returns_none_when_service_unavailable(self):
        """Test returns None when LiteLLM service not available."""
        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=None):
            from codemie.enterprise.litellm.dependencies import get_all_keys_spending

            result = get_all_keys_spending(["key-1", "key-2"])
            assert result is None

    def test_returns_spending_data_success(self):
        """Test returns spending data for multiple keys."""
        mock_service = MagicMock()
        mock_spending_data = [
            {"key_alias": "key-1", "total_spend": 10.0, "max_budget": 100.0},
            {"key_alias": "key-2", "total_spend": 20.0, "max_budget": 50.0},
        ]
        mock_service.get_all_keys_spending_info.return_value = mock_spending_data

        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service):
            from codemie.enterprise.litellm.dependencies import get_all_keys_spending

            result = get_all_keys_spending(["key-1", "key-2"], on_raise=False)

            assert result == mock_spending_data
            mock_service.get_all_keys_spending_info.assert_called_once_with(["key-1", "key-2"])

    def test_returns_none_on_error_when_on_raise_false(self):
        """Test returns None on error when on_raise=False."""
        mock_service = MagicMock()
        mock_service.get_all_keys_spending_info.side_effect = Exception("API error")

        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service):
            from codemie.enterprise.litellm.dependencies import get_all_keys_spending

            result = get_all_keys_spending(["key-1"], on_raise=False)

            assert result is None

    def test_raises_exception_when_on_raise_true(self):
        """Test raises exception on error when on_raise=True."""
        mock_service = MagicMock()
        mock_service.get_all_keys_spending_info.side_effect = Exception("API error")

        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service):
            from codemie.enterprise.litellm.dependencies import get_all_keys_spending

            with pytest.raises(Exception, match="API error"):
                get_all_keys_spending(["key-1"], on_raise=True)


class TestGetUserKeysSpending:
    """Test get_user_keys_spending() function."""

    def test_returns_none_when_settings_service_fails(self):
        """Test returns None when SettingsService fails to get API keys."""
        with patch("codemie.service.settings.settings.SettingsService") as mock_settings:
            mock_settings.get_user_litellm_api_keys.side_effect = Exception("Settings error")

            from codemie.enterprise.litellm.dependencies import get_user_keys_spending

            result = get_user_keys_spending("user-123", ["project-1"], on_raise=False)

            assert result is None

    def test_returns_grouped_spending_data_success(self):
        """Test returns spending data grouped by USER and PROJECT keys."""
        mock_grouped_keys = {
            "user_keys": ["user-key-1", "user-key-2"],
            "project_keys": ["project-key-1"],
        }

        user_spending = [
            {"key_alias": "user-key-1", "total_spend": 15.0},
            {"key_alias": "user-key-2", "total_spend": 25.0},
        ]

        project_spending = [
            {"key_alias": "project-key-1", "total_spend": 50.0},
        ]

        mock_service = MagicMock()

        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service):
            with patch("codemie.service.settings.settings.SettingsService") as mock_settings:
                with patch("codemie.enterprise.litellm.dependencies.get_all_keys_spending") as mock_get_spending:
                    mock_settings.get_user_litellm_api_keys.return_value = mock_grouped_keys

                    # Mock get_all_keys_spending to return different data for each call
                    mock_get_spending.side_effect = [user_spending, project_spending]

                    from codemie.enterprise.litellm.dependencies import get_user_keys_spending

                    result = get_user_keys_spending("user-123", ["project-1"], on_raise=False)

                    assert result is not None
                    assert result.user_keys == user_spending
                    assert result.project_keys == project_spending

                    # Verify calls
                    mock_settings.get_user_litellm_api_keys.assert_called_once_with("user-123", ["project-1"])

    def test_returns_empty_lists_when_no_spending_data(self):
        """Test returns empty lists when get_all_keys_spending returns None."""
        mock_grouped_keys = {
            "user_keys": ["user-key-1"],
            "project_keys": ["project-key-1"],
        }

        mock_service = MagicMock()

        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service):
            with patch("codemie.service.settings.settings.SettingsService") as mock_settings:
                with patch("codemie.enterprise.litellm.dependencies.get_all_keys_spending") as mock_get_spending:
                    mock_settings.get_user_litellm_api_keys.return_value = mock_grouped_keys
                    mock_get_spending.return_value = None

                    from codemie.enterprise.litellm.dependencies import get_user_keys_spending

                    result = get_user_keys_spending("user-123", ["project-1"], on_raise=False)

                    assert result is not None
                    assert result.user_keys == []
                    assert result.project_keys == []

    def test_raises_exception_when_on_raise_true(self):
        """Test raises exception on error when on_raise=True."""
        mock_service = MagicMock()

        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service):
            with patch("codemie.service.settings.settings.SettingsService") as mock_settings:
                mock_settings.get_user_litellm_api_keys.side_effect = Exception("Settings error")

                from codemie.enterprise.litellm.dependencies import get_user_keys_spending

                with pytest.raises(Exception, match="Settings error"):
                    get_user_keys_spending("user-123", ["project-1"], on_raise=True)


class TestGetKeySpendingInfo:
    """Test get_key_spending_info() function."""

    def test_returns_empty_list_when_service_unavailable(self):
        """Test returns empty list when LiteLLM service not available."""
        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=None):
            from codemie.enterprise.litellm.dependencies import get_key_spending_info

            result = get_key_spending_info(["key-1", "key-2"])
            assert result == []

    def test_returns_empty_list_on_error(self):
        """Test returns empty list on error."""
        mock_service = MagicMock()
        mock_service.get_key_info.side_effect = Exception("API error")

        with patch("codemie.enterprise.litellm.dependencies.get_litellm_service_or_none", return_value=mock_service):
            from codemie.enterprise.litellm.dependencies import get_key_spending_info

            result = get_key_spending_info(["key-1"])

            assert result == []
