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

from unittest.mock import patch

import pytest
from langchain_core.tools import ToolException

from codemie_tools.cloud.azure.models import AzureConfig
from codemie_tools.cloud.azure.tools import GenericAzureTool


@pytest.fixture
def azure_config():
    """Create a test Azure configuration."""
    return AzureConfig(
        subscription_id="12345678-1234-1234-1234-123456789012",
        tenant_id="87654321-4321-4321-4321-210987654321",
        client_id="11111111-1111-1111-1111-111111111111",
        client_secret="test_secret",
    )


@pytest.fixture
def azure_tool(azure_config):
    """Create a test Azure tool instance."""
    return GenericAzureTool(config=azure_config)


class TestGenericAzureTool:
    def test_tool_initialization(self, azure_tool):
        """Test that tool initializes correctly with client."""
        assert azure_tool.name == "Azure"
        assert azure_tool.client is not None
        assert azure_tool.config.subscription_id == "12345678-1234-1234-1234-123456789012"
        assert "SubscriptionId" in azure_tool.description

    def test_execute_get_request(self, azure_tool):
        """Test executing GET request."""
        with patch.object(azure_tool.client, 'request') as mock_request:
            mock_request.return_value = '{"value": []}'

            result = azure_tool.execute(
                method="GET", url="https://management.azure.com/subscriptions/test/resourcegroups"
            )

            assert isinstance(result, str)
            mock_request.assert_called_once()
            args = mock_request.call_args
            assert args[1]["method"] == "GET"
            assert "management.azure.com" in args[1]["url"]

    def test_execute_post_request(self, azure_tool):
        """Test executing POST request with data."""
        with patch.object(azure_tool.client, 'request') as mock_request:
            mock_request.return_value = '{"id": "test-id"}'

            result = azure_tool.execute(
                method="POST", url="https://management.azure.com/test", optional_args={"data": {"location": "eastus"}}
            )

            assert isinstance(result, str)
            mock_request.assert_called_once()
            args = mock_request.call_args
            assert args[1]["optional_args"]["data"]["location"] == "eastus"

    def test_execute_with_json_string_args(self, azure_tool):
        """Test executing with optional_args as JSON string."""
        with patch.object(azure_tool.client, 'request') as mock_request:
            mock_request.return_value = '{"status": "success"}'

            result = azure_tool.execute(
                method="PUT", url="https://management.azure.com/test", optional_args='{"data": {"name": "test"}}'
            )

            assert isinstance(result, str)
            mock_request.assert_called_once()

    def test_execute_with_markdown_code_block(self, azure_tool):
        """Test executing with args wrapped in markdown code block."""
        with patch.object(azure_tool.client, 'request') as mock_request:
            mock_request.return_value = '{"result": "ok"}'

            result = azure_tool.execute(
                method="POST",
                url="https://management.azure.com/test",
                optional_args='```json\n{"data": {"value": "test"}}\n```',
            )

            assert isinstance(result, str)
            mock_request.assert_called_once()

    def test_execute_with_custom_scope(self, azure_tool):
        """Test executing with custom OAuth scope."""
        with patch.object(azure_tool.client, 'request') as mock_request:
            mock_request.return_value = '{"users": []}'

            azure_tool.execute(
                method="GET", url="https://graph.microsoft.com/v1.0/users", scope="https://graph.microsoft.com/.default"
            )

            args = mock_request.call_args
            assert args[1]["scope"] == "https://graph.microsoft.com/.default"

    def test_execute_missing_url(self, azure_tool):
        """Test executing without URL."""
        with pytest.raises(ToolException) as exc_info:
            azure_tool.execute(method="GET", url="")

        assert "URL is required" in str(exc_info.value)

    def test_execute_missing_method(self, azure_tool):
        """Test executing without HTTP method."""
        with pytest.raises(ToolException) as exc_info:
            azure_tool.execute(method="", url="https://management.azure.com/test")

        assert "HTTP method is required" in str(exc_info.value)

    def test_execute_invalid_json_args(self, azure_tool):
        """Test executing with invalid JSON in optional_args."""
        with pytest.raises(ToolException) as exc_info:
            azure_tool.execute(
                method="POST", url="https://management.azure.com/test", optional_args='{"invalid": json}'
            )

        assert "Invalid JSON format" in str(exc_info.value)

    def test_execute_invalid_args_type(self, azure_tool):
        """Test executing with invalid optional_args type."""
        with pytest.raises(ToolException) as exc_info:
            azure_tool.execute(method="POST", url="https://management.azure.com/test", optional_args=123)

        assert "optional_args must be a JSON string or dict" in str(exc_info.value)

    def test_execute_client_exception(self, azure_tool):
        """Test handling client exceptions."""
        with patch.object(azure_tool.client, 'request') as mock_request:
            mock_request.side_effect = Exception("Azure API error")

            with pytest.raises(ToolException) as exc_info:
                azure_tool.execute(method="GET", url="https://management.azure.com/test")

            assert "Azure tool execution failed" in str(exc_info.value)

    def test_healthcheck(self, azure_tool):
        """Test health check functionality."""
        with patch.object(azure_tool.client, 'health_check') as mock_health:
            mock_health.return_value = None

            azure_tool._healthcheck()

            mock_health.assert_called_once()

    def test_healthcheck_failure(self, azure_tool):
        """Test health check when service is unavailable."""
        with patch.object(azure_tool.client, 'health_check') as mock_health:
            mock_health.side_effect = RuntimeError("Connection failed")

            with pytest.raises(RuntimeError):
                azure_tool._healthcheck()
