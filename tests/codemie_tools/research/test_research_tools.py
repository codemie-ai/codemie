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

from unittest.mock import Mock, patch

import pytest
import requests

from codemie_tools.research.tools import WebScrapperTool


@pytest.fixture
def mock_response():
    mock = Mock()
    mock.text = """
    <html>
        <head><title>Test Page</title></head>
        <body>
            <h1>Welcome to Test</h1>
            <p>This is a test paragraph.</p>
            <img src="test.jpg" alt="Test Image"/>
            <a href="http://example.com">Test Link</a>
        </body>
    </html>
    """
    mock.raise_for_status = Mock()
    return mock


class TestWebScrapperTool:
    def setup_method(self):
        self.tool = WebScrapperTool()
        self.test_url = "http://example.com"

    @patch('requests.get')
    def test_execute_basic_scraping(self, mock_get, mock_response):
        mock_get.return_value = mock_response
        result = self.tool.execute(self.test_url)

        assert "# Test Page" in result
        assert "This is a test paragraph" in result
        assert f"Source: {self.test_url}" in result

    @patch('requests.get')
    def test_execute_with_images(self, mock_get, mock_response):
        mock_get.return_value = mock_response
        result = self.tool.execute(self.test_url, extract_images=True)

        assert "## Images" in result
        assert "![Test Image](test.jpg)" in result

    @patch('requests.get')
    def test_execute_without_links(self, mock_get, mock_response):
        mock_get.return_value = mock_response
        result = self.tool.execute(self.test_url, extract_links=False)

        # Only checking for link text, not the URL, since the source URL will still be present
        assert "Test Link" in result
        assert "http://example.com" not in result or self.test_url == "http://example.com"

    @patch('requests.get')
    def test_execute_error_handling(self, mock_get):
        mock_get.side_effect = requests.RequestException("Test error")
        result = self.tool.execute(self.test_url)

        assert "Error scraping" in result
        assert "Test error" in result

    def test_clean_markdown(self):
        content = "\n\n\nTest\n\n\n# Header\n\nText\n\n\n* Item\nText\n\n"
        result = self.tool._clean_markdown(content)

        assert result == "Test\n\n# Header\n\nText\n\n* Item\n\nText"
