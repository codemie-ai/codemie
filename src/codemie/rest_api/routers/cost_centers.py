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
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field

from codemie.clients.postgres import get_session
from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.application_repository import application_repository
from codemie.rest_api.security.authentication import admin_access_only, authenticate
from codemie.rest_api.security.user import User
from codemie.service.cost_center_service import cost_center_service


router = APIRouter(
    tags=["Cost Centers"],
    prefix="/v1/admin/cost-centers",
    dependencies=[],
)


class PaginationInfo(BaseModel):
    total: int
    page: int
    per_page: int


class CostCenterCreateRequest(BaseModel):
    name: str
    description: Optional[str] = Field(default=None)


class CostCenterUpdateRequest(BaseModel):
    description: Optional[str] = Field(default=None)


class LinkedProjectResponse(BaseModel):
    name: str
    description: Optional[str] = None
    project_type: str
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    user_count: int
    admin_count: int
    cost_center_id: Optional[UUID] = None
    cost_center_name: Optional[str] = None


class CostCenterResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    created_by: str
    created_at: datetime
    project_count: int


class CostCenterDetailResponse(CostCenterResponse):
    projects: list[LinkedProjectResponse]


class PaginatedCostCenterListResponse(BaseModel):
    data: list[CostCenterResponse]
    pagination: PaginationInfo


def _ensure_user_management_enabled() -> None:
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message="User management not enabled")


def _serialize_cost_centers(session, cost_centers):
    project_counts = {
        cost_center.id: application_repository.count_active_projects_by_cost_center_id(session, cost_center.id)
        for cost_center in cost_centers
    }
    return [
        CostCenterResponse(
            id=cost_center.id,
            name=cost_center.name,
            description=cost_center.description,
            created_by=cost_center.created_by,
            created_at=cost_center.date,
            project_count=project_counts[cost_center.id],
        )
        for cost_center in cost_centers
    ]


@router.post("", response_model=CostCenterResponse, status_code=201)
def create_cost_center(
    payload: CostCenterCreateRequest,
    user: User = Depends(authenticate),
    _: None = Depends(admin_access_only),
):
    _ensure_user_management_enabled()
    with get_session() as session:
        cost_center = cost_center_service.create(
            session,
            user=user,
            name=payload.name,
            description=payload.description,
        )
        session.commit()
        session.refresh(cost_center)
        return _serialize_cost_centers(session, [cost_center])[0]


@router.get("", response_model=PaginatedCostCenterListResponse)
def list_cost_centers(
    search: str | None = Query(default=None),
    page: int = Query(default=0, ge=0),
    per_page: int = Query(default=20, ge=10, le=100),
    user: User = Depends(authenticate),
    _: None = Depends(admin_access_only),
):
    _ensure_user_management_enabled()
    with get_session() as session:
        cost_centers, total = cost_center_service.list_paginated(
            session,
            user=user,
            search=search,
            page=page,
            per_page=per_page,
        )
        return PaginatedCostCenterListResponse(
            data=_serialize_cost_centers(session, cost_centers),
            pagination=PaginationInfo(total=total, page=page, per_page=per_page),
        )


@router.get("/{costCenterId}", response_model=CostCenterDetailResponse)
def get_cost_center_detail(
    cost_center_id: UUID = Path(alias="costCenterId"),
    user: User = Depends(authenticate),
    _: None = Depends(admin_access_only),
):
    _ensure_user_management_enabled()
    with get_session() as session:
        cost_center = cost_center_service.get_or_404(session, user=user, cost_center_id=cost_center_id)
        projects = application_repository.list_projects_by_cost_center_id(session, cost_center.id)
        member_counts = application_repository.get_project_member_counts_bulk(
            session, [project.name for project in projects]
        )
        linked_projects = [
            LinkedProjectResponse(
                name=project.name,
                description=project.description,
                project_type=project.project_type,
                created_by=project.created_by,
                created_at=project.date,
                user_count=member_counts.get(project.name, (0, 0))[0],
                admin_count=member_counts.get(project.name, (0, 0))[1],
                cost_center_id=project.cost_center_id,
                cost_center_name=cost_center.name,
            )
            for project in projects
        ]
        response = _serialize_cost_centers(session, [cost_center])[0]
        return CostCenterDetailResponse(**response.model_dump(), projects=linked_projects)


@router.patch("/{costCenterId}", response_model=CostCenterResponse)
def update_cost_center(
    payload: CostCenterUpdateRequest,
    cost_center_id: UUID = Path(alias="costCenterId"),
    user: User = Depends(authenticate),
    _: None = Depends(admin_access_only),
):
    _ensure_user_management_enabled()
    with get_session() as session:
        cost_center = cost_center_service.update(
            session,
            user=user,
            cost_center_id=cost_center_id,
            description=payload.description,
        )
        session.commit()
        session.refresh(cost_center)
        return _serialize_cost_centers(session, [cost_center])[0]


@router.delete("/{costCenterId}", status_code=204)
def delete_cost_center(
    cost_center_id: UUID = Path(alias="costCenterId"),
    user: User = Depends(authenticate),
    _: None = Depends(admin_access_only),
):
    _ensure_user_management_enabled()
    with get_session() as session:
        cost_center_service.delete(session, user=user, cost_center_id=cost_center_id)
        session.commit()
