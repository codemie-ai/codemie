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
from unittest.mock import patch, MagicMock, Mock

from codemie_tools.core.project_management.confluence.models import ConfluenceConfig
from codemie_tools.core.project_management.confluence.tools import GenericConfluenceTool


class TestConfluenceConfig(unittest.TestCase):
    def test_valid_config(self):
        config = ConfluenceConfig(url="https://confluence.example.com", token="abc123")
        assert config.url == "https://confluence.example.com"
        assert config.token == "abc123"
        assert config.username is None
        assert config.cloud is False

    def test_valid_config_with_username(self):
        config = ConfluenceConfig(url="https://confluence.example.com", token="abc123", username="user1")
        assert config.url == "https://confluence.example.com"
        assert config.token == "abc123"
        assert config.username == "user1"
        assert config.cloud is False

    def test_valid_config_with_cloud(self):
        config = ConfluenceConfig(url="https://confluence.example.com", token="abc123", cloud=True)
        assert config.url == "https://confluence.example.com"
        assert config.token == "abc123"
        assert config.cloud is True

    def test_is_cloud_conversion(self):
        config = ConfluenceConfig.model_validate(
            {"url": "https://confluence.example.com", "token": "abc123", "is_cloud": True}
        )
        assert config.cloud is True
        assert not hasattr(config, "is_cloud")

    @patch('codemie_tools.core.project_management.confluence.tools.Confluence')
    @patch('codemie_tools.core.project_management.confluence.tools.validate_creds')
    def test_empty_config(self, mock_validate, mock_confluence_class):
        # Arrange - create config with empty/default values
        config = ConfluenceConfig(url="https://confluence.example.com", token="", username=None)

        # Mock the Confluence client
        mock_confluence_instance = MagicMock()
        mock_confluence_class.return_value = mock_confluence_instance

        # Mock the request response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.reason = "OK"
        mock_response.text = '{"id": "123456", "title": "Test Page"}'
        mock_confluence_instance.request.return_value = mock_response

        # Act
        tool = GenericConfluenceTool(config=config)
        result = tool.execute(method="GET", relative_url="/rest/api/content/123456")

        # Assert
        self.assertIn("HTTP: GET/rest/api/content/123456 -> 200", result)
        mock_validate.assert_called_once()
        mock_confluence_class.assert_called_once()
