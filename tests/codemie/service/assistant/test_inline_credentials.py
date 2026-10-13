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

from unittest.mock import MagicMock

import pytest

from codemie.rest_api.models.assistant import InlineCredential, PromptVariable
from codemie.rest_api.models.settings import SettingType
from codemie.service.assistant.inline_credentials import (
    validate_assistant_inline_integrations,
    _check_toolkit_credentials,
    _check_mcp_server_credentials,
    _check_object_credential_values,
    _build_validation_result,
)


class MockCredentialValues:
    def __init__(self, has_credentials=True):
        self.credential_values = {"key": "value"} if has_credentials else None


class MockSettings:
    def __init__(self, has_credentials=True):
        self.credential_values = {"key": "value"} if has_credentials else None


class MockTool:
    def __init__(self, name="Tool", label="Tool Label", has_settings=True, has_credentials=True):
        self.name = name
        self.label = label
        self.settings = MockSettings(has_credentials) if has_settings else None


class MockToolkit:
    def __init__(self, toolkit="Toolkit", label="Toolkit Label", has_settings=True, has_credentials=True, tools=None):
        self.toolkit = toolkit
        self.label = label
        self.settings = MockSettings(has_credentials) if has_settings else None
        self.tools = tools or []


class MockMCPConfig:
    def __init__(self, has_env=True):
        self.env = {"KEY1": "value1", "KEY2": "value2"} if has_env else {}


class MockMCPServer:
    def __init__(self, name="Server", has_auth=True, has_env=True, has_config_env=True):
        self.name = name
        self.mcp_connect_auth_token = MockCredentialValues(has_auth) if has_auth else None
        self.mcp_environment_vars = MockSettings(has_env) if has_env else None
        self.config = MockMCPConfig(has_config_env) if has_config_env else None


def test_check_object_credential_values():
    """Test checking for credential values in an object."""
    # Object with credential values
    obj_with_creds = MockTool(has_credentials=True)
    result = _check_object_credential_values(
        obj_with_creds, "tool_settings", toolkit_name="TestToolkit", tool_name="TestTool", label="Test Label"
    )

    assert len(result) == 1
    assert result[0].toolkit == "TestToolkit"
    assert result[0].tool == "TestTool"
    assert result[0].label == "Test Label"
    assert result[0].credential_type == "tool_settings"

    # Object without settings
    obj_no_settings = MockTool(has_settings=False)
    result = _check_object_credential_values(obj_no_settings, "tool_settings")
    assert len(result) == 0

    # Object with settings but no credential values
    obj_no_creds = MockTool(has_credentials=False)
    result = _check_object_credential_values(obj_no_creds, "tool_settings")
    assert len(result) == 0


class MockPinnedSettings:
    def __init__(self, setting_type, credential_type="Jira", alias="my personal jira"):
        self.setting_type = setting_type
        self.credential_type = credential_type
        self.alias = alias
        self.credential_values = None


class MockPinnedMCPServer:
    def __init__(self, name="Server", settings=None):
        self.name = name
        self.settings = settings
        self.mcp_connect_auth_token = None
        self.config = None


def test_check_mcp_server_pinned_user_integration_is_flagged():
    """A server pinned to a personal (USER) integration is surfaced for the Credential Review."""
    server = MockPinnedMCPServer(name="jira-mcp", settings=MockPinnedSettings(SettingType.USER))

    result = _check_mcp_server_credentials([server])

    assert len(result) == 1
    assert result[0].mcp_server == "jira-mcp"
    assert result[0].credential_type == "Jira"
    assert result[0].integration_alias == "my personal jira"
    assert result[0].toolkit == "MCP"


def test_check_mcp_server_pinned_project_integration_is_not_flagged():
    """A server pinned to a PROJECT integration is not a personal-credential leak and is ignored."""
    server = MockPinnedMCPServer(name="jira-mcp", settings=MockPinnedSettings(SettingType.PROJECT))

    result = _check_mcp_server_credentials([server])

    assert result == []


def test_check_toolkit_credentials():
    """Test checking for credentials in toolkits."""
    # Setup toolkits with various credential configurations
    toolkits = [
        # Toolkit with credentials and tools with credentials
        MockToolkit(
            toolkit="Toolkit1",
            has_credentials=True,
            tools=[MockTool(name="Tool1", has_credentials=True), MockTool(name="Tool2", has_credentials=False)],
        ),
        # Toolkit without credentials but tools with credentials
        MockToolkit(toolkit="Toolkit2", has_credentials=False, tools=[MockTool(name="Tool3", has_credentials=True)]),
        # Toolkit without credentials and tools without credentials
        MockToolkit(toolkit="Toolkit3", has_credentials=False, tools=[MockTool(name="Tool4", has_credentials=False)]),
    ]

    # Check credentials
    result = _check_toolkit_credentials(toolkits)

    # Should find 3 sets of credentials (1 in Toolkit1, 1 in Tool1, 1 in Tool3)
    assert len(result) == 3

    # Check that the credentials are from the right tools/toolkits
    found_toolkit1 = found_tool1 = found_tool3 = False
    for cred in result:
        if cred.toolkit == "Toolkit1" and cred.tool is None:
            found_toolkit1 = True
        elif cred.toolkit == "Toolkit1" and cred.tool == "Tool1":
            found_tool1 = True
        elif cred.toolkit == "Toolkit2" and cred.tool == "Tool3":
            found_tool3 = True

    assert found_toolkit1 and found_tool1 and found_tool3


