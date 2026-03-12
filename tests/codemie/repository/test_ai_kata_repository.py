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
from sqlmodel import Session

from codemie.repository.ai_kata_repository import AIKataRepository, SQLAIKataRepository
from codemie.rest_api.models.ai_kata import AIKata, KataLevel, KataStatus


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
    return SQLAIKataRepository()


@pytest.fixture
def sample_kata():
    """Sample kata entity."""
    return AIKata(
        id=str(uuid4()),
        title="Test Kata",
        description="Test kata description",
        steps="# Step 1\nContent 1",
        level=KataLevel.BEGINNER,
        creator_id="user123",
        creator_name="Test User",
        creator_username="testuser",
        duration_minutes=30,
        tags=["python", "testing"],
        roles=["developer"],
        links=[],
        references=["Reference 1"],
        status=KataStatus.PUBLISHED,
        enrollment_count=5,
        completed_count=2,
        unique_likes_count=10,
        unique_dislikes_count=1,
        date=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        update_date=datetime(2024, 1, 2, 12, 0, 0, tzinfo=UTC),
    )


@pytest.fixture
def sample_kata_no_id():
    """Sample kata entity without ID."""
    return AIKata(
        title="New Kata",
        description="New kata description",
        steps="# Step 1\nContent 1",
        level=KataLevel.INTERMEDIATE,
        creator_id="user456",
        creator_name="Another User",
        creator_username="anotheruser",
        duration_minutes=45,
        tags=["javascript"],
        roles=["developer"],
        links=[],
        references=[],
        status=KataStatus.DRAFT,
    )


class TestAIKataRepositoryCreate:
    """Tests for create method."""

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_create_kata_with_id(self, mock_session_cls, mock_get_engine, repository, sample_kata):
        """Test creating kata with pre-set ID."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        kata_id = repository.create(sample_kata)

        assert kata_id == sample_kata.id
        mock_session.add.assert_called_once_with(sample_kata)
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once_with(sample_kata)

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_create_kata_without_id(self, mock_session_cls, mock_get_engine, repository, sample_kata_no_id):
        """Test creating kata without ID - should auto-generate."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        kata_id = repository.create(sample_kata_no_id)

        assert kata_id is not None
        assert sample_kata_no_id.id is not None
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_create_kata_sets_timestamps(self, mock_session_cls, mock_get_engine, repository, sample_kata_no_id):
        """Test that create sets date and update_date if not present."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        repository.create(sample_kata_no_id)

        assert sample_kata_no_id.date is not None
        assert sample_kata_no_id.update_date is not None
        assert sample_kata_no_id.date == sample_kata_no_id.update_date


class TestAIKataRepositoryGetByID:
    """Tests for get_by_id method."""

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_get_by_id_found(self, mock_session_cls, mock_get_engine, repository, sample_kata):
        """Test getting kata by ID when it exists."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.first.return_value = sample_kata
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.get_by_id(sample_kata.id)

        assert result == sample_kata
        mock_session.exec.assert_called_once()

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_get_by_id_not_found(self, mock_session_cls, mock_get_engine, repository):
        """Test getting kata by ID when it doesn't exist."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.get_by_id("nonexistent")

        assert result is None


class TestAIKataRepositoryGetAllPublished:
    """Tests for get_all_published method."""

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_get_all_published_with_results(self, mock_session_cls, mock_get_engine, repository, sample_kata):
        """Test getting all published katas."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [sample_kata]
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.get_all_published(page=1, per_page=20)

        assert len(result) == 1
        assert result[0] == sample_kata

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_get_all_published_pagination(self, mock_session_cls, mock_get_engine, repository):
        """Test pagination parameters are used correctly."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        repository.get_all_published(page=2, per_page=10)

        mock_session.exec.assert_called_once()


class TestAIKataRepositoryGetByLevel:
    """Tests for get_by_level method."""

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_get_by_level_published_only(self, mock_session_cls, mock_get_engine, repository, sample_kata):
        """Test getting katas by level with published_only=True."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [sample_kata]
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.get_by_level(KataLevel.BEGINNER, published_only=True)

        assert len(result) == 1
        assert result[0] == sample_kata

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_get_by_level_all_statuses(self, mock_session_cls, mock_get_engine, repository, sample_kata):
        """Test getting katas by level with published_only=False."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [sample_kata]
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.get_by_level(KataLevel.BEGINNER, published_only=False)

        assert len(result) == 1
        mock_session.exec.assert_called_once()


class TestAIKataRepositorySearchByTags:
    """Tests for search_by_tags method."""

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_search_by_tags_single_tag(self, mock_session_cls, mock_get_engine, repository, sample_kata):
        """Test searching by single tag."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [sample_kata]
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.search_by_tags(["python"], published_only=True)

        assert len(result) == 1
        assert result[0] == sample_kata

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_search_by_tags_multiple_tags(self, mock_session_cls, mock_get_engine, repository):
        """Test searching by multiple tags (OR logic)."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        repository.search_by_tags(["python", "javascript", "testing"], published_only=False)

        mock_session.exec.assert_called_once()


class TestAIKataRepositoryUpdate:
    """Tests for update method."""

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_update_kata_success(self, mock_session_cls, mock_get_engine, repository, sample_kata):
        """Test updating kata successfully."""
        mock_session = MagicMock()
        mock_session.get.return_value = sample_kata
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        updates = {"title": "Updated Title", "description": "Updated description"}
        result = repository.update(sample_kata.id, updates)

        assert result is True
        assert sample_kata.title == "Updated Title"
        assert sample_kata.description == "Updated description"
        assert sample_kata.update_date is not None
        mock_session.add.assert_called_once_with(sample_kata)
        mock_session.commit.assert_called_once()

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_update_kata_not_found(self, mock_session_cls, mock_get_engine, repository):
        """Test updating kata that doesn't exist."""
        mock_session = MagicMock()
        mock_session.get.return_value = None
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.update("nonexistent", {"title": "New Title"})

        assert result is False
        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_called()

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_update_kata_exception(self, mock_session_cls, mock_get_engine, repository, sample_kata):
        """Test update method handles exceptions."""
        mock_session = MagicMock()
        mock_session.get.return_value = sample_kata
        mock_session.commit.side_effect = Exception("Database error")
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        with pytest.raises(Exception, match="Database error"):
            repository.update(sample_kata.id, {"title": "New Title"})


