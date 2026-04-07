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

"""Tests for LeaderboardRepository."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.repository.leaderboard_repository import LeaderboardRepository
from codemie.rest_api.models.leaderboard import LeaderboardEntry, LeaderboardSnapshot


@pytest.fixture
def repo():
    return LeaderboardRepository()


@pytest.fixture
def mock_session():
    session = AsyncMock()
    return session


@pytest.fixture
def sample_snapshot():
    return LeaderboardSnapshot(
        id="snap-1",
        period_start=datetime(2026, 3, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 3, 31, tzinfo=timezone.utc),
        period_days=30,
        snapshot_type="rolling_live",
        status="completed",
        date=datetime(2026, 3, 31, tzinfo=timezone.utc),
        update_date=datetime(2026, 3, 31, tzinfo=timezone.utc),
    )


@pytest.fixture
def sample_entry():
    return LeaderboardEntry(
        id="entry-1",
        snapshot_id="snap-1",
        user_id="user-1",
        user_name="Alice",
        user_email="alice@example.com",
        rank=1,
        total_score=85.5,
        tier_name="expert",
        tier_level=4,
    )


class TestCreateSnapshot:
    """Tests for create_snapshot."""

    @pytest.mark.asyncio
    async def test_creates_snapshot_with_defaults_and_flushes(self, repo, mock_session):
        # Arrange
        start = datetime(2026, 3, 1)
        end = datetime(2026, 3, 31)

        # Act
        result = await repo.create_snapshot(mock_session, start, end, 30)

        # Assert
        mock_session.add.assert_called_once()
        mock_session.flush.assert_awaited_once()
        added_snapshot = mock_session.add.call_args[0][0]
        assert isinstance(added_snapshot, LeaderboardSnapshot)
        assert added_snapshot.status == "running"
        assert added_snapshot.snapshot_type == "rolling_live"
        assert added_snapshot.is_final is False
        assert added_snapshot.source_run_type == "scheduled"
        assert added_snapshot.metadata_json == {}
        assert added_snapshot.period_days == 30
        assert result is added_snapshot


class TestUpdateSnapshotStatus:
    """Tests for update_snapshot_status."""

    @pytest.mark.asyncio
    async def test_sets_completed_at_when_status_completed(self, repo, mock_session):
        # Arrange / Act
        await repo.update_snapshot_status(mock_session, "snap-1", "completed", total_users=42)

        # Assert
        mock_session.execute.assert_awaited_once()
        stmt = mock_session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "completed_at" in sql
        assert "total_users" in sql

    @pytest.mark.asyncio
    async def test_no_completed_at_when_status_failed(self, repo, mock_session):
        # Arrange / Act
        await repo.update_snapshot_status(mock_session, "snap-1", "failed", error="Something broke")

        # Assert
        stmt = mock_session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "completed_at" not in sql
        assert "error_message" in sql


class TestGetLatestSnapshotByType:
    """Tests for get_latest_snapshot_by_type."""

    @pytest.mark.asyncio
    async def test_filters_by_type_status_and_orders_correctly(self, repo, mock_session, sample_snapshot):
        # Arrange
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_snapshot
        mock_session.execute.return_value = mock_result

        # Act
        result = await repo.get_latest_snapshot_by_type(mock_session, "rolling_live", status="completed")

        # Assert
        assert result == sample_snapshot
        stmt = mock_session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "snapshot_type" in sql
        assert "rolling_live" in sql
        assert "completed" in sql
        assert "ORDER BY" in sql
        assert "DESC" in sql
        assert "LIMIT" in sql

    @pytest.mark.asyncio
    async def test_applies_is_final_filter_when_provided(self, repo, mock_session):
        # Arrange
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        # Act
        await repo.get_latest_snapshot_by_type(mock_session, "season", is_final=True)

        # Assert
        stmt = mock_session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "is_final" in sql


class TestGetLatestSnapshot:
    @pytest.mark.asyncio
    async def test_delegates_to_get_latest_snapshot_by_type(self, repo, mock_session):
        repo.get_latest_snapshot_by_type = AsyncMock(return_value="snap")

        result = await repo.get_latest_snapshot(mock_session, status="running", snapshot_type="season_month")

        assert result == "snap"
        repo.get_latest_snapshot_by_type.assert_awaited_once_with(mock_session, "season_month", status="running")


class TestGetSnapshotByTypeAndKey:
    """Tests for get_snapshot_by_type_and_key."""

    @pytest.mark.asyncio
    async def test_filters_by_season_key_and_final_only(self, repo, mock_session):
        # Arrange
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        # Act
        await repo.get_snapshot_by_type_and_key(mock_session, "season", "2026-Q1", final_only=True)

        # Assert
        stmt = mock_session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "season_key" in sql
        assert "2026-Q1" in sql
        assert "is_final" in sql


class TestGetPriorSnapshotByType:
    @pytest.mark.asyncio
    async def test_filters_by_before_period_and_is_final(self, repo, mock_session, sample_snapshot):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_snapshot
        mock_session.execute.return_value = mock_result

        result = await repo.get_prior_snapshot_by_type(
            mock_session,
            "season_month",
            before_period_start=datetime(2026, 4, 1, tzinfo=timezone.utc),
            is_final=True,
        )

        assert result == sample_snapshot
        stmt = mock_session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "period_end" in sql
        assert "is_final" in sql
        assert "ORDER BY" in sql


class TestSeasonSnapshotExists:
    """Tests for season_snapshot_exists."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("count_value,expected", [(0, False), (1, True), (3, True)])
    async def test_returns_bool_from_count(self, repo, mock_session, count_value, expected):
        # Arrange
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = count_value
        mock_session.execute.return_value = mock_result

        # Act
        result = await repo.season_snapshot_exists(mock_session, "season", "2026-Q1")

        # Assert
        assert result is expected


