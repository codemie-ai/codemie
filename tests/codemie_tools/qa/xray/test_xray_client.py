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

"""Unit tests for XrayClient."""

import pytest
from unittest.mock import Mock, patch
from langchain_core.tools import ToolException

from codemie_tools.qa.xray.xray_client import XrayClient


class TestXrayClientInit:
    """Test cases for XrayClient initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default values."""
        client = XrayClient(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")
        assert client.base_url == "https://xray.cloud.getxray.app"
        assert client.client_id == "test_id"
        assert client.client_secret == "test_secret"
        assert client.limit == 100
        assert client.verify_ssl is True
        assert client.timeout == 30

    def test_init_with_custom_values(self):
        """Test initialization with custom values."""
        client = XrayClient(
            base_url="https://custom.xray.app/",
            client_id="custom_id",
            client_secret="custom_secret",
            limit=50,
            verify_ssl=False,
            timeout=60,
        )
        assert client.base_url == "https://custom.xray.app"  # Trailing slash removed
        assert client.limit == 50
        assert client.verify_ssl is False
        assert client.timeout == 60

    def test_endpoints_constructed(self):
        """Test that endpoints are correctly constructed."""
        client = XrayClient(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")
        assert client._graphql_endpoint == "https://xray.cloud.getxray.app/api/v2/graphql"
        assert client._auth_endpoint == "https://xray.cloud.getxray.app/api/v2/authenticate"


class TestXrayClientAuthentication:
    """Test cases for authentication methods."""

    @patch("codemie_tools.qa.xray.xray_client.httpx.Client")
    def test_authenticate_success(self, mock_client_class):
        """Test successful authentication."""
        mock_response = Mock()
        mock_response.text = '"test_token"'
        mock_response.raise_for_status = Mock()

        mock_client = Mock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.post = Mock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        client = XrayClient(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")

        token = client._authenticate()

        assert token == "test_token"
        mock_client.post.assert_called_once()

    @patch("codemie_tools.qa.xray.xray_client.httpx.Client")
    def test_authenticate_http_error(self, mock_client_class):
        """Test authentication with HTTP error."""
        import httpx

        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        mock_client = Mock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.post = Mock(
            side_effect=httpx.HTTPStatusError("Unauthorized", request=Mock(), response=mock_response)
        )
        mock_client_class.return_value = mock_client

        client = XrayClient(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")

        with pytest.raises(ToolException) as exc_info:
            client._authenticate()

        assert "Authentication failed" in str(exc_info.value)
        assert "test_id" in str(exc_info.value)


class TestXrayClientGetTests:
    """Test cases for get_tests method."""

    @patch("codemie_tools.qa.xray.xray_client.httpx.Client")
    def test_get_tests_success(self, mock_client_class):
        """Test successful get_tests."""
        # Mock authentication
        auth_response = Mock()
        auth_response.text = '"test_token"'
        auth_response.raise_for_status = Mock()

        # Mock GraphQL query
        graphql_response = Mock()
        graphql_response.json.return_value = {
            "data": {
                "getTests": {
                    "total": 2,
                    "results": [
                        {
                            "issueId": "12345",
                            "jira": {"key": "CALC-1", "summary": "Test 1"},
                            "testType": {"name": "Manual"},
                            "preconditions": {"total": 0},
                        },
                        {
                            "issueId": "12346",
                            "jira": {"key": "CALC-2", "summary": "Test 2"},
                            "testType": {"name": "Generic"},
                            "preconditions": {"total": 0},
                        },
                    ],
                }
            }
        }
        graphql_response.raise_for_status = Mock()

        mock_client = Mock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.post = Mock(side_effect=[auth_response, graphql_response])
        mock_client_class.return_value = mock_client

        client = XrayClient(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")

        result = client.get_tests('project = "CALC"')

        assert result["total_tests_count"] == 2
        assert result["returned_tests_count"] == 2
        assert len(result["tests"]) == 2
        assert result["tests"][0]["issueId"] == "12345"
        assert "preconditions" not in result["tests"][0]  # Removed empty preconditions

    @patch("codemie_tools.qa.xray.xray_client.httpx.Client")
    def test_get_tests_region_error(self, mock_client_class):
        """Test get_tests with region mismatch error."""
        import httpx

        # Mock authentication
        auth_response = Mock()
        auth_response.text = '"test_token"'
        auth_response.raise_for_status = Mock()

        # Mock 401 response with region error
        error_response = Mock()
        error_response.status_code = 401
        error_response.json.return_value = {
            "error": "Xray data is in another region. Contact support to migrate Xray data to current region."
        }
        error_response.text = (
            '{"error":"Xray data is in another region. Contact support to migrate Xray data to current region."}'
        )

        mock_client = Mock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.post = Mock(
            side_effect=[auth_response, httpx.HTTPStatusError("Unauthorized", request=Mock(), response=error_response)]
        )
        mock_client_class.return_value = mock_client

        client = XrayClient(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")

        with pytest.raises(ToolException) as exc_info:
            client.get_tests('project = "CALC"')

        error_msg = str(exc_info.value)
        assert "region" in error_msg.lower()
        assert "configuration issue" in error_msg.lower()
        # Should not retry infinitely - only auth + one request
        assert mock_client.post.call_count == 2

    @patch("codemie_tools.qa.xray.xray_client.httpx.Client")
    def test_get_tests_pagination(self, mock_client_class):
        """Test get_tests with pagination."""
        # Mock authentication
        auth_response = Mock()
        auth_response.text = '"test_token"'
        auth_response.raise_for_status = Mock()

        # Mock first page
        first_page = Mock()
        first_page.json.return_value = {
            "data": {"getTests": {"total": 2, "results": [{"issueId": "12345", "preconditions": {"total": 0}}]}}
        }
        first_page.raise_for_status = Mock()

        # Mock second page
        second_page = Mock()
        second_page.json.return_value = {
            "data": {"getTests": {"total": 2, "results": [{"issueId": "12346", "preconditions": {"total": 0}}]}}
        }
        second_page.raise_for_status = Mock()

        mock_client = Mock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.post = Mock(side_effect=[auth_response, first_page, second_page])
        mock_client_class.return_value = mock_client

        client = XrayClient(
            base_url="https://xray.cloud.getxray.app",
            client_id="test_id",
            client_secret="test_secret",
            limit=1,  # Force pagination
        )

        result = client.get_tests('project = "CALC"')

        assert result["total_tests_count"] == 2
        assert result["returned_tests_count"] == 2
        assert len(result["tests"]) == 2

    @patch("codemie_tools.qa.xray.xray_client.httpx.Client")
    def test_get_tests_token_expiration_retry(self, mock_client_class):
        """Test that token expiration is retried once successfully."""
        import httpx

        # Mock initial authentication
        auth_response_1 = Mock()
        auth_response_1.text = '"expired_token"'
        auth_response_1.raise_for_status = Mock()

        # Mock 401 response without region error (actual token expiration)
        error_response = Mock()
        error_response.status_code = 401
        error_response.json.side_effect = Exception("Not JSON")
        error_response.text = "Unauthorized"

        # Mock re-authentication
        auth_response_2 = Mock()
        auth_response_2.text = '"new_token"'
        auth_response_2.raise_for_status = Mock()

        # Mock successful GraphQL query with new token
        graphql_response = Mock()
        graphql_response.json.return_value = {
            "data": {"getTests": {"total": 1, "results": [{"issueId": "12345", "preconditions": {"total": 0}}]}}
        }
        graphql_response.raise_for_status = Mock()

        mock_client = Mock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.post = Mock(
            side_effect=[
                auth_response_1,  # Initial auth
                httpx.HTTPStatusError("Unauthorized", request=Mock(), response=error_response),  # 401 error
                auth_response_2,  # Re-auth
                graphql_response,  # Successful query with new token
            ]
        )
        mock_client_class.return_value = mock_client

        client = XrayClient(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")

        result = client.get_tests('project = "CALC"')

        assert result["total_tests_count"] == 1
        assert result["returned_tests_count"] == 1
        assert len(result["tests"]) == 1
        # Should have: initial auth, 401 error, re-auth, successful query
        assert mock_client.post.call_count == 4


class TestXrayClientCreateTest:
    """Test cases for create_test method."""

    @patch("codemie_tools.qa.xray.xray_client.httpx.Client")
    def test_create_test_success(self, mock_client_class):
        """Test successful test creation."""
        # Mock authentication
        auth_response = Mock()
        auth_response.text = '"test_token"'
        auth_response.raise_for_status = Mock()

        # Mock GraphQL mutation
        mutation_response = Mock()
        mutation_response.json.return_value = {
            "data": {
                "createTest": {"test": {"issueId": "12345", "jira": {"key": "CALC-1"}, "testType": {"name": "Manual"}}}
            }
        }
        mutation_response.raise_for_status = Mock()

        mock_client = Mock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.post = Mock(side_effect=[auth_response, mutation_response])
        mock_client_class.return_value = mock_client

        client = XrayClient(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")

        mutation = 'mutation { createTest(...) { test { issueId } } }'
        result = client.create_test(mutation)

        assert "test" in result
        assert result["test"]["issueId"] == "12345"


class TestXrayClientExecuteCustomGraphQL:
    """Test cases for execute_custom_graphql method."""

    @patch("codemie_tools.qa.xray.xray_client.httpx.Client")
    def test_execute_custom_graphql_success(self, mock_client_class):
        """Test successful custom GraphQL execution."""
        # Mock authentication
        auth_response = Mock()
        auth_response.text = '"test_token"'
        auth_response.raise_for_status = Mock()

        # Mock GraphQL query
        graphql_response = Mock()
        graphql_response.json.return_value = {"data": {"customQuery": {"result": "success"}}}
        graphql_response.raise_for_status = Mock()

        mock_client = Mock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.post = Mock(side_effect=[auth_response, graphql_response])
        mock_client_class.return_value = mock_client

        client = XrayClient(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")

        query = 'query { customQuery { result } }'
        result = client.execute_custom_graphql(query)

        assert "customQuery" in result
        assert result["customQuery"]["result"] == "success"


class TestXrayClientHealthCheck:
    """Test cases for health_check method."""

    @patch("codemie_tools.qa.xray.xray_client.httpx.Client")
    def test_health_check_success(self, mock_client_class):
        """Test successful health check."""
        auth_response = Mock()
        auth_response.text = '"test_token"'
        auth_response.raise_for_status = Mock()

        mock_client = Mock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.post = Mock(return_value=auth_response)
        mock_client_class.return_value = mock_client

        client = XrayClient(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")

        result = client.health_check()

        assert result is True

    @patch("codemie_tools.qa.xray.xray_client.httpx.Client")
    def test_health_check_failure(self, mock_client_class):
        """Test failed health check."""
        import httpx

        mock_response = Mock()
        mock_response.status_code = 401

        mock_client = Mock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.post = Mock(
            side_effect=httpx.HTTPStatusError("Unauthorized", request=Mock(), response=mock_response)
        )
        mock_client_class.return_value = mock_client

        client = XrayClient(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")

        with pytest.raises(ToolException):
            client.health_check()
