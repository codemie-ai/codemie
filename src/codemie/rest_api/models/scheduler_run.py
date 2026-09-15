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

from datetime import datetime
from enum import StrEnum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from codemie.rest_api.models.base import PaginationData


class SchedulerRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SchedulerRun(SQLModel, table=True):
    __tablename__ = "scheduler_runs"
    __table_args__ = {"schema": "codemie"}

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    scheduler_id: Optional[str] = Field(default=None, index=True)
    status: str = Field(index=True)
    trigger: str = Field(default="scheduled")
    started_at: datetime = Field(index=True)
    finished_at: Optional[datetime] = Field(default=None)
    duration_ms: Optional[int] = Field(default=None)
    execution_id: Optional[str] = Field(default=None)
    conversation_id: Optional[str] = Field(default=None)
    resource_execution_id: Optional[str] = Field(default=None)
    input_data: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    result_data: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    error_data: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    logs: Optional[list[dict[str, Any]]] = Field(default=None, sa_column=Column(JSONB))
    metrics: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))


# ── Pydantic response models ──────────────────────────────────────────────────


class ResourceRef(BaseModel):
    id: str
    name: str
    type: str  # "Assistant" | "Workflow" | "Datasource"


class ProjectRef(BaseModel):
    id: str
    name: str


class ScheduleInfo(BaseModel):
    cron: str
    description: str
    timezone: str
    nextRunAt: Optional[str] = None


class LastRunRef(BaseModel):
    id: str
    status: str
    startedAt: str


class SchedulerListItem(BaseModel):
    id: str
    name: str
    resource: ResourceRef
    project: ProjectRef
    schedule: ScheduleInfo
    isEnabled: bool
    lastRun: Optional[LastRunRef] = None


class SchedulersPaginatedResponse(BaseModel):
    items: list[SchedulerListItem]
    pagination: PaginationData


class PatchSchedulerRequest(BaseModel):
    isEnabled: bool


class SchedulerRunListItem(BaseModel):
    id: str
    scheduler: dict[str, str]
    resource: ResourceRef
    project: ProjectRef
    status: str
    trigger: str
    startedAt: str
    finishedAt: Optional[str] = None
    durationMs: Optional[int] = None
    executionId: Optional[str] = None
    error: Optional[dict[str, Any]] = None


class SchedulerRunsPaginatedResponse(BaseModel):
    items: list[SchedulerRunListItem]
    pagination: PaginationData


class SchedulerRunStats(BaseModel):
    total: int
    completed: int
    failed: int
    running: int
    cancelled: int
    successRate: float
    averageDurationMs: float


class SchedulerConfig(BaseModel):
    cron: str
    humanReadableSchedule: str
    timezone: str


class RunResult(BaseModel):
    available: bool
    content: Optional[Any] = None


class RunMetrics(BaseModel):
    inputTokens: Optional[int] = None
    outputTokens: Optional[int] = None
    cost: Optional[float] = None


class LogEntry(BaseModel):
    id: Optional[str] = None
    timestamp: str
    level: str
    message: str
    step: Optional[str] = None


class SchedulerRunDetail(BaseModel):
    id: str
    scheduler: dict[str, str]
    resource: ResourceRef
    project: ProjectRef
    status: str
    trigger: str
    startedAt: str
    finishedAt: Optional[str] = None
    durationMs: Optional[int] = None
    executionId: Optional[str] = None
    schedulerConfig: Optional[SchedulerConfig] = None
    input: Optional[dict[str, Any]] = None
    result: RunResult
    logs: list[LogEntry]
    metrics: Optional[RunMetrics] = None
    conversationId: Optional[str] = None
    resourceExecutionId: Optional[str] = None
    error: Optional[dict[str, Any]] = None
    workflowExecutionId: Optional[str] = None
    inputTokens: Optional[int] = None
    outputTokens: Optional[int] = None
    executionCost: Optional[float] = None


class SchedulerRunLogsPaginatedResponse(BaseModel):
    items: list[LogEntry]
    pagination: PaginationData


class SchedulerFilterOptions(BaseModel):
    resources: list[ResourceRef]
    projects: list[ProjectRef]
