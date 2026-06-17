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
from datetime import UTC, datetime, timezone
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, UploadFile
from pydantic import BaseModel, Field, model_validator

from codemie.clients.postgres import get_async_session, get_session
from codemie.configs import config
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.application_repository import application_repository
from codemie.repository.budget_repository import budget_repository
from codemie.repository.cost_center_repository import cost_center_repository
from codemie.repository.project_budget_repository import project_budget_assignment_repository
from codemie.repository.project_spend_tracking_repository import ProjectSpendTrackingRepository
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.budget_models import Budget
from codemie.service.budget.provider_registry import get_active_provider
from codemie.service.project.project_service import project_service
from codemie.service.project.project_visibility_service import project_visibility_service
from codemie.service.settings.settings import SettingsService


router = APIRouter(
    tags=["Projects"],
    prefix="/v1",
    dependencies=[],
)

_spend_repo = ProjectSpendTrackingRepository()


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


_WIDGET_COLUMNS = [
    SpendingWidgetColumn(id="budget_id", label="Budget", type="string", format=None),
    SpendingWidgetColumn(
        id="current_spending",
        label="Budget Period Spend ($)",
        type="number",
        format="currency",
        description="Total amount spent in current budget period",
    ),
    SpendingWidgetColumn(
        id="budget_reset_at",
        label="Budget Reset Date",
        type="string",
        format="timestamp",
        description="Timestamp when budget will reset",
    ),
    SpendingWidgetColumn(id="time_until_reset", label="Time Until Reset", type="string"),
    SpendingWidgetColumn(
        id="budget_limit",
        label="Budget Limit ($)",
        type="number",
        format="currency",
        description="Soft budget limit (warning threshold)",
    ),
    SpendingWidgetColumn(id="total", label="Total", type="number", format="percentage"),
]


class ProjectListItem(BaseModel):
    """Project list response item with member counts (Story 16)"""

    name: str
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


class PaginationInfo(BaseModel):
    """Pagination metadata for list responses"""

    total: int
    page: int
    per_page: int


class PaginatedProjectListResponse(BaseModel):
    """Paginated project list response (Story 16)"""

    data: list[ProjectListItem]
    pagination: PaginationInfo


class ProjectMember(BaseModel):
    """Project member with role (Story 16)"""

    user_id: str
    is_project_admin: bool
    date: Optional[datetime] = None


class ProjectDetailResponse(BaseModel):
    """Project detail response with member list (Story 16)"""

    name: str
    description: Optional[str] = None
    project_type: str
    created_by: Optional[str] = None
    user_count: int
    admin_count: int
    created_at: Optional[datetime] = None
    cost_center_id: Optional[UUID] = None
    cost_center_name: Optional[str] = None
    enforce_member_spend_limits: bool = False
    members: list[ProjectMember]
    spending: Optional[ProjectSpendingDetail] = None
    spending_widget: Optional[ProjectSpendingWidget] = None


class ProjectCreateRequest(BaseModel):
    name: str
    description: str = Field(description="Project description")
    cost_center_id: Optional[UUID] = None


class ProjectCreateResponse(BaseModel):
    name: str
    description: str
    project_type: str
    created_by: str
    created_at: datetime
    cost_center_id: Optional[UUID] = None
    cost_center_name: Optional[str] = None
    enforce_member_spend_limits: bool = False


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cost_center_id: Optional[UUID] = None
    clear_cost_center: bool = False
    enforce_member_spend_limits: Optional[bool] = None

    @model_validator(mode="after")
    def validate_non_empty(self):
        if (
            self.name is None
            and self.description is None
            and self.cost_center_id is None
            and self.enforce_member_spend_limits is None
            and not self.clear_cost_center
        ):
            raise ValueError("At least one mutable field must be provided")
        if self.cost_center_id is not None and self.clear_cost_center:
            raise ValueError("Provide either cost_center_id or clear_cost_center")
        return self


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


logger = logging.getLogger(__name__)


def _resolve_cost_center_name(cost_center_id: UUID | None) -> str | None:
    if cost_center_id is None:
        return None

    with get_session() as session:
        cost_center = cost_center_repository.get_by_id(session, cost_center_id)
        return cost_center.name if cost_center else None


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


