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

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import httpx
from codemie.service.security.token_providers.broker_token_exchange_provider import BrokerTokenExchangeProvider
from codemie.service.security.token_providers.base_provider import TokenProviderException
from codemie.configs.config import config


@pytest.fixture
def mock_config():
    # Helper to clean up config after tests
    original_urls = config.BROKER_TOKEN_URLS
    original_realms = config.BROKER_TOKEN_REALMS
    original_brokers = config.BROKER_TOKEN_BROKERS

    yield config

    config.BROKER_TOKEN_URLS = original_urls
    config.BROKER_TOKEN_REALMS = original_realms
    config.BROKER_TOKEN_BROKERS = original_brokers


def setup_config(mock_cfg, urls="", realms="", brokers=""):
    mock_cfg.BROKER_TOKEN_URLS = urls
    mock_cfg.BROKER_TOKEN_REALMS = realms
    mock_cfg.BROKER_TOKEN_BROKERS = brokers


def test_init_pass_through(mock_config):
    setup_config(mock_config, "", "", "")
    provider = BrokerTokenExchangeProvider()
    assert provider.urls == []
    assert provider.realms == []
    assert provider.brokers == []


def test_init_valid_config(mock_config):
    setup_config(mock_config, "url1,url2", "realm1,realm2", "broker1,broker2")
    provider = BrokerTokenExchangeProvider()
    assert provider.urls == ["url1", "url2"]
    assert provider.realms == ["realm1", "realm2"]
    assert provider.brokers == ["broker1", "broker2"]


def test_init_incomplete_config(mock_config):
    setup_config(mock_config, "url1", "", "broker1")
    with pytest.raises(TokenProviderException) as exc:
        BrokerTokenExchangeProvider()
    assert "configuration is incomplete" in str(exc.value)


def test_init_mismatched_length(mock_config):
    setup_config(mock_config, "url1,url2", "realm1", "broker1,broker2")
    with pytest.raises(TokenProviderException) as exc:
        BrokerTokenExchangeProvider()
    assert "must have the same length" in exc.value.details


@pytest.mark.asyncio
async def test_aget_token_pass_through(mock_config):
    setup_config(mock_config, "", "", "")
    provider = BrokerTokenExchangeProvider()

    with patch(
        "codemie.service.security.token_providers.broker_token_exchange_provider.get_current_auth_token",
        return_value="initial-token",
    ):
        token = await provider._aget_token()
        assert token == "initial-token"


@pytest.mark.asyncio
async def test_aget_token_no_initial_token(mock_config):
    setup_config(mock_config, "", "", "")
    provider = BrokerTokenExchangeProvider()

    with patch(
        "codemie.service.security.token_providers.broker_token_exchange_provider.get_current_auth_token",
        return_value=None,
    ):
        token = await provider._aget_token()
        assert token is None


@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_aget_token_multi_hop_success(mock_client_cls, mock_config):
    setup_config(mock_config, "http://host1", "realm1", "broker1")
    provider = BrokerTokenExchangeProvider()

    mock_client = AsyncMock()
    mock_client_cls.return_value.__aenter__.return_value = mock_client

    # Mock successful response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"access_token": "exchanged-token"}
    mock_client.get.return_value = mock_response

    with patch(
        "codemie.service.security.token_providers.broker_token_exchange_provider.get_current_auth_token",
        return_value="initial-token",
    ):
        token = await provider._aget_token()

    assert token == "exchanged-token"
    mock_client.get.assert_called_once()

    # Verify URL construction
    call_args = mock_client.get.call_args
    assert "http://host1/realms/realm1/broker/broker1/token" in call_args[0][0]
    assert call_args[1]["headers"]["Authorization"] == "Bearer initial-token"


@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_aget_token_http_error(mock_client_cls, mock_config):
    setup_config(mock_config, "http://host1", "realm1", "broker1")
    provider = BrokerTokenExchangeProvider()

    mock_client = AsyncMock()
    mock_client_cls.return_value.__aenter__.return_value = mock_client

    # Mock error response
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.reason_phrase = "Unauthorized"
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Error", request=MagicMock(), response=mock_response
    )
    mock_client.get.return_value = mock_response

    with patch(
        "codemie.service.security.token_providers.broker_token_exchange_provider.get_current_auth_token",
        return_value="initial-token",
    ):
        with pytest.raises(TokenProviderException) as exc:
            await provider._aget_token()

    assert "Broker token exchange failed with HTTP 401" in str(exc.value)


def test_get_token_sync_wrapper(mock_config):
    setup_config(mock_config, "", "", "")
    provider = BrokerTokenExchangeProvider()

    with patch(
        "codemie.service.security.token_providers.broker_token_exchange_provider.get_current_auth_token",
        return_value="initial-token",
    ):
        token = provider.get_token()
        assert token == "initial-token"
