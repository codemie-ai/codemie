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

from typing import Optional

from pydantic import BaseModel, Field

from codemie_tools.base.models import CodeMieToolConfig, CredentialTypes, RequiredField

PAGE_NUMBER_DESCRIPTION = "Number of page to retrieve. Pass if page.totalPages > 1"


class ReportPortalConfig(CodeMieToolConfig):
    """Configuration for Report Portal integration."""

    credential_type: CredentialTypes = Field(default=CredentialTypes.REPORT_PORTAL, exclude=True, frozen=True)

    url: str = RequiredField(
        description="Report Portal endpoint URL", json_schema_extra={"placeholder": "https://reportportal.example.com"}
    )
    api_key: str = RequiredField(
        description="Report Portal API key for authentication",
        json_schema_extra={
            "placeholder": "your_api_key",
            "sensitive": True,
            "help": "https://reportportal.io/docs/authorization/ApiTokens",
        },
    )
    project: str = RequiredField(
        description="Report Portal project name", json_schema_extra={"placeholder": "my_project"}
    )


class GetExtendedLaunchDataInput(BaseModel):
    """Input schema for getting extended launch data."""

    launch_id: str = Field(description="Launch ID of the launch to export")


class GetExtendedLaunchDataAsRawInput(BaseModel):
    """Input schema for getting extended launch data as raw."""

    launch_id: str = Field(description="Launch ID of the launch to export")
    format: Optional[str] = Field(default="html", description="Format of the exported data. May be 'pdf' or 'html'")


class GetLaunchDetailsInput(BaseModel):
    """Input schema for getting launch details."""

    launch_id: str = Field(description="Launch ID of the launch to get details for")


class GetAllLaunchesInput(BaseModel):
    """Input schema for getting all launches."""

    page_number: Optional[int] = Field(default=1, description=PAGE_NUMBER_DESCRIPTION)
    page_sort: Optional[str] = Field(
        default="startTime,number,DESC",
        description="Sort order for launches. Defaults to 'startTime,number,DESC' if not specified",
    )
    filter_has_composite_attribute: Optional[str] = Field(default=None, description="Composite attribute filter")


class FindTestItemByIdInput(BaseModel):
    """Input schema for finding test item by ID."""

    item_id: str = Field(description="Item ID of the item to get details for")


class GetTestItemsForLaunchInput(BaseModel):
    """Input schema for getting test items for launch."""

    launch_id: str = Field(description="Launch ID of the launch to get test items for")
    page_number: Optional[int] = Field(default=1, description=PAGE_NUMBER_DESCRIPTION)
    status: Optional[str] = Field(default=None, description="Status of the test item")


class GetLogsForTestItemInput(BaseModel):
    """Input schema for getting logs for test item."""

    item_id: str = Field(description="Item ID of the item to get logs for")
    page_number: Optional[int] = Field(default=1, description=PAGE_NUMBER_DESCRIPTION)


class GetUserInformationInput(BaseModel):
    """Input schema for getting user information."""

    username: str = Field(description="Username of the user to get information for")


class GetDashboardDataInput(BaseModel):
    """Input schema for getting dashboard data."""

    dashboard_id: str = Field(description="Dashboard ID of the dashboard to get data for")


class UpdateTestItemInput(BaseModel):
    """Input schema for updating test item."""

    item_id: str = Field(description="ID of the test item to update")
    status: str = Field(description="New status for the test item. Must be one of: FAILED, PASSED, SKIPPED")
    description: Optional[str] = Field(
        default="Status updated manually", description="Description for the status update"
    )
