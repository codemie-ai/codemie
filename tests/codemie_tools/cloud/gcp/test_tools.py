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

from codemie_tools.cloud.gcp.models import GCPConfig
from codemie_tools.cloud.gcp.tools import GenericGCPTool


@pytest.fixture
def gcp_config():
    """Create a test GCP configuration."""
    return GCPConfig(
        service_account_key='{"type": "service_account", "project_id": "test-project", "private_key_id": "key123"}'
    )


@pytest.fixture
def gcp_tool(gcp_config):
    """Create a test GCP tool instance."""
    return GenericGCPTool(config=gcp_config)


class TestGenericGCPTool:
    def test_execute_get_request(self, gcp_tool):
        """Test executing GET request."""
        with patch.object(gcp_tool.client, 'request') as mock_request:
            mock_request.return_value = '{"instances": []}'

            result = gcp_tool.execute(
                method="GET",
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
                url="https://compute.googleapis.com/compute/v1/projects/test/zones",
            )

            assert isinstance(result, str)
            mock_request.assert_called_once()
            args = mock_request.call_args
            assert args[1]["method"] == "GET"
            assert len(args[1]["scopes"]) == 1

    def test_execute_post_request(self, gcp_tool):
        """Test executing POST request with data."""
        with patch.object(gcp_tool.client, 'request') as mock_request:
            mock_request.return_value = '{"id": "test-instance"}'

            result = gcp_tool.execute(
                method="POST",
                scopes=["https://www.googleapis.com/auth/compute"],
                url="https://compute.googleapis.com/compute/v1/projects/test/instances",
                optional_args={"json": {"name": "test-vm"}},
            )

            assert isinstance(result, str)
            mock_request.assert_called_once()
            args = mock_request.call_args
            assert args[1]["optional_args"]["json"]["name"] == "test-vm"

    def test_execute_with_json_string_args(self, gcp_tool):
        """Test executing with optional_args as JSON string."""
        with patch.object(gcp_tool.client, 'request') as mock_request:
            mock_request.return_value = '{"status": "success"}'

            result = gcp_tool.execute(
                method="POST",
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
                url="https://storage.googleapis.com/storage/v1/b",
                optional_args='{"params": {"project": "test"}}',
            )

            assert isinstance(result, str)
            mock_request.assert_called_once()

    def test_execute_with_markdown_code_block(self, gcp_tool):
        """Test executing with args wrapped in markdown code block."""
        with patch.object(gcp_tool.client, 'request') as mock_request:
            mock_request.return_value = '{"result": "ok"}'

            result = gcp_tool.execute(
                method="POST",
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
                url="https://compute.googleapis.com/compute/v1/test",
                optional_args='```json\n{"data": {"value": "test"}}\n```',
            )

            assert isinstance(result, str)
            mock_request.assert_called_once()

    def test_execute_with_multiple_scopes(self, gcp_tool):
        """Test executing with multiple OAuth scopes."""
        with patch.object(gcp_tool.client, 'request') as mock_request:
            mock_request.return_value = '{"resources": []}'

            scopes = ["https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/compute"]

            gcp_tool.execute(method="GET", scopes=scopes, url="https://compute.googleapis.com/compute/v1/projects/test")

            args = mock_request.call_args
            assert len(args[1]["scopes"]) == 2

    def test_execute_missing_url(self, gcp_tool):
        """Test executing without URL."""
        with pytest.raises(ToolException) as exc_info:
            gcp_tool.execute(method="GET", scopes=["https://www.googleapis.com/auth/cloud-platform"], url="")

        assert "URL is required" in str(exc_info.value)

    def test_execute_missing_method(self, gcp_tool):
        """Test executing without HTTP method."""
        with pytest.raises(ToolException) as exc_info:
            gcp_tool.execute(
                method="",
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
                url="https://compute.googleapis.com/compute/v1/test",
            )

        assert "HTTP method is required" in str(exc_info.value)

    def test_execute_missing_scopes(self, gcp_tool):
        """Test executing without OAuth scopes."""
        with pytest.raises(ToolException) as exc_info:
            gcp_tool.execute(method="GET", scopes=[], url="https://compute.googleapis.com/compute/v1/test")

        assert "At least one OAuth scope is required" in str(exc_info.value)

    def test_execute_invalid_json_args(self, gcp_tool):
        """Test executing with invalid JSON in optional_args."""
        with pytest.raises(ToolException) as exc_info:
            gcp_tool.execute(
                method="POST",
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
                url="https://compute.googleapis.com/compute/v1/test",
                optional_args='{"invalid": json}',
            )

        assert "Invalid JSON format" in str(exc_info.value)

    def test_execute_invalid_args_type(self, gcp_tool):
        """Test executing with invalid optional_args type."""
        with pytest.raises(ToolException) as exc_info:
            gcp_tool.execute(
                method="POST",
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
                url="https://compute.googleapis.com/compute/v1/test",
                optional_args=123,
            )

        assert "optional_args must be a JSON string or dict" in str(exc_info.value)

    def test_execute_client_exception(self, gcp_tool):
        """Test handling client exceptions."""
        with patch.object(gcp_tool.client, 'request') as mock_request:
            mock_request.side_effect = Exception("GCP API error")

            with pytest.raises(ToolException) as exc_info:
                gcp_tool.execute(
                    method="GET",
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                    url="https://compute.googleapis.com/compute/v1/test",
                )

            assert "GCP tool execution failed" in str(exc_info.value)

    def test_healthcheck(self, gcp_tool):
        """Test health check functionality."""
        with patch.object(gcp_tool.client, 'health_check') as mock_health:
            mock_health.return_value = None

            gcp_tool._healthcheck()

            mock_health.assert_called_once()

    def test_healthcheck_failure(self, gcp_tool):
        """Test health check when service is unavailable."""
        with patch.object(gcp_tool.client, 'health_check') as mock_health:
            mock_health.side_effect = RuntimeError("Connection failed")

            with pytest.raises(RuntimeError):
                gcp_tool._healthcheck()
