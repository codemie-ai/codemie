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

from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from codemie.service.security.token_providers.base_provider import TokenProviderException


# ---------------------------------------------------------------------------
# Simulated TMS exceptions (no hard enterprise dependency)
# ---------------------------------------------------------------------------


class _TokenNotFound(Exception):
    pass


class _ReAuthenticationRequired(Exception):
    pass


class _TokenRefreshError(Exception):
    pass


class _TMSUnavailable(Exception):
    pass


class _TMSPersistenceError(Exception):
    pass


class _TMSCryptoError(Exception):
    pass


class _TMSAuditError(Exception):
    pass


@pytest.fixture(autouse=True)
def _patch_enterprise_imports(monkeypatch):
    """Patch deferred imports inside TMSTokenStore so enterprise package is not required."""
    fake_module = types.ModuleType("codemie_enterprise.mcp_auth")
    fake_module.TokenNotFound = _TokenNotFound
    fake_module.ReAuthenticationRequired = _ReAuthenticationRequired
    fake_module.TokenRefreshError = _TokenRefreshError
    fake_module.TMSUnavailable = _TMSUnavailable
    fake_module.TMSPersistenceError = _TMSPersistenceError
    fake_module.TMSCryptoError = _TMSCryptoError
    fake_module.TMSAuditError = _TMSAuditError

    class FakeOAuth2RefreshMetadata:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

        def model_dump(self):
            return self.__dict__.copy()

    class FakeOAuth2TokenData:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    fake_module.OAuth2TokenData = FakeOAuth2TokenData
    fake_module.OAuth2RefreshMetadata = FakeOAuth2RefreshMetadata

    monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth", fake_module)
    monkeypatch.setitem(sys.modules, "codemie_enterprise", types.ModuleType("codemie_enterprise"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeAuditContextProvider:
    def __init__(self):
        self.last_source = None
        self.last_correlation_id = None

    @contextmanager
    def context(self, *, source: str, correlation_id: str | None = None):
        self.last_source = source
        self.last_correlation_id = correlation_id
        yield


@pytest.fixture
def mock_tms():
    return MagicMock()


@pytest.fixture
def audit_ctx():
    return FakeAuditContextProvider()


@pytest.fixture
def store(mock_tms, audit_ctx):
    from codemie.service.security.tms_token_store import TMSTokenStore

    return TMSTokenStore(mock_tms, audit_ctx)


# ---------------------------------------------------------------------------
# get() tests
# ---------------------------------------------------------------------------


def test_get_happy_path(store, mock_tms):
    token_data = MagicMock()
    token_data.access_token = "jwt-token-value"
    mock_tms.retrieve.return_value = token_data

    result = store.get("user-1", "config-1")

    assert result == "jwt-token-value"
    mock_tms.retrieve.assert_called_once_with("user-1", "config-1")


def test_get_token_not_found_no_fallback(store, mock_tms):
    mock_tms.retrieve.side_effect = _TokenNotFound("not found")

    result = store.get("user-1", "config-1")

    assert result is None


def test_get_token_not_found_with_fallback(store, mock_tms):
    store._fallback["user-1:config-1"] = "fallback-token"
    mock_tms.retrieve.side_effect = _TokenNotFound("not found")

    result = store.get("user-1", "config-1")

    assert result == "fallback-token"


def test_get_reauth_required_does_not_check_fallback(store, mock_tms):
    store._fallback["user-1:config-1"] = "stale-token"
    mock_tms.retrieve.side_effect = _ReAuthenticationRequired("reauth")

    result = store.get("user-1", "config-1")

    assert result is None


def test_get_token_refresh_error_does_not_check_fallback(store, mock_tms):
    store._fallback["user-1:config-1"] = "stale-token"
    mock_tms.retrieve.side_effect = _TokenRefreshError("refresh failed")

    result = store.get("user-1", "config-1")

    assert result is None


def test_get_tms_unavailable_falls_back(store, mock_tms):
    store._fallback["user-1:config-1"] = "fallback-token"
    mock_tms.retrieve.side_effect = _TMSUnavailable("db down")

    result = store.get("user-1", "config-1")

    assert result == "fallback-token"


def test_get_tms_unavailable_no_fallback(store, mock_tms):
    mock_tms.retrieve.side_effect = _TMSUnavailable("db down")

    result = store.get("user-1", "config-1")

    assert result is None


def test_get_tms_crypto_error_falls_back(store, mock_tms):
    store._fallback["user-1:config-1"] = "fallback-token"
    mock_tms.retrieve.side_effect = _TMSCryptoError("decrypt failed")

    result = store.get("user-1", "config-1")

    assert result == "fallback-token"


def test_get_tms_audit_error_raises_token_provider_exception(store, mock_tms):
    mock_tms.retrieve.side_effect = _TMSAuditError("audit failed")

    with pytest.raises(TokenProviderException, match="audit requirement"):
        store.get("user-1", "config-1")


# ---------------------------------------------------------------------------
# put() tests
# ---------------------------------------------------------------------------


def test_put_happy_path(store, mock_tms):
    store.put(
        "user-1",
        "config-1",
        access_token="new-token",
        expires_at=datetime(2025, 6, 1, tzinfo=UTC),
    )

    mock_tms.store.assert_called_once()
    call_args = mock_tms.store.call_args[0]
    assert call_args[0] == "user-1"
    assert call_args[1] == "config-1"
    assert call_args[2].access_token == "new-token"


def test_put_expires_at_none_writes_to_fallback_only(store, mock_tms):
    store.put(
        "user-1",
        "config-1",
        access_token="no-expiry-token",
        expires_at=None,
    )

    mock_tms.store.assert_not_called()
    assert store._fallback["user-1:config-1"] == "no-expiry-token"


def test_put_with_refresh_token_builds_refresh_metadata(store, mock_tms):
    store.put(
        "user-1",
        "oidc_exchange:audience1",
        access_token="exchanged-token",
        expires_at=datetime(2025, 6, 1, tzinfo=UTC),
        refresh_token="refresh-tok",
        refresh_metadata_kwargs={
            "token_endpoint": "https://kc.example.com/token",
            "client_id": "my-client",
            "client_auth_method": "client_secret_post",
            "client_secret": "secret",
            "scopes": ["openid"],
        },
    )

    mock_tms.store.assert_called_once()
    token_data = mock_tms.store.call_args[0][2]
    assert token_data.refresh_token == "refresh-tok"
    assert token_data.refresh_metadata is not None
    assert token_data.refresh_metadata.token_endpoint == "https://kc.example.com/token"


def test_put_tms_unavailable_writes_to_fallback(store, mock_tms):
    mock_tms.store.side_effect = _TMSUnavailable("db down")

    store.put(
        "user-1",
        "config-1",
        access_token="fallback-put-token",
        expires_at=datetime(2025, 6, 1, tzinfo=UTC),
    )

    assert store._fallback["user-1:config-1"] == "fallback-put-token"


def test_put_tms_audit_error_raises(store, mock_tms):
    mock_tms.store.side_effect = _TMSAuditError("audit failed")

    with pytest.raises(TokenProviderException, match="audit requirement"):
        store.put(
            "user-1",
            "config-1",
            access_token="token",
            expires_at=datetime(2025, 6, 1, tzinfo=UTC),
        )


# ---------------------------------------------------------------------------
# put() then get() round-trip
# ---------------------------------------------------------------------------


def test_put_expires_at_none_then_get_returns_from_fallback(store, mock_tms):
    store.put("user-1", "config-1", access_token="fb-token", expires_at=None)
    mock_tms.retrieve.side_effect = _TokenNotFound("not found")

    result = store.get("user-1", "config-1")

    assert result == "fb-token"


# ---------------------------------------------------------------------------
# invalidate() tests
# ---------------------------------------------------------------------------


def test_invalidate_happy_path(store, mock_tms):
    store._fallback["user-1:config-1"] = "cached"

    store.invalidate("user-1", "config-1")

    mock_tms.delete.assert_called_once_with("user-1", "config-1")
    assert "user-1:config-1" not in store._fallback


def test_invalidate_infra_error_swallowed(store, mock_tms):
    mock_tms.delete.side_effect = _TMSUnavailable("db down")
    store._fallback["user-1:config-1"] = "cached"

    store.invalidate("user-1", "config-1")

    assert "user-1:config-1" not in store._fallback


def test_invalidate_audit_error_raises(store, mock_tms):
    mock_tms.delete.side_effect = _TMSAuditError("audit failed")

    with pytest.raises(TokenProviderException, match="audit requirement"):
        store.invalidate("user-1", "config-1")


# ---------------------------------------------------------------------------
# invalidate_all_for_user() tests
# ---------------------------------------------------------------------------


def test_invalidate_all_for_user(store, mock_tms):
    store._fallback["user-1:config-a"] = "a"
    store._fallback["user-1:config-b"] = "b"
    store._fallback["user-2:config-c"] = "c"

    store.invalidate_all_for_user("user-1")

    mock_tms.delete_all_for_user.assert_called_once_with("user-1")
    assert "user-1:config-a" not in store._fallback
    assert "user-1:config-b" not in store._fallback
    assert store._fallback["user-2:config-c"] == "c"


# ---------------------------------------------------------------------------
# Audit context tests
# ---------------------------------------------------------------------------


def test_get_sets_audit_context(store, mock_tms, audit_ctx):
    token_data = MagicMock()
    token_data.access_token = "tok"
    mock_tms.retrieve.return_value = token_data

    store.get("user-1", "my-config")

    assert audit_ctx.last_source == "token_exchange"
    assert audit_ctx.last_correlation_id == "my-config"


def test_put_sets_audit_context(store, mock_tms, audit_ctx):
    store.put("user-1", "my-config", access_token="t", expires_at=datetime(2025, 6, 1, tzinfo=UTC))

    assert audit_ctx.last_source == "token_exchange"
    assert audit_ctx.last_correlation_id == "my-config"
