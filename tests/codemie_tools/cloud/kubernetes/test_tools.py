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

from codemie_tools.cloud.kubernetes.models import KubernetesConfig
from codemie_tools.cloud.kubernetes.tools import GenericKubernetesTool


@pytest.fixture
def k8s_config():
    """Create a test Kubernetes configuration."""
    return KubernetesConfig(url="https://kubernetes.default.svc", token="test_bearer_token", verify_ssl=False)


@pytest.fixture
def k8s_tool(k8s_config):
    """Create a test Kubernetes tool instance."""
    return GenericKubernetesTool(config=k8s_config)


class TestGenericKubernetesTool:
    def test_tool_initialization(self, k8s_tool):
        """Test that tool initializes correctly with client."""
        assert k8s_tool.name == "Kubernetes"
        assert k8s_tool.client is not None
        assert k8s_tool.config.url == "https://kubernetes.default.svc"
        assert k8s_tool.config.verify_ssl is False

    def test_execute_get_request(self, k8s_tool):
        """Test executing GET request."""
        with patch.object(k8s_tool.client, 'call_api') as mock_api:
            mock_api.return_value = '{"items": []}'

            result = k8s_tool.execute(method="GET", suburl="/api/v1/namespaces")

            assert isinstance(result, str)
            mock_api.assert_called_once()
            args = mock_api.call_args
            assert args[1]["method"] == "GET"
            assert args[1]["suburl"] == "/api/v1/namespaces"

    def test_execute_post_request(self, k8s_tool):
        """Test executing POST request with body."""
        with patch.object(k8s_tool.client, 'call_api') as mock_api:
            mock_api.return_value = '{"metadata": {"name": "my-pod"}}'

            result = k8s_tool.execute(
                method="POST",
                suburl="/api/v1/namespaces/default/pods",
                body={"metadata": {"name": "my-pod"}, "spec": {}},
            )

            assert isinstance(result, str)
            mock_api.assert_called_once()
            args = mock_api.call_args
            assert args[1]["body"]["metadata"]["name"] == "my-pod"

    def test_execute_with_json_string_body(self, k8s_tool):
        """Test executing with body as JSON string."""
        with patch.object(k8s_tool.client, 'call_api') as mock_api:
            mock_api.return_value = '{"status": "success"}'

            result = k8s_tool.execute(method="POST", suburl="/api/v1/pods", body='{"metadata": {"name": "test-pod"}}')

            assert isinstance(result, str)
            mock_api.assert_called_once()

    def test_execute_with_markdown_code_block_body(self, k8s_tool):
        """Test executing with body wrapped in markdown code block."""
        with patch.object(k8s_tool.client, 'call_api') as mock_api:
            mock_api.return_value = '{"result": "ok"}'

            result = k8s_tool.execute(
                method="POST", suburl="/api/v1/pods", body='```json\n{"metadata": {"name": "test"}}\n```'
            )

            assert isinstance(result, str)
            mock_api.assert_called_once()

    def test_execute_with_headers_dict(self, k8s_tool):
        """Test executing with headers as dict."""
        with patch.object(k8s_tool.client, 'call_api') as mock_api:
            mock_api.return_value = '{"pods": []}'

            k8s_tool.execute(method="GET", suburl="/api/v1/pods", headers={"Accept": "application/json"})

            args = mock_api.call_args
            assert args[1]["headers"]["Accept"] == "application/json"

    def test_execute_with_headers_string(self, k8s_tool):
        """Test executing with headers as JSON string."""
        with patch.object(k8s_tool.client, 'call_api') as mock_api:
            mock_api.return_value = '{"namespaces": []}'

            result = k8s_tool.execute(
                method="GET", suburl="/api/v1/namespaces", headers='{"Content-Type": "application/json"}'
            )

            assert isinstance(result, str)
            mock_api.assert_called_once()

    def test_execute_with_markdown_code_block_headers(self, k8s_tool):
        """Test executing with headers wrapped in markdown code block."""
        with patch.object(k8s_tool.client, 'call_api') as mock_api:
            mock_api.return_value = '{"result": "ok"}'

            result = k8s_tool.execute(
                method="GET", suburl="/api/v1/pods", headers='```json\n{"Accept": "application/json"}\n```'
            )

            assert isinstance(result, str)
            mock_api.assert_called_once()

    def test_execute_put_request(self, k8s_tool):
        """Test executing PUT request."""
        with patch.object(k8s_tool.client, 'call_api') as mock_api:
            mock_api.return_value = '{"status": "updated"}'

            result = k8s_tool.execute(
                method="PUT",
                suburl="/api/v1/namespaces/default/pods/my-pod",
                body={"spec": {"containers": [{"name": "nginx"}]}},
            )

            assert isinstance(result, str)
            args = mock_api.call_args
            assert args[1]["method"] == "PUT"

    def test_execute_delete_request(self, k8s_tool):
        """Test executing DELETE request."""
        with patch.object(k8s_tool.client, 'call_api') as mock_api:
            mock_api.return_value = '{"status": "deleted"}'

            result = k8s_tool.execute(method="DELETE", suburl="/api/v1/namespaces/default/pods/my-pod")

            assert isinstance(result, str)
            args = mock_api.call_args
            assert args[1]["method"] == "DELETE"

    def test_execute_missing_suburl(self, k8s_tool):
        """Test executing without suburl."""
        with pytest.raises(ToolException) as exc_info:
            k8s_tool.execute(method="GET", suburl="")

        assert "suburl is required" in str(exc_info.value)

    def test_execute_missing_method(self, k8s_tool):
        """Test executing without HTTP method."""
        with pytest.raises(ToolException) as exc_info:
            k8s_tool.execute(method="", suburl="/api/v1/pods")

        assert "HTTP method is required" in str(exc_info.value)

    def test_execute_invalid_json_body(self, k8s_tool):
        """Test executing with invalid JSON in body."""
        with pytest.raises(ToolException) as exc_info:
            k8s_tool.execute(method="POST", suburl="/api/v1/pods", body='{"invalid": json}')

        assert "Invalid JSON format in body" in str(exc_info.value)

    def test_execute_invalid_body_type(self, k8s_tool):
        """Test executing with invalid body type."""
        with pytest.raises(ToolException) as exc_info:
            k8s_tool.execute(method="POST", suburl="/api/v1/pods", body=123)

        assert "body must be a JSON string or dict" in str(exc_info.value)

    def test_execute_invalid_json_headers(self, k8s_tool):
        """Test executing with invalid JSON in headers."""
        with pytest.raises(ToolException) as exc_info:
            k8s_tool.execute(method="GET", suburl="/api/v1/pods", headers='{"invalid": json}')

        assert "Invalid JSON format in headers" in str(exc_info.value)

    def test_execute_invalid_headers_type(self, k8s_tool):
        """Test executing with invalid headers type."""
        with pytest.raises(ToolException) as exc_info:
            k8s_tool.execute(method="GET", suburl="/api/v1/pods", headers=123)

        assert "headers must be a JSON string or dict" in str(exc_info.value)

    def test_execute_client_exception(self, k8s_tool):
        """Test handling client exceptions."""
        with patch.object(k8s_tool.client, 'call_api') as mock_api:
            mock_api.side_effect = Exception("Kubernetes API error")

            with pytest.raises(ToolException) as exc_info:
                k8s_tool.execute(method="GET", suburl="/api/v1/pods")

            assert "Kubernetes tool execution failed" in str(exc_info.value)

    def test_healthcheck(self, k8s_tool):
        """Test health check functionality."""
        with patch.object(k8s_tool.client, 'health_check') as mock_health:
            mock_health.return_value = None

            k8s_tool._healthcheck()

            mock_health.assert_called_once()

    def test_healthcheck_failure(self, k8s_tool):
        """Test health check when service is unavailable."""
        with patch.object(k8s_tool.client, 'health_check') as mock_health:
            mock_health.side_effect = RuntimeError("Connection failed")

            with pytest.raises(RuntimeError):
                k8s_tool._healthcheck()
