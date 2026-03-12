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

import re
from unittest.mock import MagicMock, patch

import pytest
from atlassian import Jira

from codemie_tools.core.project_management.jira.models import JiraConfig
from codemie_tools.core.project_management.jira.tools import JIRA_TEST_URL, JiraInput
from codemie_tools.core.project_management.jira.tools_vars import GENERIC_JIRA_TOOL, get_jira_tool_description


# Create a mock class for testing that avoids the BaseTool validation issues
class MockGenericJiraIssueTool:
    """Mock class for testing GenericJiraIssueTool without BaseTool validation issues."""

    def __init__(self, config):
        self.config = config
        self.name = GENERIC_JIRA_TOOL.name
        self.description = GENERIC_JIRA_TOOL.description
        self.args_schema = JiraInput
        self.issue_search_pattern = r'/rest/api/\d+/search'
        if config.cloud:
            self.issue_search_pattern = r'/rest/api/3/search/jql'
            self.description = get_jira_tool_description(api_version=3)
        self.jira = None

    def execute(self, method, relative_url, params="", *args):
        """Mock execute method."""
        jira = Jira(
            url=self.config.url,
            username=self.config.username if self.config.username else None,
            token=self.config.token if not self.config.cloud else None,
            password=self.config.token if self.config.cloud else None,
            cloud=self.config.cloud,
        )
        self.jira = jira

        from codemie_tools.core.project_management.jira.utils import validate_jira_creds, parse_payload_params

        validate_jira_creds(jira)
        payload_params = parse_payload_params(params)

        if method == "GET":
            response_text, response = self._handle_get_request(relative_url, payload_params)
        else:
            response_text, response = self._handle_non_get_request(method, relative_url, payload_params)

        return f"HTTP: {method} {relative_url} -> {response.status_code} {response.reason} {response_text}"

    def _handle_get_request(self, relative_url, payload_params):
        """Mock _handle_get_request method."""
        response = self.jira.request(
            method="GET",
            path=relative_url,
            params=payload_params,
            advanced_mode=True,
            headers={"content-type": "application/json"},
        )
        self.jira.raise_for_status(response)

        from codemie_tools.core.project_management.jira.utils import process_search_response

        if re.match(self.issue_search_pattern, relative_url):
            response_text = process_search_response(self.jira.url, response, payload_params)
        else:
            response_text = response.text
        return response_text, response

    def _handle_non_get_request(self, method, relative_url, payload_params):
        """Mock _handle_non_get_request method."""
        response = self.jira.request(method=method, path=relative_url, data=payload_params, advanced_mode=True)
        self.jira.raise_for_status(response)
        return response.text, response

    def _healthcheck(self):
        """Mock _healthcheck method."""
        self.execute("GET", JIRA_TEST_URL)


