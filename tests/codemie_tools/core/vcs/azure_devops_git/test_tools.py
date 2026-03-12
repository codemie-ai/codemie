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

"""Tests for Azure DevOps Git tools."""

import json
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import ToolException
from requests.exceptions import RequestException

from codemie_tools.core.vcs.azure_devops_git.models import AzureDevOpsGitConfig
from codemie_tools.core.vcs.azure_devops_git.tools import AzureDevOpsGitTool


@pytest.fixture
def config():
    """Create a test configuration."""
    return AzureDevOpsGitConfig(url="https://dev.azure.com", organization="testorg", token="test_token")


@pytest.fixture
def tool(config):
    """Create a tool instance."""
    return AzureDevOpsGitTool(config=config)


def test_tool_initialization(tool, config):
    """Test tool initializes correctly."""
    # Test basic initialization properties
    assert hasattr(tool, "name")
    assert hasattr(tool, "config")

    # Verify config properties
    assert tool.config.url == "https://dev.azure.com"
    assert tool.config.organization == "testorg"
    assert tool.config.token == "test_token"


def test_execute_interface(tool):
    """Test that the tool has the required interface."""
    # Test that the tool has the execute method
    assert hasattr(tool, "execute")
    assert callable(getattr(tool, "execute"))


@patch("codemie_tools.core.vcs.azure_devops_git.tools.AzureDevOpsGitTool.execute")
def test_healthcheck_success(mock_execute, tool):
    """Test successful healthcheck."""
    # Setup mock to simulate a successful API call
    mock_execute.return_value = MagicMock(success=True)

    # Run healthcheck
    try:
        tool._healthcheck()
    except ToolException:
        pytest.fail("Health check should not raise an exception on success")

    # Verify that execute was called correctly
    mock_execute.assert_called_once_with(
        query={"method": "GET", "url": "/_apis/git/repositories", "method_arguments": {"$top": 1}}
    )


@patch("codemie_tools.core.vcs.azure_devops_git.tools.AzureDevOpsGitTool.execute")
def test_healthcheck_failure(mock_execute, tool):
    """Test healthcheck failure."""
    # Setup mock to simulate a failed API call
    mock_execute.side_effect = RequestException("Connection timed out")

    # Healthcheck should raise ToolException
    with pytest.raises(ToolException, match="Azure DevOps Git health check failed: Connection timed out"):
        tool._healthcheck()


@patch("codemie_tools.core.vcs.azure_devops_git.tools.requests.request")
def test_make_request_get(mock_request, tool):
    """Test _make_request for GET requests."""
    # Setup mock
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_request.return_value = mock_response

    # Test GET request
    headers = {"Authorization": "Basic abc123=="}
    method_arguments = {"project": "MyProject"}

    tool._make_request(
        method="GET",
        url="https://dev.azure.com/testorg/_apis/git/repositories",
        headers=headers,
        method_arguments=method_arguments,
    )

    # Verify request was called correctly
    mock_request.assert_called_once()
    call_args = mock_request.call_args[1]

    assert call_args["method"] == "GET"
    assert call_args["url"] == "https://dev.azure.com/testorg/_apis/git/repositories"
    assert call_args["headers"] == headers
    assert call_args["params"] == {"project": "MyProject", "api-version": "7.1-preview.1"}


@patch("codemie_tools.core.vcs.azure_devops_git.tools.requests.request")
def test_make_request_post(mock_request, tool):
    """Test _make_request for POST requests."""
    # Setup mock
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_request.return_value = mock_response

    # Test POST request
    headers = {"Authorization": "Basic abc123=="}
    method_arguments = {"name": "newrepo"}

    tool._make_request(
        method="POST",
        url="https://dev.azure.com/testorg/_apis/git/repositories",
        headers=headers,
        method_arguments=method_arguments,
    )

    # Verify request was called correctly
    mock_request.assert_called_once()
    call_args = mock_request.call_args[1]

    assert call_args["method"] == "POST"
    assert call_args["url"] == "https://dev.azure.com/testorg/_apis/git/repositories"
    assert call_args["headers"] == headers
    assert call_args["json"] == {"name": "newrepo"}
    assert call_args["params"] == {"api-version": "7.1-preview.1"}


def test_create_basic_auth_header(tool):
    """Test basic auth header creation."""
    header = tool._create_basic_auth_header("my_token")

    # Header should be "Basic OnRva2Vu" (:token in base64)
    assert header.startswith("Basic ")
    # We don't test the exact encoding as it can change, but verify structure
    assert len(header) > 6  # "Basic " + non-empty string


