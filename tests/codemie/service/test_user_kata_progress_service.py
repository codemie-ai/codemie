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
from unittest.mock import MagicMock

import pytest
from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.ai_kata import AIKata, KataLevel, KataStatus
from codemie.rest_api.models.user_kata_progress import (
    KataProgressStatus,
    UserKataProgress,
    UserKataProgressResponse,
    UserLeaderboardEntry,
    LeaderboardEntryFromDB,
)
from codemie.rest_api.security.user import User
from codemie.service.user_kata_progress_service import UserKataProgressService


@pytest.fixture
def mock_progress_repository():
    """Mock progress repository."""
    from codemie.repository.user_kata_progress_repository import UserKataProgressRepository

    return MagicMock(spec=UserKataProgressRepository)


@pytest.fixture
def mock_kata_repository():
    """Mock kata repository."""
    from codemie.repository.ai_kata_repository import AIKataRepository

    return MagicMock(spec=AIKataRepository)


@pytest.fixture
def progress_service(mock_progress_repository, mock_kata_repository):
    """Progress service with mocked repositories."""
    service = UserKataProgressService.model_construct()
    service.repository = mock_progress_repository
    service.kata_repository = mock_kata_repository
    return service


@pytest.fixture
def sample_user():
    """Sample user fixture."""
    return User(id="user123", username="testuser", name="Test User")


@pytest.fixture
def sample_kata():
    """Sample kata entity."""
    return AIKata(
        id="kata123",
        title="Test Kata",
        description="Test kata description",
        steps="# Step 1\nContent 1",
        level=KataLevel.BEGINNER,
        creator_id="admin123",
        duration_minutes=30,
        tags=["python"],
        roles=["developer"],
        status=KataStatus.PUBLISHED,
        date=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
    )


@pytest.fixture
def sample_progress():
    """Sample user progress entity."""
    return UserKataProgress(
        id="progress123",
        user_id="user123",
        kata_id="kata123",
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
        id="progress456",
        user_id="user123",
        kata_id="kata123",
        user_name="Test User",
        user_username="testuser",
        status=KataProgressStatus.COMPLETED,
        started_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        completed_at=datetime(2024, 1, 2, 12, 0, 0, tzinfo=UTC),
        date=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        update_date=datetime(2024, 1, 2, 12, 0, 0, tzinfo=UTC),
    )


# start_kata tests


def test_start_kata_success(progress_service, sample_user, sample_kata, mock_progress_repository, mock_kata_repository):
    """Test successful kata enrollment."""
    mock_kata_repository.get_by_id.return_value = sample_kata
    mock_progress_repository.start_kata.return_value = "progress123"

    progress_id = progress_service.start_kata(kata_id="kata123", user=sample_user)

    assert progress_id == "progress123"
    mock_kata_repository.get_by_id.assert_called_once_with("kata123")
    mock_progress_repository.start_kata.assert_called_once_with(sample_user, "kata123")
    mock_kata_repository.increment_enrollment_count.assert_called_once_with("kata123")


def test_start_kata_not_found(progress_service, sample_user, mock_kata_repository):
    """Test start kata when kata doesn't exist."""
    mock_kata_repository.get_by_id.return_value = None

    with pytest.raises(ExtendedHTTPException) as exc_info:
        progress_service.start_kata(kata_id="nonexistent", user=sample_user)

    assert exc_info.value.code == status.HTTP_404_NOT_FOUND
    assert "Kata not found" in exc_info.value.message


def test_start_kata_not_published(progress_service, sample_user, sample_kata, mock_kata_repository):
    """Test start kata when kata is not published."""
    sample_kata.status = KataStatus.DRAFT
    mock_kata_repository.get_by_id.return_value = sample_kata

    with pytest.raises(ExtendedHTTPException) as exc_info:
        progress_service.start_kata(kata_id="kata123", user=sample_user)

    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
    assert "Kata not available" in exc_info.value.message


def test_start_kata_already_enrolled(
    progress_service, sample_user, sample_kata, mock_progress_repository, mock_kata_repository
):
    """Test start kata when user is already enrolled."""
    mock_kata_repository.get_by_id.return_value = sample_kata
    mock_progress_repository.start_kata.side_effect = ValueError("You are already enrolled in this kata")

    with pytest.raises(ExtendedHTTPException) as exc_info:
        progress_service.start_kata(kata_id="kata123", user=sample_user)

    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
    assert "Already enrolled" in exc_info.value.message


def test_start_kata_archived(progress_service, sample_user, sample_kata, mock_kata_repository):
    """Test start kata when kata is archived."""
    sample_kata.status = KataStatus.ARCHIVED
    mock_kata_repository.get_by_id.return_value = sample_kata

    with pytest.raises(ExtendedHTTPException) as exc_info:
        progress_service.start_kata(kata_id="kata123", user=sample_user)

    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
    assert "Kata not available" in exc_info.value.message


# complete_kata tests


