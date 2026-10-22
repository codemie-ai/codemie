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

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.mcp_config import (
    MCPConfig,
    MCPConfigCloneRequest,
    MCPConfigResponse,
    MCPServerConfigData,
)
from codemie.service.mcp_config_service import _AUTH_CONFIG_ID_INDEX, MCPConfigService


def _make_user() -> MagicMock:
    user = MagicMock()
    user.id = "user-1"
    user.name = "Test User"
    user.username = "test-user"
    return user


def _make_source(*, is_public: bool = True, is_system: bool = True, **config_overrides: Any) -> MCPConfig:
    config = {
        "url": "https://mcp.example.com/sse",
        "headers": {"Authorization": "Bearer super-secret-value"},
        "auth_token": "tok-123",
    }
    config.update(config_overrides)
    return MCPConfig(
        id="src-1",
        name="Server",
        description="desc",
        server_home_url="https://home.example.com",
        source_url="https://git.example.com",
        logo_url="https://logo.example.com/l.png",
        categories=["Development"],
        config=MCPServerConfigData(**config),
        required_env_vars=[],
        user_id="admin-0",
        is_public=is_public,
        is_system=is_system,
        created_by=CreatedByUser(id="admin-0", name="Admin", username="admin"),
        usage_count=7,
        is_active=False,
    )


def _build_clone_request(config: MCPServerConfigData | None = None, **kwargs: Any) -> MCPConfigCloneRequest:
    payload = {
        "name": "Clone 1",
        "config": config if config is not None else MCPServerConfigData(url="https://new.example.com"),
    }
    payload.update(kwargs)
    return MCPConfigCloneRequest(**payload)


@pytest.fixture
def encrypt():
    with patch.object(MCPConfigService, "encryption_service") as encryption_service:
        encryption_service.encrypt.side_effect = lambda value: f"enc:{value}"
        yield encryption_service.encrypt


@pytest.fixture(autouse=True)
def _validate_auth_config():
    with patch("codemie.service.mcp_config_service.validate_auth_config_on_save", return_value=[]):
        yield


def _clone_from_form(
    source: MCPConfig | None,
    request: MCPConfigCloneRequest,
    user: MagicMock | None = None,
    *,
    get_by_fields_result: Any = None,
) -> MCPConfigResponse:
    with (
        patch.object(MCPConfig, "find_by_id", return_value=source),
        patch.object(MCPConfig, "get_by_fields", return_value=get_by_fields_result),
        patch.object(MCPConfig, "save", return_value=MagicMock(id="new-1")),
    ):
        return MCPConfigService.clone_from_form("src-1", request, user or _make_user())


def test_is_system_and_is_public_are_always_copied_from_source_private(encrypt):
    source = _make_source(is_public=False, is_system=False)

    response = _clone_from_form(source, _build_clone_request())

    assert response.is_public is False
    assert response.is_system is False


def test_is_system_and_is_public_are_always_copied_from_source_public_system(encrypt):
    source = _make_source(is_public=True, is_system=True)

    response = _clone_from_form(source, _build_clone_request())

    assert response.is_public is True
    assert response.is_system is True


def test_auth_config_id_is_always_regenerated(encrypt):
    source = _make_source(
        auth_config={"auth_type": "oauth2", "client_type": "public", "client_id": "cid", "id": "old-id"}
    )
    request = _build_clone_request(
        MCPServerConfigData(
            auth_config={"auth_type": "oauth2", "client_type": "public", "client_id": "cid", "id": "requested-id"}
        )
    )

    response = _clone_from_form(source, request)

    assert response.config.auth_config["id"] != "old-id"
    assert response.config.auth_config["id"] != "requested-id"


def test_untouched_client_secret_is_carried_over_unchanged(encrypt):
    source = _make_source(
        auth_config={
            "auth_type": "oauth2",
            "client_type": "confidential",
            "client_id": "cid",
            "client_secret": "enc-secret",
            "id": "old-id",
        }
    )
    request = _build_clone_request(
        MCPServerConfigData(
            auth_config={"auth_type": "oauth2", "client_type": "confidential", "client_id": "cid"},
        )
    )

    response = _clone_from_form(source, request)

    assert response.config.auth_config["has_client_secret"] is True
    assert source.config.auth_config["client_secret"] == "enc-secret"
    encrypt.assert_not_called()


def test_new_client_secret_is_freshly_encrypted(encrypt):
    source = _make_source(
        auth_config={
            "auth_type": "oauth2",
            "client_type": "confidential",
            "client_id": "cid",
            "client_secret": "enc-secret",
            "id": "old-id",
        }
    )
    request = _build_clone_request(
        MCPServerConfigData(
            auth_config={
                "auth_type": "oauth2",
                "client_type": "confidential",
                "client_id": "cid",
                "client_secret": "new-plaintext",
            },
        )
    )

    _clone_from_form(source, request)

    encrypt.assert_called_once_with("new-plaintext")
    assert source.config.auth_config["client_secret"] == "enc-secret"


def test_duplicate_name_raises_same_wording_as_update(encrypt):
    source = _make_source()
    existing = MagicMock()
    request = _build_clone_request(name="Server")

    with pytest.raises(ExtendedHTTPException) as exc:
        _clone_from_form(source, request, get_by_fields_result=existing)

    assert exc.value.code == 400
    assert exc.value.message == "Name already exists"
    assert exc.value.details == "An MCP configuration with name 'Server' already exists"
    assert exc.value.help == "Use a different name"


def test_invalid_auth_config_raises_422(encrypt):
    source = _make_source()
    request = _build_clone_request(
        MCPServerConfigData(auth_config={"auth_type": "oauth2", "client_type": "confidential", "client_id": "cid"})
    )

    with patch("codemie.service.mcp_config_service.validate_auth_config_on_save", return_value=["bad config"]):
        with pytest.raises(ExtendedHTTPException) as exc:
            _clone_from_form(source, request)

    assert exc.value.code == 422


def test_unknown_source_returns_404(encrypt):
    with pytest.raises(ExtendedHTTPException) as exc:
        _clone_from_form(None, _build_clone_request())

    assert exc.value.code == 404


def test_persists_via_save_and_integrity_error_maps_to_409(encrypt):
    source = _make_source()

    with (
        patch.object(MCPConfig, "find_by_id", return_value=source),
        patch.object(MCPConfig, "get_by_fields", return_value=None),
        patch.object(
            MCPConfig,
            "save",
            side_effect=IntegrityError("stmt", {}, Exception(f"duplicate key {_AUTH_CONFIG_ID_INDEX}")),
        ),
    ):
        with pytest.raises(ExtendedHTTPException) as exc:
            MCPConfigService.clone_from_form("src-1", _build_clone_request(), _make_user())

    assert exc.value.code == 409


def test_owner_and_descriptive_fields_come_from_the_request_and_user(encrypt):
    source = _make_source()
    user = _make_user()
    request = _build_clone_request(
        description="new desc",
        server_home_url="https://new-home.example.com",
        source_url="https://new-src.example.com",
        logo_url="https://new-logo.example.com",
        categories=["AI"],
    )

    response = _clone_from_form(source, request, user)

    assert response.name == "Clone 1"
    assert response.description == "new desc"
    assert response.server_home_url == "https://new-home.example.com"
    assert response.source_url == "https://new-src.example.com"
    assert response.logo_url == "https://new-logo.example.com"
    assert response.categories == ["AI"]
    assert response.user_id == "user-1"
    assert response.created_by.id == "user-1"
    assert response.id != "src-1"