def _format_time_until_reset(budget_reset_at: datetime | None) -> str | None:
    """Return a human-readable string for time remaining until the next budget reset."""
    if budget_reset_at is None:
        return None
    now = datetime.now(timezone.utc)
    if budget_reset_at.tzinfo is None:
        budget_reset_at = budget_reset_at.replace(tzinfo=timezone.utc)
    delta = budget_reset_at - now
    if delta.total_seconds() <= 0:
        return "0 days"
    days = delta.days
    hours = delta.seconds // 3600
    if days > 0:
        return f"{days} day{'s' if days != 1 else ''}"
    return f"{hours} hour{'s' if hours != 1 else ''}"


def _parse_budget_reset_at(value: str | None) -> datetime | None:
    """Parse an ISO 8601 budget_reset_at string from the budgets table to datetime."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _budget_info(budgets_by_id: dict[str, Budget], budget_id: str | None) -> tuple[float | None, datetime | None]:
    """Return (max_budget, budget_reset_at) for a given budget_id, or (None, None)."""
    if budget_id is None:
        return None, None
    budget = budgets_by_id.get(budget_id)
    if budget is None:
        return None, None
    return budget.max_budget, _parse_budget_reset_at(budget.budget_reset_at)


def _build_widget_rows(
    budget_rows: list,
    project_name: str,
    budgets_by_id: dict[str, Budget] | None = None,
) -> list[SpendingWidgetRow]:
    """Build spending widget rows from budget-based tracking rows."""
    if budgets_by_id is None:
        budgets_by_id = {}
    rows = []
    for row in budget_rows:
        budget_limit, budget_reset_at = _budget_info(budgets_by_id, row.budget_id)
        current = float(row.budget_period_spend)
        total_pct = round((current / budget_limit * 100) if budget_limit else 0.0, 2)
        rows.append(
            SpendingWidgetRow(
                budget_id=row.budget_id if row.budget_id else "Default",
                current_spending=current,
                budget_reset_at=budget_reset_at,
                time_until_reset=_format_time_until_reset(budget_reset_at),
                budget_limit=budget_limit,
                total=total_pct,
            )
        )
    return rows


def _key_row_to_widget(row, budgets_by_id: dict[str, Budget] | None = None) -> SpendingWidgetRow:
    """Build a spending widget row from a key-based tracking row."""
    if budgets_by_id is None:
        budgets_by_id = {}
    budget_limit, budget_reset_at = _budget_info(budgets_by_id, row.budget_id)
    current = float(row.budget_period_spend)
    total_pct = round((current / budget_limit * 100) if budget_limit else 0.0, 2)
    return SpendingWidgetRow(
        budget_id="Key",
        current_spending=current,
        budget_reset_at=budget_reset_at,
        time_until_reset=_format_time_until_reset(budget_reset_at),
        budget_limit=budget_limit,
        total=total_pct,
    )


def _ensure_project_budget_list_supported() -> None:
    """Require active budget enforcement for explicit project-budget list params."""
    provider = get_active_provider()
    if not config.LLM_PROXY_BUDGET_CHECK_ENABLED or provider.provider_name == "noop":
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


def _manageable_project_names(enriched_projects: list[dict], user: User) -> list[str]:
    return [
        proj["name"]
        for proj in enriched_projects
        if user.is_admin or proj.get("is_project_admin") or proj.get("project_type") == Application.ProjectType.PERSONAL
    ]


def _latest_key_rows_by_project(key_rows: list[object]) -> dict[str, object]:
    latest_key_by_project: dict[str, object] = {}
    for row in key_rows:
        existing = latest_key_by_project.get(row.project_name)
        if existing is None or row.spend_date > existing.spend_date:
            latest_key_by_project[row.project_name] = row
    return latest_key_by_project


def _budget_rows_grouped_by_project(budget_rows: list[object]) -> dict[str, list[object]]:
    latest_budget_by_project_and_id: dict[tuple[str, str | None], object] = {}
    for row in budget_rows:
        key = (row.project_name, row.budget_id)
        existing = latest_budget_by_project_and_id.get(key)
        if existing is None or row.spend_date > existing.spend_date:
            latest_budget_by_project_and_id[key] = row

    budget_rows_by_project: dict[str, list[object]] = {}
    for row in latest_budget_by_project_and_id.values():
        budget_rows_by_project.setdefault(row.project_name, []).append(row)
    return budget_rows_by_project


def _project_spending_summary(
    key_row: object | None,
    budget_rows: list[object],
    budgets_map: dict[str, Budget],
) -> ProjectSpendingSummary | None:
    if key_row is None and not budget_rows:
        return None

    current = (float(key_row.budget_period_spend) if key_row is not None else 0.0) + sum(
        float(row.budget_period_spend) for row in budget_rows
    )
    meta_row = key_row if key_row is not None else budget_rows[0]
    meta_budget = budgets_map.get(meta_row.budget_id) if meta_row.budget_id else None
    budget_limit = float(meta_budget.max_budget) if meta_budget is not None else None
    total_pct = (current / budget_limit * 100) if budget_limit else 0.0

    return ProjectSpendingSummary(
        current_spending=round(current, 2),
        budget_limit=round(budget_limit, 2) if budget_limit is not None else None,
        total_percent=round(total_pct, 2),
    )


async def _attach_project_spending_summaries(
    items: list[ProjectListItem],
    enriched_projects: list[dict],
    user: User,
) -> None:
    manageable_names = _manageable_project_names(enriched_projects, user)
    if not manageable_names:
        return

    async with get_async_session() as async_session:
        key_rows = await _spend_repo.get_latest_spending_by_project(
            async_session, manageable_names, spend_subject_type="key"
        )
        budget_rows = await _spend_repo.get_latest_spending_by_project(
            async_session, manageable_names, spend_subject_type="budget"
        )
        budgets_map = await budget_repository.get_all_keyed_by_id(async_session)

    latest_key_by_project = _latest_key_rows_by_project(key_rows)
    budget_rows_by_project = _budget_rows_grouped_by_project(budget_rows)
    for item in items:
        item.spending = _project_spending_summary(
            key_row=latest_key_by_project.get(item.name),
            budget_rows=budget_rows_by_project.get(item.name, []),
            budgets_map=budgets_map,
        )


@router.post("/projects", response_model=ProjectCreateResponse, status_code=201)
def create_project(payload: ProjectCreateRequest, user: User = Depends(authenticate)):
    """Create a new shared project."""
    _ensure_user_management_enabled()
    create_kwargs = {
        "user": user,
        "project_name": payload.name,
        "description": payload.description,
    }
    if payload.cost_center_id is not None:
        create_kwargs["cost_center_id"] = payload.cost_center_id

    project = project_service.create_shared_project(
        **create_kwargs,
    )

    return ProjectCreateResponse(
        name=project.name,
        description=project.description or payload.description,
        project_type=project.project_type,
        created_by=project.created_by or user.id,
        created_at=project.date,
        cost_center_id=getattr(project, "cost_center_id", None),
        cost_center_name=_resolve_cost_center_name(getattr(project, "cost_center_id", None)),
        enforce_member_spend_limits=SettingsService.get_enforce_member_spend_limits(project.name),
    )


@router.get("/projects", response_model=PaginatedProjectListResponse)
async def list_projects(
    search: Optional[str] = Query(
        None, description="Search by project name or description (substring match, visibility-filtered)"
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
    if include_budgets or has_assigned_budgets or budget_category is not None:
        _ensure_project_budget_list_supported()

    enriched_projects, total_count = await asyncio.to_thread(
        _list_projects_sync,
        user_id=user.id,
        is_admin=user.is_admin,
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

    if include_budgets:
        await _attach_assigned_budget_summaries(items, budget_category)

    if include_spending:
        await _attach_project_spending_summaries(items, enriched_projects, user)

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
        is_admin=user.is_admin,
        action=f"{request.method} {request.url.path}",
    )

    response = _build_project_detail_response(project_detail, project_name)

    if include_spending and _can_see_project_spending(user, project_detail):
        await _attach_project_detail_spending(
            response=response,
            project_name=project_name,
            spending_rows_limit=spending_rows_limit,
        )

    return response


def _build_project_detail_response(project_detail: dict, project_name: str) -> ProjectDetailResponse:
    return ProjectDetailResponse(
        name=project_detail["name"],
        description=project_detail["description"],
        project_type=project_detail["project_type"],
        created_by=project_detail["created_by"],
        created_at=project_detail["created_at"],
        user_count=project_detail["user_count"],
        admin_count=project_detail["admin_count"],
        cost_center_id=project_detail.get("cost_center_id"),
        cost_center_name=project_detail.get("cost_center_name"),
        enforce_member_spend_limits=SettingsService.get_enforce_member_spend_limits(project_name),
        members=[ProjectMember(**m) for m in project_detail["members"]],
    )


def _can_see_project_spending(user: User, project_detail: dict) -> bool:
    is_personal = project_detail.get("project_type") == Application.ProjectType.PERSONAL
    is_project_admin = bool(project_detail.get("is_project_admin"))
    return user.is_admin or is_project_admin or is_personal


async def _attach_project_detail_spending(
    *,
    response: ProjectDetailResponse,
    project_name: str,
    spending_rows_limit: int,
) -> None:
    async with get_async_session() as async_session:
        key_row = await _spend_repo.get_latest_key_spending_for_project(async_session, project_name)
        budget_rows = await _spend_repo.get_latest_budget_rows_for_project(
            async_session, project_name, rows_limit=spending_rows_limit
        )
        budgets_map = await budget_repository.get_all_keyed_by_id(async_session)

    _populate_project_spending_summary(
        response=response,
        key_row=key_row,
        budget_rows=budget_rows,
        budgets_map=budgets_map,
    )
    _populate_project_spending_widget(
        response=response,
        project_name=project_name,
        key_row=key_row,
        budget_rows=budget_rows,
        budgets_map=budgets_map,
    )


def _populate_project_spending_summary(
    *,
    response: ProjectDetailResponse,
    key_row,
    budget_rows: list,
    budgets_map: dict,
) -> None:
    if key_row is None and not budget_rows:
        return

    current = (float(key_row.budget_period_spend) if key_row is not None else 0.0) + sum(
        float(row.budget_period_spend) for row in budget_rows
    )
    cumulative = (float(key_row.cumulative_spend) if key_row is not None else 0.0) + sum(
        float(row.cumulative_spend) for row in budget_rows
    )
    meta_row = key_row if key_row is not None else budget_rows[0]
    meta_budget = budgets_map.get(meta_row.budget_id) if meta_row and meta_row.budget_id else None
    budget_limit = float(meta_budget.max_budget) if meta_budget is not None else None
    budget_reset_at = _parse_budget_reset_at(meta_budget.budget_reset_at) if meta_budget else None
    total_pct = (current / budget_limit * 100) if budget_limit else 0.0
    response.spending = ProjectSpendingDetail(
        current_spending=round(current, 2),
        cumulative_spend=round(cumulative, 2),
        budget_reset_at=budget_reset_at,
        time_until_reset=_format_time_until_reset(budget_reset_at),
        budget_limit=round(budget_limit, 2) if budget_limit is not None else None,
        total=round(total_pct, 2),
    )


def _populate_project_spending_widget(
    *,
    response: ProjectDetailResponse,
    project_name: str,
    key_row,
    budget_rows: list,
    budgets_map: dict,
) -> None:
    widget_rows = _build_widget_rows(budget_rows, project_name, budgets_by_id=budgets_map)
    if key_row is not None:
        widget_rows.append(_key_row_to_widget(key_row, budgets_by_id=budgets_map))
    if widget_rows:
        response.spending_widget = ProjectSpendingWidget(
            data=SpendingWidgetData(columns=_WIDGET_COLUMNS, rows=widget_rows)
        )


@router.patch("/projects/{projectName}", response_model=ProjectCreateResponse)
def update_project(
    payload: ProjectUpdateRequest,
    project_name: str = Path(alias="projectName"),
    user: User = Depends(authenticate),
):
    _ensure_user_management_enabled()

    project = project_service.update_project(
        user=user,
        project_name=project_name,
        name=payload.name,
        description=payload.description,
        cost_center_id=None if payload.clear_cost_center else payload.cost_center_id,
        clear_cost_center=payload.clear_cost_center,
        enforce_member_spend_limits=payload.enforce_member_spend_limits,
    )

    return ProjectCreateResponse(
        name=project.name,
        description=project.description or "",
        project_type=project.project_type,
        created_by=project.created_by or user.id,
        created_at=project.date or datetime.now(UTC),
        cost_center_id=getattr(project, "cost_center_id", None),
        cost_center_name=_resolve_cost_center_name(getattr(project, "cost_center_id", None)),
        enforce_member_spend_limits=SettingsService.get_enforce_member_spend_limits(project.name),
    )


def _authorize_project_access(
    request: Request,
    project_name: str = Path(alias="projectName"),
    user: User = Depends(authenticate),
) -> Application:
    """Authorize project-level write access for project admins and super admins.

    Returns the project if the user has WRITE permission (owner, project admin, or super admin).
    Returns 404 instead of 403 to avoid revealing project existence to unauthorized users.
    """
    _ensure_user_management_enabled()

    action = f"{request.method} {request.url.path}"
    with get_session() as session:
        project = application_repository.get_by_name(session, project_name)

    # Treat deleted projects as non-existent
    if not project or project.deleted_at is not None:
        _raise_project_not_found(user_id=user.id, project_name=project_name, action=action)

    # Use Ability — returns 404 (not 403) to avoid revealing project existence
    if not Ability(user).can(Action.WRITE, project):
        _raise_project_not_found(user_id=user.id, project_name=project_name, action=action)

    return project


@router.delete("/projects/{projectName}", response_model=ProjectDeleteResponse)
def delete_project(
    request: Request,
    project_name: str = Path(alias="projectName"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    """Hard-delete a project if it has no assigned resources.

    Returns 409 if the project has any assistants, workflows, skills,
    datasources, or integrations assigned.
    Returns 403 if the project is a personal project.
    """
    with get_session() as session:
        project_service.delete_project(
            session=session,
            project_name=authorized_project.name,
            project_type=authorized_project.project_type,
            actor_id=request.state.user.id,
            action=f"{request.method} {request.url.path}",
            creator_id=authorized_project.created_by,
        )
        session.commit()

    return ProjectDeleteResponse(
        message=f"Project '{project_name}' deleted successfully",
        name=project_name,
    )


@router.post("/projects/{projectName}/assignment", response_model=ProjectAssignmentResponse)
def assign_user_to_project(
    request: Request,
    payload: ProjectAssignmentRequest,
    project_name: str = Path(alias="projectName"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    """Assign a user to project if requester is project admin or super admin."""
    from codemie.service.project.project_assignment_service import project_assignment_service

    with get_session() as session:
        result = project_assignment_service.assign_user_to_project(
            session=session,
            project=authorized_project,
            user_id=payload.user_id,
            project_name=project_name,
            is_project_admin=payload.is_project_admin,
            actor=request.state.user,
            action=f"{request.method} {request.url.path}",
        )
        session.commit()

    return ProjectAssignmentResponse(**result)


@router.put("/projects/{projectName}/assignment/{userId}", response_model=ProjectAssignmentResponse)
def update_user_project_assignment(
    request: Request,
    payload: ProjectAssignmentUpdateRequest,
    project_name: str = Path(alias="projectName"),
    user_id: str = Path(alias="userId"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    """Update user's project-admin flag for a visible project."""
    from codemie.service.project.project_assignment_service import project_assignment_service

    with get_session() as session:
        result = project_assignment_service.update_user_project_role(
            session=session,
            project=authorized_project,
            user_id=user_id,
            project_name=project_name,
            is_project_admin=payload.is_project_admin,
            actor=request.state.user,
            action=f"{request.method} {request.url.path}",
        )
        session.commit()

    return ProjectAssignmentResponse(**result)


