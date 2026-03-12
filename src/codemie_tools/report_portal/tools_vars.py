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

from codemie_tools.base.models import ToolMetadata
from codemie_tools.report_portal.models import ReportPortalConfig

GET_EXTENDED_LAUNCH_DATA_TOOL = ToolMetadata(
    name="get_extended_launch_data",
    description="""
    Use the exported data from a specific launch to generate a comprehensive test report for management.
    The AI can analyze the results, highlight key metrics, and provide insights into test coverage,
    defect density, and test execution trends. Returns content of the report as text.

    Arguments:
    - launch_id (str): Launch ID of the launch to export.
    """,
    label="Get Extended Launch Data",
    user_description="""
    Exports and retrieves comprehensive test report data from a specific Report Portal launch.
    The tool processes the exported data and returns it as readable text content for analysis.

    Before using it, you need to provide:
    1. Report Portal endpoint URL
    2. API key for authentication
    3. Project name
    """.strip(),
    config_class=ReportPortalConfig,
)

GET_EXTENDED_LAUNCH_DATA_AS_RAW_TOOL = ToolMetadata(
    name="get_extended_launch_data_as_raw",
    description="""
    Get Launch details as raw data in specified format (HTML or PDF).

    Arguments:
    - launch_id (str): Launch ID of the launch to export.
    - format (str, optional): Format of the exported data. May be 'pdf' or 'html'. Default is 'html'.
    """,
    label="Get Extended Launch Data as Raw",
    user_description="""
    Exports launch data from Report Portal in raw format (HTML or PDF).
    Returns the raw content for further processing or storage.

    Before using it, you need to provide:
    1. Report Portal endpoint URL
    2. API key for authentication
    3. Project name
    """.strip(),
    config_class=ReportPortalConfig,
)

GET_LAUNCH_DETAILS_TOOL = ToolMetadata(
    name="get_launch_details",
    description="""
    Retrieve detailed information about a launch to perform a root cause analysis of failures.
    By analyzing the launch details, the AI can identify patterns in test failures and suggest areas
    of the application that may require additional attention or testing.

    Arguments:
    - launch_id (str): Launch ID of the launch to get details for.
    """,
    label="Get Launch Details",
    user_description="""
    Retrieves comprehensive details about a specific test launch from Report Portal.
    Provides information for analyzing test failures and identifying patterns.

    Before using it, you need to provide:
    1. Report Portal endpoint URL
    2. API key for authentication
    3. Project name
    """.strip(),
    config_class=ReportPortalConfig,
)

GET_ALL_LAUNCHES_TOOL = ToolMetadata(
    name="get_all_launches",
    description="""
    Analyze the data from all launches to track the progress of testing activities over time.
    It can generate visualizations and trend analyzes to help teams understand testing velocity,
    stability, and the impact of new code changes on the overall quality.

    Arguments:
    - page_number (int, optional): Number of page to retrieve. Pass if page.totalPages > 1. Default is 1.
    - page_sort (str, optional): Controls sorting order. Default is "startTime,number,DESC".
    - filter_has_composite_attribute (str, optional): Optional filter for composite attributes.
    """,
    label="Get All Launches",
    user_description="""
    Retrieves all test launches from Report Portal with pagination and sorting support.
    Useful for tracking testing progress and analyzing trends over time.

    Before using it, you need to provide:
    1. Report Portal endpoint URL
    2. API key for authentication
    3. Project name

    Optional parameters:
    - Custom sorting order (default: startTime,number,DESC)
    - Composite attribute filtering
    """.strip(),
    config_class=ReportPortalConfig,
)

FIND_TEST_ITEM_BY_ID_TOOL = ToolMetadata(
    name="find_test_item_by_id",
    description="""
    Fetch specific test items to perform detailed analysis on individual test cases. It can evaluate
    the historical performance of the test, identify flaky tests, and suggest improvements
    or optimizations to the test suite.

    Arguments:
    - item_id (str): Item ID of the item to get details for.
    """,
    label="Find Test Item by ID",
    user_description="""
    Finds and retrieves detailed information about a specific test item by its ID.
    Useful for analyzing individual test case performance and identifying issues.

    Before using it, you need to provide:
    1. Report Portal endpoint URL
    2. API key for authentication
    3. Project name
    """.strip(),
    config_class=ReportPortalConfig,
)

