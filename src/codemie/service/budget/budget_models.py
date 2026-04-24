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
from typing import Any, Optional

from pydantic import BaseModel
from sqlalchemy import Column, Index, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.sql import func
from sqlmodel import Field, SQLModel

from codemie.service.budget.budget_enums import AllocationMode, BudgetType

BUDGET_ID_FOREIGN_KEY = "budgets.budget_id"
USER_ID_FOREIGN_KEY = "users.id"
APPLICATION_ID_FOREIGN_KEY = "applications.id"


def build_shared_project_budget_id(main_budget_id: str) -> str:
    """Return the deterministic shared child budget id for a project budget."""
    return f"{main_budget_id}:shared"


def build_override_project_budget_id(main_budget_id: str, user_id: str) -> str:
    """Return the deterministic override child budget id for a user."""
    return f"{main_budget_id}:user:{user_id}"


class BudgetProviderMetadata(BaseModel):
    """Opaque provider state stored alongside a Budget or member allocation.

    Core must not branch on provider-specific fields (key hashes, team ids,
    customer ids). Those belong exclusively in the ``raw`` dict managed by
    the enterprise provider implementation.
    """

    provider: str | None = None
    provider_budget_ref: str | None = None
    last_synced_at: datetime | None = None
    sync_status: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class Budget(SQLModel, table=True):
    """Codemie-owned budget record, mirrored in the active enforcement provider.

    budget_id is the primary key AND the provider budget reference for global
    budgets.  For project budgets the provider reference is stored in
    provider_metadata.provider_budget_ref.

    budget_type distinguishes ownership scope:
      global  – personal/default budget assignable to users
      project – project/category budget assignable to one project+category pair

    provider_metadata is an opaque JSON blob managed by the enterprise provider.
    Core must not branch on its contents.

    LiteLLM datetime fields (budget_reset_at) are stored as raw ISO 8601
    strings — exactly as returned by LiteLLM — to avoid any conversion or
    timezone ambiguity.  Codemie-managed timestamps use TIMESTAMP columns with
    DB server defaults.

    Soft-delete: set deleted_at to mark a budget as deleted without removing
    the row.  The partial unique index on name enforces uniqueness only among
    active (deleted_at IS NULL) rows, allowing name reuse after deletion.
    """

    __tablename__ = "budgets"

    budget_id: str = Field(primary_key=True, max_length=255)
    budget_type: str = Field(nullable=False, max_length=16, default=BudgetType.GLOBAL)
    budget_origin_type: str = Field(nullable=False, max_length=32, default="main")
    parent_budget_id: Optional[str] = Field(
        default=None,
        nullable=True,
        max_length=255,
        foreign_key=BUDGET_ID_FOREIGN_KEY,
    )
    owner_user_id: Optional[str] = Field(default=None, nullable=True, max_length=36, foreign_key=USER_ID_FOREIGN_KEY)
    project_name: Optional[str] = Field(
        default=None, nullable=True, max_length=100, foreign_key=APPLICATION_ID_FOREIGN_KEY
    )
    name: str = Field(nullable=False, max_length=128)
    description: Optional[str] = Field(default=None, max_length=500)
    soft_budget: float = Field(nullable=False)
    max_budget: float = Field(nullable=False)
    budget_duration: str = Field(nullable=False, max_length=16)  # e.g. "30d"
    budget_category: str = Field(nullable=False, max_length=32)  # BudgetCategory value
    budget_reset_at: Optional[str] = Field(default=None, nullable=True)
    provider_metadata: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    created_by: str = Field(nullable=False, max_length=255)  # user.id of creator
    created_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
        default=None,
    )
    updated_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=True, onupdate=func.now()),
        default=None,
    )
    deleted_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=True),
        default=None,
    )
    detached_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=True),
        default=None,
    )

    __table_args__ = (
        # Partial unique index: only active (non-deleted) budgets must have unique names.
        Index("uix_budgets_name_active", "name", unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("ix_budgets_budget_category", "budget_category"),
        Index("ix_budgets_budget_type", "budget_type"),
        Index("ix_budgets_type_category", "budget_type", "budget_category"),
        Index("ix_budgets_parent_budget_id", "parent_budget_id"),
        Index("ix_budgets_origin_type", "budget_origin_type"),
        Index("ix_budgets_project_origin", "project_name", "budget_category", "budget_origin_type"),
        Index("ix_budgets_created_by", "created_by"),
    )


class UserBudgetAssignment(SQLModel, table=True):
    """One row per (user, category) budget assignment.

    Composite primary key (user_id, category) enforces at most one budget per
    category per user. ON DELETE CASCADE on user FK; ON DELETE CASCADE on budget FK.

    This table is used only for global/personal budget assignments.
    Project-specific member caps use ProjectMemberBudgetAssignment.
    """

    __tablename__ = "user_budget_assignments"

    user_id: str = Field(
        primary_key=True,
        foreign_key=USER_ID_FOREIGN_KEY,
        max_length=36,
    )
    category: str = Field(
        primary_key=True,
        max_length=32,
        description="BudgetCategory value: platform | cli | premium_models",
    )
    budget_id: str = Field(
        foreign_key=BUDGET_ID_FOREIGN_KEY,
        nullable=False,
        max_length=255,
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


class ProjectBudgetAssignment(SQLModel, table=True):
    """One active project budget per (project_name, budget_category).

    Soft-deleted rows have deleted_at set; unique constraint applies only to
    active rows (deleted_at IS NULL), enforced by a partial unique index.
    """

    __tablename__ = "project_budget_assignments"

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=36,
    )
    project_name: str = Field(nullable=False, max_length=100, foreign_key=APPLICATION_ID_FOREIGN_KEY)
    budget_category: str = Field(nullable=False, max_length=32)
    budget_id: str = Field(
        nullable=False,
        max_length=255,
        foreign_key=BUDGET_ID_FOREIGN_KEY,
    )
    allocation_mode: str = Field(nullable=False, max_length=16, default=AllocationMode.EQUAL)
    assigned_by: str = Field(nullable=False, max_length=255)
    assigned_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
        default=None,
    )
    deleted_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=True),
        default=None,
    )

    __table_args__ = (
        Index("ix_pba_budget_id", "budget_id"),
        Index("ix_pba_project_name", "project_name"),
        Index("ix_pba_project_category", "project_name", "budget_category"),
    )


