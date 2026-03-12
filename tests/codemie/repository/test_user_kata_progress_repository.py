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
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from codemie.repository.user_kata_progress_repository import (
    UserKataProgressRepository,
    SQLUserKataProgressRepository,
)
from codemie.rest_api.models.user_kata_progress import UserKataProgress, KataProgressStatus, LeaderboardEntryFromDB
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_session():
    """Mock SQLModel Session."""
    return MagicMock(spec=Session)


@pytest.fixture
def mock_engine():
    """Mock database engine."""
    return MagicMock()


@pytest.fixture
def repository():
    """Repository instance."""
    return SQLUserKataProgressRepository()


@pytest.fixture
def sample_user():
    """Sample user fixture."""
    return User(id="user123", username="testuser", name="Test User")


@pytest.fixture
def sample_progress():
    """Sample progress entity."""
    return UserKataProgress(
        id=str(uuid4()),
        user_id="user123",
        kata_id="kata456",
        user_name="Test User",
        user_username="testuser",
        status=KataProgressStatus.IN_PROGRESS,
        started_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        completed_at=None,
        date=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        update_date=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
    )


@pytest.fixture
def sample_completed_progress():
    """Sample completed progress entity."""
    return UserKataProgress(
        id=str(uuid4()),
        user_id="user123",
        kata_id="kata789",
        user_name="Test User",
        user_username="testuser",
        status=KataProgressStatus.COMPLETED,
        started_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        completed_at=datetime(2024, 1, 5, 12, 0, 0, tzinfo=UTC),
        date=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        update_date=datetime(2024, 1, 5, 12, 0, 0, tzinfo=UTC),
    )


class TestUserKataProgressRepositoryStartKata:
    """Tests for start_kata method."""

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_start_kata_success(self, mock_session_cls, mock_get_engine, repository, sample_user):
        """Test starting kata successfully."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.first.return_value = None  # Not already enrolled
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        progress_id = repository.start_kata(sample_user, "kata456")

        assert progress_id is not None
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once()

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_start_kata_already_enrolled(
        self, mock_session_cls, mock_get_engine, repository, sample_user, sample_progress
    ):
        """Test starting kata when already enrolled."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.first.return_value = sample_progress  # Already enrolled
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        with pytest.raises(ValueError, match="You are already enrolled in this kata"):
            repository.start_kata(sample_user, "kata456")

        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_called()

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_start_kata_integrity_error(self, mock_session_cls, mock_get_engine, repository, sample_user):
        """Test starting kata with IntegrityError (race condition)."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.exec.return_value = mock_result
        mock_session.commit.side_effect = IntegrityError("msg", "params", "orig")
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        with pytest.raises(ValueError, match="You are already enrolled in this kata"):
            repository.start_kata(sample_user, "kata456")

        mock_session.rollback.assert_called_once()


class TestUserKataProgressRepositoryCompleteKata:
    """Tests for complete_kata method."""

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_complete_kata_success(self, mock_session_cls, mock_get_engine, repository, sample_progress):
        """Test completing kata successfully."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.first.return_value = sample_progress
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.complete_kata("user123", "kata456")

        assert result is True
        assert sample_progress.status == KataProgressStatus.COMPLETED
        assert sample_progress.completed_at is not None
        assert sample_progress.update_date is not None
        mock_session.add.assert_called_once_with(sample_progress)
        mock_session.commit.assert_called_once()

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_complete_kata_not_enrolled(self, mock_session_cls, mock_get_engine, repository):
        """Test completing kata when not enrolled."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.first.return_value = None  # Not enrolled
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        with pytest.raises(ValueError, match="You are not enrolled in this kata"):
            repository.complete_kata("user123", "kata456")

        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_called()

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_complete_kata_exception_handling(self, mock_session_cls, mock_get_engine, repository, sample_progress):
        """Test complete_kata handles unexpected exceptions."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.first.return_value = sample_progress
        mock_session.exec.return_value = mock_result
        mock_session.commit.side_effect = Exception("Database error")
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        with pytest.raises(Exception, match="Database error"):
            repository.complete_kata("user123", "kata456")


class TestUserKataProgressRepositoryGetUserProgress:
    """Tests for get_user_progress method."""

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_get_user_progress_found(self, mock_session_cls, mock_get_engine, repository, sample_progress):
        """Test getting user progress when it exists."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.first.return_value = sample_progress
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.get_user_progress("user123", "kata456")

        assert result == sample_progress
        mock_session.exec.assert_called_once()

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_get_user_progress_not_found(self, mock_session_cls, mock_get_engine, repository):
        """Test getting user progress when it doesn't exist."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.get_user_progress("user123", "kata456")

        assert result is None


