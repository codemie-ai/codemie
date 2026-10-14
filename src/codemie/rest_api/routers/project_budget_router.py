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

from typing import Any, NoReturn, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.clients.postgres import get_async_session
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.project_budget_repository import (
    project_budget_assignment_repository,
    project_member_budget_assignment_repository,
)
from codemie.rest_api.models.project_budget import (
    CategoryBudgetDetailResponse,
    OverrideMemberAllocationRequest,
    PaginatedProjectBudgetGroupListResponse,
    PaginatedProjectBudgetListResponse,
    ProjectBudgetCreateRequest,
    ProjectBudgetGroupCreateRequest,
    ProjectBudgetGroupResponse,
    ProjectBudgetGroupUpdateRequest,
    ProjectBudgetMemberAllocationResponse,
    ProjectBudgetMembersResponse,
    ProjectBudgetResponse,
    ProjectBudgetUpdateRequest,
    RebalanceProjectBudgetRequest,
)
from codemie.rest_api.security.authentication import authenticate, maintainer_access_only
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_enums import AllocationMode, BudgetCategory
from codemie.service.budget.budget_models import (
    Budget,
    ProjectBudgetAssignment,
    ProjectBudgetGroup,
    ProjectMemberBudgetAssignment,
)
from codemie.service.budget.project_budget_service import (
    ProjectBudgetGroupFullResult,
    project_budget_service,
)
from codemie.service.settings.settings import SettingsService

_ACCESS_DENIED_MESSAGE = "Access denied"
_ACCESS_DENIED_HELP = "If you believe you should have access, please contact your system administrator."

router = APIRouter(
    tags=["Project Budgets"],
    prefix="/v1/admin/project-budgets",
    dependencies=[],
)


# ==================== Helpers ====================


def _displayed_member_max_budget(
    allocation: ProjectMemberBudgetAssignment, budget: Budget, enforce_limit: bool
) -> float:
    """Return the member max to display: the stored amount when enforced or overridden, else the project max."""
    if enforce_limit or allocation.allocation_mode == AllocationMode.FIXED.value:
        return allocation.allocated_max_budget
    return budget.max_budget


