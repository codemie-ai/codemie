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
from codemie_tools.core.vcs.gitlab.tools import GitlabTool
from codemie_tools.core.vcs.gitlab.tools_vars import GITLAB_TOOL


class TestGitlabTool:
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

    def test_init(self, gitlab_tool):
        """Test GitlabTool initialization."""
        assert gitlab_tool.name == GITLAB_TOOL.name
        assert gitlab_tool.description == GITLAB_TOOL.description
        assert isinstance(gitlab_tool.config, GitlabConfig)
        assert gitlab_tool.config.url == "https://gitlab.example.com"
        assert gitlab_tool.config.token == "test_token"

    @patch("requests.request")
    def test_execute_get_request(self, mock_request, gitlab_tool, mock_response):
        """Test executing a GET request."""
        mock_request.return_value = mock_response

        query = {"method": "GET", "url": "/api/v4/projects", "method_arguments": {"visibility": "public"}}

        result = gitlab_tool.execute(query)

        mock_request.assert_called_once()
        assert "HTTP: GET" in result
        assert "200 OK" in result
        assert '{"id": 1, "name": "test"}' in result

    @patch("requests.request")
    def test_execute_post_request(self, mock_request, gitlab_tool, mock_response):
        """Test executing a POST request."""
        mock_request.return_value = mock_response

        query = {
            "method": "POST",
            "url": "/api/v4/projects",
            "method_arguments": {"name": "test_project", "visibility": "private"},
        }

        result = gitlab_tool.execute(query)

        mock_request.assert_called_once()
        assert "HTTP: POST" in result
        assert "200 OK" in result

    @patch("requests.request")
    def test_execute_with_custom_headers(self, mock_request, gitlab_tool, mock_response):
        """Test executing a request with custom headers."""
        mock_request.return_value = mock_response

        query = {
            "method": "GET",
            "url": "/api/v4/projects",
            "method_arguments": {},
            "custom_headers": {"X-Custom-Header": "test_value"},
        }

        gitlab_tool.execute(query)

        mock_request.assert_called_once()
        # Check that the custom header was included in the request
        headers = mock_request.call_args[1]["headers"]
        assert headers["X-Custom-Header"] == "test_value"
        assert headers["Authorization"] == "Bearer test_token"

    @patch("requests.request")
    def test_execute_with_protected_header_attempt(self, mock_request, gitlab_tool, mock_response):
        """Test that protected headers cannot be overridden."""
        mock_request.return_value = mock_response

        query = {
            "method": "GET",
            "url": "/api/v4/projects",
            "method_arguments": {},
            "custom_headers": {"Authorization": "Bearer fake_token"},
        }

        gitlab_tool.execute(query)

        mock_request.assert_called_once()
        # Check that the authorization header was not overridden
        headers = mock_request.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer test_token"

    @patch("requests.request")
    def test_execute_with_string_query(self, mock_request, gitlab_tool, mock_response):
        """Test executing with a JSON string query."""
        mock_request.return_value = mock_response

        query = json.dumps({"method": "GET", "url": "/api/v4/projects", "method_arguments": {"visibility": "public"}})

        result = gitlab_tool.execute(query)

        mock_request.assert_called_once()
        assert "HTTP: GET" in result
        assert "200 OK" in result

    def test_execute_with_invalid_json_string(self, gitlab_tool):
        """Test executing with an invalid JSON string."""
        query = "invalid json"

        with pytest.raises(ValueError) as excinfo:
            gitlab_tool.execute(query)

        assert "Query must be a JSON string" in str(excinfo.value)

    @patch("requests.request")
    def test_execute_with_request_error(self, mock_request, gitlab_tool):
        """Test handling of request errors."""
        mock_request.side_effect = Exception("Connection error")

        query = {"method": "GET", "url": "/api/v4/projects", "method_arguments": {}}

        # The execute method doesn't catch generic exceptions, so we expect the original exception
        with pytest.raises(Exception) as excinfo:
            gitlab_tool.execute(query)

        assert "Connection error" in str(excinfo.value)

    @patch("requests.request")
    def test_make_request_get(self, mock_request, gitlab_tool, mock_response):
        """Test _make_request method with GET."""
        mock_request.return_value = mock_response

        headers = {"Authorization": "Bearer test_token"}
        method_arguments = {"visibility": "public"}

        response = gitlab_tool._make_request(
            "GET", "https://gitlab.example.com/api/v4/projects", headers, method_arguments
        )

        mock_request.assert_called_with(
            method="GET", url="https://gitlab.example.com/api/v4/projects", headers=headers, params=method_arguments
        )
        assert response == mock_response

    @patch("requests.request")
    def test_make_request_post(self, mock_request, gitlab_tool, mock_response):
        """Test _make_request method with POST."""
        mock_request.return_value = mock_response

        headers = {"Authorization": "Bearer test_token"}
        method_arguments = {"name": "test_project"}

        response = gitlab_tool._make_request(
            "POST", "https://gitlab.example.com/api/v4/projects", headers, method_arguments
        )

        mock_request.assert_called_with(
            method="POST", url="https://gitlab.example.com/api/v4/projects", headers=headers, data=method_arguments
        )
        assert response == mock_response
