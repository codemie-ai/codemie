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

import asyncio
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Path, Query, Request

from codemie.clients.postgres import get_async_session, get_session
from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.project_budget_repository import project_budget_assignment_repository
from codemie.rest_api.models.project import (
    PaginatedProjectListResponse,
    PaginationInfo,
    ProjectAssignedBudgetSummary,
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectDetailResponse,
    ProjectListItem,
    ProjectMember,
)
from codemie.rest_api.routers.projects._common import (
    _ensure_user_management_enabled,
    _resolve_cost_center_name,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.provider_registry import get_active_provider
from codemie.service.project.project_service import project_service
from codemie.service.project.project_spending_service import (
    attach_project_detail_spending,
    attach_project_spending_summaries,
    can_see_project_spending,
)
from codemie.service.project.project_visibility_service import project_visibility_service
from codemie.service.settings.settings import SettingsService


router = APIRouter()


logger = logging.getLogger(__name__)


def _list_projects_sync(
    user_id: str,
    is_admin: bool,
    search: str | None,
    page: int,
    per_page: int,
    has_assigned_budgets: bool,
    budget_category: str | None,
    include_counters: bool,
    sort_by: str | None,
    sort_order: str,
) -> tuple:
    """Synchronous wrapper for project list — runs in a threadpool from async handlers."""
    with get_session() as session:
        return project_visibility_service.list_visible_projects_paginated(
            session=session,
            user_id=user_id,
            is_admin=is_admin,
            search=search,
            page=page,
            per_page=per_page,
            has_assigned_budgets=has_assigned_budgets,
            budget_category=budget_category,
            include_counters=include_counters,
            sort_by=sort_by,
            sort_order=sort_order,
        )


def _get_project_detail_sync(
    project_name: str,
    user_id: str,
    is_admin: bool,
    action: str,
) -> dict:
    """Synchronous wrapper for project detail — runs in a threadpool from async handlers."""
    with get_session() as session:
        return project_visibility_service.get_visible_project_with_members(
            session=session,
            project_name=project_name,
            user_id=user_id,
            is_admin=is_admin,
            action=action,
        )


def _is_budget_provider_active() -> bool:
    provider = get_active_provider()
    return bool(config.LLM_PROXY_BUDGET_CHECK_ENABLED) and provider.provider_name != "noop"


def _ensure_project_budget_list_supported() -> None:
    """Require active budget enforcement for explicit project-budget filter params."""
    if not _is_budget_provider_active():
        raise ExtendedHTTPException(
            code=400,
            message="Project budget filtering requires budget enforcement provider configuration",
        )


def _budget_category_value(budget_category: BudgetCategory | None) -> str | None:
    return budget_category.value if budget_category is not None else None


async def _attach_assigned_budget_summaries(
    items: list[ProjectListItem],
    budget_category: BudgetCategory | None,
) -> None:
    project_names = [item.name for item in items]
    if not project_names:
        return

    async with get_async_session() as async_session:
        budget_rows_by_project = await project_budget_assignment_repository.get_assigned_budget_summaries_for_projects(
            async_session,
            project_names,
            budget_category=_budget_category_value(budget_category),
        )

    for item in items:
        rows = budget_rows_by_project.get(item.name, [])
        budgets: list[ProjectAssignedBudgetSummary] = []
        for row in rows:
            try:
                category = BudgetCategory(row.budget_category)
            except ValueError:
                logger.warning(
                    f"Skipping assigned budget row with invalid category "
                    f"for project={item.name} budget_id={row.budget_id} category={row.budget_category}"
                )
                continue
            budgets.append(
                ProjectAssignedBudgetSummary(
                    budget_id=row.budget_id,
                    name=row.name,
                    budget_category=category,
                    soft_budget=row.soft_budget,
                    max_budget=row.max_budget,
                    budget_duration=row.budget_duration,
                    budget_reset_at=row.budget_reset_at,
                    provider_sync_status=row.provider_sync_status,
                    member_count=row.member_count,
                    allocated_member_budget_total=row.allocated_member_budget_total,
                    current_spending=row.current_spending,
                )
            )
        item.budgets = budgets


@router.post("/projects", response_model=ProjectCreateResponse, status_code=201)
def create_project(payload: ProjectCreateRequest, user: User = Depends(authenticate)):
    """Create a new shared project."""
    _ensure_user_management_enabled()
    create_kwargs = {
        "user": user,
        "project_name": payload.name,
        "description": payload.description,
    }
    if payload.display_name is not None:
        create_kwargs["display_name"] = payload.display_name
    if payload.cost_center_id is not None:
        create_kwargs["cost_center_id"] = payload.cost_center_id

    project = project_service.create_shared_project(
        **create_kwargs,
    )

    return ProjectCreateResponse(
        name=project.name,
        display_name=project.display_name,
        description=project.description,
        project_type=project.project_type,
        created_by=project.created_by or user.id,
        created_at=project.date,
        cost_center_id=getattr(project, "cost_center_id", None),
        cost_center_name=_resolve_cost_center_name(getattr(project, "cost_center_id", None)),
        enforce_member_spend_limits=SettingsService.get_enforce_member_spend_limits(project.name),
        chargeback_enabled=project.chargeback_enabled,
        chargeback_attribution=project.chargeback_attribution,
    )


@router.get("/projects", response_model=PaginatedProjectListResponse)
async def list_projects(
    search: Optional[str] = Query(
        None,
        description=(
            "Search by display name (falling back to technical name when unset) or "
            "description (substring match, visibility-filtered)"
        ),
    ),
    page: int = Query(0, ge=0, description="Page number (0-indexed)"),
    per_page: int = Query(20, ge=10, le=100, description="Items per page (10-100)"),
    include_counters: bool = Query(
        True,
        description="Include per-project resource counters (assistants, workflows, integrations, datasources, skills)",
    ),
    include_spending: bool = Query(
        False,
        description="Include compact spending summary for manageable projects",
    ),
    include_budgets: bool = Query(
        False,
        description="Include compact assigned budget summaries for project rows",
    ),
    has_assigned_budgets: bool = Query(
        False,
        description="Return only projects with at least one active assigned project budget",
    ),
    budget_category: Optional[BudgetCategory] = Query(
        None,
        description="Filter assigned project budgets by category",
    ),
    sort_by: Optional[Literal["name", "created_at"]] = Query(
        None, description="Sort field; ignored when search is active (relevance ordering takes precedence)"
    ),
    sort_order: Literal["asc", "desc"] = Query("asc", description="Sort direction"),
    user: User = Depends(authenticate),
):
    """List projects visible to current user with pagination and search.

    Story 16: Project Management API - List endpoint with pagination.

    Regular users see only projects they are members of.
    Super admins see all projects (personal + shared).

    Response includes user_count, admin_count, and optional resource counters for each project.
    When include_spending=true, compact spending summaries are added for manageable projects
    (global admins and project admins only).
    """

    _ensure_user_management_enabled()
    if has_assigned_budgets or budget_category is not None:
        _ensure_project_budget_list_supported()

    enriched_projects, total_count = await asyncio.to_thread(
        _list_projects_sync,
        user_id=user.id,
        is_admin=user.is_admin or getattr(user, "is_auditor", False),
        search=search,
        page=page,
        per_page=per_page,
        has_assigned_budgets=has_assigned_budgets,
        budget_category=_budget_category_value(budget_category),
        include_counters=include_counters,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    items = [ProjectListItem(**proj) for proj in enriched_projects]

    if include_budgets and _is_budget_provider_active():
        await _attach_assigned_budget_summaries(items, budget_category)

    if include_spending:
        await attach_project_spending_summaries(items, enriched_projects, user)

    return PaginatedProjectListResponse(
        data=items,
        pagination=PaginationInfo(total=total_count, page=page, per_page=per_page),
    )


@router.get("/projects/{projectName}", response_model=ProjectDetailResponse)
async def get_project_detail(
    request: Request,
    project_name: str = Path(alias="projectName"),
    include_spending: bool = Query(
        False,
        description="Include spending summary and widget breakdown",
    ),
    spending_rows_limit: int = Query(
        50,
        ge=1,
        le=200,
        description="Maximum number of spending widget rows to return",
    ),
    user: User = Depends(authenticate),
):
    """Get project detail with member list if project is visible to current user.

    Story 16: Project Management API - Detail endpoint with member list.

    Returns 404 if project doesn't exist or user doesn't have access (not 403).

    Response includes:
    - Project metadata (name, description, type, creator, timestamps)
    - Member counts (user_count, admin_count)
    - Full member list with roles
    - Optional spending summary and widget breakdown (when include_spending=true,
      visible to global admins and project admins only)
    """

    _ensure_user_management_enabled()

    project_detail = await asyncio.to_thread(
        _get_project_detail_sync,
        project_name=project_name,
        user_id=user.id,
        is_admin=user.is_admin or getattr(user, "is_auditor", False),
        action=f"{request.method} {request.url.path}",
    )

    response = _build_project_detail_response(project_detail, project_name)

    if include_spending and can_see_project_spending(user, project_detail):
        await attach_project_detail_spending(
            response=response,
            project_name=project_name,
            spending_rows_limit=spending_rows_limit,
        )

    return response


def _build_project_detail_response(project_detail: dict, project_name: str) -> ProjectDetailResponse:
    return ProjectDetailResponse(
        name=project_detail["name"],
        display_name=project_detail.get("display_name"),
        description=project_detail["description"],
        project_type=project_detail["project_type"],
        created_by=project_detail["created_by"],
        created_at=project_detail["created_at"],
        user_count=project_detail["user_count"],
        admin_count=project_detail["admin_count"],
        cost_center_id=project_detail.get("cost_center_id"),
        cost_center_name=project_detail.get("cost_center_name"),
        enforce_member_spend_limits=SettingsService.get_enforce_member_spend_limits(project_name),
        chargeback_enabled=project_detail.get("chargeback_enabled", False),
        chargeback_attribution=project_detail.get("chargeback_attribution", "project"),
        allowed_models=project_detail.get("allowed_models"),
        default_model=project_detail.get("default_model"),
        members=[ProjectMember(**m) for m in project_detail["members"]],
    )
