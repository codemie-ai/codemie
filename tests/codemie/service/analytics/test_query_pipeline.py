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

"""Unit tests for AnalyticsQueryPipeline.

This test suite covers the query pipeline orchestration including:
- Component initialization and coordination
- Query building with filters
- Execution time measurement
- Response formatting
- Conditional filter application
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline


class TestAnalyticsQueryPipeline:
    """Test suite for AnalyticsQueryPipeline."""

    @pytest.fixture
    def mock_user(self):
        """Mock User for pipeline context."""
        user = MagicMock()
        user.id = "test-user-id"
        user.projects = ["project1", "project2"]
        return user

    @pytest.fixture
    def mock_repository(self):
        """Mock MetricsElasticRepository."""
        from unittest.mock import AsyncMock

        repository = MagicMock()
        # Make async methods return AsyncMock instances
        repository.execute_esql_query = AsyncMock()
        repository.execute_aggregation_query = AsyncMock()
        repository.execute_search_query = AsyncMock()
        return repository

    @pytest.fixture
    def pipeline(self, mock_user, mock_repository):
        """Create pipeline with mocked dependencies."""
        return AnalyticsQueryPipeline(user=mock_user, repository=mock_repository)

    @pytest.fixture
    def sample_start_dt(self):
        """Sample start datetime."""
        return datetime(2024, 1, 1, 0, 0, 0)

    @pytest.fixture
    def sample_end_dt(self):
        """Sample end datetime."""
        return datetime(2024, 1, 31, 23, 59, 59)

    # ===== Initialization Tests =====

    def test_init_stores_user_and_repository(self, mock_user, mock_repository):
        """Verify pipeline initialization stores user and repository."""
        # Act
        pipeline = AnalyticsQueryPipeline(mock_user, mock_repository)

        # Assert
        assert pipeline._user == mock_user
        assert pipeline._repository == mock_repository

    # ===== execute_tabular_query Tests =====

    @patch("codemie.service.analytics.query_pipeline.TotalsCalculator")
    @patch("codemie.service.analytics.query_pipeline.ResponseFormatter")
    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    @patch("codemie.service.analytics.query_pipeline.TimeParser")
    @pytest.mark.asyncio
    async def test_execute_tabular_query_calls_components_in_correct_order(
        self,
        mock_time_parser,
        mock_query_builder_class,
        mock_formatter,
        mock_totals_calculator,
        pipeline,
        mock_repository,
        sample_start_dt,
        sample_end_dt,
    ):
        """Verify pipeline orchestrates components in correct order."""
        # Arrange
        mock_agg_builder = MagicMock(
            return_value={
                "query": {"match_all": {}},
                "size": 0,
                "aggs": {"paginated_results": {"terms": {"field": "test", "size": 20}}},
            }
        )
        mock_result_parser = MagicMock(return_value=[{"row": "data"}])
        columns = [{"name": "col1", "type": "string"}]

        # Mock TimeParser
        mock_time_parser.parse.return_value = (sample_start_dt, sample_end_dt)

        # Mock SecureQueryBuilder
        mock_builder = MagicMock()
        mock_builder.build.return_value = {"query": "built"}
        mock_query_builder_class.return_value = mock_builder

        # Mock repository
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {"paginated_results": {"buckets": []}, "total_buckets": {"value": 0}}
        }

        # Mock formatter
        mock_formatter.format_tabular_response.return_value = {"formatted": "response"}

        # Mock TotalsCalculator
        mock_totals_calculator.calculate_totals.return_value = {"total_cost_usd": 100.50}

        # Act
        result = await pipeline.execute_tabular_query(
            agg_builder=mock_agg_builder,
            result_parser=mock_result_parser,
            columns=columns,
            group_by_field="test.field.keyword",
            time_period="last_30_days",
            page=0,
            per_page=20,
        )

        # Assert - Verify call order and arguments
        mock_time_parser.parse.assert_called_once_with("last_30_days", None, None)
        mock_query_builder_class.assert_called_once_with(pipeline._user)
        mock_builder.add_time_range.assert_called_once_with(sample_start_dt, sample_end_dt, "@timestamp")
        mock_builder.build.assert_called_once()
        # Parallel queries: agg_builder called TWICE (data + totals) with fetch_size=10000
        assert mock_agg_builder.call_count == 2
        mock_agg_builder.assert_called_with({"query": "built"}, 10000)
        # Check that the call includes the cardinality aggregation
        call_args = mock_repository.execute_aggregation_query.call_args[0][0]
        assert "aggs" in call_args
        assert "paginated_results" in call_args["aggs"]
        assert "total_buckets" in call_args["aggs"]
        # Result parser is called TWICE: once for all rows (totals), once for paginated rows
        assert mock_result_parser.call_count == 2
        # Both calls should be with empty buckets in this test
        mock_result_parser.assert_called_with({"aggregations": {"paginated_results": {"buckets": []}}})
        # TotalsCalculator is called with columns and ALL rows (not paginated subset)
        mock_totals_calculator.calculate_totals.assert_called_once_with(columns=columns, rows=[{"row": "data"}])
        # Formatter is called with totals
        mock_formatter.format_tabular_response.assert_called_once()
        call_kwargs = mock_formatter.format_tabular_response.call_args[1]
        assert call_kwargs["totals"] == {"total_cost_usd": 100.50}
        # Pipeline adds pagination to the formatted response
        assert result["formatted"] == "response"
        assert "pagination" in result
        assert result["pagination"]["page"] == 0
        assert result["pagination"]["per_page"] == 20
        assert result["pagination"]["total_count"] == 0
        assert result["pagination"]["has_more"] is False

    @patch("codemie.service.analytics.query_pipeline.TotalsCalculator")
    @patch("codemie.service.analytics.query_pipeline.ResponseFormatter")
    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    @patch("codemie.service.analytics.query_pipeline.TimeParser")
    @pytest.mark.asyncio
    async def test_execute_tabular_query_calculates_totals_for_numeric_columns(
        self,
        mock_time_parser,
        mock_query_builder_class,
        mock_formatter,
        mock_totals_calculator,
        pipeline,
        mock_repository,
        sample_start_dt,
        sample_end_dt,
    ):
        """Verify totals are calculated and passed to response formatter."""
        # Arrange
        columns = [
            {"id": "user_email", "type": "string"},
            {"id": "total_cost_usd", "type": "number", "format": "currency"},
            {"id": "total_tokens", "type": "number"},
        ]
        rows = [
            {"user_email": "user1@example.com", "total_cost_usd": 10.50, "total_tokens": 50000},
            {"user_email": "user2@example.com", "total_cost_usd": 9.86, "total_tokens": 165724},
        ]
        expected_totals = {"total_cost_usd": 20.36, "total_tokens": 215724.0}

        mock_agg_builder = MagicMock(
            return_value={
                "query": {"match_all": {}},
                "size": 0,
                "aggs": {"paginated_results": {"terms": {"field": "test", "size": 20}}},
            }
        )
        mock_result_parser = MagicMock(return_value=rows)

        mock_time_parser.parse.return_value = (sample_start_dt, sample_end_dt)
        mock_builder = MagicMock()
        mock_builder.build.return_value = {"query": "built"}
        mock_query_builder_class.return_value = mock_builder
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {"paginated_results": {"buckets": []}, "total_buckets": {"value": 2}}
        }
        mock_totals_calculator.calculate_totals.return_value = expected_totals
        mock_formatter.format_tabular_response.return_value = {"data": {"rows": rows, "totals": expected_totals}}

        # Act
        await pipeline.execute_tabular_query(
            agg_builder=mock_agg_builder,
            result_parser=mock_result_parser,
            columns=columns,
            group_by_field="user_email.keyword",
        )

        # Assert
        # Verify TotalsCalculator was called with correct parameters (ALL rows, not paginated)
        mock_totals_calculator.calculate_totals.assert_called_once_with(columns=columns, rows=rows)

        # Verify totals were passed to ResponseFormatter
        call_kwargs = mock_formatter.format_tabular_response.call_args[1]
        assert call_kwargs["totals"] == expected_totals
        assert call_kwargs["rows"] == rows
        assert call_kwargs["columns"] == columns

    @patch("codemie.service.analytics.query_pipeline.ResponseFormatter")
    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    @patch("codemie.service.analytics.query_pipeline.TimeParser")
    @pytest.mark.asyncio
    async def test_execute_tabular_query_passes_metric_filters_to_builder(
        self,
        mock_time_parser,
        mock_query_builder_class,
        mock_formatter,
        pipeline,
        mock_repository,
        sample_start_dt,
        sample_end_dt,
    ):
        """Verify metric filters are applied when provided."""
        # Arrange
        mock_agg_builder = MagicMock(
            return_value={
                "query": {"match_all": {}},
                "size": 0,
                "aggs": {"paginated_results": {"terms": {"field": "test", "size": 20}}},
            }
        )
        mock_result_parser = MagicMock(return_value=[])
        columns = []
        metric_filters = ["metric1", "metric2"]

        mock_time_parser.parse.return_value = (sample_start_dt, sample_end_dt)
        mock_builder = MagicMock()
        mock_builder.build.return_value = {"query": "built"}
        mock_query_builder_class.return_value = mock_builder
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {"paginated_results": {"buckets": []}, "total_buckets": {"value": 0}}
        }
        mock_formatter.format_tabular_response.return_value = {}

        # Act
        await pipeline.execute_tabular_query(
            agg_builder=mock_agg_builder,
            result_parser=mock_result_parser,
            columns=columns,
            group_by_field="test.field.keyword",
            metric_filters=metric_filters,
        )

        # Assert
        mock_builder.add_metric_filter.assert_called_once_with(["metric1", "metric2"])

    @patch("codemie.service.analytics.query_pipeline.ResponseFormatter")
    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    @patch("codemie.service.analytics.query_pipeline.TimeParser")
    @pytest.mark.asyncio
    async def test_execute_tabular_query_skips_metric_filter_when_none(
        self,
        mock_time_parser,
        mock_query_builder_class,
        mock_formatter,
        pipeline,
        mock_repository,
        sample_start_dt,
        sample_end_dt,
    ):
        """Verify metric filter not added when None."""
        # Arrange
        mock_agg_builder = MagicMock(
            return_value={
                "query": {"match_all": {}},
                "size": 0,
                "aggs": {"paginated_results": {"terms": {"field": "test", "size": 20}}},
            }
        )
        mock_result_parser = MagicMock(return_value=[])
        columns = []

        mock_time_parser.parse.return_value = (sample_start_dt, sample_end_dt)
        mock_builder = MagicMock()
        mock_builder.build.return_value = {"query": "built"}
        mock_query_builder_class.return_value = mock_builder
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {"paginated_results": {"buckets": []}, "total_buckets": {"value": 0}}
        }
        mock_formatter.format_tabular_response.return_value = {}

        # Act
        await pipeline.execute_tabular_query(
            agg_builder=mock_agg_builder,
            result_parser=mock_result_parser,
            columns=columns,
            group_by_field="test.field.keyword",
            metric_filters=None,
        )

        # Assert
        mock_builder.add_metric_filter.assert_not_called()

    @patch("codemie.service.analytics.query_pipeline.ResponseFormatter")
    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    @patch("codemie.service.analytics.query_pipeline.TimeParser")
    @pytest.mark.asyncio
    async def test_execute_tabular_query_passes_pagination_to_agg_builder(
        self,
        mock_time_parser,
        mock_query_builder_class,
        mock_formatter,
        pipeline,
        mock_repository,
        sample_start_dt,
        sample_end_dt,
    ):
        """Verify pagination parameters passed to aggregation builder."""
        # Arrange
        mock_agg_builder = MagicMock(
            return_value={
                "query": {"match_all": {}},
                "size": 0,
                "aggs": {"paginated_results": {"terms": {"field": "test", "size": 150}}},
            }
        )
        mock_result_parser = MagicMock(return_value=[])
        columns = []

        mock_time_parser.parse.return_value = (sample_start_dt, sample_end_dt)
        mock_builder = MagicMock()
        mock_builder.build.return_value = {"query": "built"}
        mock_query_builder_class.return_value = mock_builder
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {"paginated_results": {"buckets": []}, "total_buckets": {"value": 0}}
        }
        mock_formatter.format_tabular_response.return_value = {}

        # Act
        await pipeline.execute_tabular_query(
            agg_builder=mock_agg_builder,
            result_parser=mock_result_parser,
            columns=columns,
            group_by_field="test.field.keyword",
            page=2,
            per_page=50,
        )

        # Assert - Parallel queries: agg_builder called TWICE (data + totals) with fetch_size=10000
        assert mock_agg_builder.call_count == 2
        mock_agg_builder.assert_called_with({"query": "built"}, 10000)

    @patch("codemie.service.analytics.query_pipeline.time")
    @patch("codemie.service.analytics.query_pipeline.ResponseFormatter")
    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    @patch("codemie.service.analytics.query_pipeline.TimeParser")
    @pytest.mark.asyncio
    async def test_execute_tabular_query_calculates_execution_time(
        self,
        mock_time_parser,
        mock_query_builder_class,
        mock_formatter,
        mock_time,
        pipeline,
        mock_repository,
        sample_start_dt,
        sample_end_dt,
    ):
        """Verify execution time is measured and passed to response formatter."""
        # Arrange
        mock_agg_builder = MagicMock(
            return_value={
                "query": {"match_all": {}},
                "size": 0,
                "aggs": {"paginated_results": {"terms": {"field": "test", "size": 20}}},
            }
        )
        mock_result_parser = MagicMock(return_value=[])
        columns = []

        # Mock time.time() to return controlled values
        mock_time.time.side_effect = [1.0, 1.5]  # Start: 1.0, End: 1.5 (0.5 seconds)

        mock_time_parser.parse.return_value = (sample_start_dt, sample_end_dt)
        mock_builder = MagicMock()
        mock_builder.build.return_value = {"query": "built"}
        mock_query_builder_class.return_value = mock_builder
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {"paginated_results": {"buckets": []}, "total_buckets": {"value": 0}}
        }
        mock_formatter.format_tabular_response.return_value = {}

        # Act
        await pipeline.execute_tabular_query(
            agg_builder=mock_agg_builder,
            result_parser=mock_result_parser,
            columns=columns,
            group_by_field="test.field.keyword",
        )

        # Assert
        call_kwargs = mock_formatter.format_tabular_response.call_args[1]
        assert call_kwargs["execution_time_ms"] == pytest.approx(500.0)  # 0.5 seconds * 1000

    @patch("codemie.service.analytics.query_pipeline.ResponseFormatter")
    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    @patch("codemie.service.analytics.query_pipeline.TimeParser")
    @pytest.mark.asyncio
    async def test_execute_tabular_query_returns_formatted_response(
        self,
        mock_time_parser,
        mock_query_builder_class,
        mock_formatter,
        pipeline,
        mock_repository,
        sample_start_dt,
        sample_end_dt,
    ):
        """Verify pipeline returns formatted response."""
        # Arrange
        mock_agg_builder = MagicMock(
            return_value={
                "query": {"match_all": {}},
                "size": 0,
                "aggs": {"paginated_results": {"terms": {"field": "test", "size": 20}}},
            }
        )
        mock_result_parser = MagicMock(return_value=[])
        columns = []
        expected_response = {"data": "formatted", "status": "success"}

        mock_time_parser.parse.return_value = (sample_start_dt, sample_end_dt)
        mock_builder = MagicMock()
        mock_builder.build.return_value = {"query": "built"}
        mock_query_builder_class.return_value = mock_builder
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {"paginated_results": {"buckets": []}, "total_buckets": {"value": 0}}
        }
        mock_formatter.format_tabular_response.return_value = expected_response

        # Act
        result = await pipeline.execute_tabular_query(
            agg_builder=mock_agg_builder,
            result_parser=mock_result_parser,
            columns=columns,
            group_by_field="test.field.keyword",
        )

        # Assert
        assert result == expected_response

    # ===== execute_summary_query Tests =====

    @patch("codemie.service.analytics.query_pipeline.ResponseFormatter")
    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    @patch("codemie.service.analytics.query_pipeline.TimeParser")
    @pytest.mark.asyncio
    async def test_execute_summary_query_calls_components_in_correct_order(
        self,
        mock_time_parser,
        mock_query_builder_class,
        mock_formatter,
        pipeline,
        mock_repository,
        sample_start_dt,
        sample_end_dt,
    ):
        """Verify summary query pipeline orchestration."""
        # Arrange
        mock_agg_builder = MagicMock(return_value={"agg": "body"})
        mock_metrics_builder = MagicMock(return_value=[{"metric": "value"}])

        mock_time_parser.parse.return_value = (sample_start_dt, sample_end_dt)
        mock_builder = MagicMock()
        mock_builder.build.return_value = {"query": "built"}
        mock_query_builder_class.return_value = mock_builder
        mock_repository.execute_aggregation_query.return_value = {"aggregations": {}}
        mock_formatter.format_summary_response.return_value = {"formatted": "summary"}

        # Act
        result = await pipeline.execute_summary_query(
            agg_builder=mock_agg_builder,
            metrics_builder=mock_metrics_builder,
            time_period="last_7_days",
        )

        # Assert - Verify call order
        mock_time_parser.parse.assert_called_once_with("last_7_days", None, None)
        mock_query_builder_class.assert_called_once_with(pipeline._user)
        mock_builder.add_time_range.assert_called_once_with(sample_start_dt, sample_end_dt, "@timestamp")
        mock_builder.build.assert_called_once()
        mock_agg_builder.assert_called_once_with({"query": "built"})
        mock_repository.execute_aggregation_query.assert_called_once_with({"agg": "body"})
        mock_metrics_builder.assert_called_once_with({"aggregations": {}})
        mock_formatter.format_summary_response.assert_called_once()
        assert result == {"formatted": "summary"}

    @patch("codemie.service.analytics.query_pipeline.ResponseFormatter")
    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    @patch("codemie.service.analytics.query_pipeline.TimeParser")
    @pytest.mark.asyncio
    async def test_execute_summary_query_agg_builder_receives_only_query(
        self,
        mock_time_parser,
        mock_query_builder_class,
        mock_formatter,
        pipeline,
        mock_repository,
        sample_start_dt,
        sample_end_dt,
    ):
        """Verify summary agg_builder doesn't receive pagination."""
        # Arrange
        mock_agg_builder = MagicMock(return_value={"agg": "body"})
        mock_metrics_builder = MagicMock(return_value=[])

        mock_time_parser.parse.return_value = (sample_start_dt, sample_end_dt)
        mock_builder = MagicMock()
        mock_builder.build.return_value = {"query": "built"}
        mock_query_builder_class.return_value = mock_builder
        mock_repository.execute_aggregation_query.return_value = {}
        mock_formatter.format_summary_response.return_value = {}

        # Act
        await pipeline.execute_summary_query(
            agg_builder=mock_agg_builder,
            metrics_builder=mock_metrics_builder,
        )

        # Assert - Only query passed, no page/per_page
        mock_agg_builder.assert_called_once_with({"query": "built"})

    @patch("codemie.service.analytics.query_pipeline.ResponseFormatter")
    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    @patch("codemie.service.analytics.query_pipeline.TimeParser")
    @pytest.mark.asyncio
    async def test_execute_summary_query_formats_response_with_metrics(
        self,
        mock_time_parser,
        mock_query_builder_class,
        mock_formatter,
        pipeline,
        mock_repository,
        sample_start_dt,
        sample_end_dt,
    ):
        """Verify metrics are passed to response formatter."""
        # Arrange
        mock_agg_builder = MagicMock(return_value={"agg": "body"})
        expected_metrics = [
            {"name": "total_requests", "value": 1000},
            {"name": "avg_latency", "value": 250},
        ]
        mock_metrics_builder = MagicMock(return_value=expected_metrics)

        mock_time_parser.parse.return_value = (sample_start_dt, sample_end_dt)
        mock_builder = MagicMock()
        mock_builder.build.return_value = {"query": "built"}
        mock_query_builder_class.return_value = mock_builder
        mock_repository.execute_aggregation_query.return_value = {}
        mock_formatter.format_summary_response.return_value = {}

        # Act
        await pipeline.execute_summary_query(
            agg_builder=mock_agg_builder,
            metrics_builder=mock_metrics_builder,
        )

        # Assert
        call_kwargs = mock_formatter.format_summary_response.call_args[1]
        assert call_kwargs["metrics"] == expected_metrics

    # ===== execute_esql_query Tests =====

    @patch("codemie.service.analytics.query_pipeline.ResponseFormatter")
    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    @patch("codemie.service.analytics.query_pipeline.TimeParser")
    @pytest.mark.asyncio
    async def test_execute_esql_query_calls_repository_with_filter_query(
        self,
        mock_time_parser,
        mock_query_builder_class,
        mock_formatter,
        pipeline,
        mock_repository,
        sample_start_dt,
        sample_end_dt,
    ):
        """Verify ES|QL query execution passes filter to repository."""
        # Arrange
        esql_query = "FROM metrics | WHERE event_type == 'request' | STATS count = COUNT(*)"
        mock_result_parser = MagicMock(return_value=[])
        columns = []

        mock_time_parser.parse.return_value = (sample_start_dt, sample_end_dt)
        mock_builder = MagicMock()
        mock_builder.build.return_value = {"query": "built"}
        mock_query_builder_class.return_value = mock_builder
        mock_repository.execute_esql_query.return_value = {}
        mock_formatter.format_tabular_response.return_value = {}

        # Act
        await pipeline.execute_esql_query(
            esql_query=esql_query,
            result_parser=mock_result_parser,
            columns=columns,
        )

        # Assert
        mock_repository.execute_esql_query.assert_called_once_with(esql_query, filter_query={"query": "built"})

    @patch("codemie.service.analytics.query_pipeline.ResponseFormatter")
    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    @patch("codemie.service.analytics.query_pipeline.TimeParser")
    @pytest.mark.asyncio
    async def test_execute_esql_query_applies_all_filters_to_filter_query(
        self,
        mock_time_parser,
        mock_query_builder_class,
        mock_formatter,
        pipeline,
        mock_repository,
        sample_start_dt,
        sample_end_dt,
    ):
        """Verify filter query includes all requested filters."""
        # Arrange
        esql_query = "FROM metrics | STATS count = COUNT(*)"
        mock_result_parser = MagicMock(return_value=[])
        columns = []

        mock_time_parser.parse.return_value = (sample_start_dt, sample_end_dt)
        mock_builder = MagicMock()
        mock_builder.build.return_value = {"query": "built"}
        mock_query_builder_class.return_value = mock_builder
        mock_repository.execute_esql_query.return_value = {}
        mock_formatter.format_tabular_response.return_value = {}

        # Act
        await pipeline.execute_esql_query(
            esql_query=esql_query,
            result_parser=mock_result_parser,
            columns=columns,
            users=["user1"],
            projects=["proj1"],
            metric_filters=["metric1"],
        )

        # Assert - Verify all filters were added
        mock_builder.add_time_range.assert_called_once_with(sample_start_dt, sample_end_dt, "@timestamp")
        mock_builder.add_metric_filter.assert_called_once_with(["metric1"])
        mock_builder.add_user_filter.assert_called_once_with(["user1"])
        mock_builder.add_project_filter.assert_called_once_with(["proj1"])

    @patch("codemie.service.analytics.query_pipeline.ResponseFormatter")
    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    @patch("codemie.service.analytics.query_pipeline.TimeParser")
    @pytest.mark.asyncio
    async def test_execute_esql_query_parses_result_and_formats_response(
        self,
        mock_time_parser,
        mock_query_builder_class,
        mock_formatter,
        pipeline,
        mock_repository,
        sample_start_dt,
        sample_end_dt,
    ):
        """Verify ES|QL result is parsed and formatted."""
        # Arrange
        esql_query = "FROM metrics | STATS count = COUNT(*)"
        esql_result = {"values": [[1000], [250]]}
        parsed_rows = [{"count": 1000}, {"count": 250}]
        mock_result_parser = MagicMock(return_value=parsed_rows)
        columns = [{"name": "count", "type": "number"}]
        expected_response = {"rows": parsed_rows, "columns": columns}

        mock_time_parser.parse.return_value = (sample_start_dt, sample_end_dt)
        mock_builder = MagicMock()
        mock_builder.build.return_value = {"query": "built"}
        mock_query_builder_class.return_value = mock_builder
        mock_repository.execute_esql_query.return_value = esql_result
        mock_formatter.format_tabular_response.return_value = expected_response

        # Act
        result = await pipeline.execute_esql_query(
            esql_query=esql_query,
            result_parser=mock_result_parser,
            columns=columns,
        )

        # Assert
        mock_result_parser.assert_called_once_with(esql_result)
        call_kwargs = mock_formatter.format_tabular_response.call_args[1]
        assert call_kwargs["rows"] == parsed_rows
        assert result == expected_response

    # ===== _build_query Tests =====

    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    def test_build_query_creates_secure_query_builder_with_user(
        self, mock_query_builder_class, pipeline, sample_start_dt, sample_end_dt
    ):
        """Verify query builder is initialized with user context."""
        # Arrange
        mock_builder = MagicMock()
        mock_builder.build.return_value = {"query": "built"}
        mock_query_builder_class.return_value = mock_builder

        # Act
        query = pipeline._build_query(sample_start_dt, sample_end_dt, None, None, None)

        # Assert
        mock_query_builder_class.assert_called_once_with(pipeline._user)
        assert query == {"query": "built"}

    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    def test_build_query_adds_time_range(self, mock_query_builder_class, pipeline, sample_start_dt, sample_end_dt):
        """Verify time range always added."""
        # Arrange
        mock_builder = MagicMock()
        mock_builder.build.return_value = {"query": "built"}
        mock_query_builder_class.return_value = mock_builder

        # Act
        pipeline._build_query(sample_start_dt, sample_end_dt, None, None, None)

        # Assert
        mock_builder.add_time_range.assert_called_once_with(sample_start_dt, sample_end_dt, "@timestamp")

    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    def test_build_query_conditionally_adds_optional_filters(
        self, mock_query_builder_class, pipeline, sample_start_dt, sample_end_dt
    ):
        """Verify optional filters only added when provided."""
        # Arrange
        mock_builder = MagicMock()
        mock_builder.build.return_value = {"query": "built"}
        mock_query_builder_class.return_value = mock_builder

        # Act
        pipeline._build_query(
            sample_start_dt,
            sample_end_dt,
            users=["u1"],
            projects=["p1"],
            metric_filters=["m1"],
        )

        # Assert
        mock_builder.add_metric_filter.assert_called_once_with(["m1"])
        mock_builder.add_user_filter.assert_called_once_with(["u1"])
        mock_builder.add_project_filter.assert_called_once_with(["p1"])

    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    def test_build_query_skips_none_filters(self, mock_query_builder_class, pipeline, sample_start_dt, sample_end_dt):
        """Verify None filters are not added."""
        # Arrange
        mock_builder = MagicMock()
        mock_builder.build.return_value = {"query": "built"}
        mock_query_builder_class.return_value = mock_builder

        # Act
        pipeline._build_query(sample_start_dt, sample_end_dt, users=None, projects=None, metric_filters=None)

        # Assert - Only time_range should be called
        mock_builder.add_time_range.assert_called_once()
        mock_builder.add_metric_filter.assert_not_called()
        mock_builder.add_user_filter.assert_not_called()
        mock_builder.add_project_filter.assert_not_called()

    @patch("codemie.service.analytics.query_pipeline.SecureQueryBuilder")
    def test_build_query_returns_built_query(self, mock_query_builder_class, pipeline, sample_start_dt, sample_end_dt):
        """Verify built query is returned."""
        # Arrange
        expected_query = {"bool": {"must": [{"range": {"timestamp": {}}}]}}
        mock_builder = MagicMock()
        mock_builder.build.return_value = expected_query
        mock_query_builder_class.return_value = mock_builder

        # Act
        result = pipeline._build_query(sample_start_dt, sample_end_dt, None, None, None)

        # Assert
        assert result == expected_query

    # ===== _build_filters_applied Tests =====

    def test_build_filters_applied_with_predefined_period(self, pipeline, sample_start_dt, sample_end_dt):
        """Verify filters_applied structure for predefined period."""
        # Act
        filters = pipeline._build_filters_applied(
            time_period="last_30_days",
            start_dt=sample_start_dt,
            end_dt=sample_end_dt,
            users=["user1", "user2"],
            projects=["project1"],
        )

        # Assert
        assert filters["time_period"] == "last_30_days"
        assert filters["start_date"] is None
        assert filters["end_date"] is None
        assert filters["users"] == ["user1", "user2"]
        assert filters["projects"] == ["project1"]

    def test_build_filters_applied_with_custom_dates(self, pipeline, sample_start_dt, sample_end_dt):
        """Verify filters_applied structure for custom date range."""
        # Act
        filters = pipeline._build_filters_applied(
            time_period=None,
            start_dt=sample_start_dt,
            end_dt=sample_end_dt,
            users=["user1"],
            projects=["project1"],
        )

        # Assert
        assert filters["time_period"] == "custom"
        assert filters["start_date"] == sample_start_dt.isoformat()
        assert filters["end_date"] == sample_end_dt.isoformat()
        assert filters["users"] == ["user1"]
        assert filters["projects"] == ["project1"]

    def test_build_filters_applied_with_none_users_and_projects(self, pipeline, sample_start_dt, sample_end_dt):
        """Verify None values are preserved."""
        # Act
        filters = pipeline._build_filters_applied(
            time_period="last_7_days",
            start_dt=sample_start_dt,
            end_dt=sample_end_dt,
            users=None,
            projects=None,
        )

        # Assert
        assert filters["users"] is None
        assert filters["projects"] is None
        assert filters["time_period"] == "last_7_days"
