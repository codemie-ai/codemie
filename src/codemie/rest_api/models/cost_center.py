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

from pydantic import BaseModel, Field


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
    display_name: Optional[str] = None
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
