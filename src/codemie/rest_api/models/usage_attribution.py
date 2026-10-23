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

"""Response models of the Usage Attribution endpoints (/v1/analytics/usage-attribution)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from codemie.rest_api.models.analytics import PaginationMetadata


class UsageAttributionRange(BaseModel):
    """The effective range: the end is rounded down to 15 minutes and the range is clamped to the maximum."""

    from_: str = Field(..., alias="from", serialization_alias="from")
    to: str
    days: float
    clamped: bool
    requested_from: str
    requested_to: str
    evidence_from: str = Field(..., description="Categories use at least the minimum evidence window")

    model_config = {"populate_by_name": True}


class AnalysisSettingsInfo(BaseModel):
    hash: str
    defaults_hash: str
    schema_version: int
    overridden: list[str] = Field(default_factory=list)
    tier: str | None = None


class AnalysisInfo(BaseModel):
    """When and how the cached analysis behind a response was computed."""

    computed_at: str | None = None
    duration_ms: float | None = None
    cache: str = Field(..., description="hit, miss, shared (computed for another request) or stale")
    stale: bool = False
    refreshing: bool = Field(False, description="A newer analysis is being computed; ask again shortly")
    error: str | None = None
    ttl_s: int | None = None
    window: dict[str, Any] | None = None
    evidence_window: dict[str, Any] | None = None
    coverage: dict[str, Any] | None = None
    unclassified_reasons: dict[str, Any] | None = None
    allocated: float | None = None
    timings: dict[str, Any] = Field(default_factory=dict)
    settings: AnalysisSettingsInfo


class UsageAttributionResponse(BaseModel):
    """A composite widget: tiles, charts, a popup or How It Works."""

    data: dict[str, Any]
    range: UsageAttributionRange | None = None
    analysis: AnalysisInfo | None = None


class UsageAttributionTableResponse(BaseModel):
    """A table: rows of the current page, totals over every matching row, and the counts behind the filters."""

    data: dict[str, Any] = Field(..., description="rows and totals")
    pagination: PaginationMetadata
    counts: dict[str, int] = Field(default_factory=dict)
    threshold: float | None = None
    display: dict[str, float] | None = None
    range: UsageAttributionRange
    analysis: AnalysisInfo


class UsageAttributionSettingsResponse(BaseModel):
    """The tunable schema, the defaults and whether the caller may tune."""

    data: dict[str, Any]