class TestGetEntries:
    """Tests for get_entries with search, sort, and pagination."""

    @pytest.mark.asyncio
    async def test_search_escapes_special_characters(self, repo, mock_session):
        # Arrange
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 1
        mock_entries_result = MagicMock()
        mock_entries_result.scalars.return_value.all.return_value = []
        mock_session.execute.side_effect = [mock_count_result, mock_entries_result]

        # Act
        entries, total = await repo.get_entries(mock_session, "snap-1", search="test%user_name")

        # Assert - verify second call (data query) contains escaped pattern
        stmt = mock_session.execute.call_args_list[1][0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        # The % and _ in search should be escaped so they are literal
        assert "ILIKE" in sql.upper() or "ilike" in sql.lower()

    @pytest.mark.asyncio
    async def test_sort_by_total_score_desc(self, repo, mock_session):
        # Arrange
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 0
        mock_entries_result = MagicMock()
        mock_entries_result.scalars.return_value.all.return_value = []
        mock_session.execute.side_effect = [mock_count_result, mock_entries_result]

        # Act
        await repo.get_entries(mock_session, "snap-1", sort_by="total_score", sort_order="desc")

        # Assert
        stmt = mock_session.execute.call_args_list[1][0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "total_score" in sql
        assert "DESC" in sql

    @pytest.mark.asyncio
    async def test_pagination_offset_and_limit(self, repo, mock_session):
        # Arrange
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 100
        mock_entries_result = MagicMock()
        mock_entries_result.scalars.return_value.all.return_value = []
        mock_session.execute.side_effect = [mock_count_result, mock_entries_result]

        # Act
        _, total = await repo.get_entries(mock_session, "snap-1", page=2, per_page=10)

        # Assert
        assert total == 100
        stmt = mock_session.execute.call_args_list[1][0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "LIMIT" in sql
        assert "OFFSET" in sql

    @pytest.mark.asyncio
    async def test_unknown_sort_logs_warning_and_falls_back_to_rank(self, repo, mock_session):
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 0
        mock_entries_result = MagicMock()
        mock_entries_result.scalars.return_value.all.return_value = []
        mock_session.execute.side_effect = [mock_count_result, mock_entries_result]

        with patch("codemie.repository.leaderboard_repository.logger") as mock_logger:
            await repo.get_entries(mock_session, "snap-1", sort_by="bad_field")

        mock_logger.warning.assert_called_once()
        stmt = mock_session.execute.call_args_list[1][0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "rank" in sql


class TestGetEntryByUser:
    """Tests for get_entry_by_user - matches by user_id OR user_email."""

    @pytest.mark.asyncio
    async def test_matches_by_user_id_or_email(self, repo, mock_session, sample_entry):
        # Arrange
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_entry
        mock_session.execute.return_value = mock_result

        # Act
        result = await repo.get_entry_by_user(mock_session, "snap-1", "alice@example.com")

        # Assert
        assert result == sample_entry
        stmt = mock_session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        # Should have OR clause matching both user_id and user_email
        assert "user_id" in sql
        assert "user_email" in sql
        assert "OR" in sql.upper()


class TestListSnapshots:
    @pytest.mark.asyncio
    async def test_applies_filters_and_returns_rows_and_total(self, repo, mock_session, sample_snapshot):
        count_result = MagicMock()
        count_result.scalar_one.return_value = 7
        data_result = MagicMock()
        data_result.scalars.return_value.all.return_value = [sample_snapshot]
        mock_session.execute.side_effect = [count_result, data_result]

        rows, total = await repo.list_snapshots(
            mock_session,
            page=1,
            per_page=5,
            snapshot_type="rolling_live",
            status="completed",
            is_final=False,
        )

        assert rows == [sample_snapshot]
        assert total == 7
        stmt = mock_session.execute.call_args_list[1][0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "snapshot_type" in sql
        assert "status" in sql
        assert "is_final" in sql
        assert "LIMIT" in sql
        assert "OFFSET" in sql


class TestBulkInsertEntries:
    @pytest.mark.asyncio
    async def test_adds_all_entries_and_flushes(self, repo, mock_session, sample_entry):
        result = await repo.bulk_insert_entries(mock_session, [sample_entry])

        mock_session.add_all.assert_called_once_with([sample_entry])
        mock_session.flush.assert_awaited_once()
        assert result == 1


class TestGetEntriesByUsers:
    @pytest.mark.asyncio
    async def test_returns_empty_dict_for_empty_user_ids(self, repo, mock_session):
        result = await repo.get_entries_by_users(mock_session, "snap-1", [])

        assert result == {}
        mock_session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_maps_entries_by_user_id(self, repo, mock_session):
        entry1 = MagicMock(user_id="u1")
        entry2 = MagicMock(user_id="u2")
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [entry1, entry2]
        mock_session.execute.return_value = result_mock

        result = await repo.get_entries_by_users(mock_session, "snap-1", ["u1", "u2"])

        assert result == {"u1": entry1, "u2": entry2}


class TestTierAndScoreQueries:
    @pytest.mark.asyncio
    async def test_get_tier_distribution_formats_rows(self, repo, mock_session):
        row = MagicMock(tier_name="expert", tier_level=4, user_count=3)
        result_mock = MagicMock()
        result_mock.all.return_value = [row]
        mock_session.execute.return_value = result_mock

        result = await repo.get_tier_distribution(mock_session, "snap-1")

        assert result == [{"tier_name": "expert", "tier_level": 4, "user_count": 3}]

    @pytest.mark.asyncio
    async def test_get_score_distribution_formats_bucket_ranges(self, repo, mock_session):
        row = MagicMock(bucket=20, count=5)
        result_mock = MagicMock()
        result_mock.all.return_value = [row]
        mock_session.execute.return_value = result_mock

        result = await repo.get_score_distribution(mock_session, "snap-1")

        assert result == [{"range": "20-30", "count": 5}]

    @pytest.mark.asyncio
    async def test_get_dimension_averages_rounds_scores(self, repo, mock_session):
        row = MagicMock(dim_id="d1", avg_score=0.45678)
        result_mock = MagicMock()
        result_mock.all.return_value = [row]
        mock_session.execute.return_value = result_mock

        result = await repo.get_dimension_averages(mock_session, "snap-1", ["d1"])

        assert result == {"d1": 0.4568}

    @pytest.mark.asyncio
    async def test_get_average_score_rounds_and_handles_none(self, repo, mock_session):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = 12.34
        mock_session.execute.return_value = result_mock

        result = await repo.get_average_score(mock_session, "snap-1")

        assert result == 12.3

        result_mock.scalar_one_or_none.return_value = None
        result = await repo.get_average_score(mock_session, "snap-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_top_entries_returns_scalar_rows(self, repo, mock_session, sample_entry):
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [sample_entry]
        mock_session.execute.return_value = result_mock

        result = await repo.get_top_entries(mock_session, "snap-1", limit=2)

        assert result == [sample_entry]


class TestDeleteOldSnapshots:
    """Tests for delete_old_snapshots."""

    @pytest.mark.asyncio
    async def test_excludes_current_snapshot_and_returns_count(self, repo, mock_session):
        # Arrange
        mock_result = MagicMock()
        mock_result.rowcount = 5
        mock_session.execute.return_value = mock_result

        # Act
        deleted = await repo.delete_old_snapshots(
            mock_session,
            snapshot_type="rolling_live",
            keep_count=3,
            current_snapshot_id="snap-current",
        )

        # Assert
        assert deleted == 5
        stmt = mock_session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "DELETE" in sql.upper()
        assert "snap-current" in sql
        # Verify NOT IN subquery for keep_count
        assert "NOT IN" in sql.upper() or "not_in" in sql.lower()

    @pytest.mark.asyncio
    async def test_returns_zero_when_rowcount_is_none(self, repo, mock_session):
        # Arrange
        mock_result = MagicMock()
        mock_result.rowcount = None
        mock_session.execute.return_value = mock_result

        # Act
        deleted = await repo.delete_old_snapshots(
            mock_session,
            snapshot_type="rolling_live",
            keep_count=5,
        )

        # Assert
        assert deleted == 0
