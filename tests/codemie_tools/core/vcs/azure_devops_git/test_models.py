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

"""Tests for Azure DevOps Git configuration models."""

from codemie_tools.core.vcs.azure_devops_git.models import (
    AzureDevOpsGitConfig,
    AzureDevOpsGitInput,
    AzureDevOpsGitOutput,
)


def test_config_valid():
    """Test valid configuration."""
    config = AzureDevOpsGitConfig(url="https://dev.azure.com", organization="myorg", token="pat_token123")
    assert config.url == "https://dev.azure.com"
    assert config.organization == "myorg"
    assert config.token == "pat_token123"


def test_config_fields():
    """Test configuration fields."""
    config = AzureDevOpsGitConfig(
        url="https://dev.azure.com",
        organization="myorg",
        project="MyProject",
        token="pat_token123",
        api_version="7.0",
    )

    # Verify fields exist
    assert hasattr(config, "url")
    assert hasattr(config, "organization")
    assert hasattr(config, "project")
    assert hasattr(config, "token")
    assert hasattr(config, "api_version")

    # Verify field values
    assert config.url == "https://dev.azure.com"
    assert config.organization == "myorg"
    assert config.project == "MyProject"
    assert config.token == "pat_token123"
    assert config.api_version == "7.0"


def test_config_defaults():
    """Test configuration default values."""
    config = AzureDevOpsGitConfig(url="https://dev.azure.com", organization="myorg", token="pat_token123")

    # Check defaults
    assert config.project is None
    assert config.api_version == "7.1-preview.1"


def test_input_model():
    """Test AzureDevOpsGitInput model."""
    # Test with string (JSON)
    input_str = '{"method": "GET", "url": "/_apis/git/repositories", "method_arguments": {"project": "MyProject"}}'
    input_model = AzureDevOpsGitInput(query=input_str)
    assert input_model.query == input_str

    # Test with dictionary
    input_dict = {
        "method": "GET",
        "url": "/_apis/git/repositories",
        "method_arguments": {"project": "MyProject"},
    }
    input_model = AzureDevOpsGitInput(query=input_dict)
    assert input_model.query == input_dict


def test_output_model():
    """Test AzureDevOpsGitOutput model."""
    # Test successful response
    success_output = AzureDevOpsGitOutput(
        success=True,
        status_code=200,
        method="GET",
        url="https://dev.azure.com/org/_apis/git/repositories",
        data={"value": []},
    )
    assert success_output.success is True
    assert success_output.status_code == 200
    assert success_output.error is None

    # Test error response
    error_output = AzureDevOpsGitOutput(
        success=False,
        status_code=404,
        method="GET",
        url="https://dev.azure.com/org/_apis/git/repositories/invalid-id",
        data={"message": "Repository not found"},
        error="HTTP 404: Not Found - Repository not found",
    )
    assert error_output.success is False
    assert error_output.status_code == 404
    assert error_output.error == "HTTP 404: Not Found - Repository not found"


def test_empty_config():
    """Test that config validation works at runtime with empty required fields."""
    from codemie_tools.core.vcs.azure_devops_git.tools import AzureDevOpsGitTool

    # Arrange - create config with empty required fields
    config = AzureDevOpsGitConfig(url="", organization="", token="")

    # Act - invoke tool (goes through _run which validates)
    tool = AzureDevOpsGitTool(config=config)
    result = tool.invoke({"query": {"method": "GET", "url": "/_apis/git/repositories", "method_arguments": {}}})

    # Assert - should return error message string when handle_tool_error=True
    assert isinstance(result, str)
    assert "Tool config is not set" in result
    # Check that at least one of the missing fields is mentioned
    assert any(field in result.lower() for field in ["url", "organization", "token"])
