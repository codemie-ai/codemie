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
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.activity_event import (
    ActivityEventProjectMetadata,
    ProjectActivityEventFilters,
    ProjectActivityEventsResponse,
)
from codemie.rest_api.models.base import PaginationData
from codemie.rest_api.security.authentication import authenticate, project_activity_instance_access
from codemie.service.activity.activity_event_service import activity_event_service


router = APIRouter(
    tags=["activity-events"],
    prefix="/v1/projects/{project_instance_id}/activity-events",
    dependencies=[Depends(authenticate)],
)


@router.get("/metadata", response_model=ActivityEventProjectMetadata, status_code=status.HTTP_200_OK)
def get_project_activity_events_metadata(
    project_instance_id: UUID,
    _: None = Depends(project_activity_instance_access),
) -> ActivityEventProjectMetadata:
    try:
        return activity_event_service.get_project_event_metadata(project_instance_id)
    except ExtendedHTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to retrieve project activity events metadata for project instance %s", project_instance_id
        )
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to retrieve project activity events metadata",
            details="Please try again later.",
        ) from exc


@router.get("", response_model=ProjectActivityEventsResponse, status_code=status.HTTP_200_OK)
def list_project_activity_events(
    project_instance_id: UUID,
    filters: Annotated[ProjectActivityEventFilters, Query()],
    _: None = Depends(project_activity_instance_access),
) -> ProjectActivityEventsResponse:
    try:
        items, total, recording_enabled = activity_event_service.list_project_events(project_instance_id, filters)
        pagination = PaginationData(
            page=filters.offset // filters.limit,
            per_page=filters.limit,
            total=total,
            pages=math.ceil(total / filters.limit),
        )
        return ProjectActivityEventsResponse(data=items, pagination=pagination, recording_enabled=recording_enabled)
    except ExtendedHTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to retrieve project activity events for project instance %s", project_instance_id)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to retrieve project activity events",
            details="Please try again later.",
        ) from exc
