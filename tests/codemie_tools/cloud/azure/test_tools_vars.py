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

from codemie_tools.cloud.azure.models import AzureConfig
from codemie_tools.cloud.azure.tools_vars import AZURE_TOOL


class TestAzureToolMetadata:
    def test_tool_name(self):
        """Test that Azure tool has correct name."""
        assert AZURE_TOOL.name == "Azure"

    def test_tool_description(self):
        """Test that Azure tool has description."""
        assert AZURE_TOOL.description is not None
        assert len(AZURE_TOOL.description) > 0
        assert "azure" in AZURE_TOOL.description.lower()

    def test_tool_label(self):
        """Test that Azure tool has correct label."""
        assert AZURE_TOOL.label == "Azure"

    def test_tool_user_description(self):
        """Test that Azure tool has user description."""
        assert AZURE_TOOL.user_description is not None
        assert len(AZURE_TOOL.user_description) > 0

    def test_tool_settings_config(self):
        """Test that Azure tool requires settings configuration."""
        assert AZURE_TOOL.settings_config is True

    def test_tool_config_class(self):
        """Test that Azure tool has correct config class."""
        assert AZURE_TOOL.config_class == AzureConfig