class TestAIKataRepositoryPublishArchive:
    """Tests for publish and archive methods."""

    @patch.object(SQLAIKataRepository, "update")
    def test_publish_kata(self, mock_update, repository):
        """Test publishing kata."""
        mock_update.return_value = True

        result = repository.publish("kata123")

        assert result is True
        mock_update.assert_called_once_with("kata123", {"status": KataStatus.PUBLISHED})

    @patch.object(SQLAIKataRepository, "update")
    def test_archive_kata(self, mock_update, repository):
        """Test archiving kata."""
        mock_update.return_value = True

        result = repository.archive("kata123")

        assert result is True
        mock_update.assert_called_once_with("kata123", {"status": KataStatus.ARCHIVED})


class TestAIKataRepositoryDelete:
    """Tests for delete method."""

    @patch.object(SQLAIKataRepository, "get_by_id")
    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_delete_kata_success(self, mock_session_cls, mock_get_engine, mock_get_by_id, repository, sample_kata):
        """Test deleting kata successfully."""
        mock_get_by_id.return_value = sample_kata
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.delete(sample_kata.id)

        assert result is True
        mock_session.delete.assert_called_once_with(sample_kata)
        mock_session.commit.assert_called_once()

    @patch.object(SQLAIKataRepository, "get_by_id")
    def test_delete_kata_not_found(self, mock_get_by_id, repository):
        """Test deleting kata that doesn't exist."""
        mock_get_by_id.return_value = None

        result = repository.delete("nonexistent")

        assert result is False


class TestAIKataRepositoryCountPublished:
    """Tests for count_published method."""

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_count_published(self, mock_session_cls, mock_get_engine, repository):
        """Test counting published katas."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.one.return_value = 42
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        count = repository.count_published()

        assert count == 42


class TestAIKataRepositoryGetWithEnrollmentCounts:
    """Tests for get_with_enrollment_counts method."""

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_get_with_enrollment_counts(self, mock_session_cls, mock_get_engine, repository):
        """Test getting enrollment counts for multiple katas."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [("kata1", 5), ("kata2", 10)]
        mock_session.exec.return_value = mock_result
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.get_with_enrollment_counts(["kata1", "kata2"])

        assert result == {"kata1": 5, "kata2": 10}

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_get_with_enrollment_counts_empty_list(self, mock_session_cls, mock_get_engine, repository):
        """Test getting enrollment counts with empty list."""
        result = repository.get_with_enrollment_counts([])

        assert result == {}


