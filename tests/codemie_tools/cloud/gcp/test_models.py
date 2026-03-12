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

from codemie_tools.cloud.gcp.models import GCPConfig, GCPInput


class TestGCPConfig:
    def test_valid_config(self):
        """Test creating a valid GCP configuration."""
        config = GCPConfig(service_account_key='{"type": "service_account", "project_id": "test-project"}')
        assert '"type": "service_account"' in config.service_account_key
        assert '"project_id": "test-project"' in config.service_account_key

    def test_legacy_credential_key(self):
        """Test backward compatibility with legacy credential key."""
        config = GCPConfig(gcp_api_key='{"type": "service_account", "project_id": "test-legacy"}')
        assert '"project_id": "test-legacy"' in config.service_account_key

    def test_config_field_metadata(self):
        """Test that config fields have correct metadata."""
        schema = GCPConfig.model_json_schema()

        # Check service_account_key field
        assert "service_account_key" in schema["properties"]
        assert schema["properties"]["service_account_key"]["sensitive"] is True


class TestGCPInput:
    def test_valid_input(self):
        """Test creating GCP input with valid parameters."""
        gcp_input = GCPInput(
            method="GET",
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
            url="https://compute.googleapis.com/compute/v1/projects/test/zones",
        )
        assert gcp_input.method == "GET"
        assert len(gcp_input.scopes) == 1
        assert "googleapis.com" in gcp_input.url

    def test_input_with_multiple_scopes(self):
        """Test GCP input with multiple scopes."""
        gcp_input = GCPInput(
            method="POST",
            scopes=["https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/compute"],
            url="https://compute.googleapis.com/compute/v1/projects/test/instances",
        )
        assert len(gcp_input.scopes) == 2

    def test_input_with_optional_args_dict(self):
        """Test GCP input with optional arguments as dict."""
        gcp_input = GCPInput(
            method="GET",
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
            url="https://storage.googleapis.com/storage/v1/b/bucket/o",
            optional_args={"params": {"maxResults": 100}},
        )
        assert gcp_input.optional_args["params"]["maxResults"] == 100

    def test_input_with_optional_args_string(self):
        """Test GCP input with optional arguments as string."""
        gcp_input = GCPInput(
            method="POST",
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
            url="https://compute.googleapis.com/compute/v1/test",
            optional_args='{"data": {"name": "test"}}',
        )
        assert isinstance(gcp_input.optional_args, str)

    def test_input_without_optional_args(self):
        """Test GCP input without optional arguments."""
        gcp_input = GCPInput(
            method="GET",
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
            url="https://compute.googleapis.com/compute/v1/test",
        )
        assert gcp_input.optional_args is None
