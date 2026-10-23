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

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException, MCPAuthenticationRequiredException
from codemie.rest_api.models.mcp_config import MCPConfig, MCPConfigTestResponse, MCPServerConfigData, MCPTestStatus
from codemie.service.mcp.mcp_tester import MCPTestResult
from codemie.service.mcp_config_service import MCPConfigService


def _make_config(**overrides) -> MCPConfig:
    return MCPConfig(id="cfg-1", name="srv", user_id="user-1", **overrides)


# ---------------------------------------------------------------------------
# MCPConfigService.test
# ---------------------------------------------------------------------------

_SERVICE = "codemie.service.mcp_config_service"


def _stored_config(**overrides) -> MCPConfig:
    return _make_config(
        config=MCPServerConfigData(command="npx", args=["-y", "pkg"]),
        **overrides,
    )


@pytest.fixture
def user():
    return MagicMock(id="admin-1")


@pytest.mark.parametrize(
    "result",
    [
        MCPTestResult(status=MCPTestStatus.SUCCESS, message="Success", tools_count=4),
        MCPTestResult(status=MCPTestStatus.WARNING, message="no tools", error_category="no_tools", tools_count=0),
        MCPTestResult(status=MCPTestStatus.FAILURE, message="boom", error_category="timeout"),
    ],
    ids=["success", "warning", "failure"],
)
def test_test_returns_result_without_persisting(result, user):
    config = _stored_config()
    with (
        patch(f"{_SERVICE}.MCPConfig.find_by_id", return_value=config),
        patch(f"{_SERVICE}.MCPServerTester") as tester_cls,
        patch.object(MCPConfig, "update") as save,
    ):
        tester_cls.return_value.run_test.return_value = result

        response = MCPConfigService.test("cfg-1", user)

    save.assert_not_called()
    assert isinstance(response, MCPConfigTestResponse)
    assert response.status == result.status
    assert response.message == result.message
    assert response.error_category == result.error_category
    assert response.tools_count == result.tools_count


def test_test_builds_server_details_from_stored_config(user):
    config = _stored_config()
    with (
        patch(f"{_SERVICE}.MCPConfig.find_by_id", return_value=config),
        patch(f"{_SERVICE}.MCPServerTester") as tester_cls,
        patch.object(MCPConfig, "update"),
    ):
        tester_cls.return_value.run_test.return_value = MCPTestResult(status=MCPTestStatus.SUCCESS, message="Success")

        MCPConfigService.test("cfg-1", user)

    request, passed_user = tester_cls.call_args.args
    assert passed_user is user
    assert request.mcp_server.name == "srv"
    assert request.mcp_server.mcp_config_id == "cfg-1"
    assert request.mcp_server.config.command == "npx"
    assert request.mcp_server.config.args == ["-y", "pkg"]


_ACCESS_CONTROL = "codemie.service.mcp.access_control"


@pytest.mark.parametrize(
    "overrides",
    [{"is_active": False, "is_public": True}, {"is_active": True, "is_public": False}],
    ids=["inactive", "non_public"],
)
def test_test_restricted_mode_blocks_unavailable_config_through_real_filter(overrides, user):
    config = _stored_config(**overrides)
    restricted_customer_config = MagicMock()
    restricted_customer_config.is_component_enabled.return_value = True
    with (
        patch(f"{_SERVICE}.MCPConfig.find_by_id", return_value=config),
        patch(f"{_ACCESS_CONTROL}.customer_config", restricted_customer_config),
        patch(f"{_ACCESS_CONTROL}.MCPConfig.get_by_ids", return_value=[config]),
        patch(f"{_SERVICE}.MCPServerTester") as tester_cls,
    ):
        response = MCPConfigService.test("cfg-1", user)

    tester_cls.assert_not_called()
    assert response.status == MCPTestStatus.FAILURE
    assert response.error_category == "restricted_mode"


