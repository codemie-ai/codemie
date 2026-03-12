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
from unittest.mock import MagicMock, patch

import pytest
import requests

from codemie_tools.core.vcs.github.models import GithubConfig
from codemie_tools.core.vcs.github.tools import GithubTool


class TestGithubTool:
    def test_init(self):
        config = GithubConfig(token="ghp_123456")
        tool = GithubTool(config=config)

        assert tool.name == "github"
        assert "GitHub API" in tool.description
        assert tool.config == config
        assert tool.tokens_size_limit == 70_000

    @patch('codemie_tools.core.vcs.github.github_client.requests.request')
    def test_execute_with_dict_query(self, mock_request):
        # Setup
        config = GithubConfig(token="ghp_123456")
        tool = GithubTool(config=config)

        mock_response = MagicMock()
        mock_response.json.return_value = {"login": "testuser", "id": 12345}
        mock_request.return_value = mock_response

        query = {"method": "GET", "url": "https://api.github.com/user", "method_arguments": {}}

        # Execute
        result = tool.execute(query)

        # Assert
        mock_request.assert_called_once_with(
            method="GET",
            url="https://api.github.com/user",
            headers={"Accept": "application/vnd.github+json", "Authorization": "Bearer ghp_123456"},
            data=json.dumps({}),
        )
        assert result == {"login": "testuser", "id": 12345}

    @patch('codemie_tools.core.vcs.github.github_client.requests.request')
    def test_execute_with_string_query(self, mock_request):
        # Setup
        config = GithubConfig(token="ghp_123456")
        tool = GithubTool(config=config)

        mock_response = MagicMock()
        mock_response.json.return_value = {"login": "testuser", "id": 12345}
        mock_request.return_value = mock_response

        query = json.dumps({"method": "GET", "url": "https://api.github.com/user", "method_arguments": {}})

        # Execute
        result = tool.execute(query)

        # Assert
        mock_request.assert_called_once_with(
            method="GET",
            url="https://api.github.com/user",
            headers={"Accept": "application/vnd.github+json", "Authorization": "Bearer ghp_123456"},
            data=json.dumps({}),
        )
        assert result == {"login": "testuser", "id": 12345}

    @patch('codemie_tools.core.vcs.github.github_client.requests.request')
    def test_execute_with_custom_headers(self, mock_request):
        # Setup
        config = GithubConfig(token="ghp_123456")
        tool = GithubTool(config=config)

        mock_response = MagicMock()
        mock_response.json.return_value = {"login": "testuser", "id": 12345}
        mock_request.return_value = mock_response

        query = {
            "method": "GET",
            "url": "https://api.github.com/user",
            "method_arguments": {},
            "custom_headers": {"X-Custom-Header": "value"},
        }

        # Execute
        result = tool.execute(query)

        # Assert
        mock_request.assert_called_once_with(
            method="GET",
            url="https://api.github.com/user",
            headers={
                "Accept": "application/vnd.github+json",
                "X-Custom-Header": "value",
                "Authorization": "Bearer ghp_123456",
            },
            data=json.dumps({}),
        )
        assert result == {"login": "testuser", "id": 12345}

    def test_execute_with_invalid_json_string(self):
        # Setup
        config = GithubConfig(token="ghp_123456")
        tool = GithubTool(config=config)

        query = "invalid json"

        # Execute and Assert
        with pytest.raises(ValueError, match="Query must be a JSON string"):
            tool.execute(query)

    @patch('codemie_tools.core.vcs.github.github_client.requests.request')
    def test_file_response_handler_decorator(self, mock_request):
        # Setup
        config = GithubConfig(token="ghp_123456")
        tool = GithubTool(config=config)

        # Create a response that looks like a file
        mock_response = MagicMock()
        mock_response.json.return_value = {"content": "base64content", "encoding": "base64"}
        mock_request.return_value = mock_response

        query = {
            "method": "GET",
            "url": "https://api.github.com/repos/owner/repo/contents/file.py",
            "method_arguments": {},
        }

        # Execute - the file_response_handler decorator should be applied automatically
        result = tool.execute(query)

        # Assert
        # Just verify the response was returned correctly
        assert result == {"content": "base64content", "encoding": "base64"}

    @patch('codemie_tools.core.vcs.github.github_client.requests.request')
    def test_execute_with_request_error(self, mock_request):
        # Setup
        config = GithubConfig(token="ghp_123456")
        tool = GithubTool(config=config)

        # Mock request to raise an exception
        mock_request.side_effect = requests.RequestException("Connection error")

        query = {"method": "GET", "url": "https://api.github.com/user", "method_arguments": {}}

        # Execute and Assert - now wrapped in ToolException by GithubClient
        from langchain_core.tools import ToolException

        with pytest.raises(ToolException, match="Failed to connect to GitHub API"):
            tool.execute(query)
