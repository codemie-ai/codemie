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
from typing import Any, NamedTuple, Optional

from pydantic import BaseModel
from sqlalchemy import Boolean, Column, Index, text
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


def member_effective_max_budget(allocation: Any, budget: Any, enforce_limit: bool) -> float:
    """Return the hard limit to push to the provider for one project member.

    A fixed override is the member's own cap whether or not equal shares are enforced; only
    unenforced equal-share members get the whole project budget.
    """
    if enforce_limit or getattr(allocation, "allocation_mode", None) == AllocationMode.FIXED.value:
        return allocation.allocated_max_budget
    return budget.max_budget


class EqualMemberShare(NamedTuple):
    """An equal member's amounts; ``member_effective_max_budget`` reads it like an equal-mode allocation."""

    allocated_soft_budget: float
    allocated_max_budget: float


def child_budget_name(main_budget: Budget, suffix: str) -> str:
    """Return ``<main name> [<suffix>]``, trimming the main name to fit the 128-character column."""
    reserved = len(suffix) + 3
    base = main_budget.name[: max(1, 128 - reserved)]
    return f"{base} [{suffix}]"


def shared_project_budget_amounts(
    main_budget: Budget, equal_share: ProjectMemberBudgetAssignment | EqualMemberShare | None, enforce_limit: bool
) -> tuple[float, float]:
    """Return (soft, max) for the shared child row of a project budget.

    Without an equal member share the row mirrors the main budget. Otherwise it holds that share,
    with the hard limit an equal member gets: the shared row serves equal members only, so the
    sample's own allocation mode is ignored.
    """
    if equal_share is None:
        return main_budget.soft_budget, main_budget.max_budget
    share = EqualMemberShare(equal_share.allocated_soft_budget, equal_share.allocated_max_budget)
    return share.allocated_soft_budget, member_effective_max_budget(share, main_budget, enforce_limit)


def build_shared_project_budget_row(
    main_budget: Budget,
    *,
    project_name: str,
    actor_id: str | None,
    soft_budget: float,
    max_budget: float,
) -> dict[str, Any]:
    """Return the column values of a project budget's shared child row."""
    return {
        "budget_id": build_shared_project_budget_id(main_budget.budget_id),
        # A system-driven add has no actor; the main budget's creator owns the row then.
        "created_by": actor_id or main_budget.created_by,
        "budget_type": BudgetType.PROJECT.value,
        "budget_origin_type": "shared_default",
        "parent_budget_id": main_budget.budget_id,
        "owner_user_id": None,
        "project_name": project_name,
        "name": child_budget_name(main_budget, "shared"),
        "description": main_budget.description,
        "soft_budget": soft_budget,
        "max_budget": max_budget,
        "budget_duration": main_budget.budget_duration,
        "budget_category": main_budget.budget_category,
        "detached_at": None,
        "deleted_at": None,
    }


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
    notification_owner_email: Optional[str] = Field(default=None, nullable=True, max_length=320)
    soft_limit_notify_once: bool = Field(default=False, nullable=False, sa_column_kwargs={"server_default": "false"})
    soft_limit_notification_enabled: bool = Field(
        default=False, nullable=False, sa_column_kwargs={"server_default": "false"}
    )
    soft_limit_notified_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=True),
        default=None,
    )
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
    # The last reset CodeMie set through the provider for a global budget, and where the period then
    # running began - a boundary the provider keeps no record of (`budget_period.running_period_start`).
    pinned_reset_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=True),
        default=None,
    )
    pinned_period_start: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=True),
        default=None,
    )
    spend_reset_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=True),
        default=None,
    )
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
    is_active: bool = Field(
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
        default=True,
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
        Index("ix_budgets_is_active", "is_active"),
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
    spend_reset_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=True),
        default=None,
    )

    __table_args__ = (
        Index("ix_uba_budget_id", "budget_id"),
        Index("ix_uba_user_id", "user_id"),
    )


PROJECT_BUDGET_GROUP_ID_FOREIGN_KEY = "project_budget_groups.id"


class ProjectBudgetGroup(SQLModel, table=True):
    """Top-level container grouping per-category budgets for a project.

    At most one active group per project (deleted_at IS NULL), enforced by a
    partial unique index.  Historical groups are preserved for audit purposes.

    project_name is cleared when the project is deleted, so the group outlives it.
    """

    __tablename__ = "project_budget_groups"

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=36,
    )
    project_name: Optional[str] = Field(
        default=None, nullable=True, max_length=100, foreign_key=APPLICATION_ID_FOREIGN_KEY
    )
    name: str = Field(default="", nullable=False, max_length=100)
    budget_duration: str = Field(nullable=False, max_length=16)
    description: Optional[str] = Field(default=None, max_length=500)
    created_by: str = Field(nullable=False, max_length=255)
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

    __table_args__ = (
        Index("ix_pbg_project_name", "project_name"),
        Index(
            "uix_pbg_project_active",
            "project_name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class ProjectBudgetAssignment(SQLModel, table=True):
    """One active project budget per (project_name, budget_category).

    Soft-deleted rows have deleted_at set; unique constraint applies only to
    active rows (deleted_at IS NULL), enforced by a partial unique index.

    group_id is nullable for backward compatibility with pre-group assignments.
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
    group_id: Optional[str] = Field(
        default=None,
        nullable=True,
        max_length=36,
        foreign_key=PROJECT_BUDGET_GROUP_ID_FOREIGN_KEY,
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
        Index("ix_pba_group_id", "group_id"),
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
