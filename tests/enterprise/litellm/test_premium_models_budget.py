# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Tests for EPMCDME-11075: optional LiteLLM premium models budget tracking.

Covers all acceptance criteria:
  - Config disabled → premium-budget logic skipped end-to-end
  - Config enabled + premium model → derived LiteLLM username used
  - Config enabled + non-premium model → standard username unchanged
  - /spending endpoint includes / omits premium_current_spending accordingly
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs.budget_config import budget_config
from codemie.configs.config import PredefinedBudgetConfig, config


# ---------------------------------------------------------------------------
# Cache clearing
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_premium_caches():
    """Clear lru_cache on cached premium-model helpers before and after every test.

    This is required because is_premium_models_enabled() and is_premium_model() use
    @lru_cache, so patching config values would have no effect on already-cached results
    without an explicit cache_clear() call.
    """
    from codemie.enterprise.litellm.dependencies import (
        is_premium_model,
        is_premium_models_enabled,
        is_proxy_budget_enabled,
    )

    is_proxy_budget_enabled.cache_clear()
    is_premium_models_enabled.cache_clear()
    is_premium_model.cache_clear()
    yield
    is_proxy_budget_enabled.cache_clear()
    is_premium_models_enabled.cache_clear()
    is_premium_model.cache_clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _patch_budget_name(value: str):
    """Enable/disable premium models feature via budget config.

    Reads predefined_budgets at __enter__ time so nested patches compose correctly.
    value non-empty → adds a premium_models budget with that budget_id.
    value empty → removes all premium_models budgets.
    """
    current = list(budget_config.predefined_budgets)
    filtered = [b for b in current if b.budget_category != "premium_models"]
    if value:
        new_budget = PredefinedBudgetConfig(
            budget_id=value,
            name="Premium",
            description=None,
            soft_budget=0.0,
            max_budget=0.0,
            budget_duration="30d",
            budget_category="premium_models",
        )
        new_list = filtered + [new_budget]
    else:
        new_list = filtered
    with patch.object(budget_config, "predefined_budgets", new_list):
        yield


def _patch_aliases(value: list[str]):
    return patch.object(config, "LITELLM_PREMIUM_MODELS_ALIASES", value)


@contextlib.contextmanager
def _patch_cli_budget_name(value: str):
    """Enable/disable CLI proxy budget feature via budget config.

    Reads predefined_budgets at __enter__ time so nested patches compose correctly.
    value non-empty → adds a cli budget with that budget_id.
    value empty → removes all cli budgets.
    """
    current = list(budget_config.predefined_budgets)
    filtered = [b for b in current if b.budget_category != "cli"]
    if value:
        new_budget = PredefinedBudgetConfig(
            budget_id=value,
            name="CLI",
            description=None,
            soft_budget=0.0,
            max_budget=0.0,
            budget_duration="30d",
            budget_category="cli",
        )
        new_list = filtered + [new_budget]
    else:
        new_list = filtered
    with patch.object(budget_config, "predefined_budgets", new_list):
        yield


# ---------------------------------------------------------------------------
# is_premium_models_enabled
# ---------------------------------------------------------------------------


class TestIsPremiumModelsEnabled:
    def test_disabled_when_budget_name_empty(self):
        with _patch_budget_name(""):
            from codemie.enterprise.litellm.dependencies import is_premium_models_enabled

            assert is_premium_models_enabled() is False

    def test_enabled_when_budget_name_set(self):
        with _patch_budget_name("premium_models"):
            from codemie.enterprise.litellm.dependencies import is_premium_models_enabled

            assert is_premium_models_enabled() is True


# ---------------------------------------------------------------------------
# is_premium_model
# ---------------------------------------------------------------------------


