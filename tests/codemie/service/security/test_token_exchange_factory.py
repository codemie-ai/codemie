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

from datetime import UTC, datetime

import pytest
from cachetools import TTLCache
from unittest.mock import MagicMock, patch
from codemie.service.security.principal_token_resolver import PrincipalType
from codemie.service.security.token_exchange_service import TokenExchangeService
from codemie.service.security.token_providers.base_provider import TokenProviderException
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_cache():
    return MagicMock()


@pytest.fixture
def mock_context_provider():
    with patch("codemie.service.security.token_exchange_service.ContextTokenProvider") as mock:
        yield mock.return_value


@pytest.fixture
def factory(mock_cache, mock_context_provider):
    # Reset singleton for each test
    TokenExchangeService._instance = None
    factory = TokenExchangeService()
    factory._cache = mock_cache
    factory._default_provider = mock_context_provider
    return factory


@pytest.fixture
def current_user():
    user = MagicMock(spec=User)
    user.id = "test-user-id"
    return user


@pytest.fixture
def user_principal_resolver():
    with patch(
        "codemie.service.security.token_exchange_service.resolve_current_bearer_token",
        return_value=("context-token", PrincipalType.USER),
    ) as mock:
        yield mock


def test_singleton_pattern():
    TokenExchangeService._instance = None
    f1 = TokenExchangeService()
    f2 = TokenExchangeService()
    assert f1 is f2


@patch("codemie.service.security.token_exchange_service.get_current_user")
def test_get_token_no_user(mock_get_user, factory):
    mock_get_user.return_value = None
    token = factory.get_token_for_current_user()
    assert token is None


@patch("codemie.service.security.token_exchange_service.resolve_current_bearer_token")
@patch("codemie.service.security.token_exchange_service.get_current_user")
def test_get_token_no_bearer_in_context(mock_get_user, mock_resolve, factory, current_user, mock_cache):
    """No bearer token in context → None, cache never consulted."""
    mock_get_user.return_value = current_user
    mock_resolve.return_value = (None, None)

    token = factory.get_token_for_current_user()

    assert token is None
    mock_cache.get.assert_not_called()


@patch("codemie.service.security.token_exchange_service.get_current_user")
def test_get_token_cache_hit(mock_get_user, factory, current_user, mock_cache, user_principal_resolver):
    mock_get_user.return_value = current_user
    mock_cache.get.return_value = "cached-token"

    token = factory.get_token_for_current_user()

    assert token == "cached-token"
    mock_cache.get.assert_called_once_with(f"auth_token:{current_user.id}:user")
    factory._default_provider.get_token.assert_not_called()


@patch("codemie.service.security.token_exchange_service.get_current_user")
def test_get_token_cache_miss(
    mock_get_user, factory, current_user, mock_cache, mock_context_provider, user_principal_resolver
):
    mock_get_user.return_value = current_user
    mock_cache.get.return_value = None
    mock_context_provider.get_token.return_value = "new-token"

    token = factory.get_token_for_current_user()

    assert token == "new-token"
    mock_cache.get.assert_called_once_with(f"auth_token:{current_user.id}:user")
    mock_context_provider.get_token.assert_called_once()
    mock_cache.__setitem__.assert_called_once_with(f"auth_token:{current_user.id}:user", "new-token")


@patch("codemie.service.security.token_exchange_service.get_current_user")
def test_get_token_with_principal_type_client(mock_get_user, factory, current_user, mock_cache, mock_context_provider):
    """Client principal uses a client-scoped cache key and returns the principal type."""
    mock_get_user.return_value = current_user
    mock_cache.get.return_value = None
    mock_context_provider.get_token.return_value = "client-token"

    with patch(
        "codemie.service.security.token_exchange_service.resolve_current_bearer_token",
        return_value=("client-token", PrincipalType.CLIENT),
    ):
        token, principal_type = factory.get_token_with_principal_type_for_current_user()

    assert token == "client-token"
    assert principal_type is PrincipalType.CLIENT
    mock_cache.get.assert_called_once_with(f"auth_token:{current_user.id}:client")
    mock_cache.__setitem__.assert_called_once_with(f"auth_token:{current_user.id}:client", "client-token")


@patch("codemie.service.security.token_exchange_service.get_current_user")
def test_get_token_with_principal_type_user(mock_get_user, factory, current_user, mock_cache, user_principal_resolver):
    mock_get_user.return_value = current_user
    mock_cache.get.return_value = "cached-token"

    token, principal_type = factory.get_token_with_principal_type_for_current_user()

    assert token == "cached-token"
    assert principal_type is PrincipalType.USER


