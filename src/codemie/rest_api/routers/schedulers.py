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
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from codemie.rest_api.models.scheduler_run import (
    PatchSchedulerRequest,
    ProjectRef,
    ResourceRef,
    SchedulerFilterOptions,
    SchedulerListItem,
    SchedulerRunDetail,
    SchedulerRunListItem,
    SchedulerRunStats,
    SchedulerRunsPaginatedResponse,
    SchedulersPaginatedResponse,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.service.scheduler_run_service import SchedulerRunService
from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

router = APIRouter(
    tags=["Schedulers"],
    prefix="/v1",
    dependencies=[Depends(authenticate)],
)


# ── Phase 1 ──────────────────────────────────────────────────────────────────


@router.get("/schedulers/filter-options", response_model=SchedulerFilterOptions, status_code=status.HTTP_200_OK)
def get_scheduler_filter_options(user=Depends(authenticate)):
    return SchedulerSettingsService.get_filter_options(user=user)


@router.get("/schedulers", response_model=SchedulersPaginatedResponse, status_code=status.HTTP_200_OK)
def list_schedulers(
    page: int = Query(0, ge=0),
    page_size: int = Query(10, ge=1, le=100, alias="pageSize"),
    search: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None, alias="resourceType"),
    project_id: Optional[str] = Query(None, alias="projectId"),
    resource_id: Optional[str] = Query(None, alias="resourceId"),
    status: Optional[str] = Query(None),
    last_run_status: Optional[str] = Query(None, alias="lastRunStatus"),
):
    return SchedulerSettingsService.list_schedulers(
        page=page,
        per_page=page_size,
        search=search,
        resource_type=resource_type,
        project_id=project_id,
        resource_id=resource_id,
        status=status,
        last_run_status=last_run_status,
    )


@router.patch("/schedulers/{scheduler_id}", response_model=SchedulerListItem, status_code=status.HTTP_200_OK)
def patch_scheduler(scheduler_id: str, body: PatchSchedulerRequest):
    return SchedulerSettingsService.patch_is_enabled(scheduler_id, body.isEnabled)


# ── Phase 2 ──────────────────────────────────────────────────────────────────


def _build_run_item(r, settings_map: dict) -> SchedulerRunListItem:
    from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService as SchedulerSvc

    setting = settings_map.get(r.scheduler_id) if r.scheduler_id else None
    _gcv = SchedulerSvc._get_cred_value
    resource_type_val = _gcv(setting, "resource_type") or "" if setting else ""
    resource_id_val = _gcv(setting, "resource_id") or "" if setting else ""
    resource_name_val = _gcv(setting, "resource_name") or "" if setting else ""
    scheduler_name = setting.alias if setting else ""
    return SchedulerRunListItem(
        id=r.id,
        scheduler={"id": r.scheduler_id or "", "name": scheduler_name},
        resource=ResourceRef(id=resource_id_val, name=resource_name_val, type=resource_type_val.capitalize()),
        project=ProjectRef(
            id=setting.project_name if setting else "",
            name=setting.project_name if setting else "",
        ),
        status=r.status,
        trigger=r.trigger,
        startedAt=r.started_at.isoformat(),
        finishedAt=r.finished_at.isoformat() if r.finished_at else None,
        durationMs=r.duration_ms,
        executionId=r.execution_id,
        error=r.error_data,
    )


def _parse_date(value: Optional[str], param_name: str) -> Optional[datetime]:
    """Parse an ISO-8601 date string to a timezone-aware datetime, or raise HTTP 422."""
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid date format for '{param_name}': expected ISO-8601 string.",
        )


@router.get("/scheduler-runs/stats", response_model=SchedulerRunStats, status_code=status.HTTP_200_OK)
def get_scheduler_run_stats(
    scheduler_id: Optional[str] = Query(None, alias="schedulerId"),
    status: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None, alias="resourceType"),
    resource_id: Optional[str] = Query(None, alias="resourceId"),
    date_from: Optional[str] = Query(None, alias="dateFrom"),
    date_to: Optional[str] = Query(None, alias="dateTo"),
):
    # CR-005: parse ISO-8601 strings to datetime before passing to the service/repository
    status_list = [s.strip() for s in status.split(",") if s.strip()] or None if status else None
    filters = {
        k: v
        for k, v in {
            "scheduler_id": scheduler_id,
            "status": status_list,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "date_from": _parse_date(date_from, "date_from"),
            "date_to": _parse_date(date_to, "date_to"),
        }.items()
        if v is not None
    }
    return SchedulerRunService().get_stats(filters)


@router.get("/scheduler-runs", response_model=SchedulerRunsPaginatedResponse, status_code=status.HTTP_200_OK)
def list_scheduler_runs(
    scheduler_id: Optional[str] = Query(None, alias="schedulerId"),
    status: Optional[str] = Query(None),  # comma-separated: "completed,failed"
    resource_type: Optional[str] = Query(None, alias="resourceType"),
    resource_id: Optional[str] = Query(None, alias="resourceId"),
    date_from: Optional[str] = Query(None, alias="dateFrom"),
    date_to: Optional[str] = Query(None, alias="dateTo"),
    sort_direction: str = Query("desc", alias="sortDirection"),
    page: int = Query(0, ge=0),
    per_page: int = Query(10, ge=1, le=100, alias="pageSize"),
):
    from codemie.rest_api.models.base import PaginationData
    from codemie.rest_api.models.settings import Settings
    from sqlmodel import Session, select

    status_list = [s.strip() for s in status.split(",") if s.strip()] or None if status else None
    runs, total = SchedulerRunService().list_runs(
        scheduler_id=scheduler_id,
        status=status_list,
        date_from=_parse_date(date_from, "date_from"),
        date_to=_parse_date(date_to, "date_to"),
        sort_direction=sort_direction,
        page=page,
        per_page=per_page,
    )

    # CR-006: bulk-fetch settings to avoid N+1 — one IN query for the whole page
    scheduler_ids = list({r.scheduler_id for r in runs if r.scheduler_id})
    if scheduler_ids:
        with Session(Settings.get_engine()) as sess:
            settings_list = sess.exec(select(Settings).where(Settings.id.in_(scheduler_ids))).all()
        settings_map = {s.id: s for s in settings_list}
    else:
        settings_map = {}

    items = [_build_run_item(r, settings_map) for r in runs]

    return SchedulerRunsPaginatedResponse(
        items=items,
        pagination=PaginationData(
            page=page,
            per_page=per_page,
            total=total,
            pages=math.ceil(total / per_page) if per_page else 0,
        ),
    )


# ── Phase 3 ──────────────────────────────────────────────────────────────────


@router.get("/scheduler-runs/{run_id}/logs", status_code=status.HTTP_200_OK)
def get_scheduler_run_logs(
    run_id: str,
    page: int = Query(0, ge=0),
    per_page: int = Query(50, ge=1, le=200),
):
    import math
    from codemie.rest_api.models.base import PaginationData

    logs, total = SchedulerRunService().get_logs(run_id, page=page, per_page=per_page)
    return {
        "items": logs,
        "pagination": PaginationData(
            page=page,
            per_page=per_page,
            total=total,
            pages=math.ceil(total / per_page) if per_page else 0,
        ).model_dump(),
    }


@router.get("/scheduler-runs/{run_id}", response_model=SchedulerRunDetail, status_code=status.HTTP_200_OK)
def get_scheduler_run(run_id: str):
    return SchedulerRunService().get_run_detail(run_id)


@router.delete("/scheduler-runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scheduler_run(run_id: str, _user=Depends(authenticate)):
    SchedulerRunService().delete_run(run_id)
