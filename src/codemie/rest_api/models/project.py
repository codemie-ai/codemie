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

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from codemie.service.budget.budget_enums import BudgetCategory


class ProjectCounters(BaseModel):
    """Resource counters for a project — reusable across different response shapes."""

    assistants_count: int = 0
    workflows_count: int = 0
    integrations_count: int = 0
    datasources_count: int = 0
    skills_count: int = 0


class ProjectSpendingSummary(BaseModel):
    """Compact spending summary for project list responses."""

    current_spending: float
    budget_limit: Optional[float] = None
    total_percent: float


class ProjectAssignedBudgetSummary(BaseModel):
    """Compact assigned project budget summary for project list responses."""

    budget_id: str
    name: str
    budget_category: BudgetCategory
    soft_budget: float
    max_budget: float
    budget_duration: str
    budget_reset_at: Optional[str] = None
    provider_sync_status: Optional[str] = None
    member_count: int
    allocated_member_budget_total: float
    current_spending: Optional[float] = None


class ProjectSpendingDetail(BaseModel):
    """Full spending detail for project detail responses."""

    current_spending: float
    cumulative_spend: float
    budget_reset_at: Optional[datetime] = None
    time_until_reset: Optional[str] = None
    budget_limit: Optional[float] = None
    total: float


class SpendingWidgetColumn(BaseModel):
    id: str
    label: str
    type: str
    format: Optional[str] = None
    description: str = ""


class SpendingWidgetRow(BaseModel):
    budget_id: str
    budget_category: str | None = None
    is_assigned: bool = True
    current_spending: float
    budget_reset_at: Optional[datetime] = None
    time_until_reset: Optional[str] = None
    budget_limit: Optional[float] = None
    total: float


class SpendingWidgetData(BaseModel):
    columns: list[SpendingWidgetColumn]
    rows: list[SpendingWidgetRow]


class ProjectSpendingWidget(BaseModel):
    data: SpendingWidgetData


class ProjectListItem(BaseModel):
    """Project list response item with member counts (Story 16)"""

    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    project_type: str
    created_by: Optional[str] = None
    user_count: int
    admin_count: int
    created_at: Optional[datetime] = None
    counters: Optional[ProjectCounters] = None
    cost_center_id: Optional[UUID] = None
    cost_center_name: Optional[str] = None
    spending: Optional[ProjectSpendingSummary] = None
    budgets: Optional[list[ProjectAssignedBudgetSummary]] = None
    chargeback_enabled: bool = False
    chargeback_attribution: str = "project"


class PaginationInfo(BaseModel):
    """Pagination metadata for list responses"""

    total: int
    page: int
    per_page: int


class PaginatedProjectListResponse(BaseModel):
    """Paginated project list response (Story 16)"""

    data: list[ProjectListItem]
    pagination: PaginationInfo


class SpendTrackingRow(BaseModel):
    """A single project_spend_tracking row returned by the spends endpoint."""

    spend_date: datetime
    daily_spend: float
    cumulative_spend: float
    budget_period_spend: float
    budget_id: Optional[str] = None


class PaginatedSpendsResponse(BaseModel):
    """Paginated response for GET /projects/{projectName}/spends."""

    total_spend: float
    rows: list[SpendTrackingRow]
    pagination: PaginationInfo


class ProjectMember(BaseModel):
    """Project member with role (Story 16)"""

    user_id: str
    is_project_admin: bool
    date: Optional[datetime] = None


class ProjectDetailResponse(BaseModel):
    """Project detail response with member list (Story 16)"""

    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    project_type: str
    created_by: Optional[str] = None
    user_count: int
    admin_count: int
    created_at: Optional[datetime] = None
    cost_center_id: Optional[UUID] = None
    cost_center_name: Optional[str] = None
    enforce_member_spend_limits: bool = False
    chargeback_enabled: bool = False
    chargeback_attribution: str = "project"
    allowed_models: Optional[list[str]] = None
    default_model: Optional[str] = None
    members: list[ProjectMember]
    spending: Optional[ProjectSpendingDetail] = None
    spending_widget: Optional[ProjectSpendingWidget] = None


