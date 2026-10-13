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

from typing import Optional

from fastapi import APIRouter, Depends, Query

from codemie.clients.postgres import get_async_session
from codemie.configs.budget_config import budget_config
from codemie.enterprise.litellm import require_litellm_enabled
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.rest_api.models.budget import (
    BudgetAssignmentBackfillResult,
    BudgetCreateRequest,
    BudgetResponse,
    BudgetSyncResult,
    BudgetUpdateRequest,
    PaginatedBudgetListResponse,
    PaginationInfo,
)
from codemie.rest_api.security.authentication import (
    admin_or_maintainer_or_auditor_access,
    authenticate,
    maintainer_access_only,
)
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_service import budget_service

router = APIRouter(
    tags=["Budgets"],
    prefix="/v1/admin/budgets",
    dependencies=[],
)


# ==================== Request/Response Schemas ====================


def _build_budget_response(budget) -> BudgetResponse:
    """Build BudgetResponse and set is_preconfigured from config."""
    preconfigured_ids = frozenset(b.budget_id for b in budget_config.predefined_budgets)
    response = BudgetResponse.model_validate(budget)
    response.is_preconfigured = budget.budget_id in preconfigured_ids
    return response


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
    _: None = Depends(admin_or_maintainer_or_auditor_access),
):
    """Paginated list of budgets with optional category filter. Maintainer or auditor."""
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
        return await budget_service.backfill_user_budget_assignments(session, actor_id=user.id)


@router.get("/{budgetId}", response_model=BudgetResponse)
async def get_budget(
    budgetId: str,  # noqa: N803
    user: User = Depends(authenticate),
    _: None = Depends(admin_or_maintainer_or_auditor_access),
):
    """Get budget detail. Maintainer or auditor."""
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
