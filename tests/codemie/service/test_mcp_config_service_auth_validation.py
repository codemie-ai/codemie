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

from __future__ import annotations

import builtins
import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.enterprise.mcp_auth.dependencies import validate_auth_config_on_save
from codemie.rest_api.models.mcp_config import MCPConfigCreateRequest, MCPConfigUpdateRequest, MCPServerConfigData
from codemie.service.mcp_config_service import MCPConfigService


def _make_user() -> MagicMock:
    user = MagicMock()
    user.id = "user-1"
    user.name = "Test User"
    user.username = "test-user"
    return user


def _build_valid_oauth2_auth_config(**overrides: Any) -> dict[str, Any]:
    payload = {
        "auth_type": "oauth2",
        "authorization_url": "https://auth.example.com/authorize",
        "token_url": "https://auth.example.com/token",
        "client_id": "client-id",
        "client_type": "public",
        "scopes": ["openid", "profile"],
        "token_delivery": {"method": "env", "key": "ACCESS_TOKEN"},
    }
    payload.update(overrides)
    return payload


def _build_valid_saml_auth_config(**overrides: Any) -> dict[str, Any]:
    payload = {
        "auth_type": "saml",
        "sso_url": "https://idp.example.com/sso",
        "entity_id": "urn:codemie:test:sp",
        "idp_entity_id": "https://idp.example.com/metadata",
        "idp_x509cert": "CERTDATA",
        "saml_credential_attribute": "mail",
        "saml_session_ttl": 3600,
        "token_delivery": {"method": "header"},
    }
    payload.update(overrides)
    return payload


def _build_create_request(config: MCPServerConfigData | None = None) -> MCPConfigCreateRequest:
    return MCPConfigCreateRequest(
        name="server-name",
        description=None,
        server_home_url=None,
        source_url=None,
        logo_url=None,
        categories=[],
        config=config or MCPServerConfigData(),
        required_env_vars=[],
        is_public=False,
    )


def _build_response_payload(config: MCPServerConfigData | None = None) -> dict[str, Any]:
    return {
        "id": "cfg-1",
        "name": "server-name",
        "description": None,
        "server_home_url": None,
        "source_url": None,
        "logo_url": None,
        "categories": [],
        "config": config,
        "required_env_vars": [],
        "user_id": "user-1",
        "is_public": True,
        "is_system": True,
        "created_by": None,
        "usage_count": 0,
        "is_active": True,
        "date": None,
        "update_date": None,
    }


def _build_update_request(config: MCPServerConfigData | None = None) -> MCPConfigUpdateRequest:
    return MCPConfigUpdateRequest(config=config)


def _make_existing_config(config: MCPServerConfigData | None = None) -> MagicMock:
    existing = MagicMock()
    existing.id = "cfg-1"
    existing.name = "server-name"
    existing.user_id = "user-1"
    existing.config = config or MCPServerConfigData()
    existing.model_dump.return_value = _build_response_payload(existing.config)
    return existing


def _assert_auth_config_matches_with_generated_id(
    actual_auth_config: dict[str, Any],
    expected_auth_config: dict[str, Any],
) -> None:
    assert actual_auth_config["id"]
    expected_without_id = {key: value for key, value in expected_auth_config.items() if key != "id"}
    assert {key: value for key, value in actual_auth_config.items() if key != "id"} == expected_without_id


