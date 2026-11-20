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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from codemie.rest_api.models.base import PaginationData
from codemie.service.activity.activity_models import ActivityDomain
from codemie.service.activity.activity_taxonomy import CATEGORY_EVENT_TYPES, EVENT_TAXONOMY


_KNOWN_DOMAINS = frozenset(v for k, v in vars(ActivityDomain).items() if not k.startswith("_"))


class ProjectActivityEventFilters(BaseModel):
    """Query filters and pagination for project-scoped activity events.

    Unknown domain / category / event_type values are rejected instead of silently matching nothing.
    """

    model_config = ConfigDict(populate_by_name=True)

    domain: list[str] | None = None
    event_type: list[str] | None = None
    category: list[str] | None = None
    actor_id: str | None = None
    from_dt: datetime | None = Field(None, alias="from")
    to_dt: datetime | None = Field(None, alias="to")
    sort_dir: Literal["asc", "desc"] = "desc"
    limit: int = Field(50, ge=1, le=1000)
    offset: int = Field(0, ge=0)

    @field_validator("domain")
    @classmethod
    def _validate_domain(cls, values: list[str] | None) -> list[str] | None:
        unknown = sorted(set(values or []) - _KNOWN_DOMAINS)
        if unknown:
            raise ValueError(f"Unknown domain: {', '.join(unknown)}. Allowed: {', '.join(sorted(_KNOWN_DOMAINS))}")
        return values

    @field_validator("category")
    @classmethod
    def _validate_category(cls, values: list[str] | None) -> list[str] | None:
        unknown = sorted(set(values or []) - CATEGORY_EVENT_TYPES.keys())
        if unknown:
            raise ValueError(
                f"Unknown category: {', '.join(unknown)}. Allowed: {', '.join(sorted(CATEGORY_EVENT_TYPES))}"
            )
        return values

    @field_validator("event_type")
    @classmethod
    def _validate_event_type(cls, values: list[str] | None) -> list[str] | None:
        unknown = sorted(set(values or []) - EVENT_TAXONOMY.keys())
        if unknown:
            raise ValueError(f"Unknown event_type: {', '.join(unknown)}")
        return values


class ActivityEventListItem(BaseModel):
    id: str
    domain: str
    event_type: str
    label: str
    category: str
    entity_type: str | None = None
    entity_id: str | None = None
    entity_label: str | None = None
    project_name: str | None = None
    actor_id: str | None = None
    actor_email: str | None = None
    actor_name: str | None = None
    attributes: dict[str, Any] | None = None
    created_at: datetime


class DomainFilterEntry(BaseModel):
    event_types: list[str]
    entity_types: list[str]


class ActivityEventFilterOptions(BaseModel):
    domains: list[str]
    event_types: list[str]
    entity_types: list[str]
    mapping: dict[str, DomainFilterEntry] = {}


class ProjectActivityEventsResponse(BaseModel):
    data: list[ActivityEventListItem]
    pagination: PaginationData
    recording_enabled: bool


class ActivityEventTypeInfo(BaseModel):
    event_type: str
    label: str
    category: str


class ActivityEventProjectMetadata(BaseModel):
    domains: list[str]
    event_types: list[ActivityEventTypeInfo]
    categories: list[str]
    recording_enabled: bool
