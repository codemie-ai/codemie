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

"""Leaderboard scoring configuration.

Dimension definitions, tier thresholds, and constants ported from
the prototype at users-leaderboard/v2/config.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field


SNAPSHOT_TYPE_ROLLING = "rolling_live"
SNAPSHOT_TYPE_MONTHLY = "season_month"
SNAPSHOT_TYPE_QUARTERLY = "season_quarter"
SNAPSHOT_TYPE_MANUAL_BACKFILL = "manual_backfill"
SNAPSHOT_TYPE_ADHOC_DEBUG = "adhoc_debug"

SEASONAL_SNAPSHOT_TYPES: tuple[str, ...] = (
    SNAPSHOT_TYPE_MONTHLY,
    SNAPSHOT_TYPE_QUARTERLY,
)

RUN_TYPE_SCHEDULED = "scheduled"
RUN_TYPE_MANUAL = "manual"
RUN_TYPE_BACKFILL = "backfill"

VIEW_CURRENT = "current"
VIEW_MONTHLY = "monthly"
VIEW_QUARTERLY = "quarterly"

VIEW_TO_SNAPSHOT_TYPE: dict[str, str] = {
    VIEW_CURRENT: SNAPSHOT_TYPE_ROLLING,
    VIEW_MONTHLY: SNAPSHOT_TYPE_MONTHLY,
    VIEW_QUARTERLY: SNAPSHOT_TYPE_QUARTERLY,
}


@dataclass(frozen=True)
class DimensionDefinition:
    """Definition of a single scoring dimension."""

    id: str
    label: str
    name: str
    weight: float
    color: str
    icon: str


DIMENSIONS: list[DimensionDefinition] = [
    DimensionDefinition("d1", "D1", "Core Platform Usage", 0.20, "#6366f1", "chart"),
    DimensionDefinition("d2", "D2", "Core Platform Creation", 0.20, "#06b6d4", "tool"),
    DimensionDefinition("d3", "D3", "Workflow Usage", 0.10, "#10b981", "refresh"),
    DimensionDefinition("d4", "D4", "Workflow Creation", 0.10, "#f59e0b", "puzzle"),
    DimensionDefinition("d5", "D5", "CLI & Agentic Engineering", 0.30, "#ef4444", "terminal"),
    DimensionDefinition("d6", "D6", "Impact & Knowledge", 0.10, "#8b5cf6", "lightbulb"),
]


@dataclass
class TierThresholds:
    """Score thresholds for each tier."""

    pioneer: float = 80.0
    expert: float = 65.0
    advanced: float = 45.0
    practitioner: float = 25.0


@dataclass
class LeaderboardSettings:
    """All leaderboard scoring settings."""

    dimensions: list[DimensionDefinition] = field(default_factory=lambda: list(DIMENSIONS))
    tiers: TierThresholds = field(default_factory=TierThresholds)
    workflow_template_keywords: tuple[str, ...] = (
        "template",
        "sample",
        "demo",
        "test",
        "copy",
        "example",
        "autoyaml",
    )
    system_creators: tuple[str, ...] = ("system", "codemie", "epm-cdme", "demo")
    cli_min_lines_for_efficiency: int = 30
    cli_min_files_for_efficiency: int = 2

    def get_tier(self, score: float) -> tuple[str, int, str]:
        """Return (tier_name, tier_level, color) for a given score."""
        if score >= self.tiers.pioneer:
            return "pioneer", 5, "#fbbf24"
        if score >= self.tiers.expert:
            return "expert", 4, "#94a3b8"
        if score >= self.tiers.advanced:
            return "advanced", 3, "#f97316"
        if score >= self.tiers.practitioner:
            return "practitioner", 2, "#818cf8"
        return "newcomer", 1, "#6b7280"


# Advisory lock ID for leaderboard scheduler — must differ from other lock IDs
# ConversationAnalysis: 987654321, SpendTracking: 987654322
LEADERBOARD_LOCK_ID = 987654323

# Singleton settings instance
leaderboard_settings = LeaderboardSettings()
