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

import json
from unittest.mock import patch, Mock

import pytest

from codemie_tools.core.vcs.gitlab.models import GitlabConfig
from codemie_tools.core.vcs.gitlab.tools import GitlabTool, GitlabInput


class TestGitlabToolAdvanced:
    """Advanced tests for GitlabTool class."""

    @pytest.fixture
    def gitlab_tool(self):
        """Create a GitlabTool instance with mock config."""
        config = GitlabConfig(url="https://gitlab.example.com", token="test_token")
        return GitlabTool(config=config)

    @pytest.fixture
    def mock_response(self):
        """Create a mock response object."""
        mock = Mock()
        mock.status_code = 200
        mock.reason = "OK"
        mock.text = '{"id": 1, "name": "test"}'
        return mock

    def test_gitlab_input_validation(self):
        """Test GitlabInput validation."""
        # Valid input
        valid_input = GitlabInput(query={"method": "GET", "url": "/api/v4/projects", "method_arguments": {}})
        assert valid_input.query["method"] == "GET"
        assert valid_input.query["url"] == "/api/v4/projects"

        # Valid input as string
        valid_string_input = GitlabInput(
            query=json.dumps({"method": "GET", "url": "/api/v4/projects", "method_arguments": {}})
        )
        assert isinstance(valid_string_input.query, str)

        # Input with custom headers
        input_with_headers = GitlabInput(
            query={
                "method": "GET",
                "url": "/api/v4/projects",
                "method_arguments": {},
                "custom_headers": {"X-Custom": "value"},
            }
        )
        assert input_with_headers.query["custom_headers"]["X-Custom"] == "value"

    @patch("requests.request")
    def test_execute_with_empty_method_arguments(self, mock_request, gitlab_tool, mock_response):
        """Test executing with empty method_arguments."""
        mock_request.return_value = mock_response

        query = {"method": "GET", "url": "/api/v4/projects", "method_arguments": {}}

        result = gitlab_tool.execute(query)

        mock_request.assert_called_once()
        assert "HTTP: GET" in result
        assert "200 OK" in result

    @patch("requests.request")
    def test_execute_with_missing_method_arguments(self, mock_request, gitlab_tool, mock_response):
        """Test executing with missing method_arguments."""
        mock_request.return_value = mock_response

        query = {"method": "GET", "url": "/api/v4/projects"}

        result = gitlab_tool.execute(query)

        mock_request.assert_called_once()
        assert "HTTP: GET" in result
        assert "200 OK" in result

    @patch("requests.request")
    def test_execute_with_error_response(self, mock_request, gitlab_tool):
        """Test handling of error responses."""
        error_response = Mock()
        error_response.status_code = 404
        error_response.reason = "Not Found"
        error_response.text = '{"message": "Project not found"}'
        mock_request.return_value = error_response

        query = {"method": "GET", "url": "/api/v4/projects/999", "method_arguments": {}}

        result = gitlab_tool.execute(query)

        mock_request.assert_called_once()
        assert "HTTP: GET" in result
        assert "404 Not Found" in result
        assert "Project not found" in result

    @patch("requests.request")
    def test_execute_with_json_parse_error(self, mock_request, gitlab_tool):
        """Test handling of JSON parse errors in the response."""
        invalid_json_response = Mock()
        invalid_json_response.status_code = 200
        invalid_json_response.reason = "OK"
        invalid_json_response.text = "Not a JSON response"
        mock_request.return_value = invalid_json_response

        query = {"method": "GET", "url": "/api/v4/projects", "method_arguments": {}}

        # The tool should return the raw response without trying to parse it
        result = gitlab_tool.execute(query)

        mock_request.assert_called_once()
        assert "HTTP: GET" in result
        assert "200 OK" in result
        assert "Not a JSON response" in result

    @patch("requests.request")
    def test_execute_with_all_http_methods(self, mock_request, gitlab_tool, mock_response):
        """Test all HTTP methods."""
        mock_request.return_value = mock_response

        http_methods = ["GET", "POST", "PUT", "DELETE", "PATCH"]

        for method in http_methods:
            query = {"method": method, "url": "/api/v4/projects", "method_arguments": {"test": "value"}}

            result = gitlab_tool.execute(query)

            assert f"HTTP: {method}" in result
            assert "200 OK" in result

            # Reset mock for next iteration
            mock_request.reset_mock()

    @patch("requests.request")
    def test_execute_with_complex_query(self, mock_request, gitlab_tool, mock_response):
        """Test executing with a complex query."""
        mock_request.return_value = mock_response

        query = {
            "method": "POST",
            "url": "/api/v4/projects",
            "method_arguments": {
                "name": "test_project",
                "description": "A test project",
                "visibility": "private",
                "initialize_with_readme": True,
                "tags": ["test", "example"],
            },
            "custom_headers": {"X-Custom-Header": "value", "Content-Type": "application/json"},
        }

        result = gitlab_tool.execute(query)

        mock_request.assert_called_once()
        assert "HTTP: POST" in result
        assert "200 OK" in result

        # Check that the custom headers were included
        headers = mock_request.call_args[1]["headers"]
        assert headers["X-Custom-Header"] == "value"
        assert headers["Content-Type"] == "application/json"

        # Check that the method arguments were passed correctly
        data = mock_request.call_args[1]["data"]
        assert "name" in data
        assert "description" in data
        assert "visibility" in data
