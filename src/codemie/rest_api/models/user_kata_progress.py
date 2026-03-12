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

from __future__ import annotations

from datetime import datetime, UTC
from enum import Enum
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field

from codemie.rest_api.models.base import BaseModelWithSQLSupport


class KataProgressStatus(str, Enum):
    """
    Kata progress status enum.

    States:
    - NOT_STARTED: User has not enrolled in the kata yet (virtual state, not stored in DB)
    - IN_PROGRESS: User has enrolled and is working on the kata
    - COMPLETED: User has finished the kata
    """

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class UserKataProgress(BaseModelWithSQLSupport, table=True):
    __tablename__ = "user_kata_progress"

    # Foreign keys
    user_id: str = Field(index=True)
    kata_id: str = Field(index=True)

    # User information (denormalized for leaderboard performance)
    user_name: str = Field(default="")
    user_username: str = Field(default="")

    # Progress tracking
    status: KataProgressStatus = Field(default=KataProgressStatus.IN_PROGRESS)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: Optional[datetime] = None

    # Indexes and constraints
    __table_args__ = (
        UniqueConstraint('user_id', 'kata_id', name='uq_user_kata'),
        Index('idx_user_status', 'user_id', 'status'),
        Index('idx_kata_status', 'kata_id', 'status'),
    )


class ReactionType(str, Enum):
    """User reaction types"""

    LIKE = "like"
    DISLIKE = "dislike"


class UserKataProgressResponse(BaseModel):
    """
    Response model for user progress.

    Note: For NOT_STARTED status, id, started_at, and completed_at will be None.
    """

    id: Optional[str] = None
    user_id: str
    kata_id: str
    status: KataProgressStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    user_reaction: Optional[ReactionType] = None


class UserLeaderboardEntry(BaseModel):
    """Leaderboard entry model"""

    user_id: str
    user_name: str
    username: str
    completed_count: int
    in_progress_count: int
    rank: int


class LeaderboardEntryFromDB(BaseModel):
    """Leaderboard entry model from database (without rank)"""

    user_id: str
    user_name: str
    user_username: str
    completed_count: int
    in_progress_count: int
