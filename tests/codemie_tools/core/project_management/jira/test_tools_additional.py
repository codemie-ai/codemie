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

from unittest.mock import MagicMock, patch

import pytest

from codemie_tools.core.project_management.jira.models import JiraConfig
from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool, JIRA_TEST_URL


class TestGenericJiraIssueToolAdditional:
    @patch('codemie_tools.core.project_management.jira.tools.validate_jira_creds')
    def test_validate_config_server(self, mock_validate_creds):
        # Setup
        config = JiraConfig(url="https://jira.example.com", token="abc123", username="testuser", cloud=False)

        # Execute
        tool = GenericJiraIssueTool(config=config)

        # Assert
        assert tool.issue_search_pattern == r'/rest/api/\d+/search'
        assert "JIRA Tool for Official Atlassian JIRA REST API V2" in tool.description
        mock_validate_creds.assert_called_once()

    @patch('codemie_tools.core.project_management.jira.tools.validate_jira_creds')
    @patch('codemie_tools.core.project_management.jira.tools.get_jira_tool_description')
    def test_validate_config_cloud(self, mock_get_description, mock_validate_creds):
        # Setup
        config = JiraConfig(url="https://jira.example.com", token="abc123", username="testuser", cloud=True)
        mock_get_description.return_value = "Cloud JIRA description"

        # Execute
        tool = GenericJiraIssueTool(config=config)

        # Assert
        assert tool.issue_search_pattern == r'/rest/api/3/search/jql'
        assert tool.description == "Cloud JIRA description"
        mock_validate_creds.assert_called_once()
        mock_get_description.assert_called_once_with(api_version=3)

    @patch('codemie_tools.core.project_management.jira.tools.validate_jira_creds')
    def test_execute_with_invalid_method(self, mock_validate_creds):
        # Setup
        config = JiraConfig(url="https://jira.example.com", token="abc123", username="testuser", cloud=False)

        tool = GenericJiraIssueTool(config=config)
        tool.jira = MagicMock()

        # Mock the request method to raise an exception
        tool.jira.request.side_effect = Exception("Invalid method")

        # Execute and Assert
        with pytest.raises(Exception, match="Invalid method"):
            tool.execute(method="INVALID", relative_url="/rest/api/2/issue/TEST-1", params="{}")

    @patch('codemie_tools.core.project_management.jira.tools.validate_jira_creds')
    def test_execute_with_api_error(self, mock_validate_creds):
        # Setup
        config = JiraConfig(url="https://jira.example.com", token="abc123", username="testuser", cloud=False)

        tool = GenericJiraIssueTool(config=config)
        tool.jira = MagicMock()

        # Mock the request method to return a response
        mock_response = MagicMock()
        tool.jira.request.return_value = mock_response

        # Mock raise_for_status to raise an exception
        tool.jira.raise_for_status.side_effect = Exception("API Error")

        # Execute and Assert
        with pytest.raises(Exception, match="API Error"):
            tool.execute(method="GET", relative_url="/rest/api/2/issue/TEST-1", params="{}")

    @patch('codemie_tools.core.project_management.jira.tools.GenericJiraIssueTool.execute')
    def test_healthcheck_success(self, mock_execute):
        # Setup
        config = JiraConfig(url="https://jira.example.com", token="abc123", username="testuser", cloud=False)

        mock_execute.return_value = "HTTP: GET /rest/api/2/myself -> 200 OK {}"

        tool = GenericJiraIssueTool(config=config)

        # Execute
        tool._healthcheck()

        # Assert
        mock_execute.assert_called_once_with("GET", JIRA_TEST_URL)

    @patch('codemie_tools.core.project_management.jira.tools.GenericJiraIssueTool.execute')
    def test_healthcheck_failure(self, mock_execute):
        # Setup
        config = JiraConfig(url="https://jira.example.com", token="abc123", username="testuser", cloud=False)

        # Mock the execute method to raise an exception
        mock_execute.side_effect = Exception("API Error")

        tool = GenericJiraIssueTool(config=config)

        # Execute and Assert
        with pytest.raises(Exception, match="API Error"):
            tool._healthcheck()

    @patch('codemie_tools.core.project_management.jira.tools.validate_jira_creds')
    def test_handle_get_request_with_search_pattern(self, mock_validate_creds):
        # Setup
        config = JiraConfig(url="https://jira.example.com", token="abc123", username="testuser", cloud=False)

        tool = GenericJiraIssueTool(config=config)
        tool.jira = MagicMock()

        # Mock the request method to return a response
        mock_response = MagicMock()
        mock_response.text = '{"issues": [{"key": "TEST-1"}]}'
        tool.jira.request.return_value = mock_response
        tool.jira.url = "https://jira.example.com"

        # Mock process_search_response
        with patch('codemie_tools.core.project_management.jira.tools.process_search_response') as mock_process:
            mock_process.return_value = "Processed search response"

            # Execute
            response_text, response = tool._handle_get_request("/rest/api/2/search", {})

            # Assert
            assert response_text == "Processed search response"
            mock_process.assert_called_once_with("https://jira.example.com", mock_response, {})
