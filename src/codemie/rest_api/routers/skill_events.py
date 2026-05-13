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

"""REST endpoint for `codemie skill *` lifecycle events.

Persists each event into Postgres (authoritative, durable) and mirrors it
into the existing Elastic-backed metrics path so legacy dashboards keep
working during the transition. The Elastic mirror can be removed in a
follow-up once analytics handlers read directly from Postgres.
"""

import math
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, Query, status

from codemie.configs import logger
from codemie.core.constants import HEADER_CODEMIE_CLI, HEADER_CODEMIE_CLIENT
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.base import PaginatedListResponse, PaginationData
from codemie.rest_api.models.skill_event import (
    SkillEventLogItem,
    SkillEventRequest,
    SkillEventResponse,
    SkillStatsListItem,
    SkillStatsResponse,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.skill_event_service import skill_event_service


_RETRY_HELP = "Please retry; if the issue persists, contact support."

router = APIRouter(
    tags=["Skills"],
    prefix="/v1/skills",
    dependencies=[Depends(authenticate)],
)


@router.post(
    "/events",
    status_code=status.HTTP_200_OK,
    response_model=SkillEventResponse,
    response_model_by_alias=True,
)
def record_skill_event(
    request: SkillEventRequest,
    user: User = Depends(authenticate),
    x_codemie_cli: Annotated[str | None, Header(alias=HEADER_CODEMIE_CLI)] = None,
    x_codemie_client: Annotated[str | None, Header(alias=HEADER_CODEMIE_CLIENT)] = None,
) -> SkillEventResponse:
    """Record a single `codemie skill *` lifecycle event.

    The CLI fans out multi-skill operations into one POST per skill. Ops
    with no targeted skill (bare `list`, `find`, interactive `add` w/o
    `--skill`) come in with `skill_*` fields null — that's fine, the row
    still records the lifecycle and contributes to per-user / per-command
    counts.
    """
    try:
        event = skill_event_service.record(
            request=request,
            user=user,
            x_codemie_cli=x_codemie_cli,
            x_codemie_client=x_codemie_client,
        )
        return SkillEventResponse(
            id=event.id,
            success=True,
            message=f"Skill event '{request.command}/{request.status}' recorded",
        )
    except ExtendedHTTPException:
        raise
    except Exception as exc:
        logger.error(
            f"Failed to record skill event command={request.command!r} status={request.status!r}: {exc}",
            exc_info=True,
        )
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to record skill event",
            details=f"command={request.command} status={request.status} error={exc}",
            help=_RETRY_HELP,
        ) from exc


@router.get(
    "/events",
    status_code=status.HTTP_200_OK,
    response_model=PaginatedListResponse[SkillEventLogItem],
)
def get_skill_event_log(
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(authenticate),
) -> PaginatedListResponse[SkillEventLogItem]:
    """Return a paginated chronological log of skill install / remove events.

    Admins see all users' events; regular users see only their own.
    Useful for audit trails and raw data export.
    """
    try:
        events, total = skill_event_service.get_event_log(
            user=user,
            from_dt=from_,
            to_dt=to,
            limit=limit,
            offset=offset,
        )
        items = [
            SkillEventLogItem(
                skill_slug=event.skill_slug,
                source=event.source,
                target_agents=event.target_agents or [],
                date=event.created_at,
                command=event.command,
                user_id=event.user_id,
                user_email=event.user_email,
            )
            for event in events
        ]
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
        logger.error(f"Failed to retrieve skill event log: {exc}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to retrieve skill event log",
            details=f"error={exc}",
            help=_RETRY_HELP,
        ) from exc


@router.get(
    "/events/stats",
    status_code=status.HTTP_200_OK,
    response_model=PaginatedListResponse[SkillStatsListItem],
)
def get_all_skills_stats(
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(authenticate),
) -> PaginatedListResponse[SkillStatsListItem]:
    """Return paginated per-skill aggregated install/remove stats.

    All authenticated users see platform-wide stats, ordered by installs
    descending (top installed first). Each item contains ``skill_slug``,
    ``installs``, ``removals``, and a ``by_agent`` breakdown of install counts
    per agent.
    """
    try:
        stats, total = skill_event_service.get_all_skills_stats(
            user=user,
            from_dt=from_,
            to_dt=to,
            limit=limit,
            offset=offset,
        )
        items = [SkillStatsListItem(**s) for s in stats]
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
        logger.error(f"Failed to retrieve all-skills stats: {exc}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to retrieve skill stats",
            details=f"error={exc}",
            help=_RETRY_HELP,
        ) from exc


@router.get(
    "/events/{skill_slug}/stats",
    status_code=status.HTTP_200_OK,
    response_model=SkillStatsResponse,
)
def get_skill_aggregated_stats(
    skill_slug: str,
    user: User = Depends(authenticate),
) -> SkillStatsResponse:
    """Return aggregated install / removal counts for a specific skill."""
    try:
        result = skill_event_service.get_skill_stats(skill_slug=skill_slug)
        if result is None:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="Skill not found",
                details=f"No events found for skill '{skill_slug}'",
                help="Verify the skill slug is correct.",
            )
        return SkillStatsResponse(**result)
    except ExtendedHTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to retrieve stats for skill '{skill_slug}': {exc}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to retrieve skill stats",
            details=f"skill_slug={skill_slug} error={exc}",
            help=_RETRY_HELP,
        ) from exc