class ProjectCreateRequest(BaseModel):
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    cost_center_id: Optional[UUID] = None


class ProjectCreateResponse(BaseModel):
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    project_type: str
    created_by: str
    created_at: datetime
    cost_center_id: Optional[UUID] = None
    cost_center_name: Optional[str] = None
    enforce_member_spend_limits: bool = False
    chargeback_enabled: bool = False
    chargeback_attribution: str = "project"
    allowed_models: Optional[list[str]] = None


class ProjectOwnershipUpdateRequest(BaseModel):
    user_id: str


class ProjectOwnershipUpdateResponse(BaseModel):
    name: str
    created_by: str
    message: str


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    clear_display_name: bool = False
    description: Optional[str] = None
    clear_description: bool = False
    cost_center_id: Optional[UUID] = None
    clear_cost_center: bool = False
    enforce_member_spend_limits: Optional[bool] = None
    chargeback_enabled: Optional[bool] = None
    chargeback_attribution: Optional[str] = None

    @model_validator(mode="after")
    def validate_non_empty(self):
        if (
            self.name is None
            and self.display_name is None
            and self.description is None
            and self.cost_center_id is None
            and self.enforce_member_spend_limits is None
            and self.chargeback_enabled is None
            and self.chargeback_attribution is None
            and not self.clear_cost_center
            and not self.clear_display_name
            and not self.clear_description
        ):
            raise ValueError("At least one mutable field must be provided")
        if self.cost_center_id is not None and self.clear_cost_center:
            raise ValueError("Provide either cost_center_id or clear_cost_center")
        if self.display_name is not None and self.clear_display_name:
            raise ValueError("Provide either display_name or clear_display_name")
        if self.description is not None and self.clear_description:
            raise ValueError("Provide either description or clear_description")
        return self


class AllowedModelsUpdateRequest(BaseModel):
    allowed_models: Optional[list[str]] = None
    default_model: Optional[str] = None


class AllowedModelsUpdateResponse(BaseModel):
    name: str
    display_name: Optional[str] = None
    description: str
    project_type: str
    created_by: str
    created_at: datetime
    cost_center_id: Optional[UUID] = None
    cost_center_name: Optional[str] = None
    allowed_models: Optional[list[str]] = None
    default_model: Optional[str] = None


class ProjectAssignmentRequest(BaseModel):
    user_id: str
    is_project_admin: bool


class ProjectAssignmentUpdateRequest(BaseModel):
    is_project_admin: bool


class ProjectAssignmentResponse(BaseModel):
    message: str
    user_id: str
    project_name: str
    is_project_admin: Optional[bool] = None


class BulkAssignmentUserItem(BaseModel):
    """Single user entry in bulk assignment request."""

    user_id: str
    is_project_admin: bool


class BulkAssignmentRequest(BaseModel):
    """Bulk assignment request body - assigns new users and/or updates existing roles."""

    users: list[BulkAssignmentUserItem] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="List of users to assign/update (1-1000)",
    )


class ProjectUpdateResponse(BaseModel):
    """Response after successful project update."""

    name: str
    description: Optional[str] = None
    project_type: str
    message: str


class ProjectDeleteResponse(BaseModel):
    """Response after successful project deletion."""

    message: str
    name: str


class BulkAssignmentResultItem(BaseModel):
    """Per-user result in bulk assignment response."""

    user_id: str
    action: Literal["assigned", "updated", "removed"]
    is_project_admin: Optional[bool] = None


class BulkAssignmentResponse(BaseModel):
    """Response for bulk assignment operations."""

    message: str
    project_name: str
    total: int
    results: list[BulkAssignmentResultItem]


class CsvImportRowResult(BaseModel):
    """Per-row result from a CSV import validation."""

    email: str
    role: str
    error: str | None


class CsvImportValidationResponse(BaseModel):
    """Response for CSV import dry-run validation."""

    users: list[CsvImportRowResult]
