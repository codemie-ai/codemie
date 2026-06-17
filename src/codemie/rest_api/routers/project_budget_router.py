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
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.clients.postgres import get_async_session
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.project_budget_repository import (
    project_budget_assignment_repository,
    project_member_budget_assignment_repository,
)
from codemie.rest_api.security.authentication import authenticate, maintainer_access_only
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_enums import AllocationMode, BudgetCategory
from codemie.service.budget.budget_models import (
    Budget,
    ProjectBudgetAssignment,
    ProjectBudgetPlan,
    ProjectMemberBudgetAssignment,
)
from codemie.service.budget.project_budget_service import (
    ProjectBudgetPlanFullResult,
    project_budget_service,
)

_DURATION_PATTERN = r"^\d+[smhd]$"

router = APIRouter(
    tags=["Project Budgets"],
    prefix="/v1/admin/project-budgets",
    dependencies=[],
)


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


# ==================== Helpers ====================


def _build_project_budget_response(
    budget: Budget,
    assignment: ProjectBudgetAssignment | None,
    allocations: list[ProjectMemberBudgetAssignment],
) -> ProjectBudgetResponse:
    provider_meta: dict = budget.provider_metadata or {}
    allocated_total = sum(a.allocated_max_budget for a in allocations)
    return ProjectBudgetResponse(
        budget_id=budget.budget_id,
        project_name=assignment.project_name if assignment else "",
        budget_category=BudgetCategory(budget.budget_category),
        budget_type=budget.budget_type,
        name=budget.name,
        description=budget.description,
        soft_budget=budget.soft_budget,
        max_budget=budget.max_budget,
        budget_duration=budget.budget_duration,
        allocation_mode=assignment.allocation_mode if assignment else AllocationMode.EQUAL.value,
        budget_reset_at=budget.budget_reset_at,
        member_count=len(allocations),
        allocated_member_budget_total=allocated_total,
        provider=provider_meta.get("provider"),
        provider_sync_status=provider_meta.get("sync_status"),
        provider_last_synced_at=provider_meta.get("last_synced_at"),
        created_by=budget.created_by,
        created_at=budget.created_at,
        updated_at=budget.updated_at,
        member_allocations=[
            ProjectBudgetMemberAllocationResponse(
                user_id=a.user_id,
                allocation_mode=a.allocation_mode,
                allocated_soft_budget=a.allocated_soft_budget,
                allocated_max_budget=a.allocated_max_budget,
                sync_status=a.sync_status,
                budget_id=_member_budget_id(a),
            )
            for a in allocations
        ],
    )


async def _load_and_build_response(session: AsyncSession, budget: Budget) -> ProjectBudgetResponse:
    """Fetch assignment + allocations for a budget and build the response."""
    assignment = await project_budget_assignment_repository.get_active_by_budget_id(session, budget.budget_id)
    allocations = await project_member_budget_assignment_repository.get_active_by_budget_id(session, budget.budget_id)
    return _build_project_budget_response(budget, assignment, allocations)


def _allocation_value(allocation: ProjectMemberBudgetAssignment | dict[str, Any], key: str) -> Any:
    if isinstance(allocation, dict):
        return allocation.get(key)
    return getattr(allocation, key, None)


def _member_budget_id(allocation: ProjectMemberBudgetAssignment | dict[str, Any]) -> str | None:
    effective_budget_id = _allocation_value(allocation, "effective_budget_id")
    if isinstance(effective_budget_id, str) and effective_budget_id:
        return effective_budget_id

    metadata = _allocation_value(allocation, "provider_metadata") or allocation
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("raw")
    if isinstance(raw, dict):
        raw_budget_id = raw.get("provider_budget_id")
        if isinstance(raw_budget_id, str) and raw_budget_id:
            return raw_budget_id

    direct_budget_id = metadata.get("provider_budget_id")
    if isinstance(direct_budget_id, str) and direct_budget_id:
        return direct_budget_id
    return None


def _can_read_project_budget(user: User, project_name: str) -> bool:
    if user.is_admin_or_maintainer:
        return True
    return project_name in (user.admin_project_names or [])


def _ensure_project_budget_read_access(user: User, project_name: str) -> None:
    if _can_read_project_budget(user, project_name):
        return
    raise ExtendedHTTPException(
        code=403,
        message="Access denied",
        details=f"You do not have permission to access project budgets for '{project_name}'.",
        help="If you believe you should have access, please contact your system administrator.",
    )


# ==================== Endpoints ====================