@patch("codemie.service.security.token_exchange_service.get_current_user")
def test_cache_keys_isolated_per_principal(mock_get_user, factory, current_user, mock_context_provider):
    """A cached client token is never served for a user-principal request."""
    from cachetools import TTLCache

    mock_get_user.return_value = current_user
    factory._cache = TTLCache(maxsize=10, ttl=300)
    mock_context_provider.get_token.side_effect = ["client-token", "user-token"]

    with patch(
        "codemie.service.security.token_exchange_service.resolve_current_bearer_token",
        return_value=("client-token", PrincipalType.CLIENT),
    ):
        client_token = factory.get_token_for_current_user()

    with patch(
        "codemie.service.security.token_exchange_service.resolve_current_bearer_token",
        return_value=("user-token", PrincipalType.USER),
    ):
        user_token = factory.get_token_for_current_user()

    assert client_token == "client-token"
    assert user_token == "user-token"
    assert factory._cache[f"auth_token:{current_user.id}:client"] == "client-token"
    assert factory._cache[f"auth_token:{current_user.id}:user"] == "user-token"


@patch("codemie.service.security.token_exchange_service.get_current_user")
def test_get_token_provider_error(
    mock_get_user, factory, current_user, mock_cache, mock_context_provider, user_principal_resolver
):
    mock_get_user.return_value = current_user
    mock_cache.get.return_value = None
    mock_context_provider.get_token.side_effect = TokenProviderException("Provider failed")

    with pytest.raises(TokenProviderException):
        factory.get_token_for_current_user()

    mock_cache.__setitem__.assert_not_called()


def test_clear_cache_specific_user_evicts_both_principals(factory, mock_cache):
    """Logout must evict the user- AND client-scoped cache slots.

    Tokens are written under `auth_token:<uid>:<principal>`, so an eviction that
    only pops the unsuffixed key is a no-op and keeps serving a revoked token
    until the TTL expires.
    """
    user_id = "user-123"
    factory.clear_cache(user_id=user_id)

    popped_keys = {call.args[0] for call in mock_cache.pop.call_args_list}
    assert popped_keys == {f"auth_token:{user_id}:user", f"auth_token:{user_id}:client"}


def test_purge_user_cache_drops_only_that_users_slots_without_tms_delete(factory, monkeypatch):
    """Logout purge is cache-only: no per-alias TMS delete, other users untouched."""
    store = MagicMock()
    monkeypatch.setattr(TokenExchangeService, "_store", store)
    factory._cache = TTLCache(maxsize=16, ttl=60)
    for user_id in ("user-1", "user-2"):
        for principal_type in PrincipalType:
            factory._cache[f"auth_token:{user_id}:{principal_type.value}"] = "token"

    factory.purge_user_cache("user-1")

    assert set(factory._cache) == {"auth_token:user-2:user", "auth_token:user-2:client"}
    store.purge_fallback_for_user.assert_called_once_with("user-1")
    store.invalidate.assert_not_called()


def test_purge_user_cache_without_tms_store_still_drops_cache(factory, monkeypatch):
    monkeypatch.setattr(TokenExchangeService, "_store", None)
    factory._cache = TTLCache(maxsize=16, ttl=60)
    factory._cache["auth_token:user-1:user"] = "token"

    factory.purge_user_cache("user-1")

    assert len(factory._cache) == 0


def test_clear_cache_all(factory, mock_cache):
    factory.clear_cache()
    mock_cache.clear.assert_called_once()


def test_get_cache_stats(factory, mock_cache):
    mock_cache.__len__ = MagicMock(return_value=10)
    stats = factory.get_cache_stats()
    assert stats["cache_size"] == 10
    assert "cache_ttl" in stats


@patch("codemie.service.security.token_exchange_service.get_current_user")
def test_get_token_provider_returns_none(
    mock_get_user, factory, current_user, mock_cache, mock_context_provider, user_principal_resolver
):
    """Provider returns None → None returned, token not stored in cache."""
    mock_get_user.return_value = current_user
    mock_cache.get.return_value = None
    mock_context_provider.get_token.return_value = None

    token = factory.get_token_for_current_user()

    assert token is None
    mock_cache.__setitem__.assert_not_called()


# ---------------------------------------------------------------------------
# TMS integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_tms_store():
    return MagicMock()