def test_build_validation_result():
    """Test building validation results for different scenarios."""
    # Case with inline credentials
    credentials = [InlineCredential(toolkit="TestToolkit", credential_type="toolkit_settings")]
    result = _build_validation_result(credentials)

    assert not result["is_valid"]
    assert "inline integration credentials" in result["message"]
    assert result["inline_credentials"] == credentials

    # Case without inline credentials
    result = _build_validation_result([])

    assert result["is_valid"]


def test_validate_assistant_inline_integrations():
    """Test the complete validation of assistant inline integrations."""
    # Create a mock assistant
    mock_assistant = MagicMock()
    mock_assistant.id = "test-id"
    mock_assistant.toolkits = [
        MockToolkit(toolkit="Toolkit1", has_credentials=True, tools=[MockTool(name="Tool1", has_credentials=True)])
    ]
    mock_assistant.mcp_servers = [MockMCPServer(name="Server1", has_auth=True)]

    # Validate assistant
    result = validate_assistant_inline_integrations(mock_assistant)

    # Should find credentials and be invalid
    assert not result["is_valid"]
    assert len(result["inline_credentials"]) > 0
    assert "inline integration credentials" in result["message"]

    # Test assistant with no credentials
    mock_assistant.toolkits = []
    mock_assistant.mcp_servers = []

    result = validate_assistant_inline_integrations(mock_assistant)

    # Should find no credentials and be valid
    assert result["is_valid"]


def _assistant_with_mcp_servers(*mcp_servers):
    assistant = MagicMock()
    assistant.toolkits = []
    assistant.mcp_servers = list(mcp_servers)
    assistant.prompt_variables = []
    return assistant


def _validated_single_mcp_credential(server):
    result = validate_assistant_inline_integrations(_assistant_with_mcp_servers(server))

    assert not result["is_valid"]
    (credential,) = result["inline_credentials"]
    return credential


def test_validate_flags_mcp_environment_vars_read_from_server_settings():
    """Env-var credentials come from server.settings.credential_values; MockMCPServer.mcp_environment_vars is never read."""
    server = MockPinnedMCPServer(name="env-server", settings=MockSettings(has_credentials=True))

    credential = _validated_single_mcp_credential(server)

    assert credential.credential_type == "mcp_environment_vars"
    assert credential.mcp_server == "env-server"
    assert credential.toolkit == "MCP"


def test_validate_flags_mcp_auth_token():
    server = MockMCPServer(name="auth-server", has_auth=True, has_env=False, has_config_env=False)

    credential = _validated_single_mcp_credential(server)

    assert credential.credential_type == "mcp_auth_token"
    assert credential.mcp_server == "auth-server"
    assert credential.toolkit == "MCP"


def test_validate_flags_mcp_inline_config_env_with_the_variable_names():
    server = MockMCPServer(name="config-server", has_auth=False, has_env=False, has_config_env=True)

    credential = _validated_single_mcp_credential(server)

    assert credential.credential_type == "mcp_inline_config_env"
    assert credential.mcp_server == "config-server"
    assert credential.toolkit == "MCP"
    assert credential.env_vars == list(server.config.env)


def test_validate_returns_prompt_variables_with_sensitive_defaults_masked():
    assistant = _assistant_with_mcp_servers()
    assistant.prompt_variables = [
        PromptVariable(key="api_key", default_value="s3cret", is_sensitive=True),
        PromptVariable(key="region", default_value="eu", is_sensitive=False),
    ]

    result = validate_assistant_inline_integrations(assistant)

    assert result["is_valid"]
    assert result["prompt_variables"] == [
        {"key": "api_key", "description": None, "default_value": "********", "is_sensitive": True},
        {"key": "region", "description": None, "default_value": "eu", "is_sensitive": False},
    ]
    assert assistant.prompt_variables[0].default_value == "s3cret"


@pytest.mark.parametrize("no_variables", [[], None], ids=["empty_list", "none"])
def test_validate_omits_prompt_variables_when_the_assistant_has_none(no_variables):
    """An unset MagicMock attribute is truthy, so the empty value has to be set explicitly."""
    assistant = _assistant_with_mcp_servers()
    assistant.prompt_variables = no_variables

    result = validate_assistant_inline_integrations(assistant)

    assert "prompt_variables" not in result
