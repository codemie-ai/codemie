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

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from codemie.service.security.oidc_token_exchange_service import OIDCTokenExchangeService
from codemie.service.security.token_providers.base_provider import TokenProviderException
from codemie.rest_api.security.user import User

_AUDIENCE = "oauth-client.epm-srdr.staffing-radar"


@pytest.fixture
def mock_cache():
    return MagicMock()


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.id = "user-123"
    return user


@pytest.fixture
def service(mock_cache):
    # Reset singleton so each test gets a clean instance with an injected mock cache
    OIDCTokenExchangeService._instance = None
    svc = OIDCTokenExchangeService()
    svc._cache = mock_cache
    return svc


# ---------------------------------------------------------------------------
# get_exchanged_token
# ---------------------------------------------------------------------------


@patch("codemie.service.security.oidc_token_exchange_service.get_current_user")
def test_get_exchanged_token_no_user(mock_get_user, service, mock_cache):
    """No user in context → return None immediately, cache never touched."""
    mock_get_user.return_value = None

    result = service.get_exchanged_token(_AUDIENCE)

    assert result is None
    mock_cache.get.assert_not_called()


@patch("codemie.service.security.oidc_token_exchange_service.get_current_user")
def test_get_exchanged_token_cache_hit(mock_get_user, service, mock_user, mock_cache):
    """Valid cache entry → return it without calling the factory or Keycloak."""
    mock_get_user.return_value = mock_user
    mock_cache.get.return_value = "cached-token"

    result = service.get_exchanged_token(_AUDIENCE)

    assert result == "cached-token"
    mock_cache.get.assert_called_once_with(f"oidc_exchange:{mock_user.id}:{_AUDIENCE}")
    # token_exchange_factory should never be reached
    mock_cache.__setitem__.assert_not_called()


@patch("codemie.service.security.oidc_token_exchange_service.get_current_user")
def test_get_exchanged_token_no_idp_token(mock_get_user, service, mock_user, mock_cache):
    """Cache miss, factory returns None → return None, nothing cached."""
    mock_get_user.return_value = mock_user
    mock_cache.get.return_value = None

    with patch("codemie.service.security.token_exchange_factory.token_exchange_factory") as mock_factory:
        mock_factory.get_token_for_current_user.return_value = None
        result = service.get_exchanged_token(_AUDIENCE)

    assert result is None
    mock_cache.__setitem__.assert_not_called()


@patch("codemie.service.security.oidc_token_exchange_service.get_current_user")
def test_get_exchanged_token_success_caches_result(mock_get_user, service, mock_user, mock_cache):
    """Cache miss, IdP token present, exchange succeeds → result cached and returned."""
    mock_get_user.return_value = mock_user
    mock_cache.get.return_value = None

    with patch("codemie.service.security.token_exchange_factory.token_exchange_factory") as mock_factory:
        mock_factory.get_token_for_current_user.return_value = "idp-token"
        with patch.object(service, "_run_async", return_value="exchanged-token"):
            result = service.get_exchanged_token(_AUDIENCE)

    assert result == "exchanged-token"
    mock_cache.__setitem__.assert_called_once_with(f"oidc_exchange:{mock_user.id}:{_AUDIENCE}", "exchanged-token")


# ---------------------------------------------------------------------------
# _aexchange_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_aexchange_token_success(mock_client_cls, service):
    """Successful POST → extracts and returns access_token."""
    mock_client = AsyncMock()
    mock_client_cls.return_value.__aenter__.return_value = mock_client

    mock_response = MagicMock()
    mock_response.json.return_value = {"access_token": "service-scoped-token"}
    mock_client.post.return_value = mock_response

    with patch("codemie.service.security.oidc_token_exchange_service.get_current_user", return_value=None):
        token = await service._aexchange_token("idp-token", _AUDIENCE)

    assert token == "service-scoped-token"
    call_kwargs = mock_client.post.call_args[1]
    assert call_kwargs["data"]["subject_token"] == "idp-token"
    assert call_kwargs["data"]["audience"] == _AUDIENCE


@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_aexchange_token_http_error(mock_client_cls, service):
    """HTTP error response → raises TokenProviderException with status code."""
    mock_client = AsyncMock()
    mock_client_cls.return_value.__aenter__.return_value = mock_client

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.reason_phrase = "Unauthorized"
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Unauthorized", request=MagicMock(), response=mock_response
    )
    mock_client.post.return_value = mock_response

    with patch("codemie.service.security.oidc_token_exchange_service.get_current_user", return_value=None):
        with pytest.raises(TokenProviderException) as exc:
            await service._aexchange_token("idp-token", _AUDIENCE)

    assert "401" in exc.value.message


@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_aexchange_token_missing_access_token(mock_client_cls, service):
    """Response body lacks access_token key → raises TokenProviderException."""
    mock_client = AsyncMock()
    mock_client_cls.return_value.__aenter__.return_value = mock_client

    mock_response = MagicMock()
    mock_response.json.return_value = {"token_type": "Bearer"}  # no access_token
    mock_client.post.return_value = mock_response

    with patch("codemie.service.security.oidc_token_exchange_service.get_current_user", return_value=None):
        with pytest.raises(TokenProviderException) as exc:
            await service._aexchange_token("idp-token", _AUDIENCE)

    assert "access_token" in exc.value.message


@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_aexchange_token_network_error(mock_client_cls, service):
    """Network-level error → raises TokenProviderException."""
    mock_client = AsyncMock()
    mock_client_cls.return_value.__aenter__.return_value = mock_client
    mock_client.post.side_effect = httpx.ConnectError("Connection refused")

    with patch("codemie.service.security.oidc_token_exchange_service.get_current_user", return_value=None):
        with pytest.raises(TokenProviderException) as exc:
            await service._aexchange_token("idp-token", _AUDIENCE)

    assert "request failed" in exc.value.message
