# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Activity event model, constants, and input model.

Append-only audit log table. One row per domain action.
Generic entity references (entity_type + entity_id) keep the table
extensible to future domains without schema migrations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, model_validator
from sqlalchemy import TIMESTAMP, Column, ForeignKey, Index, Text, VARCHAR, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from sqlmodel import Field, SQLModel


class ActivityDomain:
    USER_MANAGEMENT = "user_management"
    BUDGET_MANAGEMENT = "budget_management"
    PROJECT_MANAGEMENT = "project_management"


class ActivityEntityType:
    USER = "user"
    PROJECT = "project"
    BUDGET = "budget"
    PROJECT_BUDGET_GROUP = "project_budget_group"
    USER_BUDGET_ASSIGNMENT = "user_budget_assignment"
    PROJECT_BUDGET_ASSIGNMENT = "project_budget_assignment"


class ProjectManagementEvent:
    PROJECT_CREATED = "project.created"
    PROJECT_UPDATED = "project.updated"
    PROJECT_DELETED = "project.deleted"


class UserManagementEvent:
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_DEACTIVATED = "user.deactivated"
    USER_REACTIVATED = "user.reactivated"
    USER_LOGIN = "user.login"
    USER_LOGOUT = "user.logout"
    USER_PROJECT_ASSIGNED = "user.project.assigned"
    USER_PROJECT_REMOVED = "user.project.removed"
    USER_PROJECT_ROLE_UPDATED = "user.project.role_updated"


class BudgetManagementEvent:
    BUDGET_CREATED = "budget.created"
    BUDGET_UPDATED = "budget.updated"
    BUDGET_DELETED = "budget.deleted"
    USER_BUDGET_ASSIGNED = "budget.user_assignment.created"
    USER_BUDGET_REMOVED = "budget.user_assignment.deleted"
    PROJECT_BUDGET_ASSIGNED = "budget.project_assignment.created"
    PROJECT_BUDGET_REMOVED = "budget.project_assignment.deleted"
    PROJECT_BUDGET_CREATED = "budget.project_budget.created"
    PROJECT_BUDGET_UPDATED = "budget.project_budget.updated"
    PROJECT_BUDGET_DELETED = "budget.project_budget.deleted"
    PROJECT_BUDGET_GROUP_CREATED = "budget.project_budget_group.created"
    PROJECT_BUDGET_GROUP_UPDATED = "budget.project_budget_group.updated"
    MEMBER_ALLOCATION_OVERRIDDEN = "budget.member_allocation.overridden"
    MEMBER_ALLOCATION_OVERRIDE_CLEARED = "budget.member_allocation.override_cleared"
    PROJECT_BUDGET_REBALANCED = "budget.project_budget.rebalanced"
    PROJECT_BUDGET_RESET = "budget.project_budget.reset"
    PROJECT_BUDGET_GROUP_REBALANCED = "budget.project_budget_group.rebalanced"
    PROJECT_BUDGET_GROUP_RESET = "budget.project_budget_group.reset"


class ActivityEvent(SQLModel, table=True):
    """Append-only audit event row. Never updated or deleted by application code."""

    __tablename__ = "activity_events"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)

    domain: str = Field(sa_column=Column(VARCHAR(64), nullable=False))
    event_type: str = Field(sa_column=Column(VARCHAR(128), nullable=False))

    entity_type: Optional[str] = Field(default=None, sa_column=Column(VARCHAR(64), nullable=True))
    entity_id: Optional[str] = Field(default=None, sa_column=Column(Text(), nullable=True))

    actor_id: Optional[str] = Field(
        default=None,
        sa_column=Column(
            VARCHAR(36),
            ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    attributes: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSONB, nullable=True))

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(
            TIMESTAMP(timezone=True),
            server_default=func.now(),
            nullable=False,
        ),
    )

    __table_args__ = (
        Index(
            "ix_activity_events_entity_type_entity_id",
            "entity_type",
            "entity_id",
            postgresql_where=text("entity_id IS NOT NULL"),
        ),
        Index(
            "ix_activity_events_actor_id_created_at",
            "actor_id",
            "created_at",
            postgresql_where=text("actor_id IS NOT NULL"),
        ),
        Index(
            "ix_activity_events_domain_created_at",
            "domain",
            "created_at",
        ),
    )


class ActivityEventCreate(BaseModel):
    """Input model for emitting a new activity event.

    entity_type and entity_id are a pair: both set or both None.
    """

    domain: str
    event_type: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    actor_id: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def entity_pair_must_be_complete(self) -> ActivityEventCreate:
        if (self.entity_type is None) != (self.entity_id is None):
            raise ValueError("entity_type and entity_id must both be set or both be absent")
        return self
