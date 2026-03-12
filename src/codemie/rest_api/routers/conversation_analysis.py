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

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from codemie.configs import config, logger
from codemie.repository.conversation_analysis_queue_repository import ConversationAnalysisQueueRepository
from codemie.repository.conversation_analytics_repository import ConversationAnalyticsRepository
from codemie.rest_api.security.authentication import authenticate, admin_access_only
from codemie.rest_api.security.user import User
from codemie.service.conversation_analysis.conversation_analysis_service import ConversationAnalysisService

router = APIRouter(prefix="/v1/conversation-analysis", tags=["Conversation Analysis"])


class TriggerAnalysisRequest(BaseModel):
    """Request model for triggering conversation analysis"""

    projects: Optional[List[str]] = Field(
        default=None,
        description="Optional list of project names to filter conversations. If not provided, analyzes all projects.",
    )


@router.get("/status")
async def get_analysis_status(user: User = Depends(authenticate)):
    """Get current status of conversation analysis system"""
    queue_repo = ConversationAnalysisQueueRepository()
    analytics_repo = ConversationAnalyticsRepository()

    pending_count = await queue_repo.get_pending_count()
    stats = await analytics_repo.get_statistics()

    return {
        "enabled": config.CONVERSATION_ANALYSIS_ENABLED,
        "schedule": config.CONVERSATION_ANALYSIS_SCHEDULE,
        "start_date": config.CONVERSATION_ANALYSIS_START_DATE,
        "lookback_days": config.CONVERSATION_ANALYSIS_LOOKBACK_DAYS,
        "batch_size": config.CONVERSATION_ANALYSIS_BATCH_SIZE,
        "projects_filter": config.CONVERSATION_ANALYSIS_PROJECTS_FILTER or None,
        "queue": {
            "pending_conversations": pending_count,
        },
        "analytics": stats,
    }


@router.post("/trigger")
async def trigger_analysis_manually(
    request: TriggerAnalysisRequest,
    user: User = Depends(authenticate),
    _: None = Depends(admin_access_only),
):
    """
    Manually trigger conversation analysis job (Admin only).

    Uses the same logic and rules as the scheduled job:
    - Fetches conversations created/updated >= CONVERSATION_ANALYSIS_START_DATE
    - Filters conversations updated more than CONVERSATION_ANALYSIS_LOOKBACK_DAYS ago
    - Detects conversations needing reprocessing (updated or new messages)
    - Populates queue for distributed processing by all pods

    Project filtering priority:
    1. If projects list is provided in request, use those projects
    2. Else if CONVERSATION_ANALYSIS_PROJECTS_FILTER config is set, use config projects
    3. Else analyze all projects

    Returns:
        Dict with job execution summary
    """
    if not config.CONVERSATION_ANALYSIS_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Conversation analysis is disabled. Set CONVERSATION_ANALYSIS_ENABLED=true to enable.",
        )

    # Determine projects filter: API request → config → all projects
    if request.projects:
        projects_filter = request.projects
    elif config.CONVERSATION_ANALYSIS_PROJECTS_FILTER:
        projects_filter = config.CONVERSATION_ANALYSIS_PROJECTS_FILTER
    else:
        projects_filter = None

    projects_info = f"for projects: {', '.join(projects_filter)}" if projects_filter else "for all projects"

    logger.info(f"Manual conversation analysis triggered by user {user.id} ({user.full_name}) {projects_info}")

    try:
        # Use the same service method as the scheduled job with project filter
        analysis_service = ConversationAnalysisService()
        result = await analysis_service.schedule_analysis_job(projects=projects_filter)

        logger.info(f"Manual conversation analysis completed: {result}")

        return {
            **result,
            "triggered_by": user.full_name,
            "triggered_at": "now",
            "projects_filter": projects_filter,
            "message": "Analysis job triggered successfully. Conversations queued for processing by all pods.",
        }

    except Exception as e:
        logger.error(f"Manual conversation analysis failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to trigger analysis: {str(e)}"
        )