def _build_project_budget_response(
    budget: Budget,
    assignment: ProjectBudgetAssignment | None,
    allocations: list[ProjectMemberBudgetAssignment],
) -> ProjectBudgetResponse:
    provider_meta: dict = budget.provider_metadata or {}
    project_name = assignment.project_name if assignment else ""
    enforce_limit = SettingsService.get_enforce_member_spend_limits(project_name)
    allocated_total = sum(_displayed_member_max_budget(a, budget, enforce_limit) for a in allocations)
    return ProjectBudgetResponse(
        budget_id=budget.budget_id,
        project_name=project_name,
        budget_category=BudgetCategory(budget.budget_category),
        budget_type=budget.budget_type,
        name=budget.name,
        description=budget.description,
        soft_budget=budget.soft_budget,
        max_budget=budget.max_budget,
        budget_duration=budget.budget_duration,
        allocation_mode=assignment.allocation_mode if assignment else AllocationMode.EQUAL.value,
        is_active=budget.is_active,
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
                allocated_max_budget=_displayed_member_max_budget(a, budget, enforce_limit),
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


def _raise_access_denied(details: str) -> NoReturn:
    raise ExtendedHTTPException(
        code=403,
        message=_ACCESS_DENIED_MESSAGE,
        details=details,
        help=_ACCESS_DENIED_HELP,
    )


def _can_read_project_budget(user: User, project_name: str) -> bool:
    if user.is_admin_or_maintainer or getattr(user, "is_auditor", False):
        return True
    return user.is_application_admin(project_name)


_PROJECT_ADMIN_ALLOWED_FIELDS = {"categories", "soft_limit_notification_enabled"}


def _ensure_allowed_budget_group_update_fields(user: User, payload: "ProjectBudgetGroupUpdateRequest") -> None:
    """Project administrators may change category distribution and notification toggling."""
    if user.is_maintainer:
        return
    restricted = sorted(set(ProjectBudgetGroupUpdateRequest.model_fields) - _PROJECT_ADMIN_ALLOWED_FIELDS)
    if any(getattr(payload, field, None) is not None for field in restricted):
        raise ExtendedHTTPException(
            code=403,
            message=_ACCESS_DENIED_MESSAGE,
            details="Project administrators may only update the categories and soft_limit_notification_enabled fields.",
            help="To change other fields, contact a system administrator.",
        )


def _ensure_project_budget_read_access(user: User, project_name: str) -> None:
    if _can_read_project_budget(user, project_name):
        return
    _raise_access_denied(f"You do not have permission to access project budgets for '{project_name}'.")


def _can_manage_project_budget_members(user: User, project_name: str) -> bool:
    """Maintainers and Project Admins of the budget's project may override member allocations."""
    return user.is_maintainer or user.is_application_admin(project_name)


def _ensure_can_manage_project_budget_members(user: User, project_name: str, budget_id: str) -> None:
    if _can_manage_project_budget_members(user, project_name):
        return
    logger.warning(
        f"access_denied_project_budget_members_manage: actor_user_id={user.id}, resource={project_name}, "
        f"budget_id={budget_id}, domain=project_budget"
    )
    _raise_access_denied(f"You do not have permission to manage member budgets for '{project_name}'.")


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
    if not (user.is_admin_or_maintainer or getattr(user, "is_auditor", False)):
        allowed_projects = list(user.admin_project_names or [])
        if not allowed_projects:
            _raise_access_denied(
                "This action requires administrator, maintainer, auditor, or project administrator privileges."
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
        budget, assignment, allocations = await project_budget_service.get_project_budget(session, budget_id)
        _ensure_project_budget_read_access(user, assignment.project_name)
    enforce_limit = SettingsService.get_enforce_member_spend_limits(assignment.project_name)
    return ProjectBudgetMembersResponse(
        data=[
            ProjectBudgetMemberAllocationResponse(
                user_id=a.user_id,
                allocation_mode=a.allocation_mode,
                allocated_soft_budget=a.allocated_soft_budget,
                allocated_max_budget=_displayed_member_max_budget(a, budget, enforce_limit),
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
):
    """Set a fixed member allocation override and rebalance remaining members."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        project_name = await project_budget_service.get_project_budget_project_name(session, budget_id)
        _ensure_can_manage_project_budget_members(user, project_name, budget_id)
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
):
    """Clear a fixed member allocation override and rebalance the category."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        project_name = await project_budget_service.get_project_budget_project_name(session, budget_id)
        _ensure_can_manage_project_budget_members(user, project_name, budget_id)
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


# ==================== Group router ====================


group_router = APIRouter(
    tags=["Project Budget Groups"],
    prefix="/v1/admin/project-budget-groups",
    dependencies=[],
)


# ---- Group helpers ----


def _build_project_budget_group_response(result: ProjectBudgetGroupFullResult) -> ProjectBudgetGroupResponse:
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
    # Derive group-level notification fields from the first category budget (all categories share them).
    first_budget = result.categories[0].budget if result.categories else None
    return ProjectBudgetGroupResponse(
        group_id=result.group.id,
        project_name=result.group.project_name,
        name=result.group.name,
        budget_duration=result.group.budget_duration,
        total_amount=result.total_amount,
        description=result.group.description,
        created_by=result.group.created_by,
        created_at=result.group.created_at,
        updated_at=result.group.updated_at,
        deleted_at=result.group.deleted_at,
        categories=categories,
        notification_owner_email=first_budget.notification_owner_email if first_budget else None,
        soft_limit_notify_once=first_budget.soft_limit_notify_once if first_budget else False,
        soft_limit_notification_enabled=first_budget.soft_limit_notification_enabled if first_budget else False,
    )


def _build_project_budget_group_response_from_group(group: ProjectBudgetGroup) -> ProjectBudgetGroupResponse:
    return ProjectBudgetGroupResponse(
        group_id=group.id,
        project_name=group.project_name,
        name=group.name,
        budget_duration=group.budget_duration,
        total_amount=0.0,
        description=group.description,
        created_by=group.created_by,
        created_at=group.created_at,
        updated_at=group.updated_at,
        deleted_at=group.deleted_at,
        categories=[],
    )


# ---- Group endpoints ----


@group_router.post('', response_model=ProjectBudgetGroupResponse, status_code=201)
async def create_project_budget_group(
    payload: ProjectBudgetGroupCreateRequest,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Create a unified budget group for a project, distributing a total amount across categories."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        result = await project_budget_service.create_project_budget_group(session, payload, actor_id=user.id)
        new_group_id = result.group.id
        await session.commit()
        result = await project_budget_service.get_project_budget_group(session, new_group_id)
    return _build_project_budget_group_response(result)


@group_router.get('', response_model=PaginatedProjectBudgetGroupListResponse)
async def list_project_budget_groups(
    project_name: str = Query(..., min_length=1),
    user: User = Depends(authenticate),
):
    """List all budget groups for a project (includes audit history)."""
    _require_budgeting_enabled()
    _ensure_project_budget_read_access(user, project_name)
    async with get_async_session() as session:
        results = await project_budget_service.list_project_budget_groups(session, project_name)
        items = [_build_project_budget_group_response(result) for result in results]
    return PaginatedProjectBudgetGroupListResponse(items=items, total=len(items))


@group_router.get('/{group_id}', response_model=ProjectBudgetGroupResponse)
async def get_project_budget_group(
    group_id: str,
    user: User = Depends(authenticate),
):
    """Get a budget group with all its category breakdowns."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        result = await project_budget_service.get_project_budget_group(session, group_id)
        try:
            _ensure_project_budget_read_access(user, result.group.project_name)
        except ExtendedHTTPException as e:
            if e.code == 403:
                # Prevent group ID enumeration: return 404 instead of 403
                raise ExtendedHTTPException(code=404, message=f"Project budget group not found: {group_id}")
            raise
    return _build_project_budget_group_response(result)


@group_router.put('/{group_id}', response_model=ProjectBudgetGroupResponse)
async def update_project_budget_group(
    group_id: str,
    payload: ProjectBudgetGroupUpdateRequest,
    user: User = Depends(authenticate),
):
    """Update a budget group in-place (total amount, duration, or category distribution)."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        current = await project_budget_service.get_project_budget_group(session, group_id)
        project_name = current.group.project_name
        if not (user.is_admin_or_maintainer or user.is_application_admin(project_name)):
            _raise_access_denied(f"You do not have permission to update budget groups for '{project_name}'.")
        _ensure_allowed_budget_group_update_fields(user, payload)
        await project_budget_service.update_project_budget_group(
            session, group_id=group_id, data=payload, actor_id=user.id
        )
        await session.commit()
        result = await project_budget_service.get_project_budget_group(session, group_id)
    return _build_project_budget_group_response(result)


@group_router.delete('/{group_id}', status_code=204)
async def delete_project_budget_group(
    group_id: str,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Soft-delete a budget group and all its category budgets."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        await project_budget_service.delete_project_budget_group(session, group_id=group_id, actor_id=user.id)
        await session.commit()


@group_router.post('/{group_id}/reset', response_model=ProjectBudgetGroupResponse)
async def reset_project_budget_group(
    group_id: str,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Reset spend counters for all category budgets in a group."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        await project_budget_service.reset_project_budget_group(session, group_id=group_id, actor_id=user.id)
        await session.commit()
        result = await project_budget_service.get_project_budget_group(session, group_id)
    return _build_project_budget_group_response(result)


@group_router.post('/{group_id}/rebalance', response_model=ProjectBudgetGroupResponse)
async def rebalance_project_budget_group(
    group_id: str,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):
    """Rebalance member allocations for all category budgets in a group."""
    _require_budgeting_enabled()
    async with get_async_session() as session:
        await project_budget_service.rebalance_project_budget_group(session, group_id=group_id, actor_id=user.id)
        await session.commit()
        result = await project_budget_service.get_project_budget_group(session, group_id)
    return _build_project_budget_group_response(result)
