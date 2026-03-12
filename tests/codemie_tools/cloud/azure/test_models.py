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

from codemie_tools.cloud.azure.models import AzureConfig, AzureInput


class TestAzureConfig:
    def test_valid_config(self):
        """Test creating a valid Azure configuration."""
        config = AzureConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            tenant_id="87654321-4321-4321-4321-210987654321",
            client_id="11111111-1111-1111-1111-111111111111",
            client_secret="test_secret",
        )
        assert config.subscription_id == "12345678-1234-1234-1234-123456789012"
        assert config.tenant_id == "87654321-4321-4321-4321-210987654321"
        assert config.client_id == "11111111-1111-1111-1111-111111111111"
        assert config.client_secret == "test_secret"

    def test_legacy_credential_keys(self):
        """Test backward compatibility with legacy credential keys."""
        config = AzureConfig(
            azure_subscription_id="12345678-1234-1234-1234-123456789012",
            azure_tenant_id="87654321-4321-4321-4321-210987654321",
            azure_client_id="11111111-1111-1111-1111-111111111111",
            azure_client_secret="test_secret",
        )
        assert config.subscription_id == "12345678-1234-1234-1234-123456789012"
        assert config.tenant_id == "87654321-4321-4321-4321-210987654321"
        assert config.client_id == "11111111-1111-1111-1111-111111111111"
        assert config.client_secret == "test_secret"

    def test_config_field_metadata(self):
        """Test that config fields have correct metadata."""
        schema = AzureConfig.model_json_schema()

        # Check required fields
        assert "subscription_id" in schema["properties"]
        assert "tenant_id" in schema["properties"]
        assert "client_id" in schema["properties"]
        assert "client_secret" in schema["properties"]

        # Check sensitive field
        assert schema["properties"]["client_secret"]["sensitive"] is True


class TestAzureInput:
    def test_valid_input(self):
        """Test creating Azure input with valid parameters."""
        azure_input = AzureInput(
            method="GET", url="https://management.azure.com/subscriptions/test/resourcegroups?api-version=2021-04-01"
        )
        assert azure_input.method == "GET"
        assert "management.azure.com" in azure_input.url
        assert azure_input.scope == "https://management.azure.com/.default"

    def test_input_with_optional_args_dict(self):
        """Test Azure input with optional arguments as dict."""
        azure_input = AzureInput(
            method="POST", url="https://management.azure.com/test", optional_args={"data": {"location": "eastus"}}
        )
        assert azure_input.optional_args["data"]["location"] == "eastus"

    def test_input_with_optional_args_string(self):
        """Test Azure input with optional arguments as string."""
        azure_input = AzureInput(
            method="POST", url="https://management.azure.com/test", optional_args='{"data": {"location": "westus"}}'
        )
        assert isinstance(azure_input.optional_args, str)

    def test_input_with_custom_scope(self):
        """Test Azure input with custom OAuth scope."""
        azure_input = AzureInput(
            method="GET", url="https://graph.microsoft.com/v1.0/users", scope="https://graph.microsoft.com/.default"
        )
        assert azure_input.scope == "https://graph.microsoft.com/.default"

    def test_input_without_optional_args(self):
        """Test Azure input without optional arguments."""
        azure_input = AzureInput(method="GET", url="https://management.azure.com/test")
        assert azure_input.optional_args is None