def _require_budgeting_enabled() -> None:
    """Project budgets are core data; enforcement may be provided by noop."""
    pass


@router.post("", response_model=ProjectBudgetResponse, status_code=201)
async def create_project_budget(
    payload: ProjectBudgetCreateRequest,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Create a project category budget with automatic member allocation and provider sync.

    Super admin only. Creates:
    - A Budget record with budget_type=project
    - A ProjectBudgetAssignment for (project_name, budget_category)
    - ProjectMemberBudgetAssignment rows for all active project members
    - Provider enforcement state (sync_status exposed, no provider-specific IDs)
    """
    _require_budgeting_enabled()
    async with get_async_session() as session:
        budget = await project_budget_service.create_project_budget(
            session,
            payload,
            actor_id=user.id,
            actor_name=user.username,
        )
        await session.commit()
        await session.refresh(budget)
        response = await _load_and_build_response(session, budget)
    return response


@router.get("", response_model=PaginatedProjectBudgetListResponse)
async def list_project_budgets(
    project_name: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    page: int = Query(default=0, ge=0),
    per_page: int = Query(default=20, ge=1, le=100),
    user: User = Depends(authenticate),
):
    """List project budgets with optional filters."""
    _require_budgeting_enabled()
    allowed_projects: list[str] | None = None
    if not user.is_admin_or_maintainer:
        allowed_projects = list(user.admin_project_names or [])
        if not allowed_projects:
            raise ExtendedHTTPException(
                code=403,
                message="Access denied",
                details="This action requires administrator or project administrator privileges.",
                help="If you believe you should have access, please contact your system administrator.",
            )
        if project_name is not None:
            _ensure_project_budget_read_access(user, project_name)

    async with get_async_session() as session:
        budgets, total = await project_budget_service.list_project_budgets(
            session,
            page=page,
            per_page=per_page,
            project_name=project_name,
            category=category,
            allowed_projects=allowed_projects,
        )
        items = [await _load_and_build_response(session, b) for b in budgets]
    return PaginatedProjectBudgetListResponse(items=items, total=total, page=page, per_page=per_page)


@router.get("/{budget_id}", response_model=ProjectBudgetResponse)
async def get_project_budget(
    budget_id: str,
    user: User = Depends(authenticate),
):
    """Get a single project budget by id for an authorized admin or project admin."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        budget, assignment, allocations = await project_budget_service.get_project_budget(session, budget_id)
        if assignment is not None:
            _ensure_project_budget_read_access(user, assignment.project_name)
        response = _build_project_budget_response(budget, assignment, allocations)
    return response


@router.patch("/{budget_id}", response_model=ProjectBudgetResponse)
async def update_project_budget(
    budget_id: str,
    payload: ProjectBudgetUpdateRequest,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Update a project budget (partial update). Syncs changed amounts with the provider."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        budget = await project_budget_service.update_project_budget(
            session,
            budget_id=budget_id,
            data=payload,
            actor_id=user.id,
        )
        await session.commit()
        await session.refresh(budget)
        response = await _load_and_build_response(session, budget)
    return response


@router.delete("/{budget_id}", status_code=204)
async def delete_project_budget(
    budget_id: str,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Delete a project budget. Soft-deletes assignments, hard-deletes the budget row."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        await project_budget_service.delete_project_budget(session, budget_id=budget_id, actor_id=user.id)
        await session.commit()


@router.post("/{budget_id}/reset", response_model=ProjectBudgetResponse)
async def reset_project_budget(
    budget_id: str,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Reset project budget and member enforcement state through the active provider."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        budget = await project_budget_service.reset_project_budget(session, budget_id=budget_id, actor_id=user.id)
        await session.commit()
        await session.refresh(budget)
        response = await _load_and_build_response(session, budget)
    return response


@router.get("/{budget_id}/members", response_model=ProjectBudgetMembersResponse)
async def list_project_budget_members(
    budget_id: str,
    user: User = Depends(authenticate),
):
    """List active member allocations for a project budget when authorized."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        _budget, assignment, allocations = await project_budget_service.get_project_budget(session, budget_id)
        if assignment is not None:
            _ensure_project_budget_read_access(user, assignment.project_name)
    return ProjectBudgetMembersResponse(
        data=[
            ProjectBudgetMemberAllocationResponse(
                user_id=a.user_id,
                allocation_mode=a.allocation_mode,
                allocated_soft_budget=a.allocated_soft_budget,
                allocated_max_budget=a.allocated_max_budget,
                sync_status=a.sync_status,
                budget_id=_member_budget_id(a),
            )
            for a in allocations
        ]
    )


@router.post("/{budget_id}/rebalance", response_model=ProjectBudgetResponse)
async def rebalance_project_budget(
    budget_id: str,
    payload: RebalanceProjectBudgetRequest,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Recalculate project member allocations."""
    _require_budgeting_enabled()
    if not payload.apply_immediately:
        raise ExtendedHTTPException(code=400, message="Only immediate rebalance is currently supported")
    async with get_async_session() as session:
        budget = await project_budget_service.rebalance_project_budget(session, budget_id=budget_id, actor_id=user.id)
        await session.commit()
        await session.refresh(budget)
        response = await _load_and_build_response(session, budget)
    return response


@router.patch("/{budget_id}/members/{user_id}", response_model=ProjectBudgetResponse)
async def override_member_allocation(
    budget_id: str,
    user_id: str,
    payload: OverrideMemberAllocationRequest,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Set a fixed member allocation override and rebalance remaining members."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        await project_budget_service.override_member_allocation(
            session,
            budget_id=budget_id,
            user_id=user_id,
            allocated_max_budget=payload.allocated_max_budget,
            allocated_soft_budget=payload.allocated_soft_budget,
            override_reason=payload.override_reason,
            actor_id=user.id,
        )
        await session.commit()
        budget, assignment, allocations = await project_budget_service.get_project_budget(session, budget_id)
        response = _build_project_budget_response(budget, assignment, allocations)
    return response


@router.delete("/{budget_id}/members/{user_id}/override", response_model=ProjectBudgetResponse)
async def clear_member_override(
    budget_id: str,
    user_id: str,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Clear a fixed member allocation override and rebalance the category."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        await project_budget_service.clear_member_override(
            session,
            budget_id=budget_id,
            user_id=user_id,
            actor_id=user.id,
        )
        await session.commit()
        budget, assignment, allocations = await project_budget_service.get_project_budget(session, budget_id)
        response = _build_project_budget_response(budget, assignment, allocations)
    return response


# ==================== Plan router ====================


plan_router = APIRouter(
    tags=["Project Budget Plans"],
    prefix="/v1/admin/project-budget-plans",
    dependencies=[],
)


# ---- Plan request / response schemas ----


class CategoryBudgetSpec(BaseModel):
    pct: float = Field(gt=0, le=100, description="Percentage of total_amount allocated to this category")
    soft_budget: Optional[float] = Field(
        default=None,
        ge=0,
        description="Soft limit as an absolute amount. Defaults to 80%% of the category max when omitted.",
    )


class ProjectBudgetPlanCreateRequest(BaseModel):
    project_name: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    total_amount: float = Field(gt=0, description="Total budget amount distributed across categories")
    budget_duration: str = Field(pattern=_DURATION_PATTERN, description="e.g. '30d', '8h'")
    description: str = Field(min_length=1, max_length=500)
    categories: dict[str, CategoryBudgetSpec] = Field(
        description="Category distribution keyed by BudgetCategory value (platform/cli/premium_models)"
    )


class CategoryBudgetSpecUpdate(BaseModel):
    pct: float = Field(ge=0, le=100, description="Set to 0 to remove this category from the plan")
    soft_budget: Optional[float] = Field(default=None, ge=0, description="Soft limit as an absolute amount.")


class ProjectBudgetPlanUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    total_amount: Optional[float] = Field(default=None, gt=0)
    budget_duration: Optional[str] = Field(default=None, pattern=_DURATION_PATTERN)
    description: Optional[str] = Field(default=None, max_length=500)
    categories: Optional[dict[str, CategoryBudgetSpecUpdate]] = Field(default=None)


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


class ProjectBudgetPlanResponse(BaseModel):
    plan_id: str
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

    model_config = {'from_attributes': True}


class PaginatedPlanListResponse(BaseModel):
    items: list[ProjectBudgetPlanResponse]
    total: int


# ---- Plan helpers ----


def _build_plan_response(result: ProjectBudgetPlanFullResult) -> ProjectBudgetPlanResponse:
    categories = []
    for cat in result.categories:
        provider_meta = cat.budget.provider_metadata or {}
        categories.append(
            CategoryBudgetDetailResponse(
                budget_id=cat.budget.budget_id,
                category=cat.budget.budget_category,
                max_budget=cat.budget.max_budget,
                soft_budget=cat.budget.soft_budget,
                budget_duration=cat.budget.budget_duration,
                member_count=len(cat.allocations),
                allocated_member_budget_total=sum(a.allocated_max_budget for a in cat.allocations),
                provider_sync_status=provider_meta.get('sync_status'),
                budget_reset_at=cat.budget.budget_reset_at,
                created_at=cat.budget.created_at,
            )
        )
    return ProjectBudgetPlanResponse(
        plan_id=result.plan.id,
        project_name=result.plan.project_name,
        name=result.plan.name,
        budget_duration=result.plan.budget_duration,
        total_amount=result.total_amount,
        description=result.plan.description,
        created_by=result.plan.created_by,
        created_at=result.plan.created_at,
        updated_at=result.plan.updated_at,
        deleted_at=result.plan.deleted_at,
        categories=categories,
    )


def _build_plan_response_from_plan(plan: ProjectBudgetPlan) -> ProjectBudgetPlanResponse:
    return ProjectBudgetPlanResponse(
        plan_id=plan.id,
        project_name=plan.project_name,
        name=plan.name,
        budget_duration=plan.budget_duration,
        total_amount=0.0,
        description=plan.description,
        created_by=plan.created_by,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        deleted_at=plan.deleted_at,
        categories=[],
    )


# ---- Plan endpoints ----


@plan_router.post('', response_model=ProjectBudgetPlanResponse, status_code=201)
async def create_project_budget_plan(
    payload: ProjectBudgetPlanCreateRequest,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Create a unified budget plan for a project, distributing a total amount across categories."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        result = await project_budget_service.create_project_budget_plan(session, payload, actor_id=user.id)
        new_plan_id = result.plan.id
        await session.commit()
        result = await project_budget_service.get_project_budget_plan(session, new_plan_id)
    return _build_plan_response(result)


@plan_router.get('', response_model=PaginatedPlanListResponse)
async def list_project_budget_plans(
    project_name: str = Query(..., min_length=1),
    user: User = Depends(authenticate),
):
    """List all budget plans for a project (includes audit history)."""
    _require_budgeting_enabled()
    _ensure_project_budget_read_access(user, project_name)
    async with get_async_session() as session:
        plans = await project_budget_service.list_project_budget_plans(session, project_name)
    items = [_build_plan_response_from_plan(p) for p in plans]
    return PaginatedPlanListResponse(items=items, total=len(items))


@plan_router.get('/{plan_id}', response_model=ProjectBudgetPlanResponse)
async def get_project_budget_plan(
    plan_id: str,
    user: User = Depends(authenticate),
):
    """Get a budget plan with all its category breakdowns."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        result = await project_budget_service.get_project_budget_plan(session, plan_id)
        _ensure_project_budget_read_access(user, result.plan.project_name)
    return _build_plan_response(result)


@plan_router.put('/{plan_id}', response_model=ProjectBudgetPlanResponse)
async def update_project_budget_plan(
    plan_id: str,
    payload: ProjectBudgetPlanUpdateRequest,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Update a budget plan in-place (total amount, duration, or category distribution)."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        await project_budget_service.update_project_budget_plan(
            session, plan_id=plan_id, data=payload, actor_id=user.id
        )
        await session.commit()
        result = await project_budget_service.get_project_budget_plan(session, plan_id)
    return _build_plan_response(result)


@plan_router.delete('/{plan_id}', status_code=204)
async def delete_project_budget_plan(
    plan_id: str,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Soft-delete a budget plan and all its category budgets."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        await project_budget_service.delete_project_budget_plan(session, plan_id=plan_id, actor_id=user.id)
        await session.commit()


@plan_router.post('/{plan_id}/reset', response_model=ProjectBudgetPlanResponse)
async def reset_project_budget_plan(
    plan_id: str,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Reset spend counters for all category budgets in a plan."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        await project_budget_service.reset_project_budget_plan(session, plan_id=plan_id, actor_id=user.id)
        await session.commit()
        result = await project_budget_service.get_project_budget_plan(session, plan_id)
    return _build_plan_response(result)


@plan_router.post('/{plan_id}/rebalance', response_model=ProjectBudgetPlanResponse)
async def rebalance_project_budget_plan(
    plan_id: str,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Rebalance member allocations for all category budgets in a plan."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        await project_budget_service.rebalance_project_budget_plan(session, plan_id=plan_id, actor_id=user.id)
        await session.commit()
        result = await project_budget_service.get_project_budget_plan(session, plan_id)
    return _build_plan_response(result)
