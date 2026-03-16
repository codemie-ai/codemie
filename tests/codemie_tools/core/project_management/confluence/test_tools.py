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

from codemie_tools.core.project_management.confluence.tools import GenericConfluenceTool, CONFLUENCE_TEST_URL


class TestGenericConfluenceTool:
    @patch('codemie_tools.core.project_management.confluence.tools.Confluence')
    def test_init(self, mock_confluence_class, confluence_config):
        tool = GenericConfluenceTool(config=confluence_config)
        assert tool.config == confluence_config
        assert tool.name == "generic_confluence_tool"
        assert "Confluence" in tool.description

    @patch('codemie_tools.core.project_management.confluence.tools.Confluence')
    @patch('codemie_tools.core.project_management.confluence.tools.validate_creds')
    def test_execute_get_request(
        self, mock_validate_creds, mock_confluence_class, confluence_config, mock_confluence_response
    ):
        # Setup
        mock_confluence_instance = MagicMock()
        mock_confluence_instance.request.return_value = mock_confluence_response
        mock_confluence_class.return_value = mock_confluence_instance

        tool = GenericConfluenceTool(config=confluence_config)

        # Execute
        response = tool.execute(
            method="GET", relative_url="/rest/api/content/12345", params='{"expand": "body.storage"}'
        )

        # Assert
        mock_confluence_class.assert_called_once_with(
            url=confluence_config.url,
            username=confluence_config.username,
            token=confluence_config.token,
            password=None,
            cloud=confluence_config.cloud,
        )
        mock_validate_creds.assert_called_once_with(mock_confluence_instance)
        mock_confluence_instance.request.assert_called_once()
        assert "HTTP: GET/rest/api/content/12345 -> 200OK" in response

    @patch('codemie_tools.core.project_management.confluence.tools.Confluence')
    @patch('codemie_tools.core.project_management.confluence.tools.validate_creds')
    def test_execute_post_request(
        self, mock_validate_creds, mock_confluence_class, confluence_config, mock_confluence_response
    ):
        # Setup
        mock_confluence_instance = MagicMock()
        mock_confluence_instance.request.return_value = mock_confluence_response
        mock_confluence_class.return_value = mock_confluence_instance

        tool = GenericConfluenceTool(config=confluence_config)

        # Execute
        response = tool.execute(
            method="POST",
            relative_url="/rest/api/content",
            params='{"title": "New Page", "space": {"key": "TEST"}, "body": {"storage": {"value": "Test content", "representation": "storage"}}}',
        )

        # Assert
        mock_confluence_class.assert_called_once()
        mock_validate_creds.assert_called_once()
        mock_confluence_instance.request.assert_called_once()
        assert "HTTP: POST/rest/api/content -> 200OK" in response

    @patch('codemie_tools.core.project_management.confluence.tools.Confluence')
    @patch('codemie_tools.core.project_management.confluence.tools.validate_creds')
    @patch('codemie_tools.core.project_management.confluence.tools.prepare_page_payload')
    def test_execute_post_with_markdown(
        self,
        mock_prepare_payload,
        mock_validate_creds,
        mock_confluence_class,
        confluence_config,
        mock_confluence_response,
    ):
        # Setup
        mock_confluence_instance = MagicMock()
        mock_confluence_instance.request.return_value = mock_confluence_response
        mock_confluence_class.return_value = mock_confluence_instance
        mock_prepare_payload.return_value = {"converted": "payload"}

        tool = GenericConfluenceTool(config=confluence_config)

        # Execute
        response = tool.execute(
            method="POST",
            relative_url="/rest/api/content",
            params='{"title": "New Page", "body": {"storage": {"value": "# Test", "representation": "storage"}}}',
            is_markdown=True,
        )

        # Assert
        mock_confluence_class.assert_called_once()
        mock_validate_creds.assert_called_once()
        mock_prepare_payload.assert_called_once()
        mock_confluence_instance.request.assert_called_once()
        assert "HTTP: POST/rest/api/content -> 200OK" in response

    @patch('codemie_tools.core.project_management.confluence.tools.Confluence')
    @patch('codemie_tools.core.project_management.confluence.tools.validate_creds')
    def test_process_search_response_page(self, mock_validate_creds, mock_confluence_class, confluence_config):
        # Setup
        mock_response = MagicMock()
        mock_response.text = "<h1>Test Page</h1><p>This is a test page</p>"

        tool = GenericConfluenceTool(config=confluence_config)

        # Execute
        result = tool.process_search_response("/rest/api/content/12345", mock_response)

        # Assert
        assert "# Test Page" in result
        assert "This is a test page" in result
        assert tool.tokens_size_limit == 20000

    @patch('codemie_tools.core.project_management.confluence.tools.Confluence')
    @patch('codemie_tools.core.project_management.confluence.tools.validate_creds')
    def test_process_search_response_non_page(self, mock_validate_creds, mock_confluence_class, confluence_config):
        # Setup
        mock_response = MagicMock()
        mock_response.text = '{"results": [{"id": "12345", "title": "Test Page"}]}'

        tool = GenericConfluenceTool(config=confluence_config)

        # Execute
        result = tool.process_search_response("/rest/api/content/search", mock_response)

        # Assert
        assert result == '{"results": [{"id": "12345", "title": "Test Page"}]}'

    @patch('codemie_tools.core.project_management.confluence.tools.Confluence')
    @patch('codemie_tools.core.project_management.confluence.tools.validate_creds')
    def test_healthcheck_success(self, mock_validate_creds, mock_confluence_class, confluence_config):
        # Setup
        mock_confluence_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"type": "known", "username": "testuser"}'
        mock_confluence_instance.request.return_value = mock_response
        mock_confluence_class.return_value = mock_confluence_instance

        tool = GenericConfluenceTool(config=confluence_config)

        # This should not raise an exception
        tool._healthcheck()

        # Assert
        mock_confluence_instance.request.assert_called_once_with(
            method="GET", path=CONFLUENCE_TEST_URL, params={}, advanced_mode=True
        )

    @patch('codemie_tools.core.project_management.confluence.tools.Confluence')
    @patch('codemie_tools.core.project_management.confluence.tools.validate_creds')
    def test_healthcheck_failure_unauthorized(self, mock_validate_creds, mock_confluence_class, confluence_config):
        # Setup
        mock_confluence_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = '{"error": "Unauthorized"}'
        mock_confluence_instance.request.return_value = mock_response
        mock_confluence_class.return_value = mock_confluence_instance

        tool = GenericConfluenceTool(config=confluence_config)

        # Execute and Assert
        with pytest.raises(AssertionError, match="Access denied"):
            tool._healthcheck()

    @patch('codemie_tools.core.project_management.confluence.tools.Confluence')
    @patch('codemie_tools.core.project_management.confluence.tools.validate_creds')
    def test_healthcheck_failure_html_response(self, mock_validate_creds, mock_confluence_class, confluence_config):
        # Setup
        mock_confluence_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html><body>Login required</body></html>'
        mock_confluence_instance.request.return_value = mock_response
        mock_confluence_class.return_value = mock_confluence_instance

        tool = GenericConfluenceTool(config=confluence_config)

        # Execute and Assert
        with pytest.raises(AssertionError, match="Access denied"):
            tool._healthcheck()

    @patch('codemie_tools.core.project_management.confluence.tools.Confluence')
    @patch('codemie_tools.core.project_management.confluence.tools.validate_creds')
    def test_healthcheck_failure_error_json(self, mock_validate_creds, mock_confluence_class, confluence_config):
        # Setup
        mock_confluence_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"message": "Authentication failed"}'
        mock_confluence_instance.request.return_value = mock_response
        mock_confluence_class.return_value = mock_confluence_instance

        tool = GenericConfluenceTool(config=confluence_config)

        # Execute and Assert
        with pytest.raises(AssertionError, match="Access denied"):
            tool._healthcheck()
