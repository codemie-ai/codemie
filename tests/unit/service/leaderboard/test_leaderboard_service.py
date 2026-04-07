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

"""Unit tests for LeaderboardService.

Covers the collect -> score -> persist pipeline, period boundary logic,
season key parsing, archive deduplication, and error handling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.leaderboard.config import (
    RUN_TYPE_BACKFILL,
    RUN_TYPE_MANUAL,
    RUN_TYPE_SCHEDULED,
    SNAPSHOT_TYPE_MONTHLY,
    SNAPSHOT_TYPE_QUARTERLY,
    VIEW_CURRENT,
    VIEW_MONTHLY,
)
from codemie.service.leaderboard.leaderboard_service import (
    LeaderboardService,
    SnapshotSpec,
)


# ---------------------------------------------------------------------------
# Lightweight stubs for scorer output so we don't import heavy modules
# ---------------------------------------------------------------------------


@dataclass
class _FakeDimensionScore:
    id: str = "d1"
    label: str = "D1"
    weight: float = 0.2
    score: float = 0.75
    components: list[dict] = field(default_factory=list)


@dataclass
class _FakeScoredEntry:
    user_id: str = "user-1"
    user_name: str = "Alice"
    user_email: str | None = "alice@test.com"
    projects: list[str] = field(default_factory=list)
    rank: int = 1
    total_score: float = 72.5
    tier_name: str = "expert"
    tier_level: int = 4
    usage_intent: str = "hybrid"
    dimensions: list[_FakeDimensionScore] = field(default_factory=lambda: [_FakeDimensionScore()])
    summary_metrics: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def mock_es_repository():
    return MagicMock()


@pytest.fixture
def mock_repository():
    repo = AsyncMock()
    repo.create_snapshot = AsyncMock()
    repo.bulk_insert_entries = AsyncMock()
    repo.update_snapshot_status = AsyncMock()
    repo.delete_old_snapshots = AsyncMock(return_value=0)
    repo.get_snapshot_by_type_and_key = AsyncMock(return_value=None)
    repo.get_prior_snapshot_by_type = AsyncMock(return_value=None)
    repo.get_latest_snapshot_by_type = AsyncMock(return_value=None)
    repo.season_snapshot_exists = AsyncMock(return_value=False)
    return repo


@pytest.fixture
def service(mock_session, mock_es_repository, mock_repository):
    svc = LeaderboardService(session=mock_session, es_repository=mock_es_repository)
    svc._repository = mock_repository
    return svc


# ---------------------------------------------------------------------------
# 1. Period boundary helpers
# ---------------------------------------------------------------------------


class TestLatestClosedMonth:
    """LeaderboardService._latest_closed_month edge cases."""

    def test_january_wraps_to_previous_year(self):
        year, month = LeaderboardService._latest_closed_month(date(2026, 1, 15))

        assert year == 2025
        assert month == 12

    def test_mid_year_returns_previous_month(self):
        year, month = LeaderboardService._latest_closed_month(date(2026, 7, 1))

        assert year == 2026
        assert month == 6


class TestLatestClosedQuarter:
    """LeaderboardService._latest_closed_quarter edge cases."""

    def test_q1_wraps_to_q4_previous_year(self):
        year, quarter = LeaderboardService._latest_closed_quarter(date(2026, 2, 10))

        assert year == 2025
        assert quarter == 4

    def test_q3_returns_q2_same_year(self):
        year, quarter = LeaderboardService._latest_closed_quarter(date(2026, 8, 20))

        assert year == 2026
        assert quarter == 2


# ---------------------------------------------------------------------------
# 2. Season key parsing
# ---------------------------------------------------------------------------


class TestParseMonthlyKey:
    """Valid and invalid monthly season key parsing."""

    def test_valid_key(self):
        year, month = LeaderboardService._parse_monthly_key("2026-03")
        assert (year, month) == (2026, 3)

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="Invalid monthly season_key"):
            LeaderboardService._parse_monthly_key("not-a-date")

    def test_month_out_of_range_raises(self):
        with pytest.raises(ValueError, match="Month must be 01-12"):
            LeaderboardService._parse_monthly_key("2026-13")


class TestParseQuarterlyKey:
    """Valid and invalid quarterly season key parsing."""

    def test_valid_key(self):
        year, quarter = LeaderboardService._parse_quarterly_key("2026-Q2")
        assert (year, quarter) == (2026, 2)

    def test_missing_q_prefix_raises(self):
        with pytest.raises(ValueError, match="Invalid quarterly season_key"):
            LeaderboardService._parse_quarterly_key("2026-2")

    def test_quarter_out_of_range_raises(self):
        with pytest.raises(ValueError, match="Quarter must be Q1-Q4"):
            LeaderboardService._parse_quarterly_key("2026-Q5")


# ---------------------------------------------------------------------------
# 3. Spec builders
# ---------------------------------------------------------------------------


class TestBuildMonthlySpec:
    """_build_monthly_spec produces correct period boundaries."""

    def test_explicit_key_february(self, service):
        spec = service._build_monthly_spec("2024-02", RUN_TYPE_MANUAL)

        assert spec.snapshot_type == SNAPSHOT_TYPE_MONTHLY
        assert spec.season_key == "2024-02"
        assert spec.period_start == datetime.combine(date(2024, 2, 1), time.min)
        assert spec.period_end == datetime.combine(date(2024, 2, 29), time.max)  # 2024 is a leap year
        assert spec.period_days == 29
        assert spec.is_final is True


class TestBuildQuarterlySpec:
    """_build_quarterly_spec produces correct period boundaries."""

    def test_explicit_key_q3(self, service):
        spec = service._build_quarterly_spec("2026-Q3", RUN_TYPE_SCHEDULED)

        assert spec.snapshot_type == SNAPSHOT_TYPE_QUARTERLY
        assert spec.season_key == "2026-Q3"
        assert spec.period_start == datetime.combine(date(2026, 7, 1), time.min)
        assert spec.period_end == datetime.combine(date(2026, 9, 30), time.max)
        assert spec.period_days == 92
        assert spec.period_label == "Q3 2026"


# ---------------------------------------------------------------------------
# 4. compute_for_view dispatching
# ---------------------------------------------------------------------------


class TestComputeForView:
    """compute_for_view routes to the correct method and sets run_type."""

    @pytest.mark.asyncio
    @patch(
        "codemie.service.leaderboard.leaderboard_service.LeaderboardService.compute_rolling_snapshot",
        new_callable=AsyncMock,
        return_value="snap-rolling",
    )
    async def test_current_view_delegates_to_rolling(self, mock_rolling, service):
        result = await service.compute_for_view(view=VIEW_CURRENT, period_days=14)

        assert result == "snap-rolling"
        mock_rolling.assert_called_once_with(period_days=14, source_run_type=RUN_TYPE_MANUAL)

    @pytest.mark.asyncio
    @patch(
        "codemie.service.leaderboard.leaderboard_service.LeaderboardService.compute_monthly_archive",
        new_callable=AsyncMock,
        return_value="snap-monthly",
    )
    async def test_monthly_view_with_season_key_uses_backfill_run_type(self, mock_monthly, service):
        result = await service.compute_for_view(view=VIEW_MONTHLY, season_key="2025-12")

        assert result == "snap-monthly"
        mock_monthly.assert_called_once_with(season_key="2025-12", source_run_type=RUN_TYPE_BACKFILL)

    @pytest.mark.asyncio
    async def test_unsupported_view_raises(self, service):
        with pytest.raises(ValueError, match="Unsupported leaderboard view"):
            await service.compute_for_view(view="invalid_view")


# ---------------------------------------------------------------------------
# 5. Rolling snapshot pipeline (collect -> score -> persist -> cleanup)
# ---------------------------------------------------------------------------


class TestComputeRollingSnapshot:
    """End-to-end rolling snapshot happy path."""

    @pytest.mark.asyncio
    @patch("codemie.service.leaderboard.leaderboard_service.LeaderboardScorer")
    @patch("codemie.service.leaderboard.leaderboard_service.LeaderboardCollector")
    @patch("codemie.service.leaderboard.leaderboard_service._utcnow_naive")
    async def test_happy_path_creates_entries_and_cleans_up(
        self,
        mock_utcnow,
        mock_collector_cls,
        mock_scorer_cls,
        service,
        mock_repository,
        mock_session,
    ):
        # Arrange
        mock_utcnow.return_value = datetime(2026, 4, 6, 12, 0, 0)
        fake_snapshot = MagicMock(id="snap-123")
        mock_repository.create_snapshot.return_value = fake_snapshot

        mock_collector = AsyncMock()
        mock_collector.collect.return_value = [MagicMock()]
        mock_collector_cls.return_value = mock_collector

        mock_scorer = MagicMock()
        mock_scorer.score_all.return_value = [_FakeScoredEntry()]
        mock_scorer_cls.return_value = mock_scorer

        # Act
        result = await service.compute_rolling_snapshot(period_days=30)

        # Assert
        assert result == "snap-123"
        mock_collector.collect.assert_called_once()
        mock_scorer.score_all.assert_called_once()
        mock_repository.bulk_insert_entries.assert_called_once()
        mock_repository.update_snapshot_status.assert_called_once_with(
            mock_session,
            "snap-123",
            "completed",
            total_users=1,
        )
        mock_repository.delete_old_snapshots.assert_called_once()
        mock_session.commit.assert_called()


# ---------------------------------------------------------------------------
# 6. Error handling in _compute_snapshot
# ---------------------------------------------------------------------------


class TestComputeSnapshotErrorHandling:
    """Snapshot is marked failed when the pipeline raises."""

    @pytest.mark.asyncio
    @patch("codemie.service.leaderboard.leaderboard_service.LeaderboardCollector")
    @patch("codemie.service.leaderboard.leaderboard_service._utcnow_naive")
    async def test_marks_snapshot_failed_on_collector_error(
        self,
        mock_utcnow,
        mock_collector_cls,
        service,
        mock_repository,
        mock_session,
    ):
        # Arrange
        mock_utcnow.return_value = datetime(2026, 4, 6, 12, 0, 0)
        fake_snapshot = MagicMock(id="snap-fail")
        mock_repository.create_snapshot.return_value = fake_snapshot

        mock_collector = AsyncMock()
        mock_collector.collect.side_effect = RuntimeError("ES timeout")
        mock_collector_cls.return_value = mock_collector

        # Act / Assert
        with pytest.raises(RuntimeError, match="ES timeout"):
            await service.compute_rolling_snapshot(period_days=7)

        mock_repository.update_snapshot_status.assert_called_with(
            mock_session,
            "snap-fail",
            "failed",
            error="ES timeout",
        )


# ---------------------------------------------------------------------------
# 7. compute_missing_archives skips existing
# ---------------------------------------------------------------------------


class TestComputeMissingArchives:
    """Only archives that don't already exist should be created."""

    @pytest.mark.asyncio
    @patch(
        "codemie.service.leaderboard.leaderboard_service.LeaderboardService._compute_final_snapshot",
        new_callable=AsyncMock,
    )
    @patch("codemie.service.leaderboard.leaderboard_service._utcnow_naive")
    async def test_skips_existing_creates_missing(
        self,
        mock_utcnow,
        mock_compute_final,
        service,
        mock_repository,
    ):
        # Arrange: monthly exists, quarterly does not
        mock_utcnow.return_value = datetime(2026, 4, 6, 12, 0, 0)
        mock_repository.season_snapshot_exists.side_effect = [True, False]
        mock_compute_final.return_value = "snap-quarterly"

        # Act
        result = await service.compute_missing_archives()

        # Assert: only quarterly was computed
        assert result == ["snap-quarterly"]
        mock_compute_final.assert_called_once()


# ---------------------------------------------------------------------------
# 8. _compute_final_snapshot deduplication
# ---------------------------------------------------------------------------


class TestComputeFinalSnapshotDedup:
    """Existing completed archive should be returned without recomputation."""

    @pytest.mark.asyncio
    async def test_returns_existing_snapshot_id(self, service, mock_repository):
        # Arrange
        existing = MagicMock(id="snap-existing")
        mock_repository.get_snapshot_by_type_and_key.return_value = existing
        spec = SnapshotSpec(
            period_start=datetime(2026, 3, 1),
            period_end=datetime(2026, 3, 31, 23, 59, 59),
            period_days=31,
            snapshot_type=SNAPSHOT_TYPE_MONTHLY,
            season_key="2026-03",
            period_label="March 2026",
            is_final=True,
            source_run_type=RUN_TYPE_SCHEDULED,
        )

        # Act
        result = await service._compute_final_snapshot(spec)

        # Assert
        assert result == "snap-existing"
        mock_repository.create_snapshot.assert_not_called()