class TestIsPremiumModel:
    def test_returns_false_when_feature_disabled(self):
        with _patch_budget_name(""):
            from codemie.enterprise.litellm.dependencies import is_premium_model

            assert is_premium_model("claude-opus-4") is False

    def test_returns_false_when_aliases_empty(self):
        with _patch_budget_name("premium_models"), _patch_aliases([]):
            from codemie.enterprise.litellm.dependencies import is_premium_model

            assert is_premium_model("claude-opus-4") is False

    def test_matches_alias_partial_case_insensitive(self):
        with _patch_budget_name("premium_models"), _patch_aliases(["opus", "claude-4"]):
            from codemie.enterprise.litellm.dependencies import is_premium_model

            assert is_premium_model("claude-opus-4-20250514") is True

    def test_matches_second_alias(self):
        with _patch_budget_name("premium_models"), _patch_aliases(["opus", "claude-4"]):
            from codemie.enterprise.litellm.dependencies import is_premium_model

            assert is_premium_model("claude-4-sonnet") is True

    def test_no_match_for_non_premium_model(self):
        with _patch_budget_name("premium_models"), _patch_aliases(["opus", "claude-4"]):
            from codemie.enterprise.litellm.dependencies import is_premium_model

            assert is_premium_model("claude-3-5-sonnet") is False

    def test_case_insensitive_alias_match(self):
        with _patch_budget_name("premium_models"), _patch_aliases(["OPUS"]):
            from codemie.enterprise.litellm.dependencies import is_premium_model

            assert is_premium_model("claude-opus-4") is True


# ---------------------------------------------------------------------------
# get_premium_username
# ---------------------------------------------------------------------------


class TestGetPremiumUsername:
    def test_returns_none_when_feature_disabled(self):
        with _patch_budget_name(""):
            from codemie.enterprise.litellm.dependencies import get_premium_username

            assert get_premium_username("user@example.com", "claude-opus-4") is None

    def test_returns_none_for_non_premium_model(self):
        with _patch_budget_name("premium_models"), _patch_aliases(["opus"]):
            from codemie.enterprise.litellm.dependencies import get_premium_username

            assert get_premium_username("user@example.com", "claude-3-5-sonnet") is None

    def test_returns_derived_username_for_premium_model(self):
        with _patch_budget_name("premium_models"), _patch_aliases(["opus"]):
            from codemie.enterprise.litellm.dependencies import get_premium_username

            result = get_premium_username("user@example.com", "claude-opus-4")
            assert result == "user@example.com_codemie_premium_models"

    def test_derived_username_uses_category_suffix_not_budget_id(self):
        """Username suffix is always _codemie_{category} regardless of the configured budget_id."""
        with _patch_budget_name("costly_budget"), _patch_aliases(["opus"]):
            from codemie.enterprise.litellm.dependencies import get_premium_username

            result = get_premium_username("john@corp.com", "claude-opus-4-20250514")
            assert result == "john@corp.com_codemie_premium_models"


# ---------------------------------------------------------------------------
# get_premium_customer_spending
# ---------------------------------------------------------------------------


class TestGetPremiumCustomerSpending:
    def test_returns_none_when_feature_disabled(self):
        with _patch_budget_name(""):
            from codemie.enterprise.litellm.dependencies import get_premium_customer_spending

            result = get_premium_customer_spending("user@example.com")
            assert result is None

    def test_calls_get_customer_spending_with_derived_id(self):
        spending_data = {"customer_id": "user@example.com_premium_models", "total_spend": 12.5}

        with _patch_budget_name("premium_models"), _patch_aliases(["opus"]):
            with patch(
                "codemie.enterprise.litellm.dependencies.get_customer_spending",
                return_value=spending_data,
            ) as mock_get_spending:
                from codemie.enterprise.litellm.dependencies import get_premium_customer_spending

                result = get_premium_customer_spending("user@example.com")

                assert result == spending_data
                mock_get_spending.assert_called_once_with("user@example.com_codemie_premium_models", on_raise=False)

    def test_returns_none_when_derived_customer_not_found(self):
        with _patch_budget_name("premium_models"), _patch_aliases(["opus"]):
            with patch(
                "codemie.enterprise.litellm.dependencies.get_customer_spending",
                return_value=None,
            ):
                from codemie.enterprise.litellm.dependencies import get_premium_customer_spending

                result = get_premium_customer_spending("user@example.com")
                assert result is None

    def test_propagates_exception_when_on_raise_true(self):
        with _patch_budget_name("premium_models"), _patch_aliases(["opus"]):
            with patch(
                "codemie.enterprise.litellm.dependencies.get_customer_spending",
                side_effect=RuntimeError("LiteLLM unavailable"),
            ):
                from codemie.enterprise.litellm.dependencies import get_premium_customer_spending

                with pytest.raises(RuntimeError, match="LiteLLM unavailable"):
                    get_premium_customer_spending("user@example.com", on_raise=True)


