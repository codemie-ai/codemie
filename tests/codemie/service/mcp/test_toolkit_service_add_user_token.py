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

"""
Tests for MCPToolkitService._add_user_token_if_needed.

Covers the conditional OIDC token exchange logic introduced alongside the
audience field on MCPServerConfig.
"""

import pytest
from unittest.mock import MagicMock, patch

from codemie.service.mcp.toolkit_service import MCPToolkitService
from codemie.rest_api.security.user import User

_AUDIENCE = "oauth-client.epm-srdr.staffing-radar"
_TOKEN_EXCHANGE_URL = "https://access.epam.com/auth/realms/plusx/protocol/openid-connect/token"


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.id = "user-123"
    user.username = "test.user"
    return user


# ---------------------------------------------------------------------------
# Helper — invoke the classmethod under test
# ---------------------------------------------------------------------------


def _call(headers, env_vars, audience=None):
    MCPToolkitService._add_user_token_if_needed(headers, env_vars, audience)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_placeholder_returns_early():
    """Headers without any token placeholder → env_vars unchanged, no factory call."""
    env_vars = {"user": {}}
    headers = {"Authorization": "Bearer static-token", "X-Custom": "value"}

    with patch("codemie.service.mcp.toolkit_service.token_exchange_service") as mock_factory:
        _call(headers, env_vars)

    mock_factory.get_token_for_current_user.assert_not_called()
    assert "token" not in env_vars.get("user", {})


@patch("codemie.service.mcp.toolkit_service.get_current_user")
def test_placeholder_no_user_logs_warning(mock_get_user):
    """Token placeholder present but no user in context → warning, env_vars unchanged."""
    mock_get_user.return_value = None
    env_vars = {}
    headers = {"Authorization": "Bearer {{user.token}}"}

    with patch("codemie.service.mcp.toolkit_service.token_exchange_service") as mock_factory:
        _call(headers, env_vars)

    mock_factory.get_token_for_current_user.assert_not_called()
    assert env_vars.get("user", {}).get("token") is None


@patch("codemie.service.mcp.toolkit_service.get_current_user")
def test_placeholder_no_audience_uses_factory(mock_get_user, mock_user):
    """Placeholder present, no audience → uses token_exchange_service, injects token."""
    mock_get_user.return_value = mock_user
    env_vars = {"user": {}}
    headers = {"Authorization": "Bearer {{user.token}}"}

    with patch("codemie.service.mcp.toolkit_service.token_exchange_service") as mock_factory:
        mock_factory.get_token_for_current_user.return_value = "factory-token"
        _call(headers, env_vars, audience=None)

    mock_factory.get_token_for_current_user.assert_called_once()
    assert env_vars["user"]["token"] == "factory-token"


@patch("codemie.service.mcp.toolkit_service.get_current_user")
def test_placeholder_with_audience_uses_oidc_service(mock_get_user, mock_user):
    """Placeholder present + audience + TOKEN_EXCHANGE_URL configured → uses OIDC service."""
    mock_get_user.return_value = mock_user
    env_vars = {"user": {}}
    headers = {"Authorization": "Bearer {{user.token}}"}

    with patch("codemie.service.mcp.toolkit_service.config") as mock_config:
        mock_config.TOKEN_EXCHANGE_URL = _TOKEN_EXCHANGE_URL
        with patch("codemie.service.security.oidc_token_exchange_service.oidc_token_exchange_service") as mock_oidc:
            mock_oidc.get_exchanged_token.return_value = "oidc-exchanged-token"
            with patch("codemie.service.mcp.toolkit_service.token_exchange_service") as mock_factory:
                _call(headers, env_vars, audience=_AUDIENCE)

    mock_factory.get_token_for_current_user.assert_not_called()
    assert env_vars["user"]["token"] == "oidc-exchanged-token"


@patch("codemie.service.mcp.toolkit_service.get_current_user")
def test_token_none_logs_warning_no_injection(mock_get_user, mock_user):
    """Factory returns None → no token injected, warning logged."""
    mock_get_user.return_value = mock_user
    env_vars = {"user": {}}
    headers = {"Authorization": "Bearer {{user.token}}"}

    with patch("codemie.service.mcp.toolkit_service.token_exchange_service") as mock_factory:
        mock_factory.get_token_for_current_user.return_value = None
        _call(headers, env_vars, audience=None)

    assert "token" not in env_vars["user"]


@patch("codemie.service.mcp.toolkit_service.get_current_user")
def test_exception_during_fetch_continues(mock_get_user, mock_user):
    """Exception during token fetch → method does not raise, env_vars unchanged."""
    mock_get_user.return_value = mock_user
    env_vars = {"user": {}}
    headers = {"Authorization": "Bearer {{user.token}}"}

    with patch("codemie.service.mcp.toolkit_service.token_exchange_service") as mock_factory:
        mock_factory.get_token_for_current_user.side_effect = RuntimeError("boom")
        # Must not raise
        _call(headers, env_vars, audience=None)

    assert "token" not in env_vars["user"]
