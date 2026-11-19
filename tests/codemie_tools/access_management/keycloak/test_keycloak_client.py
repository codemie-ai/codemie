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

"""Characterization tests for KeycloakClient.

These pin the CURRENT behaviour on main, including the quirk that ``execute_request``
sends ``params`` as a JSON body even on GET calls instead of as query parameters.
A later fix to send query params must change these tests knowingly.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from langchain_core.tools import ToolException

from codemie_tools.access_management.keycloak.keycloak_client import KeycloakClient

MODULE = "codemie_tools.access_management.keycloak.keycloak_client"


@pytest.fixture
def client():
    return KeycloakClient("https://kc.example.com/", "realm1", "cid", "secret", timeout=5)


def _http_client_mock(mock_cls):
    inner = MagicMock()
    mock_cls.return_value.__enter__.return_value = inner
    return inner


def _status_error(status, text):
    response = MagicMock()
    response.status_code = status
    response.text = text
    return httpx.HTTPStatusError("boom", request=MagicMock(), response=response)


def test_get_admin_token_posts_client_credentials(client):
    with patch(f"{MODULE}.httpx.Client") as mock_cls:
        inner = _http_client_mock(mock_cls)
        inner.post.return_value.json.return_value = {"access_token": "tok"}

        assert client._get_admin_token() == "tok"

    inner.post.assert_called_once_with(
        "https://kc.example.com/realms/realm1/protocol/openid-connect/token",
        data={"client_id": "cid", "client_secret": "secret", "grant_type": "client_credentials"},
    )


def test_get_admin_token_http_error_raises_tool_exception(client):
    with patch(f"{MODULE}.httpx.Client") as mock_cls:
        inner = _http_client_mock(mock_cls)
        inner.post.return_value.raise_for_status.side_effect = _status_error(401, "denied")

        with pytest.raises(ToolException) as exc:
            client._get_admin_token()

    assert str(exc.value).startswith("Failed to obtain admin token - HTTP 401")


def test_execute_request_sends_params_as_json_body_on_get(client):
    with patch.object(client, "_get_admin_token", return_value="tok"), patch(f"{MODULE}.httpx.Client") as mock_cls:
        inner = _http_client_mock(mock_cls)
        inner.request.return_value.text = "[]"

        result = client.execute_request("GET", "/users", {"max": 10})

    assert result == "[]"
    inner.request.assert_called_once_with(
        method="GET",
        url="https://kc.example.com/admin/realms/realm1/users",
        headers={"Authorization": "Bearer tok", "Content-Type": "application/json"},
        json={"max": 10},
    )


def test_execute_request_rejects_relative_url_without_slash(client):
    with pytest.raises(ToolException):
        client.execute_request("GET", "users")


def test_health_check_true_on_token_success(client):
    with patch.object(client, "_get_admin_token", return_value="tok"):
        assert client.health_check() is True


def test_health_check_wraps_failure_in_tool_exception(client):
    with patch.object(client, "_get_admin_token", side_effect=ToolException("nope")):
        with pytest.raises(ToolException) as exc:
            client.health_check()

    assert "Keycloak health check failed" in str(exc.value)
