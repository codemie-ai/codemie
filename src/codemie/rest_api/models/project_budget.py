# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from codemie.service.budget.budget_enums import AllocationMode, BudgetCategory

_DURATION_PATTERN = r"^\d+[smhd]$"


# ==================== Request / Response Schemas ====================


class ProjectBudgetCreateRequest(BaseModel):
    budget_id: str = Field(
        min_length=1,
        max_length=95,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Human-readable prefix; a UUID suffix is appended automatically to guarantee uniqueness",
    )
    project_name: str = Field(min_length=1, max_length=100)
    budget_category: BudgetCategory
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=500)
    soft_budget: float = Field(ge=0)
    max_budget: float = Field(gt=0)
    budget_duration: str = Field(
        pattern=_DURATION_PATTERN,
        description="e.g. '30d', '8h', '3600s'",
    )
    allocation_mode: str = Field(default=AllocationMode.EQUAL.value)
    models: Optional[list[str]] = Field(default=None, description="Model allow-list for this budget")


class ProjectBudgetUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=500)
    soft_budget: Optional[float] = Field(default=None, ge=0)
    max_budget: Optional[float] = Field(default=None, gt=0)
    budget_duration: Optional[str] = Field(default=None, pattern=_DURATION_PATTERN)
    models: Optional[list[str]] = Field(default=None, description="Model allow-list for this budget")


class ProjectBudgetMemberAllocationResponse(BaseModel):
    user_id: str
    allocation_mode: str
    allocated_soft_budget: float
    allocated_max_budget: float
    sync_status: Optional[str]
    budget_id: Optional[str] = None

    model_config = {"from_attributes": True}


class ProjectBudgetResponse(BaseModel):
    budget_id: str
    project_name: str
    budget_category: BudgetCategory
    budget_type: str
    name: str
    description: Optional[str]
    soft_budget: float
    max_budget: float
    budget_duration: str
    allocation_mode: str
    is_active: bool
    budget_reset_at: Optional[str]
    member_count: int
    allocated_member_budget_total: float
    provider: Optional[str]
    provider_sync_status: Optional[str]
    provider_last_synced_at: Optional[str]
    created_by: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    member_allocations: list[ProjectBudgetMemberAllocationResponse]

    model_config = {"from_attributes": True}


class PaginatedProjectBudgetListResponse(BaseModel):
    items: list[ProjectBudgetResponse]
    total: int
    page: int
    per_page: int


class RebalanceProjectBudgetRequest(BaseModel):
    apply_immediately: bool = True


class OverrideMemberAllocationRequest(BaseModel):
    allocated_max_budget: float = Field(ge=0)
    allocated_soft_budget: float = Field(ge=0)
    override_reason: Optional[str] = Field(default=None, max_length=500)


class ProjectBudgetMembersResponse(BaseModel):
    data: list[ProjectBudgetMemberAllocationResponse]


# ---- Group request / response schemas ----


class CategoryBudgetSpec(BaseModel):
    pct: float = Field(description="Percentage of total_amount allocated to this category")
    soft_budget: Optional[float] = Field(
        default=None,
        ge=0,
        description="Soft limit as an absolute amount. Defaults to 80%% of the category max when omitted.",
    )


class ProjectBudgetGroupCreateRequest(BaseModel):
    project_name: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    total_amount: float = Field(gt=0, description="Total budget amount distributed across categories")
    budget_duration: str = Field(pattern=_DURATION_PATTERN, description="e.g. '30d', '8h'")
    description: Optional[str] = Field(default=None, max_length=500)
    categories: dict[str, CategoryBudgetSpec] = Field(
        description="Category distribution keyed by BudgetCategory value (platform/cli/premium_models)"
    )
    notification_owner_email: Optional[EmailStr] = Field(
        default=None,
        description="Email notified when any category soft limit is reached. May be a group alias.",
    )
    soft_limit_notify_once: bool = Field(
        default=False,
        description="When true the soft-limit notification fires only once per budget edit cycle.",
    )
    soft_limit_notification_enabled: bool = Field(
        default=False,
        description="When false, soft-limit notification emails are suppressed when notification_owner_email is set.",
    )


class CategoryBudgetSpecUpdate(BaseModel):
    pct: float = Field(
        json_schema_extra={"minimum": 0, "maximum": 100},
        description="Set to 0 to remove this category from the group",
    )
    soft_budget: Optional[float] = Field(default=None, ge=0, description="Soft limit as an absolute amount.")


class ProjectBudgetGroupUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    total_amount: Optional[float] = Field(default=None, gt=0)
    budget_duration: Optional[str] = Field(default=None, pattern=_DURATION_PATTERN)
    description: Optional[str] = Field(default=None, max_length=500)
    categories: Optional[dict[str, CategoryBudgetSpecUpdate]] = Field(default=None)
    notification_owner_email: Optional[EmailStr] = Field(default=None)
    soft_limit_notify_once: bool | None = Field(default=None)
    soft_limit_notification_enabled: bool | None = Field(default=None)


class CategoryBudgetDetailResponse(BaseModel):
    budget_id: str
    category: str
    max_budget: float
    soft_budget: float
    budget_duration: str
    member_count: int
    allocated_member_budget_total: float
    provider_sync_status: Optional[str]
    budget_reset_at: Optional[str]
    created_at: Optional[datetime]

    model_config = {'from_attributes': True}


class ProjectBudgetGroupResponse(BaseModel):
    group_id: str
    project_name: str
    name: str = ""
    budget_duration: str
    total_amount: float
    description: Optional[str] = None
    created_by: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    deleted_at: Optional[datetime]
    categories: list[CategoryBudgetDetailResponse]
    notification_owner_email: Optional[str] = None
    soft_limit_notify_once: bool = False
    soft_limit_notification_enabled: bool = False

    model_config = {'from_attributes': True}


class PaginatedProjectBudgetGroupListResponse(BaseModel):
    items: list[ProjectBudgetGroupResponse]
    total: int
