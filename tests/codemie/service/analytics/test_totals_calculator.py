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

"""Unit tests for TotalsCalculator."""

from __future__ import annotations

import pytest

from codemie.service.analytics.totals_calculator import TotalsCalculator


def test_basic_numeric_summation():
    """Test basic summation of numeric columns."""
    columns = [
        {"id": "total_cost_usd", "type": "number"},
        {"id": "total_tokens", "type": "number"},
    ]
    rows = [
        {"total_cost_usd": 10.50, "total_tokens": 50000},
        {"total_cost_usd": 9.86, "total_tokens": 165724},
    ]

    totals = TotalsCalculator.calculate_totals(columns, rows)

    assert totals == pytest.approx({"total_cost_usd": 20.36, "total_tokens": 215724.0})


def test_currency_format_preservation():
    """Test that currency format columns are rounded to 2 decimal places."""
    columns = [
        {"id": "total_cost_usd", "type": "number", "format": "currency"},
    ]
    rows = [
        {"total_cost_usd": 10.123456},
        {"total_cost_usd": 9.876543},
    ]

    totals = TotalsCalculator.calculate_totals(columns, rows)

    assert totals == pytest.approx({"total_cost_usd": 20.0})  # 10.123456 + 9.876543 = 19.999999, rounded to 20.00


@pytest.mark.parametrize(
    "column_id",
    [
        "unique_users",
        "unique_assistants",
        "total_unique_projects",
        "user_unique_count",
    ],
)
def test_skip_unique_columns(column_id):
    """Test that unique_* columns are excluded from totals."""
    columns = [
        {"id": "total_cost_usd", "type": "number"},
        {"id": column_id, "type": "number"},
    ]
    rows = [
        {"total_cost_usd": 10.50, column_id: 5},
        {"total_cost_usd": 9.86, column_id: 3},
    ]

    totals = TotalsCalculator.calculate_totals(columns, rows)

    assert totals == pytest.approx({"total_cost_usd": 20.36})
    assert column_id not in totals


@pytest.mark.parametrize(
    "column_id",
    [
        "error_rate_percent",
        "success_rate_percent",
        "conversion_rate",
        "usage_percent",
    ],
)
def test_skip_rate_and_percent_columns(column_id):
    """Test that *_rate_* and *_percent_* columns are excluded."""
    columns = [
        {"id": "total_requests", "type": "number"},
        {"id": column_id, "type": "number"},
    ]
    rows = [
        {"total_requests": 100, column_id: 5.5},
        {"total_requests": 200, column_id: 3.2},
    ]

    totals = TotalsCalculator.calculate_totals(columns, rows)

    assert totals == pytest.approx({"total_requests": 300.0})
    assert column_id not in totals


@pytest.mark.parametrize(
    "column_id",
    [
        "avg_messages_per_chat",
        "min_messages_per_chat",
        "max_messages_per_chat",
        "median_msg_per_chat",
    ],
)
def test_skip_aggregate_columns(column_id):
    """Test that avg_*, min_*, max_*, median_* columns are excluded."""
    columns = [
        {"id": "total_messages", "type": "number"},
        {"id": column_id, "type": "number"},
    ]
    rows = [
        {"total_messages": 1000, column_id: 25.5},
        {"total_messages": 800, column_id: 20.0},
    ]

    totals = TotalsCalculator.calculate_totals(columns, rows)

    assert totals == pytest.approx({"total_messages": 1800.0})
    assert column_id not in totals


@pytest.mark.parametrize(
    "column_id",
    [
        "last_error_timestamp",
        "last_error_time",
        "last_run",
        "last_project",
    ],
)
def test_skip_timestamp_and_last_columns(column_id):
    """Test that timestamp and last_* columns are excluded."""
    columns = [
        {"id": "total_errors", "type": "number"},
        {"id": column_id, "type": "number"},
    ]
    rows = [
        {"total_errors": 5, column_id: 1234567890},
        {"total_errors": 3, column_id: 1234567900},
    ]

    totals = TotalsCalculator.calculate_totals(columns, rows)

    assert totals == pytest.approx({"total_errors": 8.0})
    assert column_id not in totals


def test_skip_ratio_columns():
    """Test that *_per_* ratio columns are excluded."""
    columns = [
        {"id": "total_operations", "type": "number"},
        {"id": "cost_per_op_usd", "type": "number"},
    ]
    rows = [
        {"total_operations": 100, "cost_per_op_usd": 0.05},
        {"total_operations": 200, "cost_per_op_usd": 0.03},
    ]

    totals = TotalsCalculator.calculate_totals(columns, rows)

    assert totals == pytest.approx({"total_operations": 300.0})
    assert "cost_per_op_usd" not in totals


def test_empty_rows_returns_empty_dict():
    """Test that empty rows return an empty totals dict."""
    columns = [
        {"id": "total_cost_usd", "type": "number"},
        {"id": "total_tokens", "type": "number"},
    ]
    rows = []

    totals = TotalsCalculator.calculate_totals(columns, rows)

    assert totals == {}