def test_test_restricted_mode_allows_active_public_config_through_real_filter(user):
    config = _stored_config(is_active=True, is_public=True)
    restricted_customer_config = MagicMock()
    restricted_customer_config.is_component_enabled.return_value = True
    with (
        patch(f"{_SERVICE}.MCPConfig.find_by_id", return_value=config),
        patch(f"{_ACCESS_CONTROL}.customer_config", restricted_customer_config),
        patch(f"{_ACCESS_CONTROL}.MCPConfig.get_by_ids", return_value=[config]),
        patch(f"{_SERVICE}.MCPServerTester") as tester_cls,
    ):
        tester_cls.return_value.run_test.return_value = MCPTestResult(status=MCPTestStatus.SUCCESS, message="Success")

        response = MCPConfigService.test("cfg-1", user)

    tester_cls.assert_called_once()
    assert response.status == MCPTestStatus.SUCCESS


def test_test_auth_exception_propagates_without_write(user):
    config = _stored_config()
    with (
        patch(f"{_SERVICE}.MCPConfig.find_by_id", return_value=config),
        patch(f"{_SERVICE}.MCPServerTester") as tester_cls,
        patch.object(MCPConfig, "update") as save,
    ):
        tester_cls.return_value.run_test.side_effect = MCPAuthenticationRequiredException({"server": "srv"})

        with pytest.raises(MCPAuthenticationRequiredException):
            MCPConfigService.test("cfg-1", user)

    save.assert_not_called()


def test_test_unknown_id_raises_404(user):
    with patch(f"{_SERVICE}.MCPConfig.find_by_id", return_value=None):
        with pytest.raises(ExtendedHTTPException) as exc:
            MCPConfigService.test("missing", user)

    assert exc.value.code == 404


def test_test_failure_returns_full_raw_message(user):
    config = _stored_config()
    raw = "connect failed to secret-host:5432 with token abc"
    with (
        patch(f"{_SERVICE}.MCPConfig.find_by_id", return_value=config),
        patch(f"{_SERVICE}.MCPServerTester") as tester_cls,
        patch.object(MCPConfig, "update"),
    ):
        tester_cls.return_value.run_test.return_value = MCPTestResult(
            status=MCPTestStatus.FAILURE, message=raw, error_category="timeout"
        )

        response = MCPConfigService.test("cfg-1", user)

    assert response.message == raw


def test_test_inactive_config_is_tested_as_enabled(user):
    config = _stored_config(is_active=False)
    with (
        patch(f"{_SERVICE}.MCPConfig.find_by_id", return_value=config),
        patch(f"{_SERVICE}.MCPServerTester") as tester_cls,
        patch.object(MCPConfig, "update"),
    ):
        tester_cls.return_value.run_test.return_value = MCPTestResult(status=MCPTestStatus.SUCCESS, message="Success")

        MCPConfigService.test("cfg-1", user)

    request, _ = tester_cls.call_args.args
    assert request.mcp_server.enabled is True


# ---------------------------------------------------------------------------
# MCPConfigService.test with an integration (settings_id)
# ---------------------------------------------------------------------------

_SETTINGS_UTIL = "codemie.service.settings.settings_util"


def _setting(keys=("GITHUB_TOKEN",), alias="my-int", credential_type="MCP"):
    from codemie.rest_api.models.settings import CredentialTypes, CredentialValues, Settings

    return Settings(
        id="set-1",
        alias=alias,
        project_name="proj",
        user_id="admin-1",
        credential_type=CredentialTypes(credential_type),
        credential_values=[CredentialValues(key=k, value="s3cr3t-value") for k in keys],
    )


def _run_with_setting(user, setting, can_access=True, config=None, result=None, settings_id="set-1"):
    config = config or _stored_config()
    result = result or MCPTestResult(status=MCPTestStatus.SUCCESS, message="Success", tools_count=2)
    with (
        patch(f"{_SERVICE}.MCPConfig.find_by_id", return_value=config),
        patch(f"{_SETTINGS_UTIL}.search_settings_by_id", return_value=setting),
        patch(f"{_SETTINGS_UTIL}.user_can_access_setting", return_value=can_access) as access,
        patch(f"{_SERVICE}.MCPServerTester") as tester_cls,
        patch.object(MCPConfig, "update"),
    ):
        tester_cls.return_value.run_test.return_value = result
        response = MCPConfigService.test("cfg-1", user, settings_id)
    return config, response, tester_cls, access


