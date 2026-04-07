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

"""SQLModel definitions for leaderboard tables.

Stores pre-computed leaderboard snapshots and per-user scored entries.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from codemie.rest_api.models.base import BaseModelWithSQLSupport


class LeaderboardSnapshot(BaseModelWithSQLSupport, table=True):
    """Tracks each leaderboard computation run."""

    __tablename__ = "leaderboard_snapshots"

    period_start: datetime
    period_end: datetime
    period_days: int = 30
    snapshot_type: str = Field(default="rolling_live", index=True)
    season_key: str | None = Field(default=None, index=True)
    period_label: str | None = None
    is_final: bool = False
    source_run_type: str = "scheduled"
    comparison_snapshot_id: str | None = Field(default=None, foreign_key="leaderboard_snapshots.id")
    total_users: int = 0
    status: str = "running"  # running, completed, failed
    error_message: str | None = Field(default=None, sa_column=Column("error_message", Text))
    completed_at: datetime | None = None
    metadata_json: dict | None = Field(default_factory=dict, sa_column=Column("metadata", JSONB))


class LeaderboardEntry(BaseModelWithSQLSupport, table=True):
    """One row per user per snapshot with full scored result."""

    __tablename__ = "leaderboard_entries"

    snapshot_id: str = Field(foreign_key="leaderboard_snapshots.id", index=True)
    user_id: str = Field(index=True)
    user_name: str | None = None
    user_email: str | None = None
    rank: int
    total_score: float = 0.0
    tier_name: str
    tier_level: int = 0
    usage_intent: str | None = None
    dimensions: list | None = Field(default_factory=list, sa_column=Column("dimensions", JSONB))
    summary_metrics: dict | None = Field(default_factory=dict, sa_column=Column("summary_metrics", JSONB))
    projects: list | None = Field(default_factory=list, sa_column=Column("projects", JSONB))