class ProjectMemberBudgetAssignment(SQLModel, table=True):
    """Effective member allocation for a project category budget.

    Soft-deleted rows have deleted_at set; provider_metadata is an opaque
    JSON blob managed by the enterprise provider — core must not branch on
    its contents (no LiteLLM key hashes, team ids, or customer ids).
    """

    __tablename__ = "project_member_budget_assignments"

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=36,
    )
    project_name: str = Field(nullable=False, max_length=100, foreign_key=APPLICATION_ID_FOREIGN_KEY)
    budget_category: str = Field(nullable=False, max_length=32)
    project_budget_id: str = Field(
        nullable=False,
        max_length=255,
        foreign_key=BUDGET_ID_FOREIGN_KEY,
    )
    user_id: str = Field(nullable=False, max_length=36, foreign_key=USER_ID_FOREIGN_KEY)
    allocation_mode: str = Field(nullable=False, max_length=16, default=AllocationMode.EQUAL)
    allocation_weight: Optional[float] = Field(default=None, nullable=True)
    allocated_soft_budget: float = Field(nullable=False)
    allocated_max_budget: float = Field(nullable=False)
    shared_budget_id: Optional[str] = Field(
        default=None,
        nullable=True,
        max_length=255,
        foreign_key=BUDGET_ID_FOREIGN_KEY,
    )
    override_budget_id: Optional[str] = Field(
        default=None,
        nullable=True,
        max_length=255,
        foreign_key=BUDGET_ID_FOREIGN_KEY,
    )
    effective_budget_id: Optional[str] = Field(
        default=None,
        nullable=True,
        max_length=255,
        foreign_key=BUDGET_ID_FOREIGN_KEY,
    )
    provider_metadata: Optional[dict] = Field(
        default=None,
        sa_column=Column("pmba_provider_metadata", JSONB, nullable=True),
    )
    spend: Optional[float] = Field(default=None, nullable=True)
    budget_reset_at: Optional[str] = Field(default=None, nullable=True)
    last_synced_at: Optional[datetime] = Field(
        sa_column=Column("pmba_last_synced_at", TIMESTAMP(timezone=True), nullable=True),
        default=None,
    )
    sync_status: Optional[str] = Field(default=None, nullable=True, max_length=32)
    override_reason: Optional[str] = Field(default=None, nullable=True, max_length=500)
    assigned_by: Optional[str] = Field(default=None, nullable=True, max_length=255)
    assigned_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
        default=None,
    )
    deleted_at: Optional[datetime] = Field(
        sa_column=Column("pmba_deleted_at", TIMESTAMP(timezone=True), nullable=True),
        default=None,
    )

    __table_args__ = (
        Index("ix_pmba_project_budget_id", "project_budget_id"),
        Index("ix_pmba_user_id", "user_id"),
        Index("ix_pmba_project_category", "project_name", "budget_category"),
        Index("ix_pmba_effective_budget_id", "effective_budget_id"),
    )
