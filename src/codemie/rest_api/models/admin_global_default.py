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

from sqlalchemy import TIMESTAMP, CheckConstraint, Column, func
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel


class AdminGlobalDefault(SQLModel, table=True):
    """The administrator's one persisted global model choice."""

    __tablename__ = "admin_global_default"
    __table_args__ = (CheckConstraint("id = 1", name="ck_admin_global_default_singleton"),)

    id: int = SQLField(default=1, primary_key=True)
    base_name: str = SQLField(nullable=False, max_length=255)
    updated_by_id: str = SQLField(nullable=False, max_length=255)
    updated_by_name: str = SQLField(nullable=False, max_length=255)
    created_at: datetime | None = SQLField(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    )
    updated_at: datetime | None = SQLField(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    )
