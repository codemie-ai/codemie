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

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from codemie.clients.postgres import get_async_session
from codemie.configs.budget_config import budget_config
from codemie.enterprise.litellm import require_litellm_enabled
from codemie.enterprise.litellm.budget_categories import BudgetCategory
from codemie.rest_api.security.authentication import authenticate, maintainer_access_only
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_service import budget_service

router = APIRouter(
    tags=["Budgets"],
    prefix="/v1/admin/budgets",
    dependencies=[],
)


# ==================== Request/Response Schemas ====================


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


class BudgetUpdateRequest(BaseModel):
    """All fields optional; at least one must be provided."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=500)
    soft_budget: Optional[float] = Field(default=None, ge=0)
    max_budget: Optional[float] = Field(default=None, gt=0)
    budget_duration: Optional[str] = Field(default=None, pattern=r"^\d+[dhm]$")
    budget_category: Optional[BudgetCategory] = None


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

    model_config = {"from_attributes": True}


def _build_budget_response(budget) -> BudgetResponse:
    """Build BudgetResponse and set is_preconfigured from config."""
    preconfigured_ids = frozenset(b.budget_id for b in budget_config.predefined_budgets)
    response = BudgetResponse.model_validate(budget)
    response.is_preconfigured = budget.budget_id in preconfigured_ids
    return response


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


# ==================== Endpoints ====================


@router.post("", response_model=BudgetResponse, status_code=201)
async def create_budget(
    payload: BudgetCreateRequest,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Create a new budget (DB + LiteLLM sync). Super admin only."""
    require_litellm_enabled()
    async with get_async_session() as session:
        budget = await budget_service.create_budget(session, payload, actor_id=user.id, actor_name=user.username)
        await session.commit()
        await session.refresh(budget)
        return _build_budget_response(budget)


@router.get("", response_model=PaginatedBudgetListResponse)
async def list_budgets(
    page: int = Query(0, ge=0),
    per_page: int = Query(20, ge=1, le=100),
    category: Optional[BudgetCategory] = Query(None, description="Filter by budget_category"),
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Paginated list of budgets with optional category filter. Super admin only."""
    require_litellm_enabled()
    async with get_async_session() as session:
        budgets, total = await budget_service.list_budgets(
            session,
            page=page,
            per_page=per_page,
            category=category.value if category is not None else None,
        )
        return PaginatedBudgetListResponse(
            data=[_build_budget_response(b) for b in budgets],
            pagination=PaginationInfo(total=total, page=page, per_page=per_page),
        )


@router.post("/sync", response_model=BudgetSyncResult)
async def sync_budgets(
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Pull all budgets from LiteLLM and upsert into DB. Super admin only."""
    require_litellm_enabled()
    async with get_async_session() as session:
        result = await budget_service.sync_budgets_from_litellm(session, actor_id=user.id)
        return BudgetSyncResult(
            created=result.created,
            updated=result.updated,
            unchanged=result.unchanged,
            deleted=result.deleted,
            total_in_litellm=result.total_in_litellm,
            budgets=[_build_budget_response(b) for b in result.budgets],
        )


@router.post("/assignments/backfill", response_model=BudgetAssignmentBackfillResult)
async def backfill_user_budget_assignments(
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Backfill user budget assignments from existing LiteLLM customers. Super admin only."""
    require_litellm_enabled()
    async with get_async_session() as session:
        return await budget_service.backfill_user_budget_assignments_from_litellm(session, actor_id=user.id)


@router.get("/{budgetId}", response_model=BudgetResponse)
async def get_budget(
    budgetId: str,  # noqa: N803
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Get budget detail. Super admin only."""
    require_litellm_enabled()
    async with get_async_session() as session:
        budget = await budget_service.get_budget(session, budgetId)
        return _build_budget_response(budget)


@router.patch("/{budgetId}", response_model=BudgetResponse)
async def update_budget(
    budgetId: str,  # noqa: N803
    payload: BudgetUpdateRequest,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Update budget (LiteLLM delete+recreate). Super admin only."""
    require_litellm_enabled()
    async with get_async_session() as session:
        budget = await budget_service.update_budget(
            session, budgetId, payload, actor_id=user.id, actor_name=user.username
        )
        await session.commit()
        await session.refresh(budget)
        return _build_budget_response(budget)
