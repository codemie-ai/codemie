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
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Path, Query

from codemie.clients.postgres import get_async_session, get_session
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.application_repository import application_repository
from codemie.rest_api.models.project import (
    PaginatedSpendsResponse,
    PaginationInfo,
    SpendTrackingRow,
)
from codemie.rest_api.routers.projects._common import (
    _ensure_user_management_enabled,
    _raise_project_not_found,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.project.project_spending_service import spend_repo


router = APIRouter()


def _get_project_by_name_sync(project_name: str):
    """Synchronous project lookup — runs in threadpool from async handlers."""
    with get_session() as session:
        return application_repository.get_by_name(session, project_name)


@router.get("/projects/{projectName}/spends", response_model=PaginatedSpendsResponse)
async def get_project_spends(
    project_name: str = Path(alias="projectName"),
    period_from: date = Query(..., description="Inclusive start date (YYYY-MM-DD)"),
    period_to: date = Query(..., description="Inclusive end date (YYYY-MM-DD)"),
    page: int = Query(0, ge=0, description="Page number (0-indexed)"),
    per_page: int = Query(20, ge=10, le=100, description="Items per page (10-100)"),
    budget_category: Optional[BudgetCategory] = Query(
        None, description="Filter by budget category (cli, platform, premium_models)"
    ),
    spend_subject_type: Optional[Literal["key", "budget", "project_budget", "member_budget"]] = Query(
        None, description="Filter by spend subject type"
    ),
    user: User = Depends(authenticate),
) -> PaginatedSpendsResponse:
    """Return paginated spend rows and period total for a project.

    Accessible to global admins, global maintainers, and project admins.
    Returns 404 (not 403) to avoid revealing project existence to unauthorized callers.
    When spend_subject_type is omitted, project_budget rows are excluded to prevent
    double-counting against member_budget rows.
    """
    _ensure_user_management_enabled()

    if period_from > period_to:
        raise ExtendedHTTPException(code=400, message="period_from must not be after period_to")

    project = await asyncio.to_thread(_get_project_by_name_sync, project_name)

    if not project or project.deleted_at is not None:
        _raise_project_not_found(user_id=user.id, action="GET spends")

    is_personal = project.project_type == Application.ProjectType.PERSONAL
    can_access = (
        user.is_admin_or_maintainer
        or getattr(user, "is_auditor", False)
        or user.is_application_admin(project_name)
        or (is_personal and user.has_access_to_application(project_name))
    )
    if not can_access:
        _raise_project_not_found(user_id=user.id, action="GET spends")

    period_from_dt = datetime.combine(period_from, time.min, tzinfo=timezone.utc)
    period_to_dt = datetime.combine(period_to + timedelta(days=1), time.min, tzinfo=timezone.utc)

    async with get_async_session() as session:
        total_spend, total_count, rows = await spend_repo.get_spend_for_period(
            session,
            project_name,
            period_from_dt,
            period_to_dt,
            page,
            per_page,
            budget_category=budget_category,
            spend_subject_type=spend_subject_type,
        )

    return PaginatedSpendsResponse(
        total_spend=total_spend,
        rows=[
            SpendTrackingRow(
                spend_date=row.spend_date,
                daily_spend=float(row.daily_spend),
                cumulative_spend=float(row.cumulative_spend),
                budget_period_spend=float(row.budget_period_spend),
                budget_id=row.budget_id,
            )
            for row in rows
        ],
        pagination=PaginationInfo(total=total_count, page=page, per_page=per_page),
    )