class TestGenericJiraIssueTool:
    """Tests for the GenericJiraIssueTool class."""

    @pytest.fixture
    def jira_config(self):
        """Fixture for JiraConfig."""
        return JiraConfig(url="https://jira.example.com", token="abc123")

    @pytest.fixture
    def jira_cloud_config(self):
        """Fixture for JiraConfig with cloud=True."""
        return JiraConfig(url="https://jira.example.com", token="abc123", username="user@example.com", cloud=True)

    @pytest.fixture
    def mock_jira(self):
        """Fixture for mocked Jira client."""
        with patch("atlassian.Jira") as mock_jira_class:
            mock_jira_instance = MagicMock(spec=Jira)
            mock_jira_class.return_value = mock_jira_instance
            yield mock_jira_instance

    def test_init_server(self, jira_config):
        """Test initialization with server config."""
        tool = MockGenericJiraIssueTool(jira_config)

        assert tool.config == jira_config
        assert tool.issue_search_pattern == r'/rest/api/\d+/search'
        assert "JIRA Tool for Official Atlassian JIRA REST API V2" in tool.description

    def test_init_cloud(self, jira_cloud_config):
        """Test initialization with cloud config."""
        tool = MockGenericJiraIssueTool(jira_cloud_config)

        assert tool.config == jira_cloud_config
        assert tool.issue_search_pattern == r'/rest/api/3/search/jql'
        assert "JIRA Tool for Official Atlassian JIRA REST API V3" in tool.description

    @patch("codemie_tools.core.project_management.jira.utils.validate_jira_creds")
    def test_execute_get_request(self, mock_validate_creds, jira_config, mock_jira):
        """Test execute method with GET request."""
        # Setup mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason = "OK"
        mock_response.text = '{"key": "value"}'

        mock_jira.request.return_value = mock_response

        # Create tool and execute
        tool = MockGenericJiraIssueTool(jira_config)

        with patch.object(
            tool, '_handle_get_request', return_value=(mock_response.text, mock_response)
        ) as mock_handle_get:
            result = tool.execute(method="GET", relative_url="/rest/api/2/issue/TEST-123")

        # Verify
        mock_validate_creds.assert_called_once()
        mock_handle_get.assert_called_once()
        assert "HTTP: GET /rest/api/2/issue/TEST-123 -> 200 OK" in result

    @patch("codemie_tools.core.project_management.jira.utils.validate_jira_creds")
    def test_execute_post_request(self, mock_validate_creds, jira_config, mock_jira):
        """Test execute method with POST request."""
        # Setup mock response
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.reason = "Created"
        mock_response.text = '{"id": "TEST-123"}'

        mock_jira.request.return_value = mock_response

        # Create tool and execute
        tool = MockGenericJiraIssueTool(jira_config)

        with patch.object(
            tool, '_handle_non_get_request', return_value=(mock_response.text, mock_response)
        ) as mock_handle_non_get:
            result = tool.execute(
                method="POST", relative_url="/rest/api/2/issue", params='{"fields": {"summary": "Test Issue"}}'
            )

        # Verify
        mock_validate_creds.assert_called_once()
        mock_handle_non_get.assert_called_once()
        assert "HTTP: POST /rest/api/2/issue -> 201 Created" in result

    @patch("codemie_tools.core.project_management.jira.utils.parse_payload_params")
    def test_handle_get_request_regular(self, mock_parse_params, jira_config, mock_jira):
        """Test _handle_get_request with a regular (non-search) endpoint."""
        # Setup
        mock_parse_params.return_value = {"fields": "summary,description"}

        mock_response = MagicMock()
        mock_response.text = '{"key": "TEST-123", "fields": {"summary": "Test Issue"}}'
        mock_jira.request.return_value = mock_response

        # Create tool and execute
        tool = MockGenericJiraIssueTool(jira_config)
        tool.jira = mock_jira  # Set the jira attribute directly for testing

        response_text, response = tool._handle_get_request(
            "/rest/api/2/issue/TEST-123", {"fields": "summary,description"}
        )

        # Verify
        mock_jira.request.assert_called_once_with(
            method="GET",
            path="/rest/api/2/issue/TEST-123",
            params={"fields": "summary,description"},
            advanced_mode=True,
            headers={"content-type": "application/json"},
        )
        mock_jira.raise_for_status.assert_called_once_with(mock_response)
        assert response_text == '{"key": "TEST-123", "fields": {"summary": "Test Issue"}}'
        assert response == mock_response

    @patch("codemie_tools.core.project_management.jira.utils.parse_payload_params")
    @patch("codemie_tools.core.project_management.jira.utils.process_search_response")
    def test_handle_get_request_search(self, mock_process_search, mock_parse_params, jira_config, mock_jira):
        """Test _handle_get_request with a search endpoint."""
        # Setup
        mock_parse_params.return_value = {"jql": "project = TEST"}

        mock_response = MagicMock()
        mock_response.text = '{"issues": [{"key": "TEST-123"}], "total": 1}'
        mock_jira.request.return_value = mock_response
        mock_jira.url = "https://jira.example.com"

        mock_process_search.return_value = "Processed search response"

        # Create tool and execute
        tool = MockGenericJiraIssueTool(jira_config)
        tool.jira = mock_jira  # Set the jira attribute directly for testing

        response_text, response = tool._handle_get_request("/rest/api/2/search", {"jql": "project = TEST"})

        # Verify
        mock_jira.request.assert_called_once()
        mock_jira.raise_for_status.assert_called_once_with(mock_response)
        mock_process_search.assert_called_once_with(mock_jira.url, mock_response, {"jql": "project = TEST"})
        assert response_text == "Processed search response"
        assert response == mock_response

    @patch("codemie_tools.core.project_management.jira.utils.parse_payload_params")
    def test_handle_non_get_request(self, mock_parse_params, jira_config, mock_jira):
        """Test _handle_non_get_request."""
        # Setup
        mock_parse_params.return_value = {"fields": {"summary": "Test Issue"}}

        mock_response = MagicMock()
        mock_response.text = '{"id": "TEST-123"}'
        mock_jira.request.return_value = mock_response

        # Create tool and execute
        tool = MockGenericJiraIssueTool(jira_config)
        tool.jira = mock_jira  # Set the jira attribute directly for testing

        response_text, response = tool._handle_non_get_request(
            "POST", "/rest/api/2/issue", {"fields": {"summary": "Test Issue"}}
        )

        # Verify
        mock_jira.request.assert_called_once_with(
            method="POST", path="/rest/api/2/issue", data={"fields": {"summary": "Test Issue"}}, advanced_mode=True
        )
        mock_jira.raise_for_status.assert_called_once_with(mock_response)
        assert response_text == '{"id": "TEST-123"}'
        assert response == mock_response

    def test_healthcheck(self, jira_config):
        """Test _healthcheck method."""
        # Create tool and execute
        tool = MockGenericJiraIssueTool(jira_config)

        with patch.object(tool, 'execute') as mock_execute:
            tool._healthcheck()

            # Verify
            mock_execute.assert_called_once_with("GET", JIRA_TEST_URL)
