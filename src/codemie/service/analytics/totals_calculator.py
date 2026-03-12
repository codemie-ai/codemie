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

"""Totals calculator for analytics tabular responses.

This module provides utility functions for calculating sum totals
for numeric columns in analytics data.
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Patterns for columns that should NOT be summed
EXCLUSION_PATTERNS = [
    r"^unique_.*",  # unique_users, unique_assistants (cardinality metrics)
    r".*_unique_.*",  # any unique counts
    r"^total_unique_.*",  # total_unique_projects
    r".*_rate.*",  # error_rate_percent, success_rate_percent
    r".*_percent.*",  # any percentages
    r"^avg_.*",  # avg_time_seconds, avg_messages
    r"^median_.*",  # median_msg_per_chat
    r"^min_.*",  # min_messages_per_chat
    r"^max_.*",  # max_spent (already an aggregate)
    r".*_per_.*",  # cost_per_op_usd (ratios)
    r".*_timestamp.*",  # last_error_timestamp
    r".*_time$",  # last_error_time
    r"^last_.*",  # last_run, last_project
]


class TotalsCalculator:
    """Calculator for sum totals in tabular analytics data."""

    @staticmethod
    def calculate_totals(
        columns: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        exclude_columns: list[str] | None = None,
    ) -> dict[str, float]:
        """Calculate sum totals for numeric columns.

        Automatically identifies summable columns based on:
        - Column type must be "number"
        - Column ID must not match exclusion patterns
        - Column ID must not be in explicit exclude_columns list

        All totals are rounded to 2 decimal places.

        Args:
            columns: Column definitions with id, type, format metadata
            rows: Data rows to sum
            exclude_columns: Optional explicit list of column IDs to exclude

        Returns:
            Dictionary mapping column IDs to their sum totals (rounded to 2 decimals).
            Returns empty dict if no rows or no summable columns found.

        Example:
            >>> columns = [
            ...     {"id": "total_cost_usd", "type": "number", "format": "currency"},
            ...     {"id": "total_tokens", "type": "number"},
            ...     {"id": "unique_users", "type": "number"},  # excluded by pattern
            ... ]
            >>> rows = [
            ...     {"total_cost_usd": 10.50, "total_tokens": 50000, "unique_users": 5},
            ...     {"total_cost_usd": 9.86, "total_tokens": 165724, "unique_users": 3},
            ... ]
            >>> calculate_totals(columns, rows)
            {"total_cost_usd": 20.36, "total_tokens": 215724.0}
        """
        if not rows:
            logger.debug("No rows provided, returning empty totals")
            return {}

        exclude_set = set(exclude_columns or [])
        totals: dict[str, float] = {}

        for column in columns:
            column_id = column.get("id")

            # Skip non-summable columns
            if not TotalsCalculator._is_summable_column(column, exclude_set):
                continue

            # Calculate sum using generator expression
            total = round(
                sum(TotalsCalculator._get_numeric_values(rows, column_id)),
                2,
            )

            totals[column_id] = total
            logger.debug(f"Calculated total for {column_id}: {total}")

        logger.info(f"Calculated totals for {len(totals)} columns from {len(rows)} rows")
        return totals

    @staticmethod
    def _is_summable_column(column: dict[str, Any], exclude_set: set[str]) -> bool:
        """Check if column should be included in totals calculation.

        Args:
            column: Column definition with id, type, format metadata
            exclude_set: Set of explicitly excluded column IDs

        Returns:
            True if column is summable, False otherwise
        """
        column_id = column.get("id")
        column_type = column.get("type")

        # Must have an ID
        if not column_id:
            logger.debug("Skipping column without ID")
            return False

        # Must be numeric type
        if column_type != "number":
            return False

        # Must not be explicitly excluded
        if column_id in exclude_set:
            logger.debug(f"Skipping column {column_id}: explicitly excluded")
            return False

        # Must not match exclusion patterns
        if TotalsCalculator._should_exclude_column(column_id):
            logger.debug(f"Skipping column {column_id}: matches exclusion pattern")
            return False

        return True

    @staticmethod
    def _get_numeric_values(rows: list[dict[str, Any]], column_id: str) -> list[float]:
        """Extract and convert numeric values from rows for a specific column.

        Args:
            rows: Data rows
            column_id: Column ID to extract values from

        Returns:
            List of float values (skips None and non-convertible values)
        """
        values = []
        for row in rows:
            value = row.get(column_id)
            if value is not None:
                try:
                    values.append(float(value))
                except (TypeError, ValueError):
                    logger.warning(f"Could not convert value {value!r} to float for column {column_id}")
        return values

    @staticmethod
    def _should_exclude_column(column_id: str | None) -> bool:
        """Check if column ID matches any exclusion pattern.

        Args:
            column_id: Column ID to check (can be None)

        Returns:
            True if column should be excluded from totals, False otherwise
        """
        if not column_id:
            return True  # Exclude columns without ID

        return any(re.match(pattern, column_id) for pattern in EXCLUSION_PATTERNS)
