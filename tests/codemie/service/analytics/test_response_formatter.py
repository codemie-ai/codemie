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

"""Unit tests for analytics response formatter.

Tests response formatting logic including metadata creation, pagination calculation,
and structured response generation for summary and tabular data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch


from codemie.service.analytics.response_formatter import ResponseFormatter


class TestResponseFormatter:
    """Test suite for ResponseFormatter class."""

    # ============================================================================
    # create_metadata Tests
    # ============================================================================

    def test_create_metadata_includes_all_required_fields(self):
        """Verify metadata structure contains all required fields."""
        # Arrange
        filters_applied = {"time_period": "last_30_days", "users": ["user1"]}
        execution_time_ms = 123.456

        # Act
        metadata = ResponseFormatter.create_metadata(filters_applied, execution_time_ms)

        # Assert
        assert "timestamp" in metadata
        assert "data_as_of" in metadata
        assert "filters_applied" in metadata
        assert "execution_time_ms" in metadata
        assert metadata["timestamp"] is not None
        assert metadata["data_as_of"] is not None
        assert metadata["filters_applied"] is not None
        assert metadata["execution_time_ms"] is not None

    @patch("codemie.service.analytics.response_formatter.datetime")
    def test_create_metadata_timestamps_are_iso_format(self, mock_datetime):
        """Verify timestamps are ISO 8601 formatted."""
        # Arrange
        fixed_datetime = datetime(2025, 12, 19, 15, 0, 0, tzinfo=timezone.utc)
        mock_datetime.now.return_value = fixed_datetime

        # Act
        metadata = ResponseFormatter.create_metadata({}, 100.0)

        # Assert
        expected_timestamp = fixed_datetime.isoformat()
        assert metadata["timestamp"] == expected_timestamp
        assert metadata["data_as_of"] == expected_timestamp
        # Verify it's valid ISO 8601 format with timezone
        assert expected_timestamp == "2025-12-19T15:00:00+00:00"

    def test_create_metadata_rounds_execution_time(self):
        """Verify execution time is rounded to 2 decimal places."""
        # Act
        metadata = ResponseFormatter.create_metadata({}, 123.456789)

        # Assert
        assert metadata["execution_time_ms"] == 123.46

    def test_create_metadata_preserves_filters_applied(self):
        """Verify filters_applied dict is preserved as-is."""
        # Arrange
        filters = {"time_period": "last_30_days", "users": ["user1"], "projects": None}

        # Act
        metadata = ResponseFormatter.create_metadata(filters, 100.0)

        # Assert
        assert metadata["filters_applied"] == filters
        assert metadata["filters_applied"] is filters  # Same reference

    # ============================================================================
    # create_pagination Tests
    # ============================================================================

    def test_create_pagination_includes_all_fields(self):
        """Verify pagination structure contains all required fields."""
        # Act
        pagination = ResponseFormatter.create_pagination(page=2, per_page=20, total_count=100)

        # Assert
        assert pagination["page"] == 2
        assert pagination["per_page"] == 20
        assert pagination["total_count"] == 100
        assert "has_more" in pagination
        assert isinstance(pagination["has_more"], bool)

    def test_create_pagination_has_more_true_when_more_pages_exist(self):
        """Verify has_more calculation when more results available."""
        # Act
        pagination = ResponseFormatter.create_pagination(page=0, per_page=20, total_count=100)

        # Assert
        # Page 0, 20 items per page → items 0-19 shown, 80 items remain
        assert pagination["has_more"] is True

    def test_create_pagination_has_more_false_on_last_page(self):
        """Verify has_more is False on last page."""
        # Act
        pagination = ResponseFormatter.create_pagination(page=4, per_page=20, total_count=100)

        # Assert
        # Page 4, 20 items per page → items 80-99 shown, total 100 → no more
        assert pagination["has_more"] is False

    def test_create_pagination_has_more_false_when_total_less_than_per_page(self):
        """Verify has_more when all results fit in one page."""
        # Act
        pagination = ResponseFormatter.create_pagination(page=0, per_page=20, total_count=10)

        # Assert
        # Only 10 items total, page 0 shows all → no more pages
        assert pagination["has_more"] is False

    def test_create_pagination_has_more_edge_case_exact_page_boundary(self):
        """Verify has_more when total_count exactly matches page boundary."""
        # Act
        pagination = ResponseFormatter.create_pagination(page=0, per_page=20, total_count=20)

        # Assert
        # Exactly 20 items, page 0 shows all (items 0-19) → no more
        assert pagination["has_more"] is False

    def test_create_pagination_has_more_multiple_pages(self):
        """Verify has_more calculation across multiple pages."""
        # Test page 1 of 5
        pagination = ResponseFormatter.create_pagination(page=1, per_page=20, total_count=100)
        assert pagination["has_more"] is True  # Items 20-39 shown, 60 remain

        # Test page 3 of 5
        pagination = ResponseFormatter.create_pagination(page=3, per_page=20, total_count=100)
        assert pagination["has_more"] is True  # Items 60-79 shown, 20 remain

    # ============================================================================
    # format_summary_response Tests
    # ============================================================================

    def test_format_summary_response_includes_data_and_metadata(self):
        """Verify response structure for summary endpoints."""
        # Arrange
        metrics = [
            {"id": "total_tokens", "label": "Total Tokens", "value": 1000, "type": "number"},
            {"id": "avg_cost", "label": "Avg Cost", "value": 0.05, "type": "currency"},
        ]
        filters_applied = {"time_period": "last_30_days"}
        execution_time_ms = 150.0

        # Act
        response = ResponseFormatter.format_summary_response(metrics, filters_applied, execution_time_ms)

        # Assert
        assert "data" in response
        assert "metadata" in response
        assert response["data"]["metrics"] == metrics
        assert response["metadata"]["filters_applied"] == filters_applied
        assert response["metadata"]["execution_time_ms"] == 150.0
        # Verify no pagination in summary responses
        assert "pagination" not in response

    @patch.object(ResponseFormatter, "create_metadata")
    def test_format_summary_response_creates_metadata_correctly(self, mock_create_metadata):
        """Verify metadata is created via create_metadata helper."""
        # Arrange
        metrics = [{"id": "test_metric", "value": 100}]
        filters_applied = {"time_period": "last_7_days"}
        execution_time_ms = 200.5
        mock_create_metadata.return_value = {
            "timestamp": "2025-12-19T10:00:00+00:00",
            "data_as_of": "2025-12-19T10:00:00+00:00",
            "filters_applied": filters_applied,
            "execution_time_ms": 200.5,
        }

        # Act
        response = ResponseFormatter.format_summary_response(metrics, filters_applied, execution_time_ms)

        # Assert
        mock_create_metadata.assert_called_once_with(filters_applied, execution_time_ms)
        assert response["metadata"] == mock_create_metadata.return_value

    # ============================================================================
    # format_tabular_response Tests
    # ============================================================================

    def test_format_tabular_response_includes_data_and_metadata(self):
        """Verify basic tabular response structure."""
        # Arrange
        columns = [
            {"id": "user", "label": "User", "type": "string"},
            {"id": "count", "label": "Count", "type": "number"},
        ]
        rows = [{"user": "alice@example.com", "count": 10}, {"user": "bob@example.com", "count": 5}]
        filters_applied = {"project": "project-123"}
        execution_time_ms = 75.5

        # Act
        response = ResponseFormatter.format_tabular_response(columns, rows, filters_applied, execution_time_ms)

        # Assert
        assert "data" in response
        assert "metadata" in response
        assert response["data"]["columns"] == columns
        assert response["data"]["rows"] == rows
        assert response["metadata"]["filters_applied"] == filters_applied
        assert response["metadata"]["execution_time_ms"] == 75.5

    def test_format_tabular_response_includes_pagination_when_provided(self):
        """Verify pagination is added when parameters provided."""
        # Arrange
        columns = [{"id": "user", "label": "User", "type": "string"}]
        rows = [{"user": f"user{i}@example.com"} for i in range(20)]

        # Act
        response = ResponseFormatter.format_tabular_response(
            columns, rows, {}, 100.0, page=1, per_page=20, total_count=50
        )

        # Assert
        assert "pagination" in response
        assert response["pagination"]["page"] == 1
        assert response["pagination"]["per_page"] == 20
        assert response["pagination"]["total_count"] == 50
        assert response["pagination"]["has_more"] is True  # Page 1, items 20-39, 50 total

    def test_format_tabular_response_excludes_pagination_when_params_none(self):
        """Verify pagination not included when parameters are None."""
        # Arrange
        columns = [{"id": "user", "label": "User", "type": "string"}]
        rows = [{"user": "alice@example.com"}]

        # Act
        response = ResponseFormatter.format_tabular_response(
            columns, rows, {}, 100.0, page=None, per_page=None, total_count=None
        )

        # Assert
        assert "pagination" not in response

    def test_format_tabular_response_includes_totals_when_provided(self):
        """Verify totals row is added when provided."""
        # Arrange
        columns = [{"id": "user", "label": "User"}, {"id": "cost", "label": "Cost"}]
        rows = [{"user": "alice@example.com", "cost": 100.5}, {"user": "bob@example.com", "cost": 234.06}]
        totals = {"user": "Total", "cost": 334.56}

        # Act
        response = ResponseFormatter.format_tabular_response(columns, rows, {}, 100.0, totals=totals)

        # Assert
        assert "totals" in response["data"]
        assert response["data"]["totals"] == totals
        assert response["data"]["totals"]["user"] == "Total"
        assert response["data"]["totals"]["cost"] == 334.56

    def test_format_tabular_response_excludes_totals_when_none(self):
        """Verify totals not included by default."""
        # Arrange
        columns = [{"id": "user", "label": "User"}]
        rows = [{"user": "alice@example.com"}]

        # Act
        response = ResponseFormatter.format_tabular_response(columns, rows, {}, 100.0, totals=None)

        # Assert
        assert "totals" not in response["data"]

    def test_format_tabular_response_partial_pagination_params_excludes_pagination(self):
        """Verify pagination only added when ALL pagination params provided."""
        # Arrange
        columns = [{"id": "user", "label": "User", "type": "string"}]
        rows = [{"user": "alice@example.com"}]

        # Act - Missing total_count
        response = ResponseFormatter.format_tabular_response(
            columns, rows, {}, 100.0, page=1, per_page=20, total_count=None
        )

        # Assert
        assert "pagination" not in response

    def test_format_tabular_response_partial_pagination_params_missing_page(self):
        """Verify pagination excluded when page param is missing."""
        # Arrange
        columns = [{"id": "user", "label": "User"}]
        rows = [{"user": "alice@example.com"}]

        # Act - Missing page
        response = ResponseFormatter.format_tabular_response(
            columns, rows, {}, 100.0, page=None, per_page=20, total_count=100
        )

        # Assert
        assert "pagination" not in response

    def test_format_tabular_response_partial_pagination_params_missing_per_page(self):
        """Verify pagination excluded when per_page param is missing."""
        # Arrange
        columns = [{"id": "user", "label": "User"}]
        rows = [{"user": "alice@example.com"}]

        # Act - Missing per_page
        response = ResponseFormatter.format_tabular_response(
            columns, rows, {}, 100.0, page=1, per_page=None, total_count=100
        )

        # Assert
        assert "pagination" not in response

    def test_format_tabular_response_with_both_totals_and_pagination(self):
        """Verify response can include both totals and pagination."""
        # Arrange
        columns = [{"id": "user", "label": "User"}, {"id": "cost", "label": "Cost"}]
        rows = [{"user": "alice@example.com", "cost": 100.5}]
        totals = {"user": "Total", "cost": 100.5}

        # Act
        response = ResponseFormatter.format_tabular_response(
            columns, rows, {}, 100.0, totals=totals, page=0, per_page=20, total_count=50
        )

        # Assert
        assert "totals" in response["data"]
        assert "pagination" in response
        assert response["data"]["totals"] == totals
        assert response["pagination"]["page"] == 0
        assert response["pagination"]["total_count"] == 50

    # ============================================================================
    # Edge Cases & Integration
    # ============================================================================

    def test_create_metadata_with_empty_filters(self):
        """Verify metadata creation with empty filters dict."""
        # Act
        metadata = ResponseFormatter.create_metadata({}, 50.0)

        # Assert
        assert metadata["filters_applied"] == {}
        assert metadata["execution_time_ms"] == 50.0

    def test_create_metadata_with_complex_filters(self):
        """Verify metadata handles complex nested filter structures."""
        # Arrange
        complex_filters = {
            "time_period": "custom",
            "date_range": {"start": "2025-01-01", "end": "2025-12-31"},
            "users": ["user1", "user2", "user3"],
            "projects": None,
            "nested": {"level1": {"level2": "value"}},
        }

        # Act
        metadata = ResponseFormatter.create_metadata(complex_filters, 200.0)

        # Assert
        assert metadata["filters_applied"] == complex_filters
        # Verify nested structure preserved
        assert metadata["filters_applied"]["nested"]["level1"]["level2"] == "value"

    def test_create_pagination_zero_total_count(self):
        """Verify pagination with zero total count."""
        # Act
        pagination = ResponseFormatter.create_pagination(page=0, per_page=20, total_count=0)

        # Assert
        assert pagination["total_count"] == 0
        assert pagination["has_more"] is False

    def test_create_pagination_single_item(self):
        """Verify pagination with single item."""
        # Act
        pagination = ResponseFormatter.create_pagination(page=0, per_page=20, total_count=1)

        # Assert
        assert pagination["total_count"] == 1
        assert pagination["has_more"] is False

    def test_format_summary_response_empty_metrics(self):
        """Verify summary response with empty metrics list."""
        # Act
        response = ResponseFormatter.format_summary_response([], {}, 25.5)

        # Assert
        assert response["data"]["metrics"] == []
        assert "metadata" in response

    def test_format_tabular_response_empty_rows(self):
        """Verify tabular response with empty rows."""
        # Arrange
        columns = [{"id": "user", "label": "User"}]

        # Act
        response = ResponseFormatter.format_tabular_response(columns, [], {}, 10.0)

        # Assert
        assert response["data"]["columns"] == columns
        assert response["data"]["rows"] == []
        assert "metadata" in response

    def test_format_tabular_response_empty_columns(self):
        """Verify tabular response handles empty columns list."""
        # Act
        response = ResponseFormatter.format_tabular_response([], [], {}, 5.0)

        # Assert
        assert response["data"]["columns"] == []
        assert response["data"]["rows"] == []

    def test_metadata_execution_time_rounding_edge_cases(self):
        """Verify execution time rounding with various edge cases."""
        # Test rounding down
        metadata = ResponseFormatter.create_metadata({}, 100.444)
        assert metadata["execution_time_ms"] == 100.44

        # Test rounding up (use 100.446 instead of 100.445 due to banker's rounding)
        metadata = ResponseFormatter.create_metadata({}, 100.446)
        assert metadata["execution_time_ms"] == 100.45

        # Test very small numbers
        metadata = ResponseFormatter.create_metadata({}, 0.001)
        assert metadata["execution_time_ms"] == 0.0

        # Test zero
        metadata = ResponseFormatter.create_metadata({}, 0.0)
        assert metadata["execution_time_ms"] == 0.0

        # Test large numbers
        metadata = ResponseFormatter.create_metadata({}, 99999.999)
        assert metadata["execution_time_ms"] == 100000.0