class TestProxyBudgetHelpers:
    def test_proxy_budget_disabled_when_budget_name_empty(self):
        with _patch_cli_budget_name(""):
            from codemie.enterprise.litellm.dependencies import is_proxy_budget_enabled

            assert is_proxy_budget_enabled() is False

    def test_proxy_budget_enabled_when_budget_name_set(self):
        with _patch_cli_budget_name("cli_budget"):
            from codemie.enterprise.litellm.dependencies import is_proxy_budget_enabled

            assert is_proxy_budget_enabled() is True

    def test_get_proxy_username_returns_none_when_feature_disabled(self):
        with _patch_cli_budget_name(""):
            from codemie.enterprise.litellm.dependencies import get_proxy_username

            assert get_proxy_username("user@example.com") is None

    def test_get_proxy_username_returns_derived_username_when_enabled(self):
        with _patch_cli_budget_name("cli_budget"):
            from codemie.enterprise.litellm.dependencies import get_proxy_username

            assert get_proxy_username("user@example.com") == "user@example.com_codemie_cli"


# ---------------------------------------------------------------------------
# Proxy router: username injection
# ---------------------------------------------------------------------------


class TestProxyPremiumUsernameInjection:
    """Verify that _create_body_stream_with_optional_injection picks the right username."""

    @pytest.mark.asyncio
    async def test_injects_premium_username_for_premium_model(self):
        """Config enabled + premium model → derived username injected."""
        captured_usernames: list[str] = []

        def fake_inject(body_bytes, user_id, request_info):
            captured_usernames.append(user_id)

            async def gen():
                yield body_bytes

            return gen()

        with _patch_budget_name("premium_models"), _patch_aliases(["opus"]):
            with patch(
                "codemie.enterprise.litellm.proxy_router._inject_user_into_request_body_from_bytes",
                side_effect=fake_inject,
            ):
                with patch("codemie.enterprise.litellm.proxy_router.check_user_budget"):
                    from codemie.enterprise.litellm.proxy_router import (
                        _create_body_stream_with_optional_injection,
                    )

                    mock_user = MagicMock()
                    mock_user.username = "alice@example.com"

                    request_info = {"llm_model": "claude-opus-4"}

                    await _create_body_stream_with_optional_injection(
                        body_bytes=b'{"model":"claude-opus-4"}',
                        has_own_credentials=False,
                        user=mock_user,
                        request_info=request_info,
                    )

        assert captured_usernames == ["alice@example.com_codemie_premium_models"]

    @pytest.mark.asyncio
    async def test_injects_base_username_for_non_premium_model(self):
        """Config enabled + non-premium model → standard username used."""
        captured_usernames: list[str] = []

        def fake_inject(body_bytes, user_id, request_info):
            captured_usernames.append(user_id)

            async def gen():
                yield body_bytes

            return gen()

        with _patch_budget_name("premium_models"), _patch_aliases(["opus"]):
            with patch(
                "codemie.enterprise.litellm.proxy_router._inject_user_into_request_body_from_bytes",
                side_effect=fake_inject,
            ):
                with patch("codemie.enterprise.litellm.proxy_router.check_user_budget"):
                    from codemie.enterprise.litellm.proxy_router import (
                        _create_body_stream_with_optional_injection,
                    )

                    mock_user = MagicMock()
                    mock_user.username = "alice@example.com"

                    request_info = {"llm_model": "claude-3-5-sonnet"}

                    await _create_body_stream_with_optional_injection(
                        body_bytes=b'{"model":"claude-3-5-sonnet"}',
                        has_own_credentials=False,
                        user=mock_user,
                        request_info=request_info,
                    )

        assert captured_usernames == ["alice@example.com"]

    @pytest.mark.asyncio
    async def test_injects_proxy_username_for_non_premium_request(self):
        captured_usernames: list[str] = []

        def fake_inject(body_bytes, user_id, request_info):
            captured_usernames.append(user_id)

            async def gen():
                yield body_bytes

            return gen()

        with _patch_budget_name(""), _patch_cli_budget_name("cli_budget"):
            with patch(
                "codemie.enterprise.litellm.proxy_router._inject_user_into_request_body_from_bytes",
                side_effect=fake_inject,
            ):
                with patch("codemie.enterprise.litellm.proxy_router.check_user_budget"):
                    from codemie.enterprise.litellm.proxy_router import _create_body_stream_with_optional_injection

                    mock_user = MagicMock()
                    mock_user.username = "alice@example.com"

                    request_info = {"llm_model": "gpt-4.1-mini", "client_type": "web"}

                    await _create_body_stream_with_optional_injection(
                        body_bytes=b'{"model":"gpt-4.1-mini"}',
                        has_own_credentials=False,
                        user=mock_user,
                        request_info=request_info,
                    )

        assert captured_usernames == ["alice@example.com_codemie_cli"]

    @pytest.mark.asyncio
    async def test_premium_budget_takes_precedence_over_proxy_budget(self):
        captured_usernames: list[str] = []

        def fake_inject(body_bytes, user_id, request_info):
            captured_usernames.append(user_id)

            async def gen():
                yield body_bytes

            return gen()

        with _patch_budget_name("premium_models"), _patch_aliases(["opus"]), _patch_cli_budget_name("cli_budget"):
            with patch(
                "codemie.enterprise.litellm.proxy_router._inject_user_into_request_body_from_bytes",
                side_effect=fake_inject,
            ):
                with patch("codemie.enterprise.litellm.proxy_router.check_user_budget"):
                    from codemie.enterprise.litellm.proxy_router import _create_body_stream_with_optional_injection

                    mock_user = MagicMock()
                    mock_user.username = "alice@example.com"

                    request_info = {"llm_model": "claude-opus-4", "client_type": "web"}

                    await _create_body_stream_with_optional_injection(
                        body_bytes=b'{"model":"claude-opus-4"}',
                        has_own_credentials=False,
                        user=mock_user,
                        request_info=request_info,
                    )

        assert captured_usernames == ["alice@example.com_codemie_premium_models"]

    @pytest.mark.asyncio
    async def test_injects_base_username_when_config_disabled(self):
        """Config disabled → standard username used regardless of model name."""
        captured_usernames: list[str] = []

        def fake_inject(body_bytes, user_id, request_info):
            captured_usernames.append(user_id)

            async def gen():
                yield body_bytes

            return gen()

        with _patch_budget_name(""):
            with patch(
                "codemie.enterprise.litellm.proxy_router._inject_user_into_request_body_from_bytes",
                side_effect=fake_inject,
            ):
                with patch("codemie.enterprise.litellm.proxy_router.check_user_budget"):
                    from codemie.enterprise.litellm.proxy_router import (
                        _create_body_stream_with_optional_injection,
                    )

                    mock_user = MagicMock()
                    mock_user.username = "alice@example.com"

                    request_info = {"llm_model": "claude-opus-4"}

                    await _create_body_stream_with_optional_injection(
                        body_bytes=b'{"model":"claude-opus-4"}',
                        has_own_credentials=False,
                        user=mock_user,
                        request_info=request_info,
                    )

        assert captured_usernames == ["alice@example.com"]


