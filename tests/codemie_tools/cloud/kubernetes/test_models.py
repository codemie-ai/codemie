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

from codemie_tools.cloud.kubernetes.models import KubernetesConfig, KubernetesInput


class TestKubernetesConfig:
    def test_valid_config(self):
        """Test creating a valid Kubernetes configuration."""
        config = KubernetesConfig(url="https://kubernetes.default.svc", token="test_bearer_token")
        assert config.url == "https://kubernetes.default.svc"
        assert config.token == "test_bearer_token"
        assert config.verify_ssl is False

    def test_config_with_ssl_verification(self):
        """Test Kubernetes config with SSL verification enabled."""
        config = KubernetesConfig(url="https://kubernetes.example.com", token="test_token", verify_ssl=True)
        assert config.verify_ssl is True

    def test_legacy_credential_keys(self):
        """Test backward compatibility with legacy credential keys."""
        config = KubernetesConfig(kubernetes_url="https://k8s.legacy.com", kubernetes_token="legacy_token")
        assert config.url == "https://k8s.legacy.com"
        assert config.token == "legacy_token"

    def test_config_field_metadata(self):
        """Test that config fields have correct metadata."""
        schema = KubernetesConfig.model_json_schema()

        # Check required fields
        assert "url" in schema["properties"]
        assert "token" in schema["properties"]

        # Check sensitive field
        assert schema["properties"]["token"]["sensitive"] is True

        # Check optional field
        assert "verify_ssl" in schema["properties"]


class TestKubernetesInput:
    def test_valid_input(self):
        """Test creating Kubernetes input with valid parameters."""
        k8s_input = KubernetesInput(method="GET", suburl="/api/v1/namespaces")
        assert k8s_input.method == "GET"
        assert k8s_input.suburl == "/api/v1/namespaces"
        assert k8s_input.body is None
        assert k8s_input.headers is None

    def test_input_with_body_dict(self):
        """Test Kubernetes input with body as dict."""
        k8s_input = KubernetesInput(
            method="POST", suburl="/api/v1/namespaces/default/pods", body={"metadata": {"name": "my-pod"}, "spec": {}}
        )
        assert k8s_input.body["metadata"]["name"] == "my-pod"

    def test_input_with_body_string(self):
        """Test Kubernetes input with body as string."""
        k8s_input = KubernetesInput(method="POST", suburl="/api/v1/pods", body='{"metadata": {"name": "test-pod"}}')
        assert isinstance(k8s_input.body, str)

    def test_input_with_headers_dict(self):
        """Test Kubernetes input with headers as dict."""
        k8s_input = KubernetesInput(method="GET", suburl="/api/v1/pods", headers={"Content-Type": "application/json"})
        assert k8s_input.headers["Content-Type"] == "application/json"

    def test_input_with_headers_string(self):
        """Test Kubernetes input with headers as string."""
        k8s_input = KubernetesInput(method="GET", suburl="/api/v1/pods", headers='{"Accept": "application/json"}')
        assert isinstance(k8s_input.headers, str)

    def test_input_complex_operation(self):
        """Test Kubernetes input for complex operation with all parameters."""
        k8s_input = KubernetesInput(
            method="PUT",
            suburl="/api/v1/namespaces/default/pods/my-pod",
            body={"spec": {"containers": [{"name": "nginx", "image": "nginx:latest"}]}},
            headers={"Content-Type": "application/json"},
        )
        assert k8s_input.method == "PUT"
        assert "my-pod" in k8s_input.suburl
        assert k8s_input.body is not None
        assert k8s_input.headers is not None