class TestAIKataRepositoryListWithFilters:
    """Tests for list_with_filters method."""

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_list_with_filters_no_filters(self, mock_session_cls, mock_get_engine, repository, sample_kata):
        """Test listing katas without filters."""
        mock_session = MagicMock()
        mock_exec_result = MagicMock()
        mock_exec_result.all.return_value = [sample_kata]
        mock_exec_result.one.return_value = 1
        mock_session.exec.side_effect = [mock_exec_result, mock_exec_result]
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        katas, total = repository.list_with_filters(page=1, per_page=20, filters={"status": KataStatus.PUBLISHED})

        assert len(katas) == 1
        assert total == 1

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_list_with_filters_search(self, mock_session_cls, mock_get_engine, repository):
        """Test listing katas with search filter."""
        mock_session = MagicMock()
        mock_exec_result = MagicMock()
        mock_exec_result.all.return_value = []
        mock_exec_result.one.return_value = 0
        mock_session.exec.side_effect = [mock_exec_result, mock_exec_result]
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        filters = {"search": "python", "status": KataStatus.PUBLISHED}
        katas, total = repository.list_with_filters(page=1, per_page=20, filters=filters)

        assert len(katas) == 0
        assert total == 0

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_list_with_filters_level(self, mock_session_cls, mock_get_engine, repository):
        """Test listing katas with level filter."""
        mock_session = MagicMock()
        mock_exec_result = MagicMock()
        mock_exec_result.all.return_value = []
        mock_exec_result.one.return_value = 0
        mock_session.exec.side_effect = [mock_exec_result, mock_exec_result]
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        filters = {"level": KataLevel.BEGINNER, "status": KataStatus.PUBLISHED}
        repository.list_with_filters(page=1, per_page=20, filters=filters)

        mock_session.exec.assert_called()

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_list_with_filters_author(self, mock_session_cls, mock_get_engine, repository):
        """Test listing katas with author filter."""
        mock_session = MagicMock()
        mock_exec_result = MagicMock()
        mock_exec_result.all.return_value = []
        mock_exec_result.one.return_value = 0
        mock_session.exec.side_effect = [mock_exec_result, mock_exec_result]
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        filters = {"author": "user123", "status": KataStatus.PUBLISHED}
        repository.list_with_filters(page=1, per_page=20, filters=filters)

        mock_session.exec.assert_called()

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_list_with_filters_invalid_status(self, mock_session_cls, mock_get_engine, repository):
        """Test listing katas with invalid status falls back to published."""
        mock_session = MagicMock()
        mock_exec_result = MagicMock()
        mock_exec_result.all.return_value = []
        mock_exec_result.one.return_value = 0
        mock_session.exec.side_effect = [mock_exec_result, mock_exec_result]
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        filters = {"status": "invalid_status"}
        repository.list_with_filters(page=1, per_page=20, filters=filters)

        mock_session.exec.assert_called()


class TestAIKataRepositoryIncrementCounts:
    """Tests for increment count methods."""

    @patch.object(SQLAIKataRepository, "get_by_id")
    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_increment_enrollment_count(
        self, mock_session_cls, mock_get_engine, mock_get_by_id, repository, sample_kata
    ):
        """Test incrementing enrollment count."""
        mock_get_by_id.return_value = sample_kata
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        original_count = sample_kata.enrollment_count
        result = repository.increment_enrollment_count(sample_kata.id)

        assert result is True
        assert sample_kata.enrollment_count == original_count + 1
        mock_session.add.assert_called_once_with(sample_kata)
        mock_session.commit.assert_called_once()

    @patch.object(SQLAIKataRepository, "get_by_id")
    def test_increment_enrollment_count_not_found(self, mock_get_by_id, repository):
        """Test incrementing enrollment count for non-existent kata."""
        mock_get_by_id.return_value = None

        result = repository.increment_enrollment_count("nonexistent")

        assert result is False

    @patch.object(SQLAIKataRepository, "get_by_id")
    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_increment_completed_count(
        self, mock_session_cls, mock_get_engine, mock_get_by_id, repository, sample_kata
    ):
        """Test incrementing completed count."""
        mock_get_by_id.return_value = sample_kata
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        original_count = sample_kata.completed_count
        result = repository.increment_completed_count(sample_kata.id)

        assert result is True
        assert sample_kata.completed_count == original_count + 1


class TestAIKataRepositoryDecrementCounts:
    """Tests for decrement count methods."""

    @patch.object(SQLAIKataRepository, "get_by_id")
    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_decrement_enrollment_count(
        self, mock_session_cls, mock_get_engine, mock_get_by_id, repository, sample_kata
    ):
        """Test decrementing enrollment count."""
        mock_get_by_id.return_value = sample_kata
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        original_count = sample_kata.enrollment_count
        result = repository.decrement_enrollment_count(sample_kata.id)

        assert result is True
        assert sample_kata.enrollment_count == original_count - 1

    @patch.object(SQLAIKataRepository, "get_by_id")
    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_decrement_enrollment_count_prevents_negative(
        self, mock_session_cls, mock_get_engine, mock_get_by_id, repository, sample_kata
    ):
        """Test decrementing enrollment count doesn't go below zero."""
        sample_kata.enrollment_count = 0
        mock_get_by_id.return_value = sample_kata
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.decrement_enrollment_count(sample_kata.id)

        assert result is True
        assert sample_kata.enrollment_count == 0

    @patch.object(SQLAIKataRepository, "get_by_id")
    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_decrement_completed_count(
        self, mock_session_cls, mock_get_engine, mock_get_by_id, repository, sample_kata
    ):
        """Test decrementing completed count."""
        mock_get_by_id.return_value = sample_kata
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        original_count = sample_kata.completed_count
        result = repository.decrement_completed_count(sample_kata.id)

        assert result is True
        assert sample_kata.completed_count == original_count - 1

    @patch.object(SQLAIKataRepository, "get_by_id")
    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_decrement_completed_count_prevents_negative(
        self, mock_session_cls, mock_get_engine, mock_get_by_id, repository, sample_kata
    ):
        """Test decrementing completed count doesn't go below zero."""
        sample_kata.completed_count = 0
        mock_get_by_id.return_value = sample_kata
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.decrement_completed_count(sample_kata.id)

        assert result is True
        assert sample_kata.completed_count == 0


