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

import math
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.activity_event import ActivityEventFilterOptions, ActivityEventListItem
from codemie.rest_api.models.base import PaginatedListResponse, PaginationData
from codemie.rest_api.security.authentication import authenticate, maintainer_access_only
from codemie.service.activity.activity_event_service import activity_event_service


router = APIRouter(
    tags=["activity-events"],
    prefix="/v1/admin/activity-events",
    dependencies=[Depends(authenticate)],
)


@router.get("/filter-options", response_model=ActivityEventFilterOptions, status_code=status.HTTP_200_OK)
def get_filter_options(
    _: None = Depends(maintainer_access_only),
) -> ActivityEventFilterOptions:
    try:
        return activity_event_service.get_filter_options()
    except ExtendedHTTPException:
        raise
    except Exception as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to retrieve filter options",
            details=f"error={exc}",
        ) from exc


@router.get("", response_model=PaginatedListResponse[ActivityEventListItem], status_code=status.HTTP_200_OK)
def list_activity_events(
    actor_id: list[str] | None = Query(None),
    include_system: bool = Query(False, description="Also include events with no actor (system-reported)"),
    domain: list[str] | None = Query(None),
    event_type: list[str] | None = Query(None),
    entity_type: list[str] | None = Query(None),
    entity_id: str | None = Query(None),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    sort_dir: Literal["asc", "desc"] = Query("desc"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _: None = Depends(maintainer_access_only),
) -> PaginatedListResponse[ActivityEventListItem]:
    try:
        items, total = activity_event_service.list_events(
            actor_id=actor_id,
            include_system=include_system,
            domain=domain,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            from_dt=from_,
            to_dt=to,
            sort_dir=sort_dir,
            limit=limit,
            offset=offset,
        )
        pagination = PaginationData(
            page=offset // limit if limit else 0,
            per_page=limit,
            total=total,
            pages=math.ceil(total / limit) if limit else 0,
        )
        return PaginatedListResponse(data=items, pagination=pagination)
    except ExtendedHTTPException:
        raise
    except Exception as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to retrieve activity events",
            details=f"error={exc}",
        ) from exc
