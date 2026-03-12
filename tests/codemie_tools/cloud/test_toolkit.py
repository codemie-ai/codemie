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

from codemie_tools.base.models import ToolSet
from codemie_tools.cloud.toolkit import CloudToolkit, CloudToolkitUI


class TestCloudToolkit:
    def test_get_definition(self):
        """Test that get_definition returns the correct UI definition."""
        toolkit_ui = CloudToolkit.get_definition()
        assert isinstance(toolkit_ui, CloudToolkitUI)
        assert toolkit_ui.toolkit == ToolSet.CLOUD
        assert len(toolkit_ui.tools) == 4
        assert toolkit_ui.label == "Cloud"


class TestCloudToolkitUI:
    def test_toolkit_property(self):
        """Test that toolkit property is correctly set."""
        toolkit_ui = CloudToolkitUI()
        assert toolkit_ui.toolkit == ToolSet.CLOUD

    def test_tools_property(self):
        """Test that tools property returns all expected tools."""
        toolkit_ui = CloudToolkitUI()
        assert len(toolkit_ui.tools) == 4

        # Check that all expected tool names are present
        tool_names = [tool.name for tool in toolkit_ui.tools]
        expected_tools = ["AWS", "Azure", "GCP", "Kubernetes"]

        for expected_tool in expected_tools:
            assert expected_tool in tool_names, f"Tool {expected_tool} not found in toolkit"

    def test_description(self):
        """Test that toolkit has proper description."""
        toolkit_ui = CloudToolkitUI()
        assert toolkit_ui.description is not None
        assert "cloud" in toolkit_ui.description.lower()
