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

from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from codemie.repository.conversation_analytics_repository import ConversationAnalyticsRepository
from codemie.rest_api.models.conversation_analysis import (
    ConversationAnalytics,
    AssistantUsed,
    TopicAnalysis,
    TopicCategory,
    SatisfactionMetrics,
    AnswerQuality,
    IterationEfficiency,
    ConversationFocus,
    OverallScore,
    MaturityAnalysis,
    MaturityLevel,
    MaturityIndicators,
    PromptQuality,
    TaskComplexity,
    UsagePattern,
)


@pytest.fixture
def sample_analytics():
    """Sample ConversationAnalytics entity with complete data."""
    return ConversationAnalytics(
        id=str(uuid4()),
        conversation_id="conv-123",
        user_id="user-456",
        user_name="Test User",
        project="test-project",
        assistants_used=[
            AssistantUsed(
                id="assist-1",
                name="Code Assistant",
                description="Helps with coding",
                categories=["Development"],
                author="admin",
                tool_names=["GitLab", "Jira"],
                datasource_names=["project-repo"],
            )
        ],
        topics=[
            TopicAnalysis(
                topic="Python API Integration",
                category=TopicCategory.CODE_DEVELOPMENT,
                other_category=None,
                usage_intent="production",
                user_goal="Implement REST API",
                summary="Discussed FastAPI endpoint implementation",
            )
        ],
        satisfaction=SatisfactionMetrics(
            answer_quality=AnswerQuality.EXCELLENT,
            iteration_efficiency=IterationEfficiency.OPTIMAL,
            conversation_focus=ConversationFocus.FOCUSED,
            overall_score=OverallScore.HIGHLY_SATISFIED,
            evidence="User accepted solution immediately",
        ),
        maturity=MaturityAnalysis(
            level=MaturityLevel.L2,
            indicators=MaturityIndicators(
                prompt_quality=PromptQuality.INTERMEDIATE,
                task_complexity=TaskComplexity.MODERATE,
                usage_pattern=UsagePattern.REGULAR,
            ),
            justification="Regular user with clear requirements",
        ),
        anti_patterns=[],
        last_analysis_date=datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC),
        message_count_at_analysis=10,
        analyzed_at=datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC),
        llm_model_used="gpt-4",
        analysis_duration_seconds=2.5,
        date=datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC),
        update_date=datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC),
    )


@pytest.fixture
def sample_analytics_minimal():
    """Sample ConversationAnalytics with minimal required fields."""
    return ConversationAnalytics(
        conversation_id="conv-789",
        user_id="user-999",
        user_name="Minimal User",
        project=None,
        assistants_used=[],
        topics=[],
        satisfaction=None,
        maturity=None,
        anti_patterns=[],
        last_analysis_date=datetime(2024, 1, 20, 10, 0, 0, tzinfo=UTC),
        message_count_at_analysis=3,
        analyzed_at=datetime(2024, 1, 20, 10, 0, 0, tzinfo=UTC),
        llm_model_used="claude-3",
        analysis_duration_seconds=1.2,
    )


def _make_async_session_cm(mock_session):
    """Helper to create async context manager for AsyncSession."""
    cm = AsyncMock()
    cm.__aenter__.return_value = mock_session
    cm.__aexit__.return_value = False
    return cm


