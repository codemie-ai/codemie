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

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status

from codemie.core.models import BaseResponse
from codemie.rest_api.models.standard import PostResponse
from codemie.rest_api.models.user_kata_progress import (
    UserKataProgressResponse,
    UserLeaderboardEntry,
    KataProgressStatus,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.user_kata_progress_service import UserKataProgressService
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import MetricsAttributes, KATA_PROGRESS_METRIC

router = APIRouter(
    tags=["Kata Progress"],
    prefix="/v1/katas",
    dependencies=[Depends(authenticate)],
)

progress_service = UserKataProgressService()


@router.get("/leaderboard", response_model=List[UserLeaderboardEntry])
async def get_leaderboard(
    limit: int = Query(default=100, ge=1, le=1000),
    user: User = Depends(authenticate),
) -> List[UserLeaderboardEntry]:
    """
    Get leaderboard ranked by completed kata count.

    Args:
        limit: Maximum number of entries (default 100, max 1000)
        user: Authenticated user

    Returns:
        List of leaderboard entries with rank
    """
    return progress_service.get_leaderboard(limit=limit)


@router.get("/progress/my", response_model=List[UserKataProgressResponse])
async def get_my_progress(
    status_filter: Optional[KataProgressStatus] = Query(default=None, alias="status"),
    user: User = Depends(authenticate),
) -> List[UserKataProgressResponse]:
    """
    Get all progress records for the authenticated user.

    Args:
        status_filter: Optional filter by progress status
        user: Authenticated user

    Returns:
        List of user's progress records
    """
    return progress_service.get_user_all_progress(user_id=user.id, status=status_filter)


@router.post("/{kata_id}/start", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
async def start_kata(
    kata_id: str,
    user: User = Depends(authenticate),
) -> PostResponse:
    """
    Enroll in a kata (start progress tracking).

    Args:
        kata_id: ID of the kata to start
        user: Authenticated user

    Returns:
        Created progress record ID

    Raises:
        ExtendedHTTPException: If kata not found, not published, or user already enrolled
    """
    try:
        progress_id = progress_service.start_kata(kata_id=kata_id, user=user)

        # Track metrics
        _track_kata_progress_metric(
            operation="start_kata",
            kata_id=kata_id,
            progress_status=KataProgressStatus.IN_PROGRESS.value,
            user=user,
            success=True,
        )

        return PostResponse(id=progress_id)
    except Exception as e:
        _track_kata_progress_metric(
            operation="start_kata",
            kata_id=kata_id,
            progress_status=KataProgressStatus.IN_PROGRESS.value,
            user=user,
            success=False,
            additional_attributes={"error_class": e.__class__.__name__},
        )
        raise


@router.post("/{kata_id}/complete", response_model=BaseResponse)
async def complete_kata(
    kata_id: str,
    user: User = Depends(authenticate),
) -> BaseResponse:
    """
    Mark a kata as completed.

    Args:
        kata_id: ID of the kata to complete
        user: Authenticated user

    Returns:
        Success message

    Raises:
        ExtendedHTTPException: If kata not found or user not enrolled
    """
    try:
        progress_service.complete_kata(kata_id=kata_id, user=user)

        # Track metrics
        _track_kata_progress_metric(
            operation="complete_kata",
            kata_id=kata_id,
            progress_status=KataProgressStatus.COMPLETED.value,
            user=user,
            success=True,
        )

        return BaseResponse(message=f"Kata {kata_id} completed successfully")
    except Exception as e:
        _track_kata_progress_metric(
            operation="complete_kata",
            kata_id=kata_id,
            progress_status=KataProgressStatus.COMPLETED.value,
            user=user,
            success=False,
            additional_attributes={"error_class": e.__class__.__name__},
        )
        raise


@router.get("/{kata_id}/progress", response_model=UserKataProgressResponse)
async def get_kata_progress(
    kata_id: str,
    user: User = Depends(authenticate),
) -> UserKataProgressResponse | None:
    """
    Get user's progress for a specific kata.

    Args:
        kata_id: ID of the kata
        user: Authenticated user

    Returns:
        User's progress for the kata, or None if not enrolled
    """
    return progress_service.get_user_progress(kata_id=kata_id, user_id=user.id)


def _track_kata_progress_metric(
    operation: str,
    kata_id: str,
    progress_status: str,
    user: User,
    success: bool,
    additional_attributes: dict | None = None,
):
    """
    Tracks metrics for kata progress operations.

    Args:
        operation: The operation being performed (start_kata, complete_kata)
        kata_id: ID of the kata
        progress_status: Progress status (in_progress, completed)
        user: User performing the operation
        success: Whether the operation succeeded
        additional_attributes: Additional attributes to include in the metric
    """
    try:
        from codemie.configs import logger

        attributes = {
            MetricsAttributes.OPERATION: operation,
            MetricsAttributes.KATA_ID: kata_id,
            MetricsAttributes.PROGRESS_STATUS: progress_status,
            MetricsAttributes.USER_ID: user.id,
            MetricsAttributes.USER_NAME: user.name,
            MetricsAttributes.USER_EMAIL: user.username,
        }

        if additional_attributes:
            attributes.update(additional_attributes)

        metric_name = KATA_PROGRESS_METRIC if success else f"{KATA_PROGRESS_METRIC}_error"
        BaseMonitoringService.send_count_metric(name=metric_name, attributes=attributes)

    except Exception as e:
        from codemie.configs import logger

        logger.warning(
            f"Failed to track kata progress metric '{operation}': {e}",
            exc_info=True,
        )
