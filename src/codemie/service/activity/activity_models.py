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
from uuid import UUID, uuid4

from pydantic import BaseModel, model_validator
from sqlalchemy import TIMESTAMP, Column, ForeignKey, Index, Text, VARCHAR, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.sql import func
from sqlmodel import Field, SQLModel

from codemie.configs import logger
from codemie.service.activity.activity_taxonomy import EVENT_TAXONOMY


# actor_id of events written by the platform itself; such an actor has no email to snapshot.
SYSTEM_ACTOR_ID = "system"


class ActivityDomain:
    USER_MANAGEMENT = "user_management"
    BUDGET_MANAGEMENT = "budget_management"
    PROJECT_MANAGEMENT = "project_management"
    CUSTOMER_CONFIG = "customer_config"
    MARKETPLACE = "marketplace"
    ASSISTANT = "assistant"
    HOSTED_APPLICATION = "hosted_application"


class ActivityEntityType:
    USER = "user"
    PROJECT = "project"
    BUDGET = "budget"
    PROJECT_BUDGET_GROUP = "project_budget_group"
    USER_BUDGET_ASSIGNMENT = "user_budget_assignment"
    PROJECT_BUDGET_ASSIGNMENT = "project_budget_assignment"
    CUSTOMER_CONFIG_SETTING = "customer_config_setting"
    ASSISTANT = "assistant"
    HOSTED_APPLICATION = "hosted_application"


class MarketplaceModerationEvent:
    """Moderation decisions on a marketplace entity (EXECUTION-PLAN 9.3, F-35).

    These replace `mm_marketplace_event`, a table with a free-text actor, a
    float timestamp and no reader anywhere in the codebase. Each name is the
    lower-cased legacy `event_type` it supersedes, so the backfill in
    `a9c1t2i3v4e5` (`'marketplace.assistant.' || lower(e.event_type)`) and the
    live writer produce the same strings and a reader sees one continuous
    history across the cut-over.
    """

    ASSISTANT_SUBMITTED = "marketplace.assistant.submitted"
    ASSISTANT_RE_SUBMITTED = "marketplace.assistant.re_submitted"
    ASSISTANT_APPROVED = "marketplace.assistant.approved"
    ASSISTANT_REJECTED = "marketplace.assistant.rejected"
    ASSISTANT_FORCE_PUBLISHED = "marketplace.assistant.force_published"
    ASSISTANT_WITHDRAWN = "marketplace.assistant.withdrawn"
    ASSISTANT_PROMPT_VIEWED = "marketplace.assistant.prompt_viewed"
    THRESHOLD_UPDATED = "marketplace.threshold.updated"
    THRESHOLD_RESET = "marketplace.threshold.reset"


class ProjectManagementEvent:
    PROJECT_CREATED = "project.created"
    PROJECT_UPDATED = "project.updated"
    PROJECT_DELETED = "project.deleted"
    PROJECT_OWNERSHIP_CHANGED = "project.ownership_changed"
    MEMBER_SPEND_ENFORCEMENT_UPDATED = "project.member_spend_enforcement.updated"


class AssistantEvent:
    GRANT_ADDED = "assistant.grant.added"
    GRANT_REMOVED = "assistant.grant.removed"


class CustomerConfigEvent:
    SETTING_UPDATED = "customer_config.setting.updated"
    SETTING_RESET = "customer_config.setting.reset"


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
    USER_REMAPPED = "user.remapped"


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
    PROJECT_BUDGET_GROUP_DELETED = "budget.project_budget_group.deleted"
    MEMBER_ALLOCATION_OVERRIDDEN = "budget.member_allocation.overridden"
    MEMBER_ALLOCATION_OVERRIDE_CLEARED = "budget.member_allocation.override_cleared"
    PROJECT_BUDGET_REBALANCED = "budget.project_budget.rebalanced"
    PROJECT_BUDGET_RESET = "budget.project_budget.reset"
    PROJECT_BUDGET_GROUP_REBALANCED = "budget.project_budget_group.rebalanced"
    PROJECT_BUDGET_GROUP_RESET = "budget.project_budget_group.reset"
    PROJECT_BUDGET_STOPPED = "budget.project_budget.stopped"
    PROJECT_BUDGET_RESTORED = "budget.project_budget.restored"


class ActivityEvent(SQLModel, table=True):
    """Append-only audit event row. Never updated or deleted by application code."""

    __tablename__ = "activity_events"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)

    domain: str = Field(sa_column=Column(VARCHAR(64), nullable=False))
    event_type: str = Field(sa_column=Column(VARCHAR(128), nullable=False))

    entity_type: Optional[str] = Field(default=None, sa_column=Column(VARCHAR(64), nullable=True))
    entity_id: Optional[str] = Field(default=None, sa_column=Column(Text(), nullable=True))
    entity_label: Optional[str] = Field(default=None, sa_column=Column(VARCHAR(255), nullable=True))

    actor_id: Optional[str] = Field(
        default=None,
        sa_column=Column(
            VARCHAR(36),
            ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    attributes: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSONB, nullable=True))

    project_name: Optional[str] = Field(default=None, sa_column=Column(VARCHAR(255), nullable=True))
    project_instance_id: UUID | None = Field(default=None, sa_column=Column(PGUUID(as_uuid=True), nullable=True))
    actor_email: Optional[str] = Field(default=None, sa_column=Column(VARCHAR(255), nullable=True))
    actor_display_name: Optional[str] = Field(default=None, sa_column=Column(VARCHAR(255), nullable=True))

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
        Index("ix_activity_events_project_name_created_at", "project_name", "created_at"),
        Index("ix_activity_events_project_instance_id_created_at", "project_instance_id", "created_at"),
    )


class ActivityEventCreate(BaseModel):
    """Input model for emitting a new activity event.

    entity_type and entity_id are a pair: both set or both None.
    """

    domain: str
    event_type: str
    entity_type: str | None = None
    entity_id: str | None = None
    entity_label: str | None = None
    actor_id: str | None = None
    attributes: Dict[str, Any] | None = None
    project_name: str | None = None
    project_instance_id: UUID | None = None
    actor_email: str | None = None
    actor_display_name: str | None = None

    @model_validator(mode="after")
    def entity_pair_must_be_complete(self) -> ActivityEventCreate:
        if (self.entity_type is None) != (self.entity_id is None):
            raise ValueError("entity_type and entity_id must both be set or both be absent")
        if self.entity_label is not None and self.entity_type is None:
            raise ValueError("entity_label requires entity_type to be set")
        if self.project_instance_id is not None and self.project_name is None:
            raise ValueError("project_instance_id requires project_name to be set")
        if self.entity_label is not None:
            self.entity_label = self.entity_label[:255]  # column is VARCHAR(255); an overflow would drop the event
        if self.actor_id not in (None, SYSTEM_ACTOR_ID) and self.actor_email is None:
            raise ValueError("actor_email is required when actor_id is set")
        # Log, don't raise: emission is best-effort and must not break the business flow.
        if self.event_type not in EVENT_TAXONOMY:
            logger.error(f"[activity_events] event_type={self.event_type!r} has no EVENT_TAXONOMY entry")
        return self
