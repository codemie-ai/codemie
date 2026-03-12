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
from codemie_tools.report_portal.toolkit import ReportPortalToolkit, ReportPortalToolkitUI


class TestReportPortalToolkit:
    def test_get_definition(self):
        """Test that get_definition returns the correct UI definition."""
        toolkit_ui = ReportPortalToolkit.get_definition()
        assert isinstance(toolkit_ui, ReportPortalToolkitUI)
        assert toolkit_ui.toolkit == ToolSet.REPORT_PORTAL
        assert len(toolkit_ui.tools) == 10
        assert toolkit_ui.label == ToolSet.REPORT_PORTAL.value


class TestReportPortalToolkitUI:
    def test_toolkit_property(self):
        """Test that toolkit property is correctly set."""
        toolkit_ui = ReportPortalToolkitUI()
        assert toolkit_ui.toolkit == ToolSet.REPORT_PORTAL

    def test_tools_property(self):
        """Test that tools property returns all expected tools."""
        toolkit_ui = ReportPortalToolkitUI()
        assert len(toolkit_ui.tools) == 10

        # Check that all expected tool names are present
        tool_names = [tool.name for tool in toolkit_ui.tools]
        expected_tools = [
            "get_extended_launch_data",
            "get_extended_launch_data_as_raw",
            "get_launch_details",
            "get_all_launches",
            "find_test_item_by_id",
            "get_test_items_for_launch",
            "get_logs_for_test_item",
            "get_user_information",
            "get_dashboard_data",
            "update_test_item",
        ]

        for expected_tool in expected_tools:
            assert expected_tool in tool_names, f"Tool {expected_tool} not found in toolkit"

    def test_settings_config(self):
        """Test that settings_config is enabled."""
        toolkit_ui = ReportPortalToolkitUI()
        assert toolkit_ui.settings_config is True
