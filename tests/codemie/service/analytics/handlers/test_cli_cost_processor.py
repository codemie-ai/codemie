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

"""Unit tests for CLICostProcessor and CLICostAdjustmentMixin."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.cli_cost_processor import (
    CLICostAdjustmentMixin,
    CLICostProcessor,
)
from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline


@pytest.fixture
def mock_user():
    """Create mock user."""
    user = MagicMock(spec=User)
    user.project_names = []
    user.admin_project_names = []
    user.is_global_user = False
    user.id = "test-user-id"
    return user


@pytest.fixture
def mock_repository():
    """Create mock repository."""
    return MagicMock(spec=MetricsElasticRepository)


@pytest.fixture
def mixin_instance(mock_user, mock_repository):
    """Create CLICostAdjustmentMixin instance with mocked dependencies."""

    class TestMixin(CLICostAdjustmentMixin):
        def __init__(self, user: User, repository: MetricsElasticRepository):
            self._pipeline = AnalyticsQueryPipeline(user, repository)
            self.repository = repository

    return TestMixin(mock_user, mock_repository)


class TestCLICostProcessor:
    """Tests for CLICostProcessor.adjust_date_range_for_cutoff method."""

    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2026-02-01")
    def test_adjust_date_range_entire_range_before_cutoff(self):
        """Verify returns None when entire range is before cutoff."""
        # Arrange
        start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2026, 1, 31, tzinfo=timezone.utc)

        # Act
        result = CLICostProcessor.adjust_date_range_for_cutoff(start_date, end_date)

        # Assert
        assert result is None

    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2026-02-01")
    def test_adjust_date_range_spans_cutoff(self):
        """Verify adjusts start date to cutoff when range spans cutoff."""
        # Arrange
        start_date = datetime(2026, 1, 15, tzinfo=timezone.utc)
        end_date = datetime(2026, 2, 15, tzinfo=timezone.utc)

        # Act
        result = CLICostProcessor.adjust_date_range_for_cutoff(start_date, end_date)

        # Assert
        assert result is not None
        adjusted_start, adjusted_end = result
        assert adjusted_start == datetime(2026, 2, 1, tzinfo=timezone.utc)
        assert adjusted_end == end_date

    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2026-02-01")
    def test_adjust_date_range_entire_range_after_cutoff(self):
        """Verify returns original dates when entire range is after cutoff."""
        # Arrange
        start_date = datetime(2026, 2, 10, tzinfo=timezone.utc)
        end_date = datetime(2026, 2, 20, tzinfo=timezone.utc)

        # Act
        result = CLICostProcessor.adjust_date_range_for_cutoff(start_date, end_date)

        # Assert
        assert result is not None
        adjusted_start, adjusted_end = result
        assert adjusted_start == start_date
        assert adjusted_end == end_date

    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", None)
    def test_adjust_date_range_no_cutoff_configured(self):
        """Verify returns original dates when no cutoff is configured."""
        # Arrange
        start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2026, 1, 31, tzinfo=timezone.utc)

        # Act
        result = CLICostProcessor.adjust_date_range_for_cutoff(start_date, end_date)

        # Assert
        assert result is not None
        adjusted_start, adjusted_end = result
        assert adjusted_start == start_date
        assert adjusted_end == end_date

    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "")
    def test_adjust_date_range_empty_cutoff(self):
        """Verify returns original dates when cutoff is empty string."""
        # Arrange
        start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2026, 1, 31, tzinfo=timezone.utc)

        # Act
        result = CLICostProcessor.adjust_date_range_for_cutoff(start_date, end_date)

        # Assert
        assert result is not None
        adjusted_start, adjusted_end = result
        assert adjusted_start == start_date
        assert adjusted_end == end_date

    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "invalid-date")
    def test_adjust_date_range_invalid_cutoff_format(self):
        """Verify returns original dates when cutoff format is invalid."""
        # Arrange
        start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2026, 1, 31, tzinfo=timezone.utc)

        # Act
        result = CLICostProcessor.adjust_date_range_for_cutoff(start_date, end_date)

        # Assert
        assert result is not None
        adjusted_start, adjusted_end = result
        assert adjusted_start == start_date
        assert adjusted_end == end_date

    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2026-02-01")
    def test_adjust_date_range_boundary_end_equals_cutoff(self):
        """Verify behavior when end date equals cutoff (adjusts start to cutoff)."""
        # Arrange
        start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2026, 2, 1, tzinfo=timezone.utc)

        # Act
        result = CLICostProcessor.adjust_date_range_for_cutoff(start_date, end_date)

        # Assert - end_date equals cutoff, so start is adjusted to cutoff
        assert result is not None
        adjusted_start, adjusted_end = result
        assert adjusted_start == datetime(2026, 2, 1, tzinfo=timezone.utc)
        assert adjusted_end == end_date

    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2026-02-01")
    def test_adjust_date_range_boundary_start_equals_cutoff(self):
        """Verify behavior when start date equals cutoff (no adjustment needed)."""
        # Arrange
        start_date = datetime(2026, 2, 1, tzinfo=timezone.utc)
        end_date = datetime(2026, 2, 15, tzinfo=timezone.utc)

        # Act
        result = CLICostProcessor.adjust_date_range_for_cutoff(start_date, end_date)

        # Assert
        assert result is not None
        adjusted_start, adjusted_end = result
        assert adjusted_start == start_date
        assert adjusted_end == end_date


class TestCLICostAdjustmentMixin:
    """Tests for CLICostAdjustmentMixin methods."""

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2026-02-01")
    async def test_get_cli_costs_with_adjustment_entire_range_before_cutoff(self, mixin_instance):
        """Verify returns zeros when entire range is before cutoff."""
        # Arrange
        start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2026, 1, 31, tzinfo=timezone.utc)

        # Act
        result = await mixin_instance.get_cli_costs_with_adjustment(
            start_date, end_date, users=["user1"], projects=["project1"], include_cache_costs=True
        )

        # Assert
        assert result == {
            "total_cost": 0.0,
            "cache_read_cost": 0.0,
            "cache_creation_cost": 0.0,
        }

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2026-02-01")
    async def test_get_cli_costs_with_adjustment_entire_range_before_cutoff_no_cache(self, mixin_instance):
        """Verify returns only total_cost when cache costs not included."""
        # Arrange
        start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2026, 1, 31, tzinfo=timezone.utc)

        # Act
        result = await mixin_instance.get_cli_costs_with_adjustment(
            start_date, end_date, users=["user1"], projects=None, include_cache_costs=False
        )

        # Assert
        assert result == {"total_cost": 0.0}
        assert "cache_read_cost" not in result
        assert "cache_creation_cost" not in result

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2026-02-01")
    async def test_get_cli_costs_with_adjustment_queries_with_adjusted_dates(self, mixin_instance, mock_repository):
        """Verify queries repository with adjusted dates."""
        # Arrange
        start_date = datetime(2026, 1, 15, tzinfo=timezone.utc)
        end_date = datetime(2026, 2, 15, tzinfo=timezone.utc)

        mock_repository.execute_aggregation_query = AsyncMock(
            return_value={
                "aggregations": {
                    "total_cost": {"value": 123.45},
                    "cache_read_cost": {"value": 10.50},
                }
            }
        )

        # Act
        result = await mixin_instance.get_cli_costs_with_adjustment(
            start_date, end_date, users=["user1"], projects=["project1"], include_cache_costs=True
        )

        # Assert - cache_creation_cost is now hardcoded to 0.0 (field removed from NEW metric)
        assert result == {
            "total_cost": 123.45,
            "cache_read_cost": 10.50,
            "cache_creation_cost": 0.0,
        }
        # Verify repository was called
        assert mock_repository.execute_aggregation_query.call_count == 1

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2026-02-01")
    async def test_get_cli_costs_with_adjustment_handles_none_values(self, mixin_instance, mock_repository):
        """Verify handles None values from ES gracefully."""
        # Arrange
        start_date = datetime(2026, 2, 1, tzinfo=timezone.utc)
        end_date = datetime(2026, 2, 15, tzinfo=timezone.utc)

        mock_repository.execute_aggregation_query = AsyncMock(
            return_value={
                "aggregations": {
                    "total_cost": {"value": None},
                    "cache_read_cost": {"value": None},
                    "cache_creation_cost": {"value": None},
                }
            }
        )

        # Act
        result = await mixin_instance.get_cli_costs_with_adjustment(
            start_date, end_date, users=None, projects=None, include_cache_costs=True
        )

        # Assert
        assert result == {
            "total_cost": 0.0,
            "cache_read_cost": 0.0,
            "cache_creation_cost": 0.0,
        }

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2026-02-01")
    async def test_get_cli_costs_with_adjustment_handles_missing_aggregations(self, mixin_instance, mock_repository):
        """Verify handles missing aggregations from ES."""
        # Arrange
        start_date = datetime(2026, 2, 1, tzinfo=timezone.utc)
        end_date = datetime(2026, 2, 15, tzinfo=timezone.utc)

        mock_repository.execute_aggregation_query = AsyncMock(return_value={"aggregations": {}})

        # Act
        result = await mixin_instance.get_cli_costs_with_adjustment(
            start_date, end_date, users=None, projects=None, include_cache_costs=True
        )

        # Assert
        assert result == {
            "total_cost": 0.0,
            "cache_read_cost": 0.0,
            "cache_creation_cost": 0.0,
        }

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2026-02-01")
    async def test_get_cli_costs_grouped_by_entire_range_before_cutoff(self, mixin_instance):
        """Verify returns empty dict when entire range is before cutoff."""
        # Arrange
        start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2026, 1, 31, tzinfo=timezone.utc)

        # Act
        result = await mixin_instance.get_cli_costs_grouped_by(
            start_date,
            end_date,
            group_by_field="attributes.project.keyword",
            entity_name="project",
            users=None,
            projects=None,
        )

        # Assert
        assert result == {}

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2026-02-01")
    async def test_get_cli_costs_grouped_by_queries_with_adjusted_dates(self, mixin_instance, mock_repository):
        """Verify queries repository with adjusted dates for grouped costs."""
        # Arrange
        start_date = datetime(2026, 1, 15, tzinfo=timezone.utc)
        end_date = datetime(2026, 2, 15, tzinfo=timezone.utc)

        mock_repository.execute_aggregation_query = AsyncMock(
            return_value={
                "aggregations": {
                    "grouped_entities": {
                        "buckets": [
                            {"key": "project-a", "cli_cost": {"value": 100.50}},
                            {"key": "project-b", "cli_cost": {"value": 50.25}},
                            {"key": "project-c", "cli_cost": {"value": None}},  # None should become 0
                        ]
                    }
                }
            }
        )

        # Act
        result = await mixin_instance.get_cli_costs_grouped_by(
            start_date,
            end_date,
            group_by_field="attributes.project.keyword",
            entity_name="project",
            users=["user1"],
            projects=None,
        )

        # Assert
        assert result == {
            "project-a": 100.50,
            "project-b": 50.25,
            "project-c": 0.0,
        }
        assert mock_repository.execute_aggregation_query.call_count == 1

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2026-02-01")
    async def test_get_cli_costs_grouped_by_handles_empty_buckets(self, mixin_instance, mock_repository):
        """Verify handles empty buckets from ES."""
        # Arrange
        start_date = datetime(2026, 2, 1, tzinfo=timezone.utc)
        end_date = datetime(2026, 2, 15, tzinfo=timezone.utc)

        mock_repository.execute_aggregation_query = AsyncMock(
            return_value={"aggregations": {"grouped_entities": {"buckets": []}}}
        )

        # Act
        result = await mixin_instance.get_cli_costs_grouped_by(
            start_date,
            end_date,
            group_by_field="attributes.user_id.keyword",
            entity_name="user",
            users=None,
            projects=["project1"],
        )

        # Assert
        assert result == {}

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2024-01-01")
    async def test_query_cli_costs_without_cache_costs(self, mixin_instance, mock_repository):
        """Verify _query_cli_costs excludes cache costs when not requested."""
        # Arrange
        start_date = datetime(2026, 2, 1, tzinfo=timezone.utc)
        end_date = datetime(2026, 2, 15, tzinfo=timezone.utc)

        mock_repository.execute_aggregation_query = AsyncMock(
            return_value={"aggregations": {"total_cost": {"value": 75.00}}}
        )

        # Act
        result = await mixin_instance._query_cli_costs(
            start_date, end_date, users=None, projects=None, include_cache_costs=False
        )

        # Assert
        assert result == {"total_cost": 75.00}
        assert "cache_read_cost" not in result
        assert "cache_creation_cost" not in result

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2024-01-01")
    async def test_query_cli_costs_with_cache_costs(self, mixin_instance, mock_repository):
        """Verify _query_cli_costs includes cache costs when requested."""
        # Arrange
        start_date = datetime(2026, 2, 1, tzinfo=timezone.utc)
        end_date = datetime(2026, 2, 15, tzinfo=timezone.utc)

        mock_repository.execute_aggregation_query = AsyncMock(
            return_value={
                "aggregations": {
                    "total_cost": {"value": 100.00},
                    "cache_read_cost": {"value": 20.00},
                }
            }
        )

        # Act
        result = await mixin_instance._query_cli_costs(
            start_date, end_date, users=["user1"], projects=["project1"], include_cache_costs=True
        )

        # Assert - cache_creation_cost is now hardcoded to 0.0 (field removed from NEW metric)
        assert result == {
            "total_cost": 100.00,
            "cache_read_cost": 20.00,
            "cache_creation_cost": 0.0,
        }

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2024-01-01")
    async def test_query_cli_costs_rounds_to_two_decimals(self, mixin_instance, mock_repository):
        """Verify costs are rounded to 2 decimal places."""
        # Arrange
        start_date = datetime(2026, 2, 1, tzinfo=timezone.utc)
        end_date = datetime(2026, 2, 15, tzinfo=timezone.utc)

        mock_repository.execute_aggregation_query = AsyncMock(
            return_value={
                "aggregations": {
                    "total_cost": {"value": 123.456789},
                    "cache_read_cost": {"value": 10.999},
                }
            }
        )

        # Act
        result = await mixin_instance._query_cli_costs(
            start_date, end_date, users=None, projects=None, include_cache_costs=True
        )

        # Assert - cache_creation_cost is now hardcoded to 0.0 (field removed from NEW metric)
        assert result == {
            "total_cost": 123.46,  # Rounded
            "cache_read_cost": 11.00,  # Rounded
            "cache_creation_cost": 0.0,  # Hardcoded to 0.0
        }
