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

from typing import Optional, Dict, Any

import httpx
from langchain_core.tools import ToolException


class ReportPortalClient:
    """Report Portal REST API client for making HTTP requests."""

    def __init__(self, endpoint: str, project: str, api_key: str):
        """
        Initialize the Report Portal client.

        Args:
            endpoint: Report Portal endpoint URL
            project: Report Portal project name
            api_key: API key for authentication
        """
        # Strip endpoint from trailing slash
        self.endpoint = endpoint[:-1] if endpoint.endswith("/") else endpoint
        self.api_key = api_key
        self.project = project
        self.headers = {"Accept": "application/json", "Authorization": f"Bearer {self.api_key}"}

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """
        Make an HTTP request.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            url: Full URL for the request
            **kwargs: Additional arguments for httpx.request

        Returns:
            httpx.Response: HTTP response

        Raises:
            ToolException: If request fails
        """
        try:
            with httpx.Client() as client:
                response = client.request(method=method, url=url, headers=self.headers, **kwargs)
                response.raise_for_status()
                return response
        except httpx.HTTPStatusError as e:
            raise ToolException(f"HTTP {e.response.status_code}: {e.response.text}")
        except httpx.RequestError as e:
            raise ToolException(f"Request failed: {str(e)}")
        except Exception as e:
            raise ToolException(f"Unexpected error: {str(e)}")

    def export_specified_launch(self, launch_id: str, export_format: Optional[str] = None) -> httpx.Response:
        """
        Export launch data in specified format.

        Args:
            launch_id: Launch ID of the launch to export
            export_format: Format of the export (html or pdf)

        Returns:
            httpx.Response: Response with exported content

        Raises:
            ToolException: If export fails
        """
        url = f"{self.endpoint}/api/v1/{self.project}/launch/{launch_id}/report"
        if export_format:
            url += f"?view={export_format}"

        return self._request("GET", url)

    def get_launch_details(self, launch_id: str) -> Dict[str, Any]:
        """
        Get detailed information about a specific launch.

        Args:
            launch_id: Launch ID of the launch to get details for

        Returns:
            dict: Launch details

        Raises:
            ToolException: If retrieval fails
        """
        url = f"{self.endpoint}/api/v1/{self.project}/launch/{launch_id}"
        response = self._request("GET", url)
        return response.json()

    def get_all_launches(
        self,
        page_number: int = 1,
        page_sort: str = "startTime,number,DESC",
        filter_has_composite_attribute: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get all launches with pagination support.

        Args:
            page_number: Number of page to retrieve
            page_sort: Sort order for launches
            filter_has_composite_attribute: Composite attribute filter

        Returns:
            dict: List of launches with pagination info

        Raises:
            ToolException: If retrieval fails
        """
        url = f"{self.endpoint}/api/v1/{self.project}/launch?page.page={page_number}&page.sort={page_sort}"

        if filter_has_composite_attribute:
            url += f"&filter.has.compositeAttribute={filter_has_composite_attribute}"

        response = self._request("GET", url)
        return response.json()

    def find_test_item_by_id(self, item_id: str) -> Dict[str, Any]:
        """
        Find specific test item by ID.

        Args:
            item_id: Item ID of the item to find

        Returns:
            dict: Test item details

        Raises:
            ToolException: If retrieval fails
        """
        url = f"{self.endpoint}/api/v1/{self.project}/item/{item_id}"
        response = self._request("GET", url)
        return response.json()

    def get_test_items_for_launch(self, launch_id: str, page_number: int = 1, status: str = None) -> Dict[str, Any]:
        """
        Get all test items for a specific launch with pagination.

        Args:
            launch_id: Launch ID of the launch to get test items for
            page_number: Number of page to retrieve
            status: Status filter for test items

        Returns:
            dict: List of test items with pagination info

        Raises:
            ToolException: If retrieval fails
        """
        url = f"{self.endpoint}/api/v1/{self.project}/item?filter.eq.launchId={launch_id}&page.page={page_number}"
        if status:
            url = f"{url}&filter.eq.status={status}"

        response = self._request("GET", url)
        return response.json()

    def get_logs_for_test_items(self, item_id: str, page_number: int = 1) -> Dict[str, Any]:
        """
        Get logs for specific test item with pagination.

        Args:
            item_id: Item ID of the item to get logs for
            page_number: Number of page to retrieve

        Returns:
            dict: Test item logs with pagination info

        Raises:
            ToolException: If retrieval fails
        """
        url = f"{self.endpoint}/api/v1/{self.project}/log?filter.eq.item={item_id}&page.page={page_number}"
        response = self._request("GET", url)
        return response.json()

    def get_user_information(self, username: str) -> Dict[str, Any]:
        """
        Get user information by username.

        Args:
            username: Username of the user to get information for

        Returns:
            dict: User information

        Raises:
            ToolException: If retrieval fails
        """
        url = f"{self.endpoint}/api/users/{username}"
        response = self._request("GET", url)
        return response.json()

    def get_dashboard_data(self, dashboard_id: str) -> Dict[str, Any]:
        """
        Get dashboard data by ID.

        Args:
            dashboard_id: Dashboard ID of the dashboard to get data for

        Returns:
            dict: Dashboard data

        Raises:
            ToolException: If retrieval fails
        """
        url = f"{self.endpoint}/api/v1/{self.project}/dashboard/{dashboard_id}"
        response = self._request("GET", url)
        return response.json()

    def update_test_item(
        self, item_id: str, status: str, description: str = "Status updated manually"
    ) -> Dict[str, Any]:
        """
        Update test item status and description.

        Args:
            item_id: ID of the test item to update
            status: New status for the test item
            description: Description for the status update

        Returns:
            dict: Update result

        Raises:
            ToolException: If update fails
        """
        url = f"{self.endpoint}/api/v1/{self.project}/item/{item_id}/update"
        payload = {"status": status, "description": description}
        headers = {**self.headers, "Content-Type": "application/json"}

        try:
            with httpx.Client() as client:
                response = client.request(method="PUT", url=url, json=payload, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            raise ToolException(f"HTTP {e.response.status_code}: {e.response.text}")
        except httpx.RequestError as e:
            raise ToolException(f"Request failed: {str(e)}")
        except Exception as e:
            raise ToolException(f"Unexpected error: {str(e)}")
