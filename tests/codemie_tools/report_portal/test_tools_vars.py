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

from codemie_tools.report_portal.tools_vars import (
    GET_EXTENDED_LAUNCH_DATA_TOOL,
    GET_EXTENDED_LAUNCH_DATA_AS_RAW_TOOL,
    GET_LAUNCH_DETAILS_TOOL,
    GET_ALL_LAUNCHES_TOOL,
    FIND_TEST_ITEM_BY_ID_TOOL,
    GET_TEST_ITEMS_FOR_LAUNCH_TOOL,
    GET_LOGS_FOR_TEST_ITEM_TOOL,
    GET_USER_INFORMATION_TOOL,
    GET_DASHBOARD_DATA_TOOL,
    UPDATE_TEST_ITEM_TOOL,
)


class TestToolsVars:
    def test_get_extended_launch_data_tool_metadata(self):
        """Test that GET_EXTENDED_LAUNCH_DATA_TOOL has the correct metadata."""
        assert GET_EXTENDED_LAUNCH_DATA_TOOL.name == "get_extended_launch_data"
        assert "Use the exported data from a specific launch" in GET_EXTENDED_LAUNCH_DATA_TOOL.description
        assert "launch_id" in GET_EXTENDED_LAUNCH_DATA_TOOL.description
        assert GET_EXTENDED_LAUNCH_DATA_TOOL.label == "Get Extended Launch Data"
        assert "Exports and retrieves comprehensive test report data" in GET_EXTENDED_LAUNCH_DATA_TOOL.user_description

    def test_get_extended_launch_data_as_raw_tool_metadata(self):
        """Test that GET_EXTENDED_LAUNCH_DATA_AS_RAW_TOOL has the correct metadata."""
        assert GET_EXTENDED_LAUNCH_DATA_AS_RAW_TOOL.name == "get_extended_launch_data_as_raw"
        assert "Get Launch details as raw data" in GET_EXTENDED_LAUNCH_DATA_AS_RAW_TOOL.description
        assert "launch_id" in GET_EXTENDED_LAUNCH_DATA_AS_RAW_TOOL.description
        assert "format" in GET_EXTENDED_LAUNCH_DATA_AS_RAW_TOOL.description
        assert GET_EXTENDED_LAUNCH_DATA_AS_RAW_TOOL.label == "Get Extended Launch Data as Raw"
        assert (
            "Exports launch data from Report Portal in raw format"
            in GET_EXTENDED_LAUNCH_DATA_AS_RAW_TOOL.user_description
        )

    def test_get_launch_details_tool_metadata(self):
        """Test that GET_LAUNCH_DETAILS_TOOL has the correct metadata."""
        assert GET_LAUNCH_DETAILS_TOOL.name == "get_launch_details"
        assert "Retrieve detailed information about a launch" in GET_LAUNCH_DETAILS_TOOL.description
        assert "launch_id" in GET_LAUNCH_DETAILS_TOOL.description
        assert GET_LAUNCH_DETAILS_TOOL.label == "Get Launch Details"
        assert (
            "Retrieves comprehensive details about a specific test launch" in GET_LAUNCH_DETAILS_TOOL.user_description
        )

    def test_get_all_launches_tool_metadata(self):
        """Test that GET_ALL_LAUNCHES_TOOL has the correct metadata."""
        assert GET_ALL_LAUNCHES_TOOL.name == "get_all_launches"
        assert "Analyze the data from all launches" in GET_ALL_LAUNCHES_TOOL.description
        assert "page_number" in GET_ALL_LAUNCHES_TOOL.description
        assert GET_ALL_LAUNCHES_TOOL.label == "Get All Launches"
        assert "Retrieves all test launches from Report Portal" in GET_ALL_LAUNCHES_TOOL.user_description

    def test_find_test_item_by_id_tool_metadata(self):
        """Test that FIND_TEST_ITEM_BY_ID_TOOL has the correct metadata."""
        assert FIND_TEST_ITEM_BY_ID_TOOL.name == "find_test_item_by_id"
        assert "Fetch specific test items" in FIND_TEST_ITEM_BY_ID_TOOL.description
        assert "item_id" in FIND_TEST_ITEM_BY_ID_TOOL.description
        assert FIND_TEST_ITEM_BY_ID_TOOL.label == "Find Test Item by ID"
        assert (
            "Finds and retrieves detailed information about a specific test item"
            in FIND_TEST_ITEM_BY_ID_TOOL.user_description
        )

    def test_get_test_items_for_launch_tool_metadata(self):
        """Test that GET_TEST_ITEMS_FOR_LAUNCH_TOOL has the correct metadata."""
        assert GET_TEST_ITEMS_FOR_LAUNCH_TOOL.name == "get_test_items_for_launch"
        assert "Compile all test items from a launch" in GET_TEST_ITEMS_FOR_LAUNCH_TOOL.description
        assert "launch_id" in GET_TEST_ITEMS_FOR_LAUNCH_TOOL.description
        assert "page_number" in GET_TEST_ITEMS_FOR_LAUNCH_TOOL.description
        assert "status (str, optional)" in GET_TEST_ITEMS_FOR_LAUNCH_TOOL.description
        assert "If not provided, returns all test items" in GET_TEST_ITEMS_FOR_LAUNCH_TOOL.description
        assert GET_TEST_ITEMS_FOR_LAUNCH_TOOL.label == "Get Test Items for Launch"
        assert "Retrieves all test items for a specific launch" in GET_TEST_ITEMS_FOR_LAUNCH_TOOL.user_description
        assert "Optional filtering" in GET_TEST_ITEMS_FOR_LAUNCH_TOOL.user_description

    def test_get_logs_for_test_item_tool_metadata(self):
        """Test that GET_LOGS_FOR_TEST_ITEM_TOOL has the correct metadata."""
        assert GET_LOGS_FOR_TEST_ITEM_TOOL.name == "get_logs_for_test_item"
        assert "Process the logs for test items" in GET_LOGS_FOR_TEST_ITEM_TOOL.description
        assert "item_id" in GET_LOGS_FOR_TEST_ITEM_TOOL.description
        assert "page_number" in GET_LOGS_FOR_TEST_ITEM_TOOL.description
        assert GET_LOGS_FOR_TEST_ITEM_TOOL.label == "Get Logs for Test Item"
        assert "Retrieves logs for a specific test item" in GET_LOGS_FOR_TEST_ITEM_TOOL.user_description

    def test_get_user_information_tool_metadata(self):
        """Test that GET_USER_INFORMATION_TOOL has the correct metadata."""
        assert GET_USER_INFORMATION_TOOL.name == "get_user_information"
        assert "Use user information to personalize dashboards" in GET_USER_INFORMATION_TOOL.description
        assert "username" in GET_USER_INFORMATION_TOOL.description
        assert GET_USER_INFORMATION_TOOL.label == "Get User Information"
        assert "Retrieves information about a specific user" in GET_USER_INFORMATION_TOOL.user_description

    def test_get_dashboard_data_tool_metadata(self):
        """Test that GET_DASHBOARD_DATA_TOOL has the correct metadata."""
        assert GET_DASHBOARD_DATA_TOOL.name == "get_dashboard_data"
        assert "Analyze dashboard data to create executive summaries" in GET_DASHBOARD_DATA_TOOL.description
        assert "dashboard_id" in GET_DASHBOARD_DATA_TOOL.description
        assert GET_DASHBOARD_DATA_TOOL.label == "Get Dashboard Data"
        assert "Retrieves data from a specific dashboard" in GET_DASHBOARD_DATA_TOOL.user_description

    def test_update_test_item_tool_metadata(self):
        """Test that UPDATE_TEST_ITEM_TOOL has the correct metadata."""
        assert UPDATE_TEST_ITEM_TOOL.name == "update_test_item"
        assert "Update the status of a test item in Report Portal" in UPDATE_TEST_ITEM_TOOL.description
        assert "item_id" in UPDATE_TEST_ITEM_TOOL.description
        assert "status" in UPDATE_TEST_ITEM_TOOL.description
        assert UPDATE_TEST_ITEM_TOOL.label == "Update Test Item"
        assert "Updates the status of a specific test item in Report Portal" in UPDATE_TEST_ITEM_TOOL.user_description
