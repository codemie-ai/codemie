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

from codemie_tools.cloud.gcp.models import GCPConfig
from codemie_tools.cloud.gcp.tools_vars import GCP_TOOL


class TestGCPToolMetadata:
    def test_tool_name(self):
        """Test that GCP tool has correct name."""
        assert GCP_TOOL.name == "GCP"

    def test_tool_description(self):
        """Test that GCP tool has description."""
        assert GCP_TOOL.description is not None
        assert len(GCP_TOOL.description) > 0
        assert "google cloud" in GCP_TOOL.description.lower()

    def test_tool_label(self):
        """Test that GCP tool has correct label."""
        assert GCP_TOOL.label == "GCP"

    def test_tool_user_description(self):
        """Test that GCP tool has user description."""
        assert GCP_TOOL.user_description is not None
        assert len(GCP_TOOL.user_description) > 0

    def test_tool_settings_config(self):
        """Test that GCP tool requires settings configuration."""
        assert GCP_TOOL.settings_config is True

    def test_tool_config_class(self):
        """Test that GCP tool has correct config class."""
        assert GCP_TOOL.config_class == GCPConfig