@pytest.fixture
def factory_with_tms(factory, mock_tms_store):
    TokenExchangeService._store = mock_tms_store
    yield factory
    TokenExchangeService._store = None


def test_get_token_returns_tms_cached_token(factory_with_tms, mock_tms_store, current_user, user_principal_resolver):
    mock_tms_store.get.return_value = "tms-cached-jwt"

    with patch("codemie.service.security.token_exchange_service.get_current_user", return_value=current_user):
        result = factory_with_tms.get_token_for_current_user()

    assert result == "tms-cached-jwt"
    mock_tms_store.get.assert_called_once_with("test-user-id", "__idp_token__")


def test_get_token_tms_miss_calls_provider_and_stores(
    factory_with_tms, mock_tms_store, mock_context_provider, current_user, user_principal_resolver
):
    mock_tms_store.get.return_value = None
    mock_context_provider.get_token.return_value = "fresh-jwt"

    with patch("codemie.service.security.token_exchange_service.get_current_user", return_value=current_user):
        with patch("codemie.service.security.token_exchange_service.parse_jwt_exp") as mock_parse:
            mock_parse.return_value = datetime(2025, 6, 1, tzinfo=UTC)
            result = factory_with_tms.get_token_for_current_user()

    assert result == "fresh-jwt"
    mock_tms_store.put.assert_called_once_with(
        "test-user-id",
        "__idp_token__",
        access_token="fresh-jwt",
        expires_at=datetime(2025, 6, 1, tzinfo=UTC),
    )


def test_get_token_tms_uses_client_alias_for_client_principal(factory_with_tms, mock_tms_store, current_user):
    """Client-principal requests use a separate TMS alias from user-principal ones."""
    mock_tms_store.get.return_value = "tms-client-jwt"

    with patch("codemie.service.security.token_exchange_service.get_current_user", return_value=current_user):
        with patch(
            "codemie.service.security.token_exchange_service.resolve_current_bearer_token",
            return_value=("client-token", PrincipalType.CLIENT),
        ):
            result = factory_with_tms.get_token_for_current_user()

    assert result == "tms-client-jwt"
    mock_tms_store.get.assert_called_once_with("test-user-id", "__client_token__")


def test_get_token_no_store_uses_legacy_cache(
    factory, mock_context_provider, current_user, mock_cache, user_principal_resolver
):
    TokenExchangeService._store = None
    mock_cache.get.return_value = "legacy-cached"

    with patch("codemie.service.security.token_exchange_service.get_current_user", return_value=current_user):
        result = factory.get_token_for_current_user()

    assert result == "legacy-cached"


def test_clear_cache_user_calls_store_invalidate(factory_with_tms, mock_tms_store):
    """Both TMS slots must be invalidated, not just the user-scoped one."""
    factory_with_tms.clear_cache(user_id="test-user-id")

    invalidated = {call.args for call in mock_tms_store.invalidate.call_args_list}
    assert invalidated == {
        ("test-user-id", "__idp_token__"),
        ("test-user-id", "__client_token__"),
    }


def test_clear_cache_none_does_not_call_store(factory_with_tms, mock_tms_store):
    factory_with_tms.clear_cache(user_id=None)

    mock_tms_store.invalidate.assert_not_called()


@pytest.mark.parametrize("principal_type", [PrincipalType.USER, PrincipalType.CLIENT])
def test_clear_cache_evicts_token_written_by_get_token(factory, mock_context_provider, current_user, principal_type):
    """Round-trip guard: the key clear_cache pops must be the key the read path wrote.

    Asserting the literal key string in isolation lets the two sides drift; this
    caches a real token through the public API, clears it, and requires the next
    call to reach the provider again.
    """
    from cachetools import TTLCache

    TokenExchangeService._store = None
    factory._cache = TTLCache(maxsize=10, ttl=300)
    mock_context_provider.get_token.return_value = "fresh-token"

    with (
        patch("codemie.service.security.token_exchange_service.get_current_user", return_value=current_user),
        patch(
            "codemie.service.security.token_exchange_service.resolve_current_bearer_token",
            return_value=("context-token", principal_type),
        ),
    ):
        assert factory.get_token_for_current_user() == "fresh-token"
        assert len(factory._cache) == 1, "token should now be cached"

        factory.clear_cache(user_id=current_user.id)
        assert len(factory._cache) == 0, "clear_cache left the cached token behind"

        mock_context_provider.get_token.reset_mock()
        factory.get_token_for_current_user()
        mock_context_provider.get_token.assert_called_once()