def test_test_with_settings_id_passes_setting(user):
    setting = _setting()
    config, response, tester_cls, access = _run_with_setting(user, setting)

    request, _ = tester_cls.call_args.args
    assert request.mcp_server.settings.id == setting.id
    assert request.mcp_server.integration_alias is None
    assert request.mcp_server.mcp_connect_url is None
    access.assert_called_once_with(setting, user, None, marketplace=True)


def test_test_settings_not_found_raises_404_before_testing(user):
    with (
        patch(f"{_SERVICE}.MCPConfig.find_by_id", return_value=_stored_config()),
        patch(f"{_SETTINGS_UTIL}.search_settings_by_id", return_value=None),
        patch(f"{_SERVICE}.MCPServerTester") as tester_cls,
    ):
        with pytest.raises(ExtendedHTTPException) as exc:
            MCPConfigService.test("cfg-1", user, "nope")

    assert exc.value.code == 404
    tester_cls.assert_not_called()


def test_test_wrong_credential_type_raises_400(user):
    with pytest.raises(ExtendedHTTPException) as exc:
        _run_with_setting(user, _setting(credential_type="Jira"))
    assert exc.value.code == 400


def test_test_inaccessible_setting_raises_403(user):
    with pytest.raises(ExtendedHTTPException) as exc:
        _run_with_setting(user, _setting(), can_access=False)
    assert exc.value.code == 403


def test_missing_required_vars_returns_missing_credentials_without_calling_server(user):
    from codemie.rest_api.models.mcp_config import MCPVariableDefinition

    config = _stored_config(
        required_env_vars=[
            MCPVariableDefinition(name="GITHUB_TOKEN", description="t", required=True),
            MCPVariableDefinition(name="OPTIONAL", description="o", required=False),
        ]
    )
    with (
        patch(f"{_SERVICE}.MCPConfig.find_by_id", return_value=config),
        patch(f"{_SERVICE}.MCPServerTester") as tester_cls,
        patch.object(MCPConfig, "update") as save,
    ):
        response = MCPConfigService.test("cfg-1", user)

    tester_cls.assert_not_called()
    save.assert_not_called()
    assert response.status == MCPTestStatus.FAILURE
    assert response.error_category == "missing_credentials"
    assert "GITHUB_TOKEN" in response.message
    assert "OPTIONAL" not in response.message


def test_integration_keys_satisfy_required_vars_and_secret_never_in_response(user):
    from codemie.rest_api.models.mcp_config import MCPVariableDefinition

    config = _stored_config(required_env_vars=[MCPVariableDefinition(name="GITHUB_TOKEN", description="t")])
    config, response, tester_cls, _ = _run_with_setting(user, _setting(), config=config)

    tester_cls.assert_called_once()
    assert response.status == MCPTestStatus.SUCCESS
    assert "s3cr3t-value" not in response.model_dump_json()


def test_config_env_literal_satisfies_required_var(user):
    from codemie.rest_api.models.mcp_config import MCPVariableDefinition

    config = _stored_config(
        required_env_vars=[MCPVariableDefinition(name="GITHUB_TOKEN", description="t")],
    )
    config.config = MCPServerConfigData(command="npx", env={"GITHUB_TOKEN": "literal"})
    with (
        patch(f"{_SERVICE}.MCPConfig.find_by_id", return_value=config),
        patch(f"{_SERVICE}.MCPServerTester") as tester_cls,
        patch.object(MCPConfig, "update"),
    ):
        tester_cls.return_value.run_test.return_value = MCPTestResult(status=MCPTestStatus.SUCCESS, message="Success")
        MCPConfigService.test("cfg-1", user)

    tester_cls.assert_called_once()


def test_missing_credentials_precheck_with_integration_does_not_call_server(user):
    from codemie.rest_api.models.mcp_config import MCPVariableDefinition

    config = _stored_config(required_env_vars=[MCPVariableDefinition(name="OTHER_KEY", description="t")])
    _, response, tester_cls, _ = _run_with_setting(user, _setting(), config=config)

    tester_cls.assert_not_called()
    assert response.error_category == "missing_credentials"
