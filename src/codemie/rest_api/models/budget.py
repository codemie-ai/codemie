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

from codemie.service.budget.budget_enums import BudgetCategory


class BudgetCreateRequest(BaseModel):
    budget_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Stable identifier, also used as LiteLLM budget_id",
    )
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=500)
    soft_budget: float = Field(ge=0)
    max_budget: float = Field(gt=0)
    budget_duration: str = Field(pattern=r"^\d+[dhm]$", description="e.g. '30d'")
    budget_category: BudgetCategory
    notification_owner_email: Optional[EmailStr] = Field(
        default=None,
        description="Email to notify when the soft limit is reached. May be a group alias.",
    )
    soft_limit_notify_once: bool = Field(
        default=False,
        description="When true the soft-limit notification fires only once per budget edit cycle (no repeats).",
    )
    soft_limit_notification_enabled: bool = Field(
        default=False,
        description="When false, soft-limit notification emails are suppressed when notification_owner_email is set.",
    )


class BudgetUpdateRequest(BaseModel):
    """All fields optional; at least one must be provided."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=500)
    soft_budget: Optional[float] = Field(default=None, ge=0)
    max_budget: Optional[float] = Field(default=None, gt=0)
    budget_duration: Optional[str] = Field(default=None, pattern=r"^\d+[dhm]$")
    budget_category: Optional[BudgetCategory] = None
    notification_owner_email: Optional[EmailStr] = Field(default=None)
    soft_limit_notify_once: bool | None = Field(default=None)
    soft_limit_notification_enabled: bool | None = Field(default=None)


class BudgetResponse(BaseModel):
    budget_id: str
    name: str
    description: Optional[str]
    soft_budget: float
    max_budget: float
    budget_duration: str
    budget_category: BudgetCategory
    budget_reset_at: Optional[str]
    created_by: str
    created_at: datetime
    updated_at: Optional[datetime]
    is_preconfigured: bool = False
    notification_owner_email: Optional[str] = None
    soft_limit_notification_enabled: bool = False
    soft_limit_notify_once: bool = False

    model_config = {"from_attributes": True}


class PaginationInfo(BaseModel):
    total: int
    page: int
    per_page: int


class PaginatedBudgetListResponse(BaseModel):
    data: list[BudgetResponse]
    pagination: PaginationInfo


class BudgetSyncResult(BaseModel):
    """Summary returned by POST /v1/admin/budgets/sync."""

    created: int
    updated: int
    unchanged: int
    deleted: int
    total_in_litellm: int
    budgets: list[BudgetResponse]

    model_config = {"from_attributes": True}


class BudgetAssignmentBackfillResult(BaseModel):
    """Summary returned by POST /v1/admin/budgets/assignments/backfill."""

    imported: int
    skipped_existing: int
    skipped_missing_user: int
    created_budgets: int
    failed: int
    total_in_litellm: int
