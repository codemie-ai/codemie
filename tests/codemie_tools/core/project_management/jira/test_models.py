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

import unittest
from unittest.mock import MagicMock, patch, Mock

from codemie_tools.core.project_management.jira.models import JiraConfig
from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool


class TestJiraConfig(unittest.TestCase):
    """Tests for the JiraConfig model."""

    def test_valid_config(self):
        """Test creating a valid JiraConfig."""
        config = JiraConfig(url="https://jira.example.com", token="abc123")
        assert config.url == "https://jira.example.com"
        assert config.token == "abc123"
        assert config.username is None
        assert config.cloud is False

    def test_valid_config_with_username(self):
        """Test creating a valid JiraConfig with username."""
        config = JiraConfig(url="https://jira.example.com", token="abc123", username="user")
        assert config.url == "https://jira.example.com"
        assert config.token == "abc123"
        assert config.username == "user"
        assert config.cloud is False

    def test_valid_config_with_cloud(self):
        """Test creating a valid JiraConfig with cloud flag."""
        config = JiraConfig(url="https://jira.example.com", token="abc123", cloud=True)
        assert config.url == "https://jira.example.com"
        assert config.token == "abc123"
        assert config.username is None
        assert config.cloud is True

    def test_is_cloud_conversion(self):
        """Test that is_cloud is converted to cloud."""
        config = JiraConfig.model_validate({"url": "https://jira.example.com", "token": "abc123", "is_cloud": True})
        assert config.cloud is True
        assert not hasattr(config, "is_cloud")

    @patch('codemie_tools.core.project_management.jira.tools.Jira')
    @patch('codemie_tools.core.project_management.jira.tools.validate_jira_creds')
    def test_empty_config(self, mock_validate, mock_jira_class):
        # Arrange - create config with empty/default values
        config = JiraConfig(url="https://jira.example.com", token="", username=None)

        # Mock the Jira client
        mock_jira_instance = MagicMock()
        mock_jira_class.return_value = mock_jira_instance

        # Mock the request response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.reason = "OK"
        mock_response.text = '{"key": "TEST-123", "fields": {"summary": "Test Issue"}}'
        mock_response.json.return_value = {"key": "TEST-123", "fields": {"summary": "Test Issue"}}
        mock_jira_instance.request.return_value = mock_response
        mock_jira_instance.raise_for_status = Mock()

        # Act
        tool = GenericJiraIssueTool(config=config)
        result = tool.execute(method="GET", relative_url="/rest/api/2/issue/TEST-123")

        # Assert
        self.assertIn("HTTP: GET /rest/api/2/issue/TEST-123 -> 200", result)
        mock_validate.assert_called_once()
        mock_jira_class.assert_called_once()