class TestConversationAnalyticsRepositoryUpsertAnalysis:
    """Tests for upsert_analysis method."""

    @pytest.mark.asyncio
    @patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine")
    @patch("codemie.repository.conversation_analytics_repository.AsyncSession")
    async def test_upsert_analysis_insert_new_record(
        self, mock_async_session_cls, mock_get_engine, sample_analytics_minimal
    ):
        """Test inserting new conversation analysis (UPSERT - INSERT path)."""
        # Arrange
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # Not exists
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_async_session_cls.return_value = _make_async_session_cm(mock_session)
        mock_get_engine.return_value = MagicMock()

        # Act
        result_analytics, is_new = await ConversationAnalyticsRepository.upsert_analysis(sample_analytics_minimal)

        # Assert
        assert is_new is True
        assert result_analytics is not None
        assert result_analytics.id is not None  # ID should be generated
        mock_session.add.assert_called_once()
        mock_session.commit.assert_awaited_once()
        mock_session.refresh.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine")
    @patch("codemie.repository.conversation_analytics_repository.AsyncSession")
    async def test_upsert_analysis_update_existing_record(
        self, mock_async_session_cls, mock_get_engine, sample_analytics
    ):
        """Test updating existing conversation analysis (UPSERT - UPDATE path)."""
        # Arrange
        existing_id = str(uuid4())
        existing_date = datetime(2024, 1, 10, 8, 0, 0, tzinfo=UTC)
        existing_analytics = ConversationAnalytics(
            id=existing_id,
            conversation_id="conv-123",
            user_id="user-456",
            user_name="Old Name",
            project="old-project",
            assistants_used=[],
            topics=[],
            satisfaction=None,
            maturity=None,
            anti_patterns=[],
            last_analysis_date=datetime(2024, 1, 10, 8, 0, 0, tzinfo=UTC),
            message_count_at_analysis=5,
            analyzed_at=datetime(2024, 1, 10, 8, 0, 0, tzinfo=UTC),
            llm_model_used="gpt-3.5",
            analysis_duration_seconds=1.0,
            date=existing_date,
            update_date=datetime(2024, 1, 10, 8, 0, 0, tzinfo=UTC),
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_analytics
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_async_session_cls.return_value = _make_async_session_cm(mock_session)
        mock_get_engine.return_value = MagicMock()

        # New analytics with updated data
        sample_analytics.conversation_id = "conv-123"
        sample_analytics.message_count_at_analysis = 15

        # Act
        result_analytics, is_new = await ConversationAnalyticsRepository.upsert_analysis(sample_analytics)

        # Assert
        assert is_new is False
        assert result_analytics.id == existing_id  # Should preserve existing ID
        assert result_analytics.date == existing_date  # Should preserve original creation date
        assert result_analytics.update_date is not None  # Should have new update date
        mock_session.add.assert_called_once_with(existing_analytics)
        mock_session.commit.assert_awaited_once()
        mock_session.refresh.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine")
    @patch("codemie.repository.conversation_analytics_repository.AsyncSession")
    async def test_upsert_analysis_preserves_existing_id_and_date(
        self, mock_async_session_cls, mock_get_engine, sample_analytics
    ):
        """Test that UPSERT preserves existing ID and creation date on update."""
        # Arrange
        existing_id = "preserved-id-123"
        original_date = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
        existing_analytics = ConversationAnalytics(
            id=existing_id,
            conversation_id="conv-123",
            user_id="user-456",
            user_name="Original User",
            project="original-project",
            assistants_used=[],
            topics=[],
            satisfaction=None,
            maturity=None,
            anti_patterns=[],
            last_analysis_date=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            message_count_at_analysis=1,
            analyzed_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            llm_model_used="model-old",
            analysis_duration_seconds=0.5,
            date=original_date,
            update_date=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_analytics
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_async_session_cls.return_value = _make_async_session_cm(mock_session)
        mock_get_engine.return_value = MagicMock()

        sample_analytics.conversation_id = "conv-123"

        # Act
        result_analytics, is_new = await ConversationAnalyticsRepository.upsert_analysis(sample_analytics)

        # Assert
        assert sample_analytics.id == existing_id  # ID should be set to existing
        assert sample_analytics.date == original_date  # Date should be preserved
        assert is_new is False


class TestConversationAnalyticsRepositoryGetByConversationId:
    """Tests for get_by_conversation_id method."""

    @pytest.mark.asyncio
    @patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine")
    @patch("codemie.repository.conversation_analytics_repository.AsyncSession")
    async def test_get_by_conversation_id_found(self, mock_async_session_cls, mock_get_engine, sample_analytics):
        """Test retrieving existing analytics by conversation ID."""
        # Arrange
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_analytics
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_async_session_cls.return_value = _make_async_session_cm(mock_session)
        mock_get_engine.return_value = MagicMock()

        # Act
        result = await ConversationAnalyticsRepository.get_by_conversation_id("conv-123")

        # Assert
        assert result is not None
        assert result.conversation_id == "conv-123"
        assert result.user_id == sample_analytics.user_id
        mock_session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine")
    @patch("codemie.repository.conversation_analytics_repository.AsyncSession")
    async def test_get_by_conversation_id_not_found(self, mock_async_session_cls, mock_get_engine):
        """Test retrieving non-existent analytics by conversation ID returns None."""
        # Arrange
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_async_session_cls.return_value = _make_async_session_cm(mock_session)
        mock_get_engine.return_value = MagicMock()

        # Act
        result = await ConversationAnalyticsRepository.get_by_conversation_id("non-existent-conv")

        # Assert
        assert result is None

    @pytest.mark.asyncio
    @patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine")
    @patch("codemie.repository.conversation_analytics_repository.AsyncSession")
    async def test_get_by_conversation_id_empty_string(self, mock_async_session_cls, mock_get_engine):
        """Test retrieving analytics with empty conversation ID."""
        # Arrange
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_async_session_cls.return_value = _make_async_session_cm(mock_session)
        mock_get_engine.return_value = MagicMock()

        # Act
        result = await ConversationAnalyticsRepository.get_by_conversation_id("")

        # Assert
        assert result is None


class TestConversationAnalyticsRepositoryGetReprocessingCandidates:
    """Tests for get_reprocessing_candidates method."""

    @pytest.mark.asyncio
    @patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine")
    @patch("codemie.repository.conversation_analytics_repository.AsyncSession")
    async def test_get_reprocessing_candidates_with_results(
        self, mock_async_session_cls, mock_get_engine, sample_analytics, sample_analytics_minimal
    ):
        """Test batch fetching analytics for reprocessing check with results."""
        # Arrange
        sample_analytics.conversation_id = "conv-1"
        sample_analytics_minimal.conversation_id = "conv-2"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [sample_analytics, sample_analytics_minimal]
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_async_session_cls.return_value = _make_async_session_cm(mock_session)
        mock_get_engine.return_value = MagicMock()

        # Act
        result = await ConversationAnalyticsRepository.get_reprocessing_candidates(["conv-1", "conv-2", "conv-3"])

        # Assert
        assert len(result) == 2
        assert "conv-1" in result
        assert "conv-2" in result
        assert "conv-3" not in result  # Not analyzed yet
        assert result["conv-1"].conversation_id == "conv-1"
        assert result["conv-2"].conversation_id == "conv-2"

    @pytest.mark.asyncio
    @patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine")
    @patch("codemie.repository.conversation_analytics_repository.AsyncSession")
    async def test_get_reprocessing_candidates_empty_input(self, mock_async_session_cls, mock_get_engine):
        """Test batch fetching with empty conversation ID list returns empty dict."""
        # Arrange & Act
        result = await ConversationAnalyticsRepository.get_reprocessing_candidates([])

        # Assert
        assert result == {}

    @pytest.mark.asyncio
    @patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine")
    @patch("codemie.repository.conversation_analytics_repository.AsyncSession")
    async def test_get_reprocessing_candidates_no_matches(self, mock_async_session_cls, mock_get_engine):
        """Test batch fetching when no conversations have been analyzed yet."""
        # Arrange
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_async_session_cls.return_value = _make_async_session_cm(mock_session)
        mock_get_engine.return_value = MagicMock()

        # Act
        result = await ConversationAnalyticsRepository.get_reprocessing_candidates(["conv-new-1", "conv-new-2"])

        # Assert
        assert len(result) == 0

    @pytest.mark.asyncio
    @patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine")
    @patch("codemie.repository.conversation_analytics_repository.AsyncSession")
    async def test_get_reprocessing_candidates_partial_matches(
        self, mock_async_session_cls, mock_get_engine, sample_analytics
    ):
        """Test batch fetching when only some conversations have been analyzed."""
        # Arrange
        sample_analytics.conversation_id = "conv-existing"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [sample_analytics]
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_async_session_cls.return_value = _make_async_session_cm(mock_session)
        mock_get_engine.return_value = MagicMock()

        # Act
        result = await ConversationAnalyticsRepository.get_reprocessing_candidates(
            ["conv-existing", "conv-new", "conv-another"]
        )

        # Assert
        assert len(result) == 1
        assert "conv-existing" in result
        assert "conv-new" not in result
        assert "conv-another" not in result


class TestConversationAnalyticsRepositoryGetStatistics:
    """Tests for get_statistics method."""

    @pytest.mark.asyncio
    @patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine")
    @patch("codemie.repository.conversation_analytics_repository.AsyncSession")
    async def test_get_statistics_with_data(self, mock_async_session_cls, mock_get_engine):
        """Test getting analytics statistics when data exists."""
        # Arrange
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_row = MagicMock()
        mock_row.total_analyzed = 150
        mock_row.avg_messages = 12.5
        mock_row.latest_analysis = datetime(2024, 1, 20, 15, 30, 0, tzinfo=UTC)
        mock_result.one.return_value = mock_row
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_async_session_cls.return_value = _make_async_session_cm(mock_session)
        mock_get_engine.return_value = MagicMock()

        # Act
        result = await ConversationAnalyticsRepository.get_statistics()

        # Assert
        assert result["total_conversations_analyzed"] == 150
        assert result["avg_message_count"] == 12.5
        assert result["latest_analysis_date"] == datetime(2024, 1, 20, 15, 30, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    @patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine")
    @patch("codemie.repository.conversation_analytics_repository.AsyncSession")
    async def test_get_statistics_empty_database(self, mock_async_session_cls, mock_get_engine):
        """Test getting statistics when no analytics exist (empty database)."""
        # Arrange
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_row = MagicMock()
        mock_row.total_analyzed = None
        mock_row.avg_messages = None
        mock_row.latest_analysis = None
        mock_result.one.return_value = mock_row
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_async_session_cls.return_value = _make_async_session_cm(mock_session)
        mock_get_engine.return_value = MagicMock()

        # Act
        result = await ConversationAnalyticsRepository.get_statistics()

        # Assert
        assert result["total_conversations_analyzed"] == 0
        assert result["avg_message_count"] == 0.0
        assert result["latest_analysis_date"] is None

    @pytest.mark.asyncio
    @patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine")
    @patch("codemie.repository.conversation_analytics_repository.AsyncSession")
    async def test_get_statistics_single_record(self, mock_async_session_cls, mock_get_engine):
        """Test getting statistics with exactly one analyzed conversation."""
        # Arrange
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_row = MagicMock()
        mock_row.total_analyzed = 1
        mock_row.avg_messages = 5.0
        mock_row.latest_analysis = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        mock_result.one.return_value = mock_row
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_async_session_cls.return_value = _make_async_session_cm(mock_session)
        mock_get_engine.return_value = MagicMock()

        # Act
        result = await ConversationAnalyticsRepository.get_statistics()

        # Assert
        assert result["total_conversations_analyzed"] == 1
        assert result["avg_message_count"] == 5.0
        assert result["latest_analysis_date"] == datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    @patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine")
    @patch("codemie.repository.conversation_analytics_repository.AsyncSession")
    async def test_get_statistics_zero_count_with_null_aggregates(self, mock_async_session_cls, mock_get_engine):
        """Test statistics when count is 0 and aggregates are NULL (edge case)."""
        # Arrange
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_row = MagicMock()
        mock_row.total_analyzed = 0
        mock_row.avg_messages = None  # NULL average when count is 0
        mock_row.latest_analysis = None  # NULL max when count is 0
        mock_result.one.return_value = mock_row
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_async_session_cls.return_value = _make_async_session_cm(mock_session)
        mock_get_engine.return_value = MagicMock()

        # Act
        result = await ConversationAnalyticsRepository.get_statistics()

        # Assert
        assert result["total_conversations_analyzed"] == 0
        assert result["avg_message_count"] == 0.0  # Should default to 0.0
        assert result["latest_analysis_date"] is None


class TestConversationAnalyticsRepositoryEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    @patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine")
    @patch("codemie.repository.conversation_analytics_repository.AsyncSession")
    async def test_upsert_analysis_generates_id_when_missing(
        self, mock_async_session_cls, mock_get_engine, sample_analytics_minimal
    ):
        """Test that UPSERT generates UUID for new records when ID is None."""
        # Arrange
        sample_analytics_minimal.id = None  # Explicitly no ID

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # New record
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_async_session_cls.return_value = _make_async_session_cm(mock_session)
        mock_get_engine.return_value = MagicMock()

        # Act
        result_analytics, is_new = await ConversationAnalyticsRepository.upsert_analysis(sample_analytics_minimal)

        # Assert
        assert is_new is True
        assert sample_analytics_minimal.id is not None  # ID should be generated
        assert len(sample_analytics_minimal.id) == 36  # UUID format

    @pytest.mark.asyncio
    @patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine")
    @patch("codemie.repository.conversation_analytics_repository.AsyncSession")
    async def test_upsert_analysis_sets_timestamps_on_insert(
        self, mock_async_session_cls, mock_get_engine, sample_analytics_minimal
    ):
        """Test that UPSERT sets date and update_date on insert."""
        # Arrange
        sample_analytics_minimal.date = None
        sample_analytics_minimal.update_date = None

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # New record
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_async_session_cls.return_value = _make_async_session_cm(mock_session)
        mock_get_engine.return_value = MagicMock()

        # Act
        result_analytics, is_new = await ConversationAnalyticsRepository.upsert_analysis(sample_analytics_minimal)

        # Assert
        assert is_new is True
        assert sample_analytics_minimal.date is not None
        assert sample_analytics_minimal.update_date is not None

    @pytest.mark.asyncio
    @patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine")
    @patch("codemie.repository.conversation_analytics_repository.AsyncSession")
    async def test_upsert_analysis_updates_all_fields_except_id_and_date(
        self, mock_async_session_cls, mock_get_engine, sample_analytics
    ):
        """Test that UPSERT updates all fields except id and date on update."""
        # Arrange
        existing_id = "preserved-id"
        existing_date = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
        existing_analytics = ConversationAnalytics(
            id=existing_id,
            conversation_id="conv-test",
            user_id="user-old",
            user_name="Old Name",
            project="old-project",
            assistants_used=[],
            topics=[],
            satisfaction=None,
            maturity=None,
            anti_patterns=[],
            last_analysis_date=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            message_count_at_analysis=1,
            analyzed_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            llm_model_used="old-model",
            analysis_duration_seconds=0.1,
            date=existing_date,
            update_date=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_analytics
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_async_session_cls.return_value = _make_async_session_cm(mock_session)
        mock_get_engine.return_value = MagicMock()

        # Update with new data
        sample_analytics.conversation_id = "conv-test"
        sample_analytics.user_name = "New Name"
        sample_analytics.project = "new-project"
        sample_analytics.message_count_at_analysis = 20

        # Act
        result_analytics, is_new = await ConversationAnalyticsRepository.upsert_analysis(sample_analytics)

        # Assert
        assert is_new is False
        # Verify preserved fields
        assert existing_analytics.id == existing_id
        assert existing_analytics.date == existing_date
        # Verify updated fields were applied
        assert existing_analytics.user_name == "New Name"
        assert existing_analytics.project == "new-project"
        assert existing_analytics.message_count_at_analysis == 20

    @pytest.mark.asyncio
    async def test_get_reprocessing_candidates_returns_dict_mapping(self, sample_analytics, sample_analytics_minimal):
        """Test that get_reprocessing_candidates returns correct dict mapping structure."""
        # Arrange
        sample_analytics.conversation_id = "conv-a"
        sample_analytics_minimal.conversation_id = "conv-b"

        with patch("codemie.repository.conversation_analytics_repository.PostgresClient.get_async_engine"):
            with patch("codemie.repository.conversation_analytics_repository.AsyncSession") as mock_async_session_cls:
                mock_session = AsyncMock()
                mock_result = MagicMock()
                mock_scalars = MagicMock()
                mock_scalars.all.return_value = [sample_analytics, sample_analytics_minimal]
                mock_result.scalars.return_value = mock_scalars
                mock_session.execute = AsyncMock(return_value=mock_result)
                mock_async_session_cls.return_value = _make_async_session_cm(mock_session)

                # Act
                result = await ConversationAnalyticsRepository.get_reprocessing_candidates(["conv-a", "conv-b"])

                # Assert
                assert isinstance(result, dict)
                assert len(result) == 2
                assert result["conv-a"] == sample_analytics
                assert result["conv-b"] == sample_analytics_minimal