def test_complete_kata_success(
    progress_service, sample_user, sample_kata, mock_progress_repository, mock_kata_repository
):
    """Test successful kata completion."""
    mock_kata_repository.get_by_id.return_value = sample_kata
    mock_progress_repository.complete_kata.return_value = True

    success = progress_service.complete_kata(kata_id="kata123", user=sample_user)

    assert success is True
    mock_kata_repository.get_by_id.assert_called_once_with("kata123")
    mock_progress_repository.complete_kata.assert_called_once_with("user123", "kata123")
    mock_kata_repository.increment_completed_count.assert_called_once_with("kata123")


def test_complete_kata_not_found(progress_service, sample_user, mock_kata_repository):
    """Test complete kata when kata doesn't exist."""
    mock_kata_repository.get_by_id.return_value = None

    with pytest.raises(ExtendedHTTPException) as exc_info:
        progress_service.complete_kata(kata_id="nonexistent", user=sample_user)

    assert exc_info.value.code == status.HTTP_404_NOT_FOUND
    assert "Kata not found" in exc_info.value.message


def test_complete_kata_not_enrolled(
    progress_service, sample_user, sample_kata, mock_progress_repository, mock_kata_repository
):
    """Test complete kata when user is not enrolled."""
    mock_kata_repository.get_by_id.return_value = sample_kata
    mock_progress_repository.complete_kata.side_effect = ValueError("You are not enrolled in this kata")

    with pytest.raises(ExtendedHTTPException) as exc_info:
        progress_service.complete_kata(kata_id="kata123", user=sample_user)

    assert exc_info.value.code == status.HTTP_404_NOT_FOUND
    assert "Not enrolled" in exc_info.value.message


# get_user_progress tests


def test_get_user_progress_success(progress_service, sample_progress, mock_progress_repository):
    """Test successful get user progress."""
    mock_progress_repository.get_user_progress.return_value = sample_progress

    result = progress_service.get_user_progress(kata_id="kata123", user_id="user123")

    assert result is not None
    assert isinstance(result, UserKataProgressResponse)
    assert result.id == "progress123"
    assert result.user_id == "user123"
    assert result.kata_id == "kata123"
    assert result.status == KataProgressStatus.IN_PROGRESS
    mock_progress_repository.get_user_progress.assert_called_once_with("user123", "kata123")


def test_get_user_progress_not_found(progress_service, mock_progress_repository):
    """Test get user progress when no progress exists."""
    mock_progress_repository.get_user_progress.return_value = None

    result = progress_service.get_user_progress(kata_id="kata123", user_id="user123")

    assert result is None


def test_get_user_progress_completed(progress_service, sample_completed_progress, mock_progress_repository):
    """Test get user progress for completed kata."""
    mock_progress_repository.get_user_progress.return_value = sample_completed_progress

    result = progress_service.get_user_progress(kata_id="kata123", user_id="user123")

    assert result is not None
    assert result.status == KataProgressStatus.COMPLETED
    assert result.completed_at is not None


# get_user_all_progress tests


def test_get_user_all_progress_success(progress_service, sample_progress, mock_progress_repository):
    """Test successful get all user progress."""
    mock_progress_repository.get_user_all_progress.return_value = [sample_progress]

    result = progress_service.get_user_all_progress(user_id="user123")

    assert len(result) == 1
    assert isinstance(result[0], UserKataProgressResponse)
    assert result[0].id == "progress123"
    mock_progress_repository.get_user_all_progress.assert_called_once_with("user123", None)


def test_get_user_all_progress_with_status_filter(
    progress_service, sample_completed_progress, mock_progress_repository
):
    """Test get all user progress with status filter."""
    mock_progress_repository.get_user_all_progress.return_value = [sample_completed_progress]

    result = progress_service.get_user_all_progress(user_id="user123", status=KataProgressStatus.COMPLETED)

    assert len(result) == 1
    assert result[0].status == KataProgressStatus.COMPLETED
    mock_progress_repository.get_user_all_progress.assert_called_once_with("user123", KataProgressStatus.COMPLETED)


def test_get_user_all_progress_empty(progress_service, mock_progress_repository):
    """Test get all user progress when user has no progress."""
    mock_progress_repository.get_user_all_progress.return_value = []

    result = progress_service.get_user_all_progress(user_id="user123")

    assert len(result) == 0


def test_get_user_all_progress_multiple(
    progress_service, sample_progress, sample_completed_progress, mock_progress_repository
):
    """Test get all user progress with multiple entries."""
    mock_progress_repository.get_user_all_progress.return_value = [sample_progress, sample_completed_progress]

    result = progress_service.get_user_all_progress(user_id="user123")

    assert len(result) == 2
    assert result[0].status == KataProgressStatus.IN_PROGRESS
    assert result[1].status == KataProgressStatus.COMPLETED


# get_leaderboard tests