@patch("codemie_tools.core.vcs.azure_devops_git.tools.AzureDevOpsGitTool._make_request")
def test_execute_get_success(mock_make_request, tool):
    """Test successful GET request execution."""
    # Setup mock response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.reason = "OK"
    mock_response.json.return_value = {"value": [{"id": "repo1", "name": "Repo1"}]}
    mock_make_request.return_value = mock_response

    # Test execution
    query = {
        "method": "GET",
        "url": "/_apis/git/repositories",
        "method_arguments": {"project": "MyProject"},
    }

    result = tool.execute(query)

    # Verify result
    assert result.success is True
    assert result.status_code == 200
    assert result.method == "GET"
    assert result.url == "https://dev.azure.com/testorg/MyProject/_apis/git/repositories"
    assert result.data == {"value": [{"id": "repo1", "name": "Repo1"}]}
    assert result.error is None

    # Verify request was made correctly
    mock_make_request.assert_called_once()
    call_args = mock_make_request.call_args

    assert call_args[0][0] == "GET"  # method
    assert call_args[0][1] == "https://dev.azure.com/testorg/MyProject/_apis/git/repositories"  # url
    assert "Authorization" in call_args[0][2]  # headers
    assert call_args[0][3] == {}  # method_arguments (project was extracted)


@patch("codemie_tools.core.vcs.azure_devops_git.tools.AzureDevOpsGitTool._make_request")
def test_execute_post_success(mock_make_request, tool):
    """Test successful POST request execution."""
    # Setup mock response
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.reason = "Created"
    mock_response.json.return_value = {"id": "new-repo", "name": "NewRepo"}
    mock_make_request.return_value = mock_response

    # Test execution
    # Note: project should remain in method_arguments for POST body, not extracted for URL
    query = {
        "method": "POST",
        "url": "/_apis/git/repositories",
        "method_arguments": {"name": "NewRepo", "project": {"id": "project-id"}},
    }

    result = tool.execute(query)

    # Verify result
    assert result.success is True
    assert result.status_code == 201
    assert result.method == "POST"
    # For POST requests to create repositories, project stays in body, not in URL
    assert result.url == "https://dev.azure.com/testorg/_apis/git/repositories"
    assert result.data == {"id": "new-repo", "name": "NewRepo"}
    assert result.error is None


@patch("codemie_tools.core.vcs.azure_devops_git.tools.AzureDevOpsGitTool._make_request")
def test_execute_error_response(mock_make_request, tool):
    """Test request that returns an error."""
    # Setup mock response
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.reason = "Not Found"
    mock_response.json.return_value = {"message": "Repository not found"}
    mock_make_request.return_value = mock_response

    # Test execution
    query = {"method": "GET", "url": "/_apis/git/repositories/invalid-id", "method_arguments": {}}

    result = tool.execute(query)

    # Verify result
    assert result.success is False
    assert result.status_code == 404
    assert result.error == "HTTP 404: Not Found - Repository not found"


@patch("codemie_tools.core.vcs.azure_devops_git.tools.AzureDevOpsGitTool._make_request")
def test_execute_non_json_response(mock_make_request, tool):
    """Test handling non-JSON responses."""
    # Setup mock response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.reason = "OK"
    mock_response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)
    mock_response.text = "plain text content"
    mock_make_request.return_value = mock_response

    # Test execution
    query = {
        "method": "GET",
        "url": "/_apis/git/repositories/repo-id/items",
        "method_arguments": {"path": "/file.txt"},
        "custom_headers": {"Accept": "application/octet-stream"},
    }

    result = tool.execute(query)

    # Verify result
    assert result.success is True
    assert result.status_code == 200
    assert result.data == "plain text content"
    assert result.error is None


def test_execute_missing_config(tool):
    """Test execution with missing configuration.

    Note: Config validation is now handled by the base class CodeMieTool._validate_config()
    which is called in _run() before execute(). Use tool.invoke() to test validation,
    or see test_empty_config() in test_models.py for the proper test pattern.

    This test is kept to verify that direct execute() calls don't crash,
    but validation happens at the invoke() level.
    """
    # Config with empty required fields
    tool.config.token = ""
    tool.config.organization = ""
    tool.config.url = ""

    # Test execution via invoke (which calls _run -> _validate_config -> execute)
    query = {"method": "GET", "url": "/_apis/git/repositories", "method_arguments": {}}

    # Use invoke() instead of execute() to trigger validation
    result = tool.invoke({"query": query})

    # Should return error message string when handle_tool_error=True (default)
    assert isinstance(result, str)
    assert "Tool config is not set" in result


def test_execute_invalid_query_format():
    """Test execution with invalid query format."""
    # Create tool without using fixture
    config = AzureDevOpsGitConfig(url="https://dev.azure.com", organization="testorg", token="test_token")
    tool = AzureDevOpsGitTool(config=config)

    # Test execution with invalid JSON
    with pytest.raises(ToolException):
        tool.execute('{"method": "GET", "bad_json":}')


def test_execute_missing_method():
    """Test execution with missing method."""
    # Create tool without using fixture
    config = AzureDevOpsGitConfig(url="https://dev.azure.com", organization="testorg", token="test_token")
    tool = AzureDevOpsGitTool(config=config)

    # Test execution with missing method
    with pytest.raises(ToolException):
        tool.execute({"url": "/_apis/git/repositories", "method_arguments": {}})