def test_explicit_exclude_columns():
    """Test that explicit exclude_columns list is respected."""
    columns = [
        {"id": "total_cost_usd", "type": "number"},
        {"id": "total_tokens", "type": "number"},
        {"id": "total_requests", "type": "number"},
    ]
    rows = [
        {"total_cost_usd": 10.50, "total_tokens": 50000, "total_requests": 100},
        {"total_cost_usd": 9.86, "total_tokens": 165724, "total_requests": 200},
    ]

    totals = TotalsCalculator.calculate_totals(columns, rows, exclude_columns=["total_tokens"])

    assert totals == pytest.approx({"total_cost_usd": 20.36, "total_requests": 300.0})
    assert "total_tokens" not in totals


def test_mixed_summable_and_non_summable_columns():
    """Test mixed column types with string, summable, and non-summable columns."""
    columns = [
        {"id": "user_email", "type": "string"},
        {"id": "total_cost_usd", "type": "number"},
        {"id": "total_tokens", "type": "number"},
        {"id": "unique_users", "type": "number"},
        {"id": "error_rate_percent", "type": "number"},
    ]
    rows = [
        {
            "user_email": "user1@example.com",
            "total_cost_usd": 10.50,
            "total_tokens": 50000,
            "unique_users": 5,
            "error_rate_percent": 2.5,
        },
        {
            "user_email": "user2@example.com",
            "total_cost_usd": 9.86,
            "total_tokens": 165724,
            "unique_users": 3,
            "error_rate_percent": 1.2,
        },
    ]

    totals = TotalsCalculator.calculate_totals(columns, rows)

    assert totals == pytest.approx({"total_cost_usd": 20.36, "total_tokens": 215724.0})
    assert "user_email" not in totals
    assert "unique_users" not in totals
    assert "error_rate_percent" not in totals


def test_all_columns_excluded_returns_empty_dict():
    """Test that when all columns are excluded, empty dict is returned."""
    columns = [
        {"id": "unique_users", "type": "number"},
        {"id": "error_rate_percent", "type": "number"},
        {"id": "avg_time_seconds", "type": "number"},
    ]
    rows = [
        {"unique_users": 5, "error_rate_percent": 2.5, "avg_time_seconds": 120},
        {"unique_users": 3, "error_rate_percent": 1.2, "avg_time_seconds": 95},
    ]

    totals = TotalsCalculator.calculate_totals(columns, rows)

    assert totals == {}


def test_float_rounding_for_different_formats():
    """Test that float values are properly rounded for different formats."""
    columns = [
        {"id": "total_cost_usd", "type": "number", "format": "currency"},
        {"id": "total_tokens", "type": "number", "format": "number"},
        {"id": "total_requests", "type": "number"},
    ]
    rows = [
        {"total_cost_usd": 10.123456789, "total_tokens": 50000.987654, "total_requests": 100.111111},
        {"total_cost_usd": 9.876543211, "total_tokens": 165724.123456, "total_requests": 200.888888},
    ]

    totals = TotalsCalculator.calculate_totals(columns, rows)

    # All should be rounded to 2 decimal places
    assert totals == pytest.approx({"total_cost_usd": 20.0, "total_tokens": 215725.11, "total_requests": 301.0})


def test_null_and_zero_values():
    """Test handling of None and 0 values."""
    columns = [
        {"id": "total_cost_usd", "type": "number", "format": "currency"},
        {"id": "total_tokens", "type": "number"},
    ]
    rows = [
        {"total_cost_usd": 10.50, "total_tokens": None},
        {"total_cost_usd": None, "total_tokens": 50000},
        {"total_cost_usd": 0, "total_tokens": 0},
    ]

    totals = TotalsCalculator.calculate_totals(columns, rows)

    assert totals == pytest.approx({"total_cost_usd": 10.5, "total_tokens": 50000.0})


def test_missing_column_in_rows():
    """Test that missing columns in rows are handled gracefully."""
    columns = [
        {"id": "total_cost_usd", "type": "number"},
        {"id": "total_tokens", "type": "number"},
    ]
    rows = [
        {"total_cost_usd": 10.50},  # missing total_tokens
        {"total_tokens": 50000},  # missing total_cost_usd
    ]

    totals = TotalsCalculator.calculate_totals(columns, rows)

    assert totals == pytest.approx({"total_cost_usd": 10.5, "total_tokens": 50000.0})


def test_non_numeric_values_are_skipped():
    """Test that non-numeric string values in numeric columns are skipped."""
    columns = [
        {"id": "total_cost_usd", "type": "number"},
    ]
    rows = [
        {"total_cost_usd": 10.50},
        {"total_cost_usd": "invalid"},  # should be skipped with warning
        {"total_cost_usd": 5.25},
    ]

    totals = TotalsCalculator.calculate_totals(columns, rows)

    assert totals == pytest.approx({"total_cost_usd": 15.75})


@pytest.mark.parametrize(
    ("column_id", "should_exclude"),
    [
        # Should be excluded
        ("unique_users", True),
        ("total_unique_projects", True),
        ("error_rate_percent", True),
        ("success_rate", True),
        ("avg_time_seconds", True),
        ("median_messages", True),
        ("min_tokens", True),
        ("max_spent", True),
        ("cost_per_op_usd", True),
        ("last_error_timestamp", True),
        ("last_run", True),
        ("created_at_time", True),
        # Should NOT be excluded
        ("total_cost_usd", False),
        ("total_tokens", False),
        ("total_requests", False),
        ("input_tokens", False),
        ("output_tokens", False),
    ],
)
def test_should_exclude_column(column_id, should_exclude):
    """Test _should_exclude_column method with various patterns."""
    assert TotalsCalculator._should_exclude_column(column_id) is should_exclude
