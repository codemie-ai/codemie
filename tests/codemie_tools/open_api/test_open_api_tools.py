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

import base64
from unittest.mock import MagicMock, patch

import pytest

from codemie_tools.open_api.models import OpenApiConfig
from codemie_tools.open_api.tools import GetOpenApiSpec, InvokeRestApiBySpec, _get_auth_header_value


@pytest.mark.parametrize(
    "config, expected_header",
    [
        (OpenApiConfig(spec="test_spec", api_key="test_key", is_basic_auth=False, username="", timeout=60), "test_key"),
        (
            OpenApiConfig(spec="test_spec", api_key="test_pass", is_basic_auth=True, username="test_user", timeout=60),
            f"Basic {base64.b64encode('test_user:test_pass'.encode('utf-8')).decode('utf-8')}",
        ),
    ],
    ids=["bearer_auth", "basic_auth"],
)
def test_get_auth_header_value(config, expected_header):
    assert _get_auth_header_value(config) == expected_header


class TestOpenApiTools:
    @pytest.fixture
    def mock_requests(self):
        with patch('codemie_tools.open_api.tools.requests.request') as mock:
            yield mock

    @pytest.mark.parametrize(
        "method, url, headers, fields, body, filter_fields, config, expected_headers, expected_response, expected_result",
        [
            (
                "GET",
                "https://api.example.com/test",
                '{"Content-Type": "application/json"}',
                "",
                "",
                "",
                OpenApiConfig(spec="test_spec", api_key="test_key", is_basic_auth=False, username="", timeout=60),
                {"Content-Type": "application/json", "Authorization": "test_key"},
                b'{"result": "success", "data": {"id": 123}}',
                '{"result": "success", "data": {"id": 123}}',
            ),
            (
                "POST",
                "https://api.example.com/test",
                "",
                '{"param1": "value1"}',
                '{"data": "test"}',
                "",
                OpenApiConfig(
                    spec="test_spec", api_key="test_pass", is_basic_auth=True, username="test_user", timeout=60
                ),
                {"Authorization": f"Basic {base64.b64encode('test_user:test_pass'.encode('utf-8')).decode('utf-8')}"},
                b'{"result": "created"}',
                '{"result": "created"}',
            ),
            (
                "GET",
                "https://api.example.com/test",
                '{"Content-Type": "application/json"}',
                "",
                "",
                "result,data.id",
                OpenApiConfig(spec="test_spec", api_key="test_key", is_basic_auth=False, username="", timeout=60),
                {"Content-Type": "application/json", "Authorization": "test_key"},
                b'{"result": "success", "data": {"id": 123, "name": "test"}, "metadata": {"timestamp": 1234567890}}',
                '{"result": "success", "data": {"id": 123}}',
            ),
            (
                "GET",
                "https://api.example.com/test",
                '{"Content-Type": "application/json"}',
                "",
                "",
                "",
                OpenApiConfig(
                    spec="test_spec",
                    api_key="test_key",
                    is_basic_auth=False,
                    username="",
                    timeout=60,
                    auth_header_name="X-API-Key",
                ),
                {"Content-Type": "application/json", "X-API-Key": "test_key"},
                b'{"result": "success"}',
                '{"result": "success"}',
            ),
        ],
        ids=["get_request_bearer_auth", "post_request_basic_auth", "get_with_filter_fields", "custom_auth_header"],
    )
    def test_execute(
        self,
        mock_requests,
        method,
        url,
        headers,
        fields,
        body,
        filter_fields,
        config,
        expected_headers,
        expected_response,
        expected_result,
    ):
        # Arrange
        mock_response = MagicMock()
        mock_response.text = (
            expected_response.decode('utf-8') if isinstance(expected_response, bytes) else expected_response
        )
        mock_response.headers = {"Content-Type": "application/json"}

        # Setup json method for the mock response
        if isinstance(expected_response, bytes):
            import json

            mock_response.json.return_value = json.loads(expected_response.decode('utf-8'))
        else:
            mock_response.json.return_value = json.loads(expected_response)

        mock_requests.return_value = mock_response

        tool = InvokeRestApiBySpec(config=config)
        # Mock the metadata attribute
        tool.metadata = {}

        # Act
        result = tool.execute(
            method=method, url=url, headers=headers, fields=fields, body=body, filter_fields=filter_fields
        )

        # Assert
        mock_requests.assert_called_once_with(
            method=method,
            url=url,
            params={"param1": "value1"} if fields else None,
            headers=expected_headers,
            data=body if body else None,
            timeout=config.timeout,
            # it must be false for internal tools and usage
            verify=False,
        )
        assert result == expected_result

    def test_execute_without_auth(self, mock_requests):
        # Arrange
        config = OpenApiConfig(spec="test_spec", api_key="", is_basic_auth=False, username="", timeout=60)
        mock_response = MagicMock()
        mock_response.text = '{"result": "success"}'
        mock_response.headers = {"Content-Type": "application/json"}
        mock_requests.return_value = mock_response

        tool = InvokeRestApiBySpec(config=config)
        # Mock the metadata attribute
        tool.metadata = {}

        result = tool.execute(method="GET", url="https://api.example.com/test")

        mock_requests.assert_called_once_with(
            method="GET",
            url="https://api.example.com/test",
            params=None,
            headers={},
            data=None,
            timeout=60,
            verify=False,
        )
        assert result == '{"result": "success"}'

    def test_get_open_api_spec(self):
        test_spec = "test spec"
        config = OpenApiConfig(spec=test_spec, api_key="key", timeout=60)
        tool = GetOpenApiSpec(config=config)
        assert tool.openapi_spec == test_spec

    def test_extract_filtered_fields(self):
        # Arrange
        config = OpenApiConfig(spec="test_spec", api_key="test_key", is_basic_auth=False, username="", timeout=60)
        tool = InvokeRestApiBySpec(config=config)

        # Test data
        json_data = {
            "transcription": "Hello world",
            "author": "John Doe",
            "data": {"people": ["Alice", "Bob"], "locations": ["New York", "London"]},
            "metadata": {"timestamp": 1234567890, "source": "API"},
        }

        # Test cases
        test_cases = [
            # Single top-level field
            ("transcription", {"transcription": "Hello world"}),
            # Multiple top-level fields
            ("transcription,author", {"transcription": "Hello world", "author": "John Doe"}),
            # Nested field
            ("data.people", {"data": {"people": ["Alice", "Bob"]}}),
            # Mixed top-level and nested fields
            ("author,data.people", {"author": "John Doe", "data": {"people": ["Alice", "Bob"]}}),
            # Empty filter string
            ("", json_data),
            # Multiple nested fields in the same parent
            ("data.people,data.locations", {"data": {"people": ["Alice", "Bob"], "locations": ["New York", "London"]}}),
        ]

        # Act & Assert
        for filter_fields, expected_result in test_cases:
            result = tool._extract_filtered_fields(json_data, filter_fields)
            assert result == expected_result

    def test_unicode_content_handling(self, mock_requests):
        # Arrange
        config = OpenApiConfig(spec="test_spec", api_key="", is_basic_auth=False, username="", timeout=60)
        mock_response = MagicMock()

        # Ukrainian text: "це мій респонс" ("this is my response")
        ukrainian_text = "це мій респонс"
        mock_response.text = ukrainian_text
        mock_response.headers = {"Content-Type": "text/plain; charset=utf-8"}
        mock_requests.return_value = mock_response

        tool = InvokeRestApiBySpec(config=config)
        # Mock the metadata attribute
        tool.metadata = {}

        # Act
        result = tool.execute(method="GET", url="https://api.example.com/test")

        # Assert
        assert result == "це мій респонс"
        assert "\\x" not in result  # Ensure no escaped bytes in the output

    def test_unicode_content_without_charset(self, mock_requests):
        # Arrange
        config = OpenApiConfig(spec="test_spec", api_key="", is_basic_auth=False, username="", timeout=60)
        mock_response = MagicMock()

        # Ukrainian text without charset in Content-Type
        ukrainian_text = "це мій респонс"
        mock_response.text = ukrainian_text
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_requests.return_value = mock_response

        tool = InvokeRestApiBySpec(config=config)
        # Mock the metadata attribute
        tool.metadata = {}

        # Act
        result = tool.execute(method="GET", url="https://api.example.com/test")

        # Assert
        assert result == "це мій респонс"
        assert "\\x" not in result  # Ensure no escaped bytes in the output

    def test_invalid_unicode_content(self, mock_requests):
        # Arrange
        config = OpenApiConfig(spec="test_spec", api_key="", is_basic_auth=False, username="", timeout=60)
        mock_response = MagicMock()

        # For invalid UTF-8 sequence, requests would have already handled the decoding
        # and either replaced invalid chars or raised an exception
        mock_response.text = "�������"  # Replacement characters
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_requests.return_value = mock_response

        tool = InvokeRestApiBySpec(config=config)
        # Mock the metadata attribute
        tool.metadata = {}

        # Act
        result = tool.execute(method="GET", url="https://api.example.com/test")

        # Assert
        assert isinstance(result, str)  # Result should be a string
        assert len(result) > 0  # Result should not be empty

    def test_custom_timeout(self, mock_requests):
        # Arrange
        custom_timeout = 120
        config = OpenApiConfig(spec="test_spec", api_key="", is_basic_auth=False, username="", timeout=custom_timeout)
        mock_response = MagicMock()
        mock_response.text = '{"result": "success"}'
        mock_requests.return_value = mock_response

        tool = InvokeRestApiBySpec(config=config)
        # Mock the metadata attribute
        tool.metadata = {}

        # Act
        result = tool.execute(method="GET", url="https://api.example.com/test")

        # Assert
        mock_requests.assert_called_once_with(
            method="GET",
            url="https://api.example.com/test",
            params=None,
            headers={},
            data=None,
            timeout=custom_timeout,
            verify=False,
        )
        assert result == '{"result": "success"}'

    def test_empty_config(self, mock_requests):
        # Arrange - create config with no values provided
        config = OpenApiConfig()
        mock_response = MagicMock()
        mock_response.text = '{"result": "success"}'
        mock_requests.return_value = mock_response

        tool = InvokeRestApiBySpec(config=config)
        # Mock the metadata attribute
        tool.metadata = {}

        # Act
        result = tool.execute(method="GET", url="https://api.example.com/test")

        # Assert
        mock_requests.assert_called_once_with(
            method="GET",
            url="https://api.example.com/test",
            params=None,
            headers={},  # No auth headers since api_key is empty
            data=None,
            timeout=120,  # Default timeout
            verify=False,
        )
        assert result == '{"result": "success"}'

    def test_empty_spec(self):
        # Test that GetOpenApiSpec handles empty spec gracefully
        config = OpenApiConfig()
        tool = GetOpenApiSpec(config=config)

        # Act
        result = tool.execute()

        # Assert
        assert "No OpenAPI specification provided" in result
