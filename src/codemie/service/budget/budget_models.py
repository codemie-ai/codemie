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
from typing import Optional

from sqlalchemy import Column, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.sql import func
from sqlmodel import Field, SQLModel


class Budget(SQLModel, table=True):
    """Codemie-owned budget record, mirrored in LiteLLM proxy.

    budget_id is the primary key AND the LiteLLM budget_id — a stable
    user-supplied or auto-generated slug (e.g. "proj-alpha-30d").
    name / description are display-only fields not sent to LiteLLM.

    LiteLLM datetime fields (budget_reset_at) are stored as raw ISO 8601
    strings — exactly as returned by LiteLLM — to avoid any conversion or
    timezone ambiguity. Codemie-managed timestamps (created_at, updated_at)
    use TIMESTAMP columns with DB server defaults.
    """

    __tablename__ = "budgets"

    budget_id: str = Field(primary_key=True, max_length=128)
    name: str = Field(nullable=False, max_length=128)
    description: Optional[str] = Field(default=None, max_length=500)
    soft_budget: float = Field(nullable=False)
    max_budget: float = Field(nullable=False)
    budget_duration: str = Field(nullable=False, max_length=16)  # e.g. "30d"
    budget_category: str = Field(nullable=False, max_length=32)  # BudgetCategory value
    budget_reset_at: Optional[str] = Field(default=None, nullable=True)
    # e.g. "2026-04-18T09:24:21.103000Z" — stored verbatim from LiteLLM, no conversion
    created_by: str = Field(nullable=False, max_length=255)  # user.id of creator
    created_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
        default=None,
    )
    updated_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=True, onupdate=func.now()),
        default=None,
    )

    __table_args__ = (
        UniqueConstraint("name", name="uix_budgets_name"),
        Index("ix_budgets_budget_category", "budget_category"),
        Index("ix_budgets_created_by", "created_by"),
    )


class UserBudgetAssignment(SQLModel, table=True):
    """One row per (user, category) budget assignment.

    Composite primary key (user_id, category) enforces at most one budget per
    category per user. ON DELETE CASCADE on user FK; ON DELETE CASCADE on budget FK.
    """

    __tablename__ = "user_budget_assignments"

    user_id: str = Field(
        primary_key=True,
        foreign_key="users.id",
        max_length=36,
    )
    category: str = Field(
        primary_key=True,
        max_length=32,
        description="BudgetCategory value: platform | cli | premium_models",
    )
    budget_id: str = Field(
        foreign_key="budgets.budget_id",
        nullable=False,
        max_length=128,
    )
    assigned_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
        default=None,
    )
    assigned_by: Optional[str] = Field(default=None, nullable=True, max_length=255)

    __table_args__ = (
        Index("ix_uba_budget_id", "budget_id"),
        Index("ix_uba_user_id", "user_id"),
    )
