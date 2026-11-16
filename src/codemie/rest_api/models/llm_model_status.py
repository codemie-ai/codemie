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

import uuid
from datetime import datetime

from sqlalchemy import Column, TIMESTAMP, func
from sqlmodel import SQLModel, Field as SQLField


class LLMModelStatus(SQLModel, table=True):
    """Persists the admin-managed status override for an LLM model base name."""

    __tablename__ = "llm_model_status"

    id: uuid.UUID = SQLField(default_factory=uuid.uuid4, primary_key=True)
    base_name: str = SQLField(nullable=False, unique=True, index=True, max_length=255)
    status: str = SQLField(nullable=False, default="enabled", max_length=20)
    updated_by_id: str | None = SQLField(default=None, max_length=255)
    updated_by_name: str | None = SQLField(default=None, max_length=255)
    created_at: datetime | None = SQLField(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    )
    updated_at: datetime | None = SQLField(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=True, server_default=func.now(), onupdate=func.now())
    )