def test_get_leaderboard_success(progress_service, mock_progress_repository):
    """Test successful leaderboard retrieval."""
    mock_leaderboard_data = [
        LeaderboardEntryFromDB(
            user_id="user1", user_name="User One", user_username="userone", completed_count=10, in_progress_count=2
        ),
        LeaderboardEntryFromDB(
            user_id="user2", user_name="User Two", user_username="usertwo", completed_count=8, in_progress_count=3
        ),
        LeaderboardEntryFromDB(
            user_id="user3", user_name="User Three", user_username="userthree", completed_count=5, in_progress_count=1
        ),
    ]
    mock_progress_repository.get_leaderboard.return_value = mock_leaderboard_data

    result = progress_service.get_leaderboard(limit=100)

    assert len(result) == 3
    assert isinstance(result[0], UserLeaderboardEntry)
    assert result[0].user_id == "user1"
    assert result[0].user_name == "User One"
    assert result[0].username == "userone"
    assert result[0].completed_count == 10
    assert result[0].in_progress_count == 2
    assert result[0].rank == 1
    assert result[1].rank == 2
    assert result[2].rank == 3


def test_get_leaderboard_with_empty_username(progress_service, mock_progress_repository):
    """Test leaderboard with fallback logic."""
    mock_leaderboard_data = [
        LeaderboardEntryFromDB(
            user_id="user1", user_name="User One", user_username="", completed_count=10, in_progress_count=2
        ),
        LeaderboardEntryFromDB(user_id="user2", user_name="", user_username="", completed_count=8, in_progress_count=3),
    ]
    mock_progress_repository.get_leaderboard.return_value = mock_leaderboard_data

    result = progress_service.get_leaderboard(limit=100)

    assert len(result) == 2
    assert result[0].user_name == "User One"
    assert result[0].username == "user1"  # Falls back to user_id when user_username is empty
    assert result[1].user_name == "user2"  # Falls back to user_id when user_name is empty
    assert result[1].username == "user2"  # Falls back to user_id


def test_get_leaderboard_empty(progress_service, mock_progress_repository):
    """Test leaderboard when no data exists."""
    mock_progress_repository.get_leaderboard.return_value = []

    result = progress_service.get_leaderboard(limit=100)

    assert len(result) == 0


def test_get_leaderboard_limit_validation(progress_service, mock_progress_repository):
    """Test leaderboard limit validation."""
    mock_progress_repository.get_leaderboard.return_value = []

    # Test negative limit (should default to 100)
    progress_service.get_leaderboard(limit=-1)
    mock_progress_repository.get_leaderboard.assert_called_with(100)

    # Test zero limit (should default to 100)
    progress_service.get_leaderboard(limit=0)
    mock_progress_repository.get_leaderboard.assert_called_with(100)

    # Test excessive limit (should cap to 1000)
    progress_service.get_leaderboard(limit=5000)
    mock_progress_repository.get_leaderboard.assert_called_with(1000)

    # Test valid limit
    progress_service.get_leaderboard(limit=50)
    mock_progress_repository.get_leaderboard.assert_called_with(50)


def test_get_leaderboard_username_priority(progress_service, mock_progress_repository):
    """Test leaderboard username/name prioritization."""
    mock_leaderboard_data = [
        LeaderboardEntryFromDB(
            user_id="user1", user_name="Name One", user_username="username1", completed_count=10, in_progress_count=2
        ),
        LeaderboardEntryFromDB(
            user_id="user2", user_name="Name Two", user_username="", completed_count=8, in_progress_count=3
        ),
        LeaderboardEntryFromDB(user_id="user3", user_name="", user_username="", completed_count=5, in_progress_count=1),
    ]
    mock_progress_repository.get_leaderboard.return_value = mock_leaderboard_data

    result = progress_service.get_leaderboard(limit=100)

    assert len(result) == 3
    assert result[0].user_name == "Name One"
    assert result[0].username == "username1"  # user_username
    assert result[1].user_name == "Name Two"
    assert result[1].username == "user2"  # Falls back to user_id when user_username is empty
    assert result[2].user_name == "user3"  # Falls back to user_id when user_name is empty
    assert result[2].username == "user3"  # Falls back to user_id


# _to_response tests


def test_to_response_in_progress(progress_service, sample_progress):
    """Test conversion of in-progress progress to response."""
    result = progress_service._to_response(sample_progress)

    assert isinstance(result, UserKataProgressResponse)
    assert result.id == "progress123"
    assert result.user_id == "user123"
    assert result.kata_id == "kata123"
    assert result.status == KataProgressStatus.IN_PROGRESS
    assert result.started_at is not None
    assert result.completed_at is None


def test_to_response_completed(progress_service, sample_completed_progress):
    """Test conversion of completed progress to response."""
    result = progress_service._to_response(sample_completed_progress)

    assert isinstance(result, UserKataProgressResponse)
    assert result.status == KataProgressStatus.COMPLETED
    assert result.started_at is not None
    assert result.completed_at is not None