GET_TEST_ITEMS_FOR_LAUNCH_TOOL = ToolMetadata(
    name="get_test_items_for_launch",
    description="""
    Compile all test items from a launch to create a test execution summary.
    It can categorize tests by outcome, identify areas with high failure rates,
    and provide recommendations for test prioritization in future test cycles.

    Arguments:
    - launch_id (str): Launch ID of the launch to get test items for.
    - page_number (int, optional): Number of page to retrieve. Pass if page.totalPages > 1. Default is 1.
    - status (str, optional): Status filter for test items (e.g., PASSED, FAILED, SKIPPED). If not provided, returns all test items regardless of status.
    """,
    label="Get Test Items for Launch",
    user_description="""
    Retrieves all test items for a specific launch from Report Portal.
    Provides comprehensive test execution data for analysis and reporting.

    Before using it, you need to provide:
    1. Report Portal endpoint URL
    2. API key for authentication
    3. Project name

    Optional filtering:
    - Filter by test status (PASSED, FAILED, SKIPPED) or omit to get all items
    """.strip(),
    config_class=ReportPortalConfig,
)

GET_LOGS_FOR_TEST_ITEM_TOOL = ToolMetadata(
    name="get_logs_for_test_item",
    description="""
    Process the logs for test items to assist in automated debugging.
    By applying natural language processing, the AI can extract meaningful information from logs,
    correlate errors with source code changes, and assist developers in pinpointing issues.

    Arguments:
    - item_id (str): Item ID of the item to get logs for.
    - page_number (int, optional): Number of page to retrieve. Pass if page.totalPages > 1. Default is 1.
    """,
    label="Get Logs for Test Item",
    user_description="""
    Retrieves logs for a specific test item from Report Portal.
    Useful for debugging test failures and analyzing error details.

    Before using it, you need to provide:
    1. Report Portal endpoint URL
    2. API key for authentication
    3. Project name
    """.strip(),
    config_class=ReportPortalConfig,
)

GET_USER_INFORMATION_TOOL = ToolMetadata(
    name="get_user_information",
    description="""
    Use user information to personalize dashboards and reports. It can also analyze user activity to optimize
    test assignment and load balancing among QA team members based on their expertise and past performance.

    Arguments:
    - username (str): Username of the user to get information for.
    """,
    label="Get User Information",
    user_description="""
    Retrieves information about a specific user in Report Portal.
    Useful for personalizing reports and analyzing user activity.

    Before using it, you need to provide:
    1. Report Portal endpoint URL
    2. API key for authentication
    3. Project name
    """.strip(),
    config_class=ReportPortalConfig,
)

GET_DASHBOARD_DATA_TOOL = ToolMetadata(
    name="get_dashboard_data",
    description="""
    Analyze dashboard data to create executive summaries that highlight key performance indicators (KPIs),
    overall project health, and areas requiring immediate attention.
    It can also provide predictive analytics for future test planning.

    Arguments:
    - dashboard_id (str): Dashboard ID of the dashboard to get data for.
    """,
    label="Get Dashboard Data",
    user_description="""
    Retrieves data from a specific dashboard in Report Portal.
    Provides KPIs and analytics for executive reporting and project health monitoring.

    Before using it, you need to provide:
    1. Report Portal endpoint URL
    2. API key for authentication
    3. Project name
    """.strip(),
    config_class=ReportPortalConfig,
)

UPDATE_TEST_ITEM_TOOL = ToolMetadata(
    name="update_test_item",
    description="""
    Update the status of a test item in Report Portal. This tool enables manual correction of test status
    (Failed, Passed, or Skipped) with an optional description. Useful for workflow automation and
    integration with other systems.

    Arguments:
    - item_id (str): ID of the test item to update.
    - status (str): New status for the test item. Must be one of: "FAILED", "PASSED", "SKIPPED".
    - description (str, optional): Description for the status update. Defaults to "Status updated manually".
    """,
    label="Update Test Item",
    user_description="""
    Updates the status of a specific test item in Report Portal.
    Allows manual correction of test statuses with optional description.

    Before using it, you need to provide:
    1. Report Portal endpoint URL
    2. API key for authentication
    3. Project name
    """.strip(),
    config_class=ReportPortalConfig,
)
