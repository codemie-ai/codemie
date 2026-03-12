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

"""Unit tests for ProjectHandler."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.project_handler import ProjectHandler


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
def handler(mock_user, mock_repository):
    """Create handler with mocked dependencies."""
    return ProjectHandler(mock_user, mock_repository)


class TestGetProjectsUniqueDaily:
    """Tests for get_projects_unique_daily method."""

    def test_build_projects_unique_daily_aggregation_structure(self, handler):
        """Verify date_histogram aggregation structure with cardinality on project field."""
        # Arrange
        query = {
            "bool": {
                "filter": [
                    {"range": {"@timestamp": {"gte": "2026-01-01T00:00:00.000Z", "lte": "2026-01-31T23:59:59.999Z"}}}
                ]
            }
        }

        # Act
        agg_body = handler._build_projects_unique_daily_aggregation(query)

        # Assert
        assert agg_body["query"] == query
        assert agg_body["size"] == 0

        # Verify paginated_results structure uses date_histogram
        date_hist_agg = agg_body["aggs"]["paginated_results"]
        assert "date_histogram" in date_hist_agg
        assert date_hist_agg["date_histogram"]["field"] == "time"
        assert date_hist_agg["date_histogram"]["calendar_interval"] == "1d"
        assert date_hist_agg["date_histogram"]["time_zone"] == "UTC"
        assert date_hist_agg["date_histogram"]["order"] == {"_key": "asc"}

        # Verify extended_bounds is present
        assert "extended_bounds" in date_hist_agg["date_histogram"]
        extended_bounds = date_hist_agg["date_histogram"]["extended_bounds"]
        assert "min" in extended_bounds
        assert "max" in extended_bounds

        # Verify nested cardinality aggregation on project field
        assert "aggs" in date_hist_agg
        assert "unique_projects" in date_hist_agg["aggs"]
        assert "cardinality" in date_hist_agg["aggs"]["unique_projects"]
        assert date_hist_agg["aggs"]["unique_projects"]["cardinality"]["field"] == "attributes.project.keyword"

    def test_build_projects_unique_daily_handles_missing_time_range(self, handler):
        """Verify aggregation works without time range (no extended_bounds)."""
        # Arrange
        query = {"bool": {"filter": []}}  # No time range filter

        # Act
        agg_body = handler._build_projects_unique_daily_aggregation(query)

        # Assert
        assert agg_body["query"] == query
        date_hist_agg = agg_body["aggs"]["paginated_results"]
        # extended_bounds should not be present when time range is missing
        assert "extended_bounds" not in date_hist_agg["date_histogram"]

    def test_parse_projects_unique_daily_result_formats_dates_from_key_as_string(self, handler):
        """Verify date formatting from key_as_string and cardinality extraction."""
        # Arrange - Mock ES result with date buckets
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": 1735689600000,  # epoch millis
                            "key_as_string": "2026-01-01T00:00:00.000Z",
                            "unique_projects": {"value": 5},
                        },
                        {
                            "key": 1735776000000,
                            "key_as_string": "2026-01-02T00:00:00.000Z",
                            "unique_projects": {"value": 8},
                        },
                        {
                            "key": 1735862400000,
                            "key_as_string": "2026-01-03T00:00:00.000Z",
                            "unique_projects": {"value": 12},
                        },
                    ]
                }
            }
        }

        # Act
        rows = handler._parse_projects_unique_daily_result(result)

        # Assert
        assert len(rows) == 3
        # Verify first row
        assert rows[0]["date"] == "2026-01-01"
        assert rows[0]["unique_projects"] == 5
        # Verify second row
        assert rows[1]["date"] == "2026-01-02"
        assert rows[1]["unique_projects"] == 8
        # Verify third row
        assert rows[2]["date"] == "2026-01-03"
        assert rows[2]["unique_projects"] == 12

    def test_parse_projects_unique_daily_result_formats_dates_from_epoch_millis(self, handler):
        """Verify date formatting from epoch millis when key_as_string is missing."""
        # Arrange - Mock ES result without key_as_string (fallback to epoch)
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": 1735689600000,  # 2025-01-01T00:00:00.000Z
                            "unique_projects": {"value": 5},
                        },
                        {
                            "key": 1735776000000,  # 2025-01-02T00:00:00.000Z
                            "unique_projects": {"value": 8},
                        },
                    ]
                }
            }
        }

        # Act
        rows = handler._parse_projects_unique_daily_result(result)

        # Assert
        assert len(rows) == 2
        assert rows[0]["date"] == "2025-01-01"
        assert rows[0]["unique_projects"] == 5
        assert rows[1]["date"] == "2025-01-02"
        assert rows[1]["unique_projects"] == 8

    def test_parse_projects_unique_daily_result_handles_empty_buckets(self, handler):
        """Verify empty buckets handling."""
        # Arrange
        result = {"aggregations": {"paginated_results": {"buckets": []}}}

        # Act
        rows = handler._parse_projects_unique_daily_result(result)

        # Assert
        assert rows == []

    def test_parse_projects_unique_daily_result_skips_invalid_date_format(self, handler):
        """Verify buckets with invalid date format are skipped with warning."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key_as_string": "invalid-date-format",  # No "T" separator
                            "unique_projects": {"value": 5},
                        },
                        {
                            "key_as_string": "2026-01-02T00:00:00.000Z",
                            "unique_projects": {"value": 8},
                        },
                    ]
                }
            }
        }

        # Act
        rows = handler._parse_projects_unique_daily_result(result)

        # Assert - First bucket skipped, second bucket processed
        assert len(rows) == 1
        assert rows[0]["date"] == "2026-01-02"
        assert rows[0]["unique_projects"] == 8

    def test_parse_projects_unique_daily_result_skips_missing_key(self, handler):
        """Verify buckets with missing key are skipped with warning."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            # Missing both key_as_string and key
                            "unique_projects": {"value": 5},
                        },
                        {
                            "key_as_string": "2026-01-02T00:00:00.000Z",
                            "unique_projects": {"value": 8},
                        },
                    ]
                }
            }
        }

        # Act
        rows = handler._parse_projects_unique_daily_result(result)

        # Assert - First bucket skipped, second bucket processed
        assert len(rows) == 1
        assert rows[0]["date"] == "2026-01-02"

    def test_parse_projects_unique_daily_result_skips_negative_key_millis(self, handler):
        """Verify buckets with negative key_millis are skipped."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": -1000,  # Negative epoch millis (invalid)
                            "unique_projects": {"value": 5},
                        },
                        {
                            "key": 1735689600000,  # 2025-01-01T00:00:00.000Z
                            "unique_projects": {"value": 8},
                        },
                    ]
                }
            }
        }

        # Act
        rows = handler._parse_projects_unique_daily_result(result)

        # Assert - First bucket skipped, second bucket processed
        assert len(rows) == 1
        assert rows[0]["date"] == "2025-01-01"
        assert rows[0]["unique_projects"] == 8

    def test_parse_projects_unique_daily_result_converts_unique_projects_to_int(self, handler):
        """Verify unique_projects count is converted to integer."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key_as_string": "2026-01-01T00:00:00.000Z",
                            "unique_projects": {"value": 5.0},  # Float value from ES
                        }
                    ]
                }
            }
        }

        # Act
        rows = handler._parse_projects_unique_daily_result(result)

        # Assert
        assert len(rows) == 1
        assert rows[0]["unique_projects"] == 5
        assert isinstance(rows[0]["unique_projects"], int)

    def test_parse_projects_unique_daily_result_handles_zero_projects(self, handler):
        """Verify buckets with zero unique projects are included."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key_as_string": "2026-01-01T00:00:00.000Z",
                            "unique_projects": {"value": 0},  # Zero projects
                        }
                    ]
                }
            }
        }

        # Act
        rows = handler._parse_projects_unique_daily_result(result)

        # Assert
        assert len(rows) == 1
        assert rows[0]["unique_projects"] == 0

    def test_get_projects_unique_daily_columns(self, handler):
        """Verify column definitions for projects unique daily."""
        # Act
        columns = handler._get_projects_unique_daily_columns()

        # Assert
        assert len(columns) == 2
        # Verify date column
        assert columns[0]["id"] == "date"
        assert columns[0]["label"] == "Date"
        assert columns[0]["type"] == "date"
        # Verify unique_projects column
        assert columns[1]["id"] == "unique_projects"
        assert columns[1]["label"] == "Unique Projects"
        assert columns[1]["type"] == "number"

    def test_build_projects_unique_daily_converts_iso_strings_to_epoch_millis(self, handler):
        """Verify ISO date strings are converted to epoch milliseconds for extended_bounds."""
        # Arrange
        query = {
            "bool": {
                "filter": [
                    {
                        "range": {
                            "@timestamp": {
                                "gte": "2026-01-01T00:00:00.000Z",
                                "lte": "2026-01-31T23:59:59.999Z",
                            }
                        }
                    }
                ]
            }
        }

        # Act
        agg_body = handler._build_projects_unique_daily_aggregation(query)

        # Assert
        extended_bounds = agg_body["aggs"]["paginated_results"]["date_histogram"]["extended_bounds"]
        assert isinstance(extended_bounds["min"], int)
        assert isinstance(extended_bounds["max"], int)
        # Verify they're reasonable epoch milliseconds (13 digits)
        assert len(str(extended_bounds["min"])) == 13
        assert len(str(extended_bounds["max"])) == 13
        assert extended_bounds["min"] < extended_bounds["max"]