@router.delete("/projects/{projectName}/assignment/{userId}", response_model=ProjectAssignmentResponse)
def remove_user_from_project(
    request: Request,
    project_name: str = Path(alias="projectName"),
    user_id: str = Path(alias="userId"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    """Remove user from project if requester is project admin or super admin."""
    from codemie.service.project.project_assignment_service import project_assignment_service

    with get_session() as session:
        result = project_assignment_service.remove_user_from_project(
            session=session,
            project=authorized_project,
            user_id=user_id,
            project_name=project_name,
            actor=request.state.user,
            action=f"{request.method} {request.url.path}",
        )
        session.commit()

    return ProjectAssignmentResponse(**result)


@router.post("/projects/{projectName}/assignments", response_model=BulkAssignmentResponse)
def bulk_assign_users_to_project(
    request: Request,
    payload: BulkAssignmentRequest,
    project_name: str = Path(alias="projectName"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    """Bulk assign/upsert users to a project.

    Assigns new users and updates roles for existing members in a single
    atomic operation. All users are validated before any changes are applied.
    """
    from codemie.service.project.project_assignment_service import project_assignment_service

    with get_session() as session:
        results = project_assignment_service.bulk_assign_users_to_project(
            session=session,
            project=authorized_project,
            users=[{"user_id": u.user_id, "is_project_admin": u.is_project_admin} for u in payload.users],
            project_name=project_name,
            actor=request.state.user,
            action=f"{request.method} {request.url.path}",
        )
        session.commit()

    return BulkAssignmentResponse(
        message=f"Bulk assignment completed: {len(results)} users processed",
        project_name=project_name,
        total=len(results),
        results=[BulkAssignmentResultItem(**r) for r in results],
    )


@router.post("/projects/{projectName}/import-users/validate", response_model=CsvImportValidationResponse)
def validate_users_from_csv(
    file: UploadFile,
    project_name: str = Path(alias="projectName"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    """Dry-run validation of a CSV file for user import.

    Validates each row (email format, role, system user existence) without
    modifying any data. Returns per-row results with email, role, and error.
    Structural errors (decode failure, missing columns, row count) still return 422.
    """
    from codemie.service.project.csv_import_service import MAX_CSV_BYTES, csv_import_service

    _ensure_user_management_enabled()

    content = file.file.read(MAX_CSV_BYTES + 1)
    if len(content) > MAX_CSV_BYTES:
        raise ExtendedHTTPException(
            code=413,
            message="File too large",
            details=f"CSV file must not exceed {MAX_CSV_BYTES // (1024 * 1024)} MB",
        )

    with get_session() as session:
        results = csv_import_service.validate_csv(session=session, content=content)

    return CsvImportValidationResponse(users=[CsvImportRowResult(**r) for r in results])


@router.post("/projects/{projectName}/import-users", response_model=BulkAssignmentResponse)
def import_users_from_csv(
    request: Request,
    file: UploadFile,
    project_name: str = Path(alias="projectName"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    """Assign users to a project from a CSV file upload.

    CSV must include a header row with 'email' and 'role' columns.
    Allowed roles: 'administrator' (project admin), 'user' (regular member).

    All rows are validated before any changes are applied.
    Returns 422 with details if any emails are invalid, roles are unrecognized,
    or users cannot be found.
    """
    from codemie.service.project.csv_import_service import MAX_CSV_BYTES, csv_import_service

    _ensure_user_management_enabled()

    content = file.file.read(MAX_CSV_BYTES + 1)
    if len(content) > MAX_CSV_BYTES:
        raise ExtendedHTTPException(
            code=413,
            message="File too large",
            details=f"CSV file must not exceed {MAX_CSV_BYTES // (1024 * 1024)} MB",
        )

    with get_session() as session:
        results = csv_import_service.assign_from_csv(
            session=session,
            content=content,
            project=authorized_project,
            project_name=project_name,
            actor=request.state.user,
            action=f"{request.method} {request.url.path}",
        )
        session.commit()

    return BulkAssignmentResponse(
        message=f"CSV import completed: {len(results)} users processed",
        project_name=project_name,
        total=len(results),
        results=[BulkAssignmentResultItem(**r) for r in results],
    )


@router.delete("/projects/{projectName}/assignments", response_model=BulkAssignmentResponse)
def bulk_remove_users_from_project(
    request: Request,
    user_id: list[str] = Query(
        ...,
        min_length=1,
        max_length=100,
        description="User IDs to remove (pass multiple times: ?user_id=x&user_id=y)",
    ),
    project_name: str = Path(alias="projectName"),
    authorized_project: Application = Depends(_authorize_project_access),
):
    """Bulk remove users from a project.

    Removes multiple users from the project in a single atomic operation.
    All users are validated before any removals are applied.
    """
    from codemie.service.project.project_assignment_service import project_assignment_service

    with get_session() as session:
        results = project_assignment_service.bulk_remove_users_from_project(
            session=session,
            project=authorized_project,
            user_ids=user_id,
            project_name=project_name,
            actor=request.state.user,
            action=f"{request.method} {request.url.path}",
        )
        session.commit()

    return BulkAssignmentResponse(
        message=f"Bulk removal completed: {len(results)} users removed",
        project_name=project_name,
        total=len(results),
        results=[BulkAssignmentResultItem(**r) for r in results],
    )


def _raise_project_not_found(user_id: str, project_name: str, action: str) -> None:
    """Log a security-safe warning and raise 404.

    Project name is intentionally omitted from the log to prevent PII leakage.
    """
    timestamp = datetime.now(UTC).isoformat()
    http_method = action.split()[0] if action else "UNKNOWN"
    logger.warning(f"project_authorization_failed: user_id={user_id}, method={http_method}, timestamp={timestamp}")
    raise ExtendedHTTPException(code=404, message="Project not found")


def _ensure_user_management_enabled() -> None:
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message="User management not enabled")