class TestAIKataRepositoryUpdateReactionCounts:
    """Tests for update_reaction_counts method."""

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_update_reaction_counts_success(self, mock_session_cls, mock_get_engine, repository, sample_kata):
        """Test updating reaction counts successfully."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.update_reaction_counts(sample_kata, like_count=15, dislike_count=3)

        assert result is True
        assert sample_kata.unique_likes_count == 15
        assert sample_kata.unique_dislikes_count == 3
        assert sample_kata.update_date is not None
        mock_session.add.assert_called_once_with(sample_kata)
        mock_session.commit.assert_called_once()

    @patch("codemie.rest_api.models.ai_kata.AIKata.get_engine")
    @patch("codemie.repository.ai_kata_repository.Session")
    def test_update_reaction_counts_exception(self, mock_session_cls, mock_get_engine, repository, sample_kata):
        """Test update_reaction_counts handles exceptions."""
        mock_session = MagicMock()
        mock_session.commit.side_effect = Exception("Database error")
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_get_engine.return_value = MagicMock()

        result = repository.update_reaction_counts(sample_kata, like_count=10, dislike_count=2)

        assert result is False


class TestAIKataRepositoryFilterConditions:
    """Tests for filter condition builder methods."""

    def test_build_search_condition(self, repository):
        """Test building search condition."""
        condition = repository._build_search_condition("python")
        assert condition is not None

    def test_build_level_condition_with_enum(self, repository):
        """Test building level condition with enum."""
        condition = repository._build_level_condition(KataLevel.BEGINNER)
        assert condition is not None

    def test_build_level_condition_with_string(self, repository):
        """Test building level condition with string."""
        condition = repository._build_level_condition("beginner")
        assert condition is not None

    def test_build_level_condition_invalid(self, repository):
        """Test building level condition with invalid value."""
        condition = repository._build_level_condition("invalid_level")
        assert condition is None

    def test_build_tags_condition(self, repository):
        """Test building tags condition."""
        condition = repository._build_tags_condition(["python", "testing"])
        assert condition is not None

    def test_build_tags_condition_empty(self, repository):
        """Test building tags condition with empty list."""
        condition = repository._build_tags_condition([])
        assert condition is None

    def test_build_roles_condition(self, repository):
        """Test building roles condition."""
        condition = repository._build_roles_condition(["developer"])
        assert condition is not None

    def test_build_roles_condition_empty(self, repository):
        """Test building roles condition with empty list."""
        condition = repository._build_roles_condition([])
        assert condition is None

    def test_build_status_condition_with_enum(self, repository):
        """Test building status condition with enum."""
        condition = repository._build_status_condition(KataStatus.PUBLISHED)
        assert condition is not None

    def test_build_status_condition_with_string(self, repository):
        """Test building status condition with string."""
        condition = repository._build_status_condition("published")
        assert condition is not None

    def test_build_status_condition_invalid(self, repository):
        """Test building status condition with invalid value."""
        condition = repository._build_status_condition("invalid_status")
        assert condition is None


class TestAIKataRepositoryAbstractClass:
    """Tests for abstract base class."""

    def test_abstract_class_cannot_instantiate(self):
        """Test that abstract class cannot be instantiated."""
        with pytest.raises(TypeError):
            AIKataRepository()

    def test_concrete_class_implements_all_methods(self):
        """Test that SQLAIKataRepository implements all abstract methods."""
        abstract_methods = [
            "create",
            "get_by_id",
            "get_all_published",
            "get_by_level",
            "search_by_tags",
            "update",
            "publish",
            "archive",
            "delete",
            "count_published",
            "get_with_enrollment_counts",
            "list_with_filters",
            "increment_enrollment_count",
            "increment_completed_count",
            "decrement_enrollment_count",
            "decrement_completed_count",
            "update_reaction_counts",
        ]

        repo = SQLAIKataRepository()
        for method in abstract_methods:
            assert hasattr(repo, method)
            assert callable(getattr(repo, method))
