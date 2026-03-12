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

from codemie.rest_api.models.assistant import InlineCredential
from codemie.rest_api.routers.assistant import (
    _validate_assistant_inline_integrations,
    _check_toolkit_credentials,
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
    result = _validate_assistant_inline_integrations(mock_assistant)

    # Should find credentials and be invalid
    assert not result["is_valid"]
    assert len(result["inline_credentials"]) > 0
    assert "inline integration credentials" in result["message"]

    # Test assistant with no credentials
    mock_assistant.toolkits = []
    mock_assistant.mcp_servers = []

    result = _validate_assistant_inline_integrations(mock_assistant)

    # Should find no credentials and be valid
    assert result["is_valid"]
