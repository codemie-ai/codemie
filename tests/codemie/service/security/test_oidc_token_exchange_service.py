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
from codemie.service.security.principal_token_resolver import PrincipalType
from codemie.service.security.thread_safe_ttl_cache import ThreadSafeTTLCache
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
    # token_exchange_service should never be reached
    mock_cache.__setitem__.assert_not_called()


@patch("codemie.service.security.oidc_token_exchange_service.get_current_user")
def test_get_exchanged_token_no_idp_token(mock_get_user, service, mock_user, mock_cache):
    """Cache miss, factory returns None → return None, nothing cached."""
    mock_get_user.return_value = mock_user
    mock_cache.get.return_value = None

    with patch("codemie.service.security.token_exchange_service.token_exchange_service") as mock_factory:
        mock_factory.get_token_with_principal_type_for_current_user.return_value = (None, None)
        result = service.get_exchanged_token(_AUDIENCE)

    assert result is None
    mock_cache.__setitem__.assert_not_called()


@patch("codemie.service.security.oidc_token_exchange_service.get_current_user")
def test_get_exchanged_token_success_caches_result(mock_get_user, service, mock_user, mock_cache):
    """Cache miss, IdP token present, exchange succeeds → result cached and returned."""
    mock_get_user.return_value = mock_user
    mock_cache.get.return_value = None

    with patch("codemie.service.security.token_exchange_service.token_exchange_service") as mock_factory:
        mock_factory.get_token_with_principal_type_for_current_user.return_value = ("idp-token", PrincipalType.USER)
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


# ---------------------------------------------------------------------------
# TMS integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_tms_store():
    return MagicMock()


@pytest.fixture
def service_with_tms(service, mock_tms_store):
    OIDCTokenExchangeService._store = mock_tms_store
    yield service
    OIDCTokenExchangeService._store = None


def test_get_exchanged_token_returns_tms_cached(service_with_tms, mock_tms_store, mock_user):
    mock_tms_store.get.return_value = "tms-exchanged-token"

    with patch(
        "codemie.service.security.oidc_token_exchange_service.get_current_user",
        return_value=mock_user,
    ):
        result = service_with_tms.get_exchanged_token(_AUDIENCE)

    assert result == "tms-exchanged-token"
    mock_tms_store.get.assert_called_once_with("user-123", f"oidc_exchange:{_AUDIENCE}")


def test_get_exchanged_token_tms_miss_exchanges_and_stores(service_with_tms, mock_tms_store, mock_user):
    mock_tms_store.get.return_value = None

    with patch(
        "codemie.service.security.oidc_token_exchange_service.get_current_user",
        return_value=mock_user,
    ):
        with patch("codemie.service.security.token_exchange_service.token_exchange_service") as mock_tes:
            mock_tes.get_token_with_principal_type_for_current_user.return_value = ("idp-token", PrincipalType.USER)
            with patch.object(
                service_with_tms,
                "_run_async",
                return_value=(
                    "new-exchanged-tok",
                    {
                        "access_token": "new-exchanged-tok",
                        "expires_in": 3600,
                        "refresh_token": "rt-1",
                        "scope": "openid",
                    },
                ),
            ):
                result = service_with_tms.get_exchanged_token(_AUDIENCE)

    assert result == "new-exchanged-tok"
    mock_tms_store.put.assert_called_once()
    put_kwargs = mock_tms_store.put.call_args
    assert put_kwargs[0][0] == "user-123"
    assert put_kwargs[0][1] == f"oidc_exchange:{_AUDIENCE}"
    assert put_kwargs[1]["access_token"] == "new-exchanged-tok"
    assert put_kwargs[1]["refresh_token"] == "rt-1"
    assert put_kwargs[1]["refresh_metadata_kwargs"] is not None
    assert put_kwargs[1]["scope"] == "openid"


def test_get_exchanged_token_tms_miss_no_refresh_token(service_with_tms, mock_tms_store, mock_user):
    mock_tms_store.get.return_value = None

    with patch(
        "codemie.service.security.oidc_token_exchange_service.get_current_user",
        return_value=mock_user,
    ):
        with patch("codemie.service.security.token_exchange_service.token_exchange_service") as mock_tes:
            mock_tes.get_token_with_principal_type_for_current_user.return_value = ("idp-token", PrincipalType.USER)
            with patch.object(
                service_with_tms,
                "_run_async",
                return_value=("exchanged-tok", {"access_token": "exchanged-tok", "expires_in": 1800}),
            ):
                result = service_with_tms.get_exchanged_token(_AUDIENCE)

    assert result == "exchanged-tok"
    put_kwargs = mock_tms_store.put.call_args[1]
    assert put_kwargs["refresh_token"] is None
    assert put_kwargs["refresh_metadata_kwargs"] is None


def test_get_exchanged_token_no_store_uses_legacy(service, mock_cache, mock_user):
    OIDCTokenExchangeService._store = None
    mock_cache.get.return_value = "legacy-exchange"

    with patch(
        "codemie.service.security.oidc_token_exchange_service.get_current_user",
        return_value=mock_user,
    ):
        result = service.get_exchanged_token(_AUDIENCE)

    assert result == "legacy-exchange"


def test_purge_user_cache_drops_only_that_users_entries_without_tms_delete(service, monkeypatch):
    """Logout purge is cache-only: legacy cache and TMS fallback go, no TMS delete, others untouched."""
    store = MagicMock()
    monkeypatch.setattr(OIDCTokenExchangeService, "_store", store)
    service._cache = ThreadSafeTTLCache(maxsize=16, ttl=60)
    service._cache[f"oidc_exchange:user-1:{_AUDIENCE}"] = "one"
    service._cache["oidc_exchange:user-1:other-audience"] = "one-b"
    service._cache[f"oidc_exchange:user-2:{_AUDIENCE}"] = "two"

    service.purge_user_cache("user-1")

    assert set(service._cache) == {f"oidc_exchange:user-2:{_AUDIENCE}"}
    store.purge_fallback_for_user.assert_called_once_with("user-1")
    store.invalidate.assert_not_called()
    store.invalidate_all_for_user.assert_not_called()


def test_purge_user_cache_without_tms_store_still_drops_cache(service, monkeypatch):
    monkeypatch.setattr(OIDCTokenExchangeService, "_store", None)
    service._cache = ThreadSafeTTLCache(maxsize=16, ttl=60)
    service._cache[f"oidc_exchange:user-1:{_AUDIENCE}"] = "one"

    service.purge_user_cache("user-1")

    assert len(service._cache) == 0


def test_exchange_cache_is_thread_safe():
    """Exchanged tokens are read, written and purged from different threads."""
    OIDCTokenExchangeService._instance = None

    assert isinstance(OIDCTokenExchangeService()._cache, ThreadSafeTTLCache)
