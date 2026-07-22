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
from typing import Any

from pydantic import BaseModel


class ActivityEventListItem(BaseModel):
    id: str
    domain: str
    event_type: str
    entity_type: str | None = None
    entity_id: str | None = None
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
