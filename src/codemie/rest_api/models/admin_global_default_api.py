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

from pydantic import BaseModel, Field


class AdminGlobalDefaultRequest(BaseModel):
    base_name: str = Field(min_length=1)


class AdminGlobalDefaultResponse(BaseModel):
    saved_base_name: str | None
    updated_by_id: str | None
    updated_by_name: str | None
    updated_at: datetime | None
    effective_base_name: str | None
    source: str | None
    temporary_fallback: bool
