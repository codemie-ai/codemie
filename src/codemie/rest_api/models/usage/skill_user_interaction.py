# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""
Models for tracking skill usage by users.
Similar pattern to AssistantUserInteraction.
"""

from datetime import datetime, UTC
from enum import Enum
from typing import Optional
from uuid import uuid4

from sqlmodel import Field, Index
from sqlalchemy import UniqueConstraint

from codemie.rest_api.models.base import BaseModelWithSQLSupport, CommonBaseModel


class ReactionType(str, Enum):
    """Enum for reaction types"""

    LIKE = "like"
    DISLIKE = "dislike"


class SkillUserInteractionBase(CommonBaseModel):
    """Base model for tracking skill usage by unique users"""

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    skill_id: str = Field(index=True)
    user_id: str = Field(index=True)
    first_used_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    usage_count: int = Field(default=1)
    reaction: Optional[ReactionType] = Field(default=None)
    reaction_at: Optional[datetime] = Field(default=None)
    project: Optional[str] = Field(default=None, index=True)

    __table_args__ = (
        UniqueConstraint("skill_id", "user_id", name="uix_skill_user"),
        Index("ix_skill_usage_skill_id", "skill_id"),
        Index("ix_skill_usage_user_id", "user_id"),
        Index("ix_skill_usage_project", "project"),
        Index("ix_skill_usage_reaction", "reaction"),
        Index("ix_skill_usage_last_used_at", "last_used_at"),
    )


class SkillUserInteraction(BaseModelWithSQLSupport, SkillUserInteractionBase, table=True):
    """SQLModel version for PostgreSQL storage"""

    __tablename__ = "skill_user_interaction"