class TestMCPConfigServiceAuthValidation:
    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_create_rejects_missing_required_field_before_save(self, mock_mcp_config_class: MagicMock) -> None:
        mock_mcp_config_class.get_by_fields.return_value = None

        request = _build_create_request(
            MCPServerConfigData(auth_config=_build_valid_oauth2_auth_config(authorization_url=None))
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            MCPConfigService.create(request, _make_user())

        exc = exc_info.value
        assert exc.code == 422
        assert exc.message == "Invalid auth_config"
        assert exc.details == "Required field 'authorization_url' missing for auth_type 'oauth2'"
        assert exc.help == "Fix the auth_config validation errors and retry the save"
        mock_mcp_config_class.return_value.save.assert_not_called()

    @pytest.mark.parametrize("field_name", ["idp_entity_id", "idp_x509cert"])
    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_create_rejects_missing_required_saml_trust_field_before_save(
        self,
        mock_mcp_config_class: MagicMock,
        field_name: str,
    ) -> None:
        mock_mcp_config_class.get_by_fields.return_value = None

        request = _build_create_request(
            MCPServerConfigData(auth_config=_build_valid_saml_auth_config(**{field_name: None}))
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            MCPConfigService.create(request, _make_user())

        exc = exc_info.value
        assert exc.code == 422
        assert exc.message == "Invalid auth_config"
        assert exc.details == f"Required field '{field_name}' missing for auth_type 'saml'"
        assert exc.help == "Fix the auth_config validation errors and retry the save"
        mock_mcp_config_class.return_value.save.assert_not_called()

    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_create_rejects_saml_for_http_transport(self, mock_mcp_config_class: MagicMock) -> None:
        mock_mcp_config_class.get_by_fields.return_value = None

        request = _build_create_request(
            MCPServerConfigData(url="https://mcp.example.com", auth_config=_build_valid_saml_auth_config())
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            MCPConfigService.create(request, _make_user())

        assert exc_info.value.details == "SAML is not supported for HTTP transport. Use OAuth2 for HTTP MCP servers"
        mock_mcp_config_class.return_value.save.assert_not_called()

    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_create_rejects_missing_auth_type_before_create(self, mock_mcp_config_class: MagicMock) -> None:
        mock_mcp_config_class.get_by_fields.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            MCPConfigService.create(_build_create_request(MCPServerConfigData(auth_config={})), _make_user())

        assert exc_info.value.details == "Unsupported auth_type: None"
        mock_mcp_config_class.return_value.save.assert_not_called()

    @pytest.mark.parametrize(
        ("field_name", "auth_config"),
        [
            (
                "authorization_url",
                _build_valid_oauth2_auth_config(authorization_url="http://auth.example.com/authorize"),
            ),
            ("token_url", _build_valid_oauth2_auth_config(token_url="http://auth.example.com/token")),
            ("sso_url", _build_valid_saml_auth_config(sso_url="http://idp.example.com/sso")),
        ],
    )
    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_create_rejects_non_https_callable_endpoints(
        self,
        mock_mcp_config_class: MagicMock,
        field_name: str,
        auth_config: dict[str, Any],
    ) -> None:
        mock_mcp_config_class.get_by_fields.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            MCPConfigService.create(_build_create_request(MCPServerConfigData(auth_config=auth_config)), _make_user())

        assert exc_info.value.details == f"'{field_name}' must use HTTPS"
        mock_mcp_config_class.return_value.save.assert_not_called()

    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_create_rejects_unsupported_auth_type(self, mock_mcp_config_class: MagicMock) -> None:
        mock_mcp_config_class.get_by_fields.return_value = None

        request = _build_create_request(MCPServerConfigData(auth_config={"auth_type": "basic"}))

        with pytest.raises(ExtendedHTTPException) as exc_info:
            MCPConfigService.create(request, _make_user())

        assert exc_info.value.details == "Unsupported auth_type: basic"
        mock_mcp_config_class.return_value.save.assert_not_called()

    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_create_rejects_reserved_discovered_auth_config_id(self, mock_mcp_config_class: MagicMock) -> None:
        mock_mcp_config_class.get_by_fields.return_value = None
        request = _build_create_request(
            MCPServerConfigData(auth_config=_build_valid_oauth2_auth_config(id="discovered:" + "a" * 64))
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            MCPConfigService.create(request, _make_user())

        assert exc_info.value.code == 422
        assert exc_info.value.message == "Invalid auth_config"
        assert exc_info.value.details == "auth_config.id cannot use reserved 'discovered:' prefix"
        mock_mcp_config_class.return_value.save.assert_not_called()

    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_update_rejects_missing_auth_type_before_update(self, mock_mcp_config_class: MagicMock) -> None:
        existing = _make_existing_config()
        mock_mcp_config_class.find_by_id.return_value = existing

        with pytest.raises(ExtendedHTTPException) as exc_info:
            MCPConfigService.update("cfg-1", _build_update_request(MCPServerConfigData(auth_config={})))

        assert exc_info.value.details == "Unsupported auth_type: None"
        existing.update.assert_not_called()

    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_update_rejects_missing_required_field_before_update(self, mock_mcp_config_class: MagicMock) -> None:
        existing = _make_existing_config()
        mock_mcp_config_class.find_by_id.return_value = existing

        with pytest.raises(ExtendedHTTPException) as exc_info:
            MCPConfigService.update(
                "cfg-1",
                _build_update_request(
                    MCPServerConfigData(auth_config=_build_valid_oauth2_auth_config(authorization_url=None))
                ),
            )

        assert exc_info.value.details == "Required field 'authorization_url' missing for auth_type 'oauth2'"
        existing.update.assert_not_called()

    @pytest.mark.parametrize("field_name", ["idp_entity_id", "idp_x509cert"])
    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_update_rejects_missing_required_saml_trust_field_before_update(
        self,
        mock_mcp_config_class: MagicMock,
        field_name: str,
    ) -> None:
        existing = _make_existing_config()
        mock_mcp_config_class.find_by_id.return_value = existing

        with pytest.raises(ExtendedHTTPException) as exc_info:
            MCPConfigService.update(
                "cfg-1",
                _build_update_request(
                    MCPServerConfigData(auth_config=_build_valid_saml_auth_config(**{field_name: None}))
                ),
            )

        exc = exc_info.value
        assert exc.code == 422
        assert exc.message == "Invalid auth_config"
        assert exc.details == f"Required field '{field_name}' missing for auth_type 'saml'"
        assert exc.help == "Fix the auth_config validation errors and retry the save"
        existing.update.assert_not_called()

    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_update_rejects_saml_for_http_transport(self, mock_mcp_config_class: MagicMock) -> None:
        existing = _make_existing_config()
        mock_mcp_config_class.find_by_id.return_value = existing

        with pytest.raises(ExtendedHTTPException) as exc_info:
            MCPConfigService.update(
                "cfg-1",
                _build_update_request(
                    MCPServerConfigData(url="https://mcp.example.com", auth_config=_build_valid_saml_auth_config())
                ),
            )

        assert exc_info.value.details == "SAML is not supported for HTTP transport. Use OAuth2 for HTTP MCP servers"
        existing.update.assert_not_called()

    @pytest.mark.parametrize(
        ("field_name", "auth_config"),
        [
            (
                "authorization_url",
                _build_valid_oauth2_auth_config(authorization_url="http://auth.example.com/authorize"),
            ),
            ("token_url", _build_valid_oauth2_auth_config(token_url="http://auth.example.com/token")),
            ("sso_url", _build_valid_saml_auth_config(sso_url="http://idp.example.com/sso")),
        ],
    )
    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_update_rejects_non_https_callable_endpoints(
        self,
        mock_mcp_config_class: MagicMock,
        field_name: str,
        auth_config: dict[str, Any],
    ) -> None:
        existing = _make_existing_config()
        mock_mcp_config_class.find_by_id.return_value = existing

        with pytest.raises(ExtendedHTTPException) as exc_info:
            MCPConfigService.update("cfg-1", _build_update_request(MCPServerConfigData(auth_config=auth_config)))

        assert exc_info.value.details == f"'{field_name}' must use HTTPS"
        existing.update.assert_not_called()

    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_update_rejects_unsupported_auth_type_before_update(self, mock_mcp_config_class: MagicMock) -> None:
        existing = _make_existing_config()
        mock_mcp_config_class.find_by_id.return_value = existing

        with pytest.raises(ExtendedHTTPException) as exc_info:
            MCPConfigService.update(
                "cfg-1",
                _build_update_request(MCPServerConfigData(auth_config={"auth_type": "basic"})),
            )

        assert exc_info.value.details == "Unsupported auth_type: basic"
        existing.update.assert_not_called()

    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_update_rejects_reserved_discovered_auth_config_id(self, mock_mcp_config_class: MagicMock) -> None:
        existing = _make_existing_config()
        mock_mcp_config_class.find_by_id.return_value = existing
        request = _build_update_request(
            MCPServerConfigData(auth_config=_build_valid_oauth2_auth_config(id="discovered:" + "b" * 64))
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            MCPConfigService.update("cfg-1", request)

        assert exc_info.value.code == 422
        assert exc_info.value.message == "Invalid auth_config"
        assert exc_info.value.details == "auth_config.id cannot use reserved 'discovered:' prefix"
        existing.update.assert_not_called()

    @patch(
        "codemie.service.mcp_config_service.validate_auth_config_on_save",
        return_value=["scopes: must be a list of strings"],
    )
    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_create_runs_validation_before_save(
        self,
        mock_mcp_config_class: MagicMock,
        mock_validate: MagicMock,
    ) -> None:
        mock_mcp_config_class.get_by_fields.return_value = None
        request = _build_create_request(MCPServerConfigData(auth_config=_build_valid_oauth2_auth_config()))

        with pytest.raises(ExtendedHTTPException) as exc_info:
            MCPConfigService.create(request, _make_user())

        assert exc_info.value.details == "scopes: must be a list of strings"
        mock_validate.assert_called_once_with(request.config.auth_config, "stdio")
        mock_mcp_config_class.return_value.save.assert_not_called()

    @patch(
        "codemie.service.mcp_config_service.validate_auth_config_on_save",
        return_value=["scopes: must be a list of strings"],
    )
    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_update_runs_validation_before_update(
        self,
        mock_mcp_config_class: MagicMock,
        mock_validate: MagicMock,
    ) -> None:
        existing = _make_existing_config()
        mock_mcp_config_class.find_by_id.return_value = existing
        request = _build_update_request(MCPServerConfigData(auth_config=_build_valid_oauth2_auth_config()))

        with pytest.raises(ExtendedHTTPException) as exc_info:
            MCPConfigService.update("cfg-1", request)

        assert exc_info.value.details == "scopes: must be a list of strings"
        validated_auth_config, transport = mock_validate.call_args.args
        assert transport == "stdio"
        _assert_auth_config_matches_with_generated_id(validated_auth_config, request.config.auth_config)
        existing.update.assert_not_called()

    @pytest.mark.parametrize(
        ("config", "expected_transport"),
        [
            (MCPServerConfigData(url="https://mcp.example.com", auth_config=_build_valid_oauth2_auth_config()), "http"),
            (MCPServerConfigData(type="streamable-http", auth_config=_build_valid_oauth2_auth_config()), "http"),
            (
                MCPServerConfigData(
                    url="https://mcp.example.com",
                    command="npx",
                    auth_config=_build_valid_oauth2_auth_config(),
                ),
                "http",
            ),
            (MCPServerConfigData(auth_config=_build_valid_oauth2_auth_config()), "stdio"),
        ],
    )
    @patch("codemie.service.mcp_config_service.validate_auth_config_on_save", return_value=[])
    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_create_normalizes_transport_before_validation(
        self,
        mock_mcp_config_class: MagicMock,
        mock_validate: MagicMock,
        config: MCPServerConfigData,
        expected_transport: str,
    ) -> None:
        mock_mcp_config_class.get_by_fields.return_value = None
        mock_instance = MagicMock()
        mock_instance.save.return_value = MagicMock(id="cfg-1")
        mock_instance.model_dump.return_value = _build_response_payload(config)
        mock_mcp_config_class.return_value = mock_instance

        MCPConfigService.create(_build_create_request(config), _make_user())

        validated_auth_config, transport = mock_validate.call_args.args
        assert transport == expected_transport
        _assert_auth_config_matches_with_generated_id(validated_auth_config, config.auth_config)
        mock_instance.save.assert_called_once()

    @pytest.mark.parametrize(
        ("config", "expected_transport"),
        [
            (MCPServerConfigData(url="https://mcp.example.com", auth_config=_build_valid_oauth2_auth_config()), "http"),
            (MCPServerConfigData(type="streamable-http", auth_config=_build_valid_oauth2_auth_config()), "http"),
            (
                MCPServerConfigData(
                    url="https://mcp.example.com",
                    command="npx",
                    auth_config=_build_valid_oauth2_auth_config(),
                ),
                "http",
            ),
            (MCPServerConfigData(auth_config=_build_valid_oauth2_auth_config()), "stdio"),
        ],
    )
    @patch("codemie.service.mcp_config_service.validate_auth_config_on_save", return_value=[])
    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_update_normalizes_transport_before_validation(
        self,
        mock_mcp_config_class: MagicMock,
        mock_validate: MagicMock,
        config: MCPServerConfigData,
        expected_transport: str,
    ) -> None:
        existing = _make_existing_config()
        existing.config = MCPServerConfigData()
        existing.model_dump.return_value = _build_response_payload(config)
        mock_mcp_config_class.find_by_id.return_value = existing

        MCPConfigService.update("cfg-1", _build_update_request(config))

        validated_auth_config, transport = mock_validate.call_args.args
        assert transport == expected_transport
        _assert_auth_config_matches_with_generated_id(validated_auth_config, config.auth_config)
        existing.update.assert_called_once()

    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_create_allows_valid_payload_when_enterprise_validator_is_unavailable(
        self,
        mock_mcp_config_class: MagicMock,
    ) -> None:
        original_import = builtins.__import__

        def _import_with_missing_enterprise(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "codemie_enterprise.mcp_auth.validation":
                raise ImportError("No module named 'codemie_enterprise'", name="codemie_enterprise")
            return original_import(name, *args, **kwargs)

        mock_mcp_config_class.get_by_fields.return_value = None
        mock_instance = MagicMock()
        request = _build_create_request(MCPServerConfigData(auth_config=_build_valid_oauth2_auth_config()))
        mock_instance.save.return_value = MagicMock(id="cfg-1")
        mock_instance.model_dump.return_value = _build_response_payload(request.config)
        mock_mcp_config_class.return_value = mock_instance

        with (
            patch("codemie.enterprise.mcp_auth.dependencies.HAS_MCP_AUTH", True),
            patch(
                "builtins.__import__",
                side_effect=_import_with_missing_enterprise,
            ),
        ):
            response = MCPConfigService.create(request, _make_user())

        assert response.id == "cfg-1"
        mock_instance.save.assert_called_once()

    @patch("codemie.service.mcp_config_service.MCPConfig")
    def test_update_allows_valid_payload_when_enterprise_validator_is_unavailable(
        self,
        mock_mcp_config_class: MagicMock,
    ) -> None:
        original_import = builtins.__import__

        def _import_with_missing_enterprise(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "codemie_enterprise.mcp_auth.validation":
                raise ImportError("No module named 'codemie_enterprise'", name="codemie_enterprise")
            return original_import(name, *args, **kwargs)

        config = MCPServerConfigData(auth_config=_build_valid_oauth2_auth_config())
        existing = _make_existing_config(config)
        mock_mcp_config_class.find_by_id.return_value = existing
        existing.model_dump.return_value = _build_response_payload(config)

        with (
            patch("codemie.enterprise.mcp_auth.dependencies.HAS_MCP_AUTH", True),
            patch(
                "builtins.__import__",
                side_effect=_import_with_missing_enterprise,
            ),
        ):
            response = MCPConfigService.update("cfg-1", _build_update_request(config))

        assert response.id == "cfg-1"
        existing.update.assert_called_once()

    def test_validate_auth_config_on_save_propagates_runtime_error_from_enterprise_validator(self) -> None:
        enterprise_module = types.ModuleType("codemie_enterprise")
        mcp_auth_module = types.ModuleType("codemie_enterprise.mcp_auth")
        validation_module = types.ModuleType("codemie_enterprise.mcp_auth.validation")

        def _raise_runtime_error(raw_dict: dict[str, Any], transport: str) -> list[str]:
            del raw_dict, transport
            raise RuntimeError("boom")

        validation_module.validate_auth_config_structure = _raise_runtime_error

        with (
            patch("codemie.enterprise.mcp_auth.dependencies.HAS_MCP_AUTH", True),
            patch.dict(
                sys.modules,
                {
                    "codemie_enterprise": enterprise_module,
                    "codemie_enterprise.mcp_auth": mcp_auth_module,
                    "codemie_enterprise.mcp_auth.validation": validation_module,
                },
            ),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                validate_auth_config_on_save(_build_valid_oauth2_auth_config(), "stdio")
