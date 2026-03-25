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

"""Pydantic models for analytics API responses.

This module defines all response models for the analytics dashboard API,
matching the OpenAPI specification exactly.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# Field descriptions
RESPONSE_METADATA_DESC = "Response metadata"


class ResponseMetadata(BaseModel):
    """Metadata for analytics responses."""

    timestamp: str = Field(..., description="Response generation timestamp (ISO 8601)")
    data_as_of: str = Field(..., description="Data freshness timestamp (ISO 8601)")
    filters_applied: dict[str, Any] = Field(..., description="Filters applied to query")
    execution_time_ms: float = Field(..., description="Query execution time in milliseconds")


class PaginationMetadata(BaseModel):
    """Pagination information for tabular responses."""

    page: int = Field(..., description="Current page number (zero-indexed)")
    per_page: int = Field(..., description="Items per page")
    total_count: int = Field(..., description="Total number of items")
    has_more: bool = Field(..., description="Whether more pages exist")


class Metric(BaseModel):
    """Individual metric data point."""

    id: str = Field(..., description="Unique metric identifier")
    label: str = Field(..., description="Human-readable metric name")
    type: str = Field(..., description="Metric type (e.g., 'number', 'currency')")
    value: Any = Field(..., description="Metric value")
    format: str | None = Field(None, description="Display format hint")
    description: str | None = Field(None, description="Metric description")
    fixed_timeframe: str | None = Field(None, description="Fixed time window this metric always reflects")
    secondary_metrics: list['Metric'] | None = Field(None, description="Nested secondary metrics for hierarchical data")


class ColumnDefinition(BaseModel):
    """Column definition for tabular data."""

    id: str = Field(..., description="Column identifier")
    label: str = Field(..., description="Column display name")
    type: str = Field(..., description="Data type (string, number, date, etc.)")
    format: str | None = Field(None, description="Format hint for display")
    description: str | None = Field(None, description="Column description")


class SummariesData(BaseModel):
    """Summary metrics data."""

    metrics: list[Metric] = Field(..., description="List of summary metrics")


class SummariesResponse(BaseModel):
    """Response for /analytics/summaries endpoint."""

    data: SummariesData = Field(..., description="Summary metrics data")
    metadata: ResponseMetadata = Field(..., description=RESPONSE_METADATA_DESC)


class KeySpendingItem(BaseModel):
    """Individual virtual key spending with its metrics."""

    key_identifier: str = Field(..., description="Virtual key identifier/alias")
    metrics: list[Metric] = Field(..., description="Spending metrics for this key")


class KeySpendingData(BaseModel):
    """Virtual keys spending data grouped by type (USER vs PROJECT)."""

    user_keys: list[KeySpendingItem] = Field(..., description="USER-scoped virtual keys spending")
    project_keys: list[KeySpendingItem] = Field(..., description="PROJECT-scoped virtual keys spending")


class KeySpendingResponse(BaseModel):
    """Response for /analytics/key_spending endpoint."""

    data: KeySpendingData = Field(..., description="Virtual keys spending data grouped by type")
    metadata: ResponseMetadata = Field(..., description=RESPONSE_METADATA_DESC)


class TabularData(BaseModel):
    """Tabular data with columns, rows, and optional totals."""

    columns: list[ColumnDefinition] = Field(..., description="Column definitions")
    rows: list[dict[str, Any]] = Field(..., description="Data rows")
    totals: dict[str, Any] | None = Field(None, description="Total/summary row")


class TabularResponse(BaseModel):
    """Response for tabular analytics endpoints."""

    data: TabularData = Field(..., description="Tabular data")
    metadata: ResponseMetadata = Field(..., description=RESPONSE_METADATA_DESC)
    pagination: PaginationMetadata | None = Field(None, description="Pagination info")
    fixed_timeframe: str | None = Field(None, description="Fixed time window this response always reflects")


class CliSummaryData(BaseModel):
    """CLI-specific summary metrics."""

    metrics: list[Metric] = Field(..., description="CLI metrics")


class CliSummaryResponse(BaseModel):
    """Response for CLI summary endpoint."""

    data: CliSummaryData = Field(..., description="CLI summary data")
    metadata: ResponseMetadata = Field(..., description=RESPONSE_METADATA_DESC)


class AnalyticsDetailResponse(BaseModel):
    """Response for analytics detail/drilldown endpoints."""

    data: dict[str, Any] = Field(..., description="Detail payload")
    metadata: ResponseMetadata = Field(..., description=RESPONSE_METADATA_DESC)


class UserListItem(BaseModel):
    """Individual user item in users list."""

    id: str = Field(..., description="User ID")
    name: str = Field(..., description="User display name")


class UsersListData(BaseModel):
    """Users list data."""

    users: list[UserListItem] = Field(..., description="List of unique users")
    total_count: int = Field(..., description="Total number of users")


class UsersListResponse(BaseModel):
    """Response for /analytics/users endpoint."""

    data: UsersListData = Field(..., description="Users list data")
    metadata: ResponseMetadata = Field(..., description=RESPONSE_METADATA_DESC)


class ErrorDetail(BaseModel):
    """Error response detail."""

    message: str = Field(..., description="Error message")
    details: str | None = Field(None, description="Detailed error information")
    help: str | None = Field(None, description="Help text for resolving the error")


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: ErrorDetail = Field(..., description="Error information")
