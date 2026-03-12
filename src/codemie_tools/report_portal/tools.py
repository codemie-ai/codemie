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

import logging
from typing import Type, Optional
from io import BytesIO

import pdfplumber
from langchain_core.tools import ToolException
from pydantic import BaseModel

from codemie_tools.base.codemie_tool import CodeMieTool
from .models import (
    ReportPortalConfig,
    GetExtendedLaunchDataInput,
    GetExtendedLaunchDataAsRawInput,
    GetLaunchDetailsInput,
    GetAllLaunchesInput,
    FindTestItemByIdInput,
    GetTestItemsForLaunchInput,
    GetLogsForTestItemInput,
    GetUserInformationInput,
    GetDashboardDataInput,
    UpdateTestItemInput,
)
from .report_portal_client import ReportPortalClient
from .tools_vars import (
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

logger = logging.getLogger(__name__)


class BaseReportPortalTool(CodeMieTool):
    """Base class for Report Portal tools."""

    config: ReportPortalConfig
    client: Optional[ReportPortalClient] = None

    def __init__(self, config: ReportPortalConfig):
        """Initialize the tool with configuration."""
        super().__init__(config=config)
        if config.url and config.api_key and config.project:
            self.client = ReportPortalClient(endpoint=config.url, project=config.project, api_key=config.api_key)

    def _healthcheck(self):
        self.client.get_all_launches(page_number=1)


class GetExtendedLaunchDataTool(BaseReportPortalTool):
    """Tool to get extended launch data from Report Portal."""

    name: str = GET_EXTENDED_LAUNCH_DATA_TOOL.name
    description: str = GET_EXTENDED_LAUNCH_DATA_TOOL.description
    args_schema: Type[BaseModel] = GetExtendedLaunchDataInput

    def __init__(self, config: ReportPortalConfig):
        super().__init__(config)

    def execute(self, launch_id: str):
        """
        Get extended launch data.

        Args:
            launch_id: Launch ID of the launch to export

        Returns:
            str: Text content of the launch report

        Raises:
            ToolException: If export fails
        """
        try:
            format_type = 'html'
            response = self.client.export_specified_launch(launch_id, format_type)

            if response.headers['Content-Type'] in ['application/pdf', 'text/html']:
                # Use pdfplumber for PDF text extraction (Apache-compatible)
                with pdfplumber.open(BytesIO(response.content)) as report:
                    text_content = ''
                    for page in report.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text_content += page_text
                    return text_content
            else:
                logger.warning(f"Exported data for launch {launch_id} is in an unsupported format.")
                return None
        except Exception as e:
            logger.error(f"Error getting extended launch data: {str(e)}")
            raise ToolException(f"Error getting extended launch data: {str(e)}")


class GetExtendedLaunchDataAsRawTool(BaseReportPortalTool):
    """Tool to get extended launch data as raw from Report Portal."""

    name: str = GET_EXTENDED_LAUNCH_DATA_AS_RAW_TOOL.name
    description: str = GET_EXTENDED_LAUNCH_DATA_AS_RAW_TOOL.description
    args_schema: Type[BaseModel] = GetExtendedLaunchDataAsRawInput

    def __init__(self, config: ReportPortalConfig):
        super().__init__(config)

    def execute(self, launch_id: str, format: str = 'html'):
        """
        Get extended launch data as raw.

        Args:
            launch_id: Launch ID of the launch to export
            format: Format of the exported data ('pdf' or 'html')

        Returns:
            bytes: Raw content of the launch report

        Raises:
            ToolException: If export fails
        """
        try:
            response = self.client.export_specified_launch(launch_id, format)
            if not response.headers.get('Content-Disposition'):
                logger.warning(f"Exported data for launch {launch_id} is empty.")
                return None
            return response.content
        except Exception as e:
            logger.error(f"Error getting extended launch data as raw: {str(e)}")
            raise ToolException(f"Error getting extended launch data as raw: {str(e)}")


class GetLaunchDetailsTool(BaseReportPortalTool):
    """Tool to get launch details from Report Portal."""

    name: str = GET_LAUNCH_DETAILS_TOOL.name
    description: str = GET_LAUNCH_DETAILS_TOOL.description
    args_schema: Type[BaseModel] = GetLaunchDetailsInput

    def __init__(self, config: ReportPortalConfig):
        super().__init__(config)

    def execute(self, launch_id: str):
        """
        Get launch details.

        Args:
            launch_id: Launch ID of the launch to get details for

        Returns:
            dict: Launch details

        Raises:
            ToolException: If retrieval fails
        """
        try:
            return self.client.get_launch_details(launch_id)
        except Exception as e:
            logger.error(f"Error getting launch details: {str(e)}")
            raise ToolException(f"Error getting launch details: {str(e)}")


class GetAllLaunchesTool(BaseReportPortalTool):
    """Tool to get all launches from Report Portal."""

    name: str = GET_ALL_LAUNCHES_TOOL.name
    description: str = GET_ALL_LAUNCHES_TOOL.description
    args_schema: Type[BaseModel] = GetAllLaunchesInput

    def __init__(self, config: ReportPortalConfig):
        super().__init__(config)

    def execute(
        self,
        page_number: int = 1,
        page_sort: str = "startTime,number,DESC",
        filter_has_composite_attribute: Optional[str] = None,
    ):
        """
        Get all launches.

        Args:
            page_number: Number of page to retrieve
            page_sort: Sort order for launches
            filter_has_composite_attribute: Composite attribute filter

        Returns:
            dict: List of launches

        Raises:
            ToolException: If retrieval fails
        """
        try:
            return self.client.get_all_launches(page_number, page_sort, filter_has_composite_attribute)
        except Exception as e:
            logger.error(f"Error getting all launches: {str(e)}")
            raise ToolException(f"Error getting all launches: {str(e)}")


class FindTestItemByIdTool(BaseReportPortalTool):
    """Tool to find test item by ID from Report Portal."""

    name: str = FIND_TEST_ITEM_BY_ID_TOOL.name
    description: str = FIND_TEST_ITEM_BY_ID_TOOL.description
    args_schema: Type[BaseModel] = FindTestItemByIdInput

    def __init__(self, config: ReportPortalConfig):
        super().__init__(config)

    def execute(self, item_id: str):
        """
        Find test item by ID.

        Args:
            item_id: Item ID of the item to get details for

        Returns:
            dict: Test item details

        Raises:
            ToolException: If retrieval fails
        """
        try:
            return self.client.find_test_item_by_id(item_id)
        except Exception as e:
            logger.error(f"Error finding test item by ID: {str(e)}")
            raise ToolException(f"Error finding test item by ID: {str(e)}")


class GetTestItemsForLaunchTool(BaseReportPortalTool):
    """Tool to get test items for launch from Report Portal."""

    name: str = GET_TEST_ITEMS_FOR_LAUNCH_TOOL.name
    description: str = GET_TEST_ITEMS_FOR_LAUNCH_TOOL.description
    args_schema: Type[BaseModel] = GetTestItemsForLaunchInput

    def __init__(self, config: ReportPortalConfig):
        super().__init__(config)

    def execute(self, launch_id: str, page_number: int = 1, status: str = None) -> dict:
        """
        Get test items for launch.

        Args:
            launch_id: Launch ID of the launch to get test items for
            page_number: Number of page to retrieve
            status: Status of the test item

        Returns:
            dict: List of test items

        Raises:
            ToolException: If retrieval fails
        """
        try:
            return self.client.get_test_items_for_launch(launch_id, page_number, status)
        except Exception as e:
            logger.error(f"Error getting test items for launch: {str(e)}")
            raise ToolException(f"Error getting test items for launch: {str(e)}")


class GetLogsForTestItemTool(BaseReportPortalTool):
    """Tool to get logs for test item from Report Portal."""

    name: str = GET_LOGS_FOR_TEST_ITEM_TOOL.name
    description: str = GET_LOGS_FOR_TEST_ITEM_TOOL.description
    args_schema: Type[BaseModel] = GetLogsForTestItemInput

    def __init__(self, config: ReportPortalConfig):
        super().__init__(config)

    def execute(self, item_id: str, page_number: int = 1):
        """
        Get logs for test item.

        Args:
            item_id: Item ID of the item to get logs for
            page_number: Number of page to retrieve

        Returns:
            dict: Test item logs

        Raises:
            ToolException: If retrieval fails
        """
        try:
            return self.client.get_logs_for_test_items(item_id, page_number)
        except Exception as e:
            logger.error(f"Error getting logs for test item: {str(e)}")
            raise ToolException(f"Error getting logs for test item: {str(e)}")


class GetUserInformationTool(BaseReportPortalTool):
    """Tool to get user information from Report Portal."""

    name: str = GET_USER_INFORMATION_TOOL.name
    description: str = GET_USER_INFORMATION_TOOL.description
    args_schema: Type[BaseModel] = GetUserInformationInput

    def __init__(self, config: ReportPortalConfig):
        super().__init__(config)

    def execute(self, username: str):
        """
        Get user information.

        Args:
            username: Username of the user to get information for

        Returns:
            dict: User information

        Raises:
            ToolException: If retrieval fails
        """
        try:
            return self.client.get_user_information(username)
        except Exception as e:
            logger.error(f"Error getting user information: {str(e)}")
            raise ToolException(f"Error getting user information: {str(e)}")


class GetDashboardDataTool(BaseReportPortalTool):
    """Tool to get dashboard data from Report Portal."""

    name: str = GET_DASHBOARD_DATA_TOOL.name
    description: str = GET_DASHBOARD_DATA_TOOL.description
    args_schema: Type[BaseModel] = GetDashboardDataInput

    def __init__(self, config: ReportPortalConfig):
        super().__init__(config)

    def execute(self, dashboard_id: str):
        """
        Get dashboard data.

        Args:
            dashboard_id: Dashboard ID of the dashboard to get data for

        Returns:
            dict: Dashboard data

        Raises:
            ToolException: If retrieval fails
        """
        try:
            return self.client.get_dashboard_data(dashboard_id)
        except Exception as e:
            logger.error(f"Error getting dashboard data: {str(e)}")
            raise ToolException(f"Error getting dashboard data: {str(e)}")


class UpdateTestItemTool(BaseReportPortalTool):
    """Tool to update test item status in Report Portal."""

    name: str = UPDATE_TEST_ITEM_TOOL.name
    description: str = UPDATE_TEST_ITEM_TOOL.description
    args_schema: Type[BaseModel] = UpdateTestItemInput

    def __init__(self, config: ReportPortalConfig):
        super().__init__(config)

    def execute(self, item_id: str, status: str, description: str = "Status updated manually"):
        """
        Update test item status.

        Args:
            item_id: ID of the test item to update
            status: New status for the test item (FAILED, PASSED, SKIPPED)
            description: Description for the status update

        Returns:
            dict: Update result

        Raises:
            ToolException: If update fails
        """
        # Validate status values
        valid_statuses = ["FAILED", "PASSED", "SKIPPED"]
        if status.upper() not in valid_statuses:
            raise ToolException(f"Invalid status '{status}'. Must be one of: {', '.join(valid_statuses)}")

        try:
            return self.client.update_test_item(item_id, status.upper(), description)
        except Exception as e:
            logger.error(f"Error updating test item: {str(e)}")
            raise ToolException(f"Error updating test item: {str(e)}")