# ---------------------------------------------------------------------------
# /spending endpoint: premium_current_spending metric
# ---------------------------------------------------------------------------


class TestSpendingEndpointPremiumMetric:
    """Verify /spending returns premium/cli spending metrics when available."""

    @pytest.mark.asyncio
    async def test_includes_premium_metric_when_configured(self):
        standard_spending = {
            "customer_id": "alice@example.com",
            "total_spend": 50.0,
            "max_budget": 300.0,
            "budget_reset_at": None,
        }
        premium_spending = {
            "customer_id": "alice@example.com_premium_models",
            "total_spend": 12.5,
            "max_budget": None,
            "budget_reset_at": None,
        }

        mock_user = MagicMock()
        mock_user.id = "user-1"
        mock_user.username = "alice@example.com"

        with _patch_budget_name("premium_models"):
            with patch(
                "codemie.rest_api.routers.analytics.asyncio.to_thread",
                new=AsyncMock(side_effect=[standard_spending, None, premium_spending]),
            ):
                from codemie.rest_api.routers.analytics import get_user_spending

                response = await get_user_spending(user=mock_user)
                body = response.body
                import json

                data = json.loads(body)
                metric_ids = [m["id"] for m in data["data"]["metrics"]]
                assert "premium_current_spending" in metric_ids

    @pytest.mark.asyncio
    async def test_includes_cli_metric_when_proxy_budget_configured(self):
        standard_spending = {
            "customer_id": "alice@example.com",
            "total_spend": 50.0,
            "max_budget": 300.0,
            "budget_reset_at": None,
        }
        cli_spending = {
            "customer_id": "alice@example.com_cli_budget",
            "total_spend": 7.5,
            "max_budget": None,
            "budget_reset_at": None,
        }

        mock_user = MagicMock()
        mock_user.id = "user-1"
        mock_user.username = "alice@example.com"

        with _patch_cli_budget_name("cli_budget"):
            with patch(
                "codemie.rest_api.routers.analytics.asyncio.to_thread",
                new=AsyncMock(side_effect=[standard_spending, cli_spending]),
            ):
                from codemie.rest_api.routers.analytics import get_user_spending

                response = await get_user_spending(user=mock_user)
                import json

                data = json.loads(response.body)
                metric_ids = [m["id"] for m in data["data"]["metrics"]]
                assert "cli_current_spending" in metric_ids

    @pytest.mark.asyncio
    async def test_omits_premium_metric_when_config_disabled(self):
        standard_spending = {
            "customer_id": "alice@example.com",
            "total_spend": 50.0,
            "max_budget": 300.0,
            "budget_reset_at": None,
        }

        mock_user = MagicMock()
        mock_user.id = "user-1"
        mock_user.username = "alice@example.com"

        with _patch_budget_name(""):
            with patch(
                "codemie.rest_api.routers.analytics.asyncio.to_thread",
                new=AsyncMock(return_value=standard_spending),
            ):
                from codemie.rest_api.routers.analytics import get_user_spending

                response = await get_user_spending(user=mock_user)
                import json

                data = json.loads(response.body)
                metric_ids = [m["id"] for m in data["data"]["metrics"]]
                assert "premium_current_spending" not in metric_ids

    @pytest.mark.asyncio
    async def test_omits_premium_metric_when_premium_spending_is_none(self):
        """Feature enabled but no premium customer yet → no premium metric in response."""
        standard_spending = {
            "customer_id": "alice@example.com",
            "total_spend": 50.0,
            "max_budget": 300.0,
            "budget_reset_at": None,
        }

        mock_user = MagicMock()
        mock_user.id = "user-1"
        mock_user.username = "alice@example.com"

        with _patch_budget_name("premium_models"):
            with patch(
                "codemie.rest_api.routers.analytics.asyncio.to_thread",
                new=AsyncMock(side_effect=[standard_spending, None]),
            ):
                from codemie.rest_api.routers.analytics import get_user_spending

                response = await get_user_spending(user=mock_user)
                import json

                data = json.loads(response.body)
                metric_ids = [m["id"] for m in data["data"]["metrics"]]
                assert "premium_current_spending" not in metric_ids