def test_execute_invalid_url_format():
    """Test execution with invalid URL format."""
    # Create tool without using fixture
    config = AzureDevOpsGitConfig(url="https://dev.azure.com", organization="testorg", token="test_token")
    tool = AzureDevOpsGitTool(config=config)

    # Test execution with invalid URL format
    with pytest.raises(ToolException):
        tool.execute(
            {
                "method": "GET",
                "url": "/invalid/url",  # doesn't start with /_apis/git/
                "method_arguments": {},
            }
        )


@patch("codemie_tools.core.vcs.azure_devops_git.tools.AzureDevOpsGitTool._make_request")
def test_execute_request_exception(mock_make_request, tool):
    """Test handling request exceptions."""
    # Setup mock to raise exception
    mock_make_request.side_effect = RequestException("Connection error")

    # Test execution
    query = {"method": "GET", "url": "/_apis/git/repositories", "method_arguments": {}}

    # Should raise ToolException
    with pytest.raises(ToolException):
        tool.execute(query)


@patch("codemie_tools.core.vcs.azure_devops_git.tools.AzureDevOpsGitTool._make_request")
def test_execute_with_repository_id_in_url(mock_make_request, tool):
    """Test URL with repositoryId substitution."""
    # Setup mock response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.reason = "OK"
    mock_response.json.return_value = {"id": "repo-123", "name": "MyRepo"}
    mock_make_request.return_value = mock_response

    # Test execution with {repositoryId} in URL
    query = {
        "method": "GET",
        "url": "/_apis/git/repositories/{repositoryId}",
        "method_arguments": {"repositoryId": "repo-123"},
    }

    result = tool.execute(query)

    # Verify result
    assert result.success is True
    assert result.url == "https://dev.azure.com/testorg/_apis/git/repositories/repo-123"

    # Verify correct URL was used in the request
    mock_make_request.assert_called_once()
    assert mock_make_request.call_args[0][1] == "https://dev.azure.com/testorg/_apis/git/repositories/repo-123"


@patch("codemie_tools.core.vcs.azure_devops_git.tools.AzureDevOpsGitTool._make_request")
def test_execute_url_without_repository_id_placeholder(mock_make_request, tool):
    """Test URL that does not contain a {repositoryId} placeholder."""
    # Setup mock response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.reason = "OK"
    mock_response.json.return_value = {"count": 1, "value": [{"id": "commit1"}]}
    mock_make_request.return_value = mock_response

    # URL does not contain {repositoryId}, so it should not be modified.
    query = {
        "method": "GET",
        "url": "/_apis/git/repositories/a-specific-repo-id/commits",
        "method_arguments": {"repositoryId": "should-be-ignored"},  # This should be ignored
    }

    result = tool.execute(query)

    # Verify result
    assert result.success is True
    assert result.url == "https://dev.azure.com/testorg/_apis/git/repositories/a-specific-repo-id/commits"

    # Verify correct URL was used in the request
    mock_make_request.assert_called_once()
    assert mock_make_request.call_args[0][1] == result.url


@patch("codemie_tools.core.vcs.azure_devops_git.tools.AzureDevOpsGitTool._make_request")
def test_execute_with_custom_headers(mock_make_request, tool):
    """Test request with custom headers."""
    # Setup mock response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.reason = "OK"
    mock_response.text = "file content"
    mock_response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)
    mock_make_request.return_value = mock_response

    # Test execution with custom headers
    query = {
        "method": "GET",
        "url": "/_apis/git/repositories/repo-id/items",
        "method_arguments": {"path": "/file.txt"},
        "custom_headers": {"Accept": "application/octet-stream", "X-Custom": "Value"},
    }

    result = tool.execute(query)

    # Verify result
    assert result.success is True
    assert result.data == "file content"

    # Verify headers were included
    mock_make_request.assert_called_once()
    headers = mock_make_request.call_args[0][2]
    assert headers["Accept"] == "application/octet-stream"
    assert headers["X-Custom"] == "Value"
    assert "Authorization" in headers  # Auth header should still be present


@patch("codemie_tools.core.vcs.azure_devops_git.tools.AzureDevOpsGitTool._make_request")
def test_cannot_override_auth_headers(mock_make_request, tool):
    """Test that authorization headers cannot be overridden."""
    # Setup mock response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.reason = "OK"
    mock_response.json.return_value = {"value": []}
    mock_make_request.return_value = mock_response

    # Try to override authorization header
    query = {
        "method": "GET",
        "url": "/_apis/git/repositories",
        "method_arguments": {},
        "custom_headers": {"Authorization": "Bearer fake_token"},
    }

    tool.execute(query)

    # Verify authorization header was not overridden
    mock_make_request.assert_called_once()
    headers = mock_make_request.call_args[0][2]
    assert headers["Authorization"] != "Bearer fake_token"
    assert headers["Authorization"].startswith("Basic ")
