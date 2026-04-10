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
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import Column, Index, Numeric
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID as PG_UUID
from sqlalchemy.sql import func
from sqlmodel import Field, SQLModel


class ProjectSpendTracking(SQLModel, table=True):
    """Unified per-project spend snapshot for LiteLLM spend tracking.

    Stores one row per tracked subject (key or budget bucket) per snapshot.
    ``cumulative_spend`` is the lifetime spend that never resets.
    ``budget_period_spend`` is the spend value reported by LiteLLM for the active
    budget period and may reset.

    Two subject types are supported:
    - ``key``: one snapshot row per project API key (``key_hash`` required)
    - ``budget``: one snapshot row per project budget bucket (``key_hash`` null)
    """

    __tablename__ = "project_spend_tracking"

    id: UUID = Field(sa_column=Column(PG_UUID(as_uuid=True), primary_key=True))
    project_name: str = Field(nullable=False)
    cost_center_id: Optional[UUID] = Field(default=None, sa_column=Column(PG_UUID(as_uuid=True), nullable=True))
    cost_center_name: Optional[str] = Field(default=None, nullable=True)
    key_hash: Optional[str] = Field(default=None, nullable=True)
    spend_date: datetime = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False),
    )
    daily_spend: Decimal = Field(
        sa_column=Column(Numeric(18, 9), nullable=False),
        default=Decimal("0"),
    )
    cumulative_spend: Decimal = Field(
        sa_column=Column(Numeric(18, 9), nullable=False),
        default=Decimal("0"),
    )
    budget_period_spend: Decimal = Field(
        sa_column=Column(Numeric(18, 9), nullable=False),
        default=Decimal("0"),
    )
    budget_reset_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=True),
        default=None,
    )
    budget_id: Optional[str] = Field(default=None, nullable=True)
    soft_budget: Optional[Decimal] = Field(
        sa_column=Column(Numeric(18, 9), nullable=True),
        default=None,
    )
    max_budget: Optional[Decimal] = Field(
        sa_column=Column(Numeric(18, 9), nullable=True),
        default=None,
    )
    budget_duration: Optional[str] = Field(default=None, nullable=True)
    spend_subject_type: Optional[str] = Field(default=None, nullable=True)
    # Server default NOW(); nullable in Python so insert dict can omit it and let DB fill it
    created_at: Optional[datetime] = Field(
        sa_column=Column(
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
        default=None,
    )

    __table_args__ = (
        Index("ix_project_spend_tracking_project_name", "project_name"),
        Index("ix_project_spend_tracking_key_hash", "key_hash"),
        Index("ix_project_spend_tracking_spend_date", "spend_date"),
    )