class TestUserKataProgressRepositoryGetUserAllProgress:
    """Tests for get_user_all_progress method."""

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_get_user_all_progress_no_filter(
        self, mock_session_cls, mock_get_engine, repository, sample_progress, sample_completed_progress
    ):
        """Test getting all user progress without status filter."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [sample_progress, sample_completed_progress]
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.get_user_all_progress("user123")

        assert len(result) == 2
        assert sample_progress in result
        assert sample_completed_progress in result

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_get_user_all_progress_with_status_filter(
        self, mock_session_cls, mock_get_engine, repository, sample_completed_progress
    ):
        """Test getting user progress filtered by status."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [sample_completed_progress]
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.get_user_all_progress("user123", status=KataProgressStatus.COMPLETED)

        assert len(result) == 1
        assert result[0] == sample_completed_progress

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_get_user_all_progress_empty(self, mock_session_cls, mock_get_engine, repository):
        """Test getting user progress when user has no progress."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.get_user_all_progress("user123")

        assert len(result) == 0


class TestUserKataProgressRepositoryGetKataEnrollmentCount:
    """Tests for get_kata_enrollment_count method."""

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_get_kata_enrollment_count(self, mock_session_cls, mock_get_engine, repository):
        """Test getting kata enrollment count."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.one.return_value = 5
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        count = repository.get_kata_enrollment_count("kata456")

        assert count == 5

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_get_kata_enrollment_count_zero(self, mock_session_cls, mock_get_engine, repository):
        """Test getting kata enrollment count when no enrollments."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.one.return_value = 0
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        count = repository.get_kata_enrollment_count("kata456")

        assert count == 0


class TestUserKataProgressRepositoryGetLeaderboard:
    """Tests for get_leaderboard method."""

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_get_leaderboard_with_results(self, mock_session_cls, mock_get_engine, repository):
        """Test getting leaderboard with results."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            ("user1", "User One", "userone", 10, 5),
            ("user2", "User Two", "usertwo", 8, 3),
            ("user3", "User Three", "userthree", 5, 10),
        ]
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.get_leaderboard(limit=100)

        assert len(result) == 3
        assert isinstance(result[0], LeaderboardEntryFromDB)
        assert result[0].user_id == "user1"
        assert result[0].user_name == "User One"
        assert result[0].user_username == "userone"
        assert result[0].completed_count == 10
        assert result[0].in_progress_count == 5
        assert result[1].user_id == "user2"
        assert result[2].user_id == "user3"

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_get_leaderboard_custom_limit(self, mock_session_cls, mock_get_engine, repository):
        """Test getting leaderboard with custom limit."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [("user1", "User One", "userone", 10, 5)]
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        repository.get_leaderboard(limit=10)

        mock_session.exec.assert_called_once()

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_get_leaderboard_empty(self, mock_session_cls, mock_get_engine, repository):
        """Test getting leaderboard when no data."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.get_leaderboard()

        assert len(result) == 0


class TestUserKataProgressRepositoryBulkGetUserProgress:
    """Tests for bulk_get_user_progress method."""

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_bulk_get_user_progress_with_results(
        self, mock_session_cls, mock_get_engine, repository, sample_progress, sample_completed_progress
    ):
        """Test bulk getting user progress with results."""
        sample_progress.kata_id = "kata1"
        sample_completed_progress.kata_id = "kata2"

        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [sample_progress, sample_completed_progress]
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.bulk_get_user_progress("user123", ["kata1", "kata2", "kata3"])

        assert len(result) == 2
        assert "kata1" in result
        assert "kata2" in result
        assert "kata3" not in result
        assert result["kata1"] == sample_progress
        assert result["kata2"] == sample_completed_progress

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_bulk_get_user_progress_empty_list(self, mock_session_cls, mock_get_engine, repository):
        """Test bulk getting user progress with empty kata list."""
        result = repository.bulk_get_user_progress("user123", [])

        assert result == {}

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_bulk_get_user_progress_no_matches(self, mock_session_cls, mock_get_engine, repository):
        """Test bulk getting user progress with no matches."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.bulk_get_user_progress("user123", ["kata1", "kata2"])

        assert len(result) == 0


class TestUserKataProgressRepositoryBulkGetEnrollmentCounts:
    """Tests for bulk_get_enrollment_counts method."""

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_bulk_get_enrollment_counts_with_results(self, mock_session_cls, mock_get_engine, repository):
        """Test bulk getting enrollment counts with results."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [("kata1", 5), ("kata2", 10), ("kata3", 3)]
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.bulk_get_enrollment_counts(["kata1", "kata2", "kata3"])

        assert len(result) == 3
        assert result["kata1"] == 5
        assert result["kata2"] == 10
        assert result["kata3"] == 3

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_bulk_get_enrollment_counts_empty_list(self, mock_session_cls, mock_get_engine, repository):
        """Test bulk getting enrollment counts with empty kata list."""
        result = repository.bulk_get_enrollment_counts([])

        assert result == {}

    @patch("codemie.rest_api.models.user_kata_progress.UserKataProgress.get_engine")
    @patch("codemie.repository.user_kata_progress_repository.Session")
    def test_bulk_get_enrollment_counts_partial_results(self, mock_session_cls, mock_get_engine, repository):
        """Test bulk getting enrollment counts with partial results."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [("kata1", 5)]
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.bulk_get_enrollment_counts(["kata1", "kata2", "kata3"])

        assert len(result) == 1
        assert result["kata1"] == 5
        assert "kata2" not in result
        assert "kata3" not in result


class TestUserKataProgressRepositoryAbstractClass:
    """Tests for abstract base class."""

    def test_abstract_class_cannot_instantiate(self):
        """Test that abstract class cannot be instantiated."""
        with pytest.raises(TypeError):
            UserKataProgressRepository()

    def test_concrete_class_implements_all_methods(self):
        """Test that SQLUserKataProgressRepository implements all abstract methods."""
        abstract_methods = [
            "start_kata",
            "complete_kata",
            "get_user_progress",
            "get_user_all_progress",
            "get_kata_enrollment_count",
            "get_leaderboard",
            "bulk_get_user_progress",
            "bulk_get_enrollment_counts",
        ]

        repo = SQLUserKataProgressRepository()
        for method in abstract_methods:
            assert hasattr(repo, method)
            assert callable(getattr(repo, method))
