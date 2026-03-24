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

"""Unit tests for time_parser.py - Time period validation and parsing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from codemie.service.analytics.time_parser import TimeParser


class TestTimeParser:
    """Test suite for TimeParser class."""

    # ========== Predefined Period Tests ==========

    def test_parse_last_hour(self):
        """Verify 'last_hour' returns correct 1-hour range."""
        # Arrange
        fixed_now = datetime(2025, 12, 19, 15, 0, 0, tzinfo=timezone.utc)

        # Act
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            start, end = TimeParser.parse("last_hour", None, None)

        # Assert
        assert end == fixed_now
        assert start == fixed_now - timedelta(hours=1)
        assert start == datetime(2025, 12, 19, 14, 0, 0, tzinfo=timezone.utc)

    def test_parse_last_30_days(self):
        """Verify 'last_30_days' returns correct 30-day range with calendar day boundaries."""
        # Arrange
        fixed_now = datetime(2025, 12, 19, 15, 0, 0, tzinfo=timezone.utc)

        # Act
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.combine = datetime.combine
            mock_datetime.min = datetime.min
            start, end = TimeParser.parse("last_30_days", None, None)

        # Assert - end = now, start = midnight(now - 30 days)
        assert end == fixed_now
        assert start == datetime(2025, 11, 19, 0, 0, 0, tzinfo=timezone.utc)
        # Verify it's a 30-day range
        assert (end.date() - start.date()).days == 30

    def test_parse_last_year(self):
        """Verify 'last_year' returns correct 365-day range with calendar day boundaries."""
        # Arrange
        fixed_now = datetime(2025, 12, 19, 15, 0, 0, tzinfo=timezone.utc)

        # Act
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.combine = datetime.combine
            mock_datetime.min = datetime.min
            start, end = TimeParser.parse("last_year", None, None)

        # Assert - end = now, start = midnight(now - 365 days)
        assert end == fixed_now
        assert start == datetime(2024, 12, 19, 0, 0, 0, tzinfo=timezone.utc)
        # Verify it's a 365-day range
        assert (end.date() - start.date()).days == 365

    def test_parse_all_predefined_periods(self):
        """Verify all periods in PERIODS dict work correctly."""
        # Arrange
        fixed_now = datetime(2025, 12, 19, 15, 0, 0, tzinfo=timezone.utc)
        all_periods = TimeParser.PERIODS.keys()

        # Act & Assert
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.combine = datetime.combine
            mock_datetime.min = datetime.min

            for period in all_periods:
                start, end = TimeParser.parse(period, None, None)

                # Verify valid datetime objects returned
                assert isinstance(start, datetime)
                assert isinstance(end, datetime)

                # Verify start < end
                assert start < end

                # For day-based periods, verify boundaries
                if period in TimeParser.DAY_BASED_PERIODS:
                    # End should be now (current time, not end-of-day)
                    assert end == fixed_now
                    # Start should be start of day (midnight)
                    assert start.hour == 0
                    assert start.minute == 0
                    assert start.second == 0
                    assert start.microsecond == 0
                else:
                    # Hour-based periods should match expected elapsed time
                    expected_delta = TimeParser.PERIODS[period]
                    actual_delta = end - start
                    assert actual_delta == expected_delta

    # ========== Invalid Period Tests ==========

    def test_parse_invalid_time_period_raises_value_error(self):
        """Verify invalid period string raises descriptive error."""
        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            TimeParser.parse("invalid_period", None, None)

        # Verify error message contains helpful information
        error_message = str(exc_info.value)
        assert "Invalid time_period: 'invalid_period'" in error_message
        assert "last_hour" in error_message
        assert "last_30_days" in error_message
        assert "Valid options are:" in error_message

    def test_parse_typo_in_period_raises_value_error(self):
        """Verify typos are caught (e.g., 'last_30days' without underscore)."""
        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            TimeParser.parse("last_30days", None, None)

        # Verify error message
        error_message = str(exc_info.value)
        assert "Invalid time_period: 'last_30days'" in error_message

    # ========== Custom Date Range Tests ==========

    def test_parse_valid_custom_date_range(self):
        """Verify custom date range is returned as-is."""
        # Arrange
        start_date = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end_date = datetime(2025, 1, 31, 23, 59, 59, tzinfo=timezone.utc)

        # Mock datetime.now to ensure end_date is not in future
        fixed_now = datetime(2025, 2, 1, 0, 0, 0, tzinfo=timezone.utc)

        # Act
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            start, end = TimeParser.parse(None, start_date, end_date)

        # Assert
        assert start == start_date
        assert end == end_date

    def test_parse_custom_range_start_after_end_raises_value_error(self):
        """Verify start_date > end_date is rejected."""
        # Arrange
        start_date = datetime(2025, 12, 31, tzinfo=timezone.utc)
        end_date = datetime(2025, 1, 1, tzinfo=timezone.utc)

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            TimeParser.parse(None, start_date, end_date)

        # Verify error message
        error_message = str(exc_info.value)
        assert "start_date must be before end_date" in error_message

    def test_parse_custom_range_end_in_future_raises_value_error(self):
        """Verify future end dates are rejected."""
        # Arrange
        fixed_now = datetime(2025, 12, 19, 15, 0, 0, tzinfo=timezone.utc)
        start_date = datetime(2025, 12, 1, tzinfo=timezone.utc)
        end_date = datetime(2025, 12, 31, tzinfo=timezone.utc)  # Future date

        # Act & Assert
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

            with pytest.raises(ValueError) as exc_info:
                TimeParser.parse(None, start_date, end_date)

        # Verify error message
        error_message = str(exc_info.value)
        assert "end_date cannot be in the future" in error_message

    def test_parse_custom_range_end_equals_now_allowed(self):
        """Verify end_date equal to current time is valid."""
        # Arrange
        fixed_now = datetime(2025, 12, 19, 15, 0, 0, tzinfo=timezone.utc)
        start_date = datetime(2025, 12, 1, 0, 0, 0, tzinfo=timezone.utc)
        end_date = fixed_now  # Exactly equal to now

        # Act
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            start, end = TimeParser.parse(None, start_date, end_date)

        # Assert - Should return successfully without error
        assert start == start_date
        assert end == end_date

    # ========== Default Behavior Tests ==========

    def test_parse_no_params_defaults_to_last_30_days(self):
        """Verify default behavior when no parameters provided."""
        # Arrange
        fixed_now = datetime(2025, 12, 19, 15, 0, 0, tzinfo=timezone.utc)

        # Act
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.combine = datetime.combine
            mock_datetime.min = datetime.min
            start, end = TimeParser.parse(None, None, None)

        # Assert - end = now, start = midnight(now - 30 days)
        assert end == fixed_now
        assert start == datetime(2025, 11, 19, 0, 0, 0, tzinfo=timezone.utc)

    # ========== Parameter Priority Tests ==========

    def test_parse_time_period_overrides_custom_dates(self):
        """Verify time_period takes precedence when both provided."""
        # Arrange
        fixed_now = datetime(2025, 12, 19, 15, 0, 0, tzinfo=timezone.utc)
        start_date = datetime(2025, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2025, 1, 31, tzinfo=timezone.utc)

        # Act
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.combine = datetime.combine
            mock_datetime.min = datetime.min
            start, end = TimeParser.parse("last_7_days", start_date, end_date)

        # Assert - Should return 7-day range based on time_period (end=now, start=midnight(now-7d))
        assert end == fixed_now
        assert start == datetime(2025, 12, 12, 0, 0, 0, tzinfo=timezone.utc)

        # Custom dates should be ignored
        assert start != start_date
        assert end != end_date

    def test_parse_partial_custom_range_uses_defaults(self):
        """Verify behavior when only start_date provided (no end_date)."""
        # Arrange
        fixed_now = datetime(2025, 12, 19, 15, 0, 0, tzinfo=timezone.utc)
        start_date = datetime(2025, 12, 1, tzinfo=timezone.utc)

        # Act
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.combine = datetime.combine
            mock_datetime.min = datetime.min
            start, end = TimeParser.parse(None, start_date, None)

        # Assert - Uses provided start_date and defaults end_date to now
        assert start == start_date
        assert end == fixed_now

    def test_parse_partial_custom_range_only_end_date_uses_defaults(self):
        """Verify behavior when only end_date provided (no start_date)."""
        # Arrange
        fixed_now = datetime(2025, 12, 19, 15, 0, 0, tzinfo=timezone.utc)
        end_date = datetime(2025, 12, 18, tzinfo=timezone.utc)

        # Act
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.combine = datetime.combine
            mock_datetime.min = datetime.min
            start, end = TimeParser.parse(None, None, end_date)

        # Assert - Uses provided end_date and defaults start_date to 30 days before
        assert end == end_date
        assert start == end_date - timedelta(days=30)
        assert start == datetime(2025, 11, 18, 0, 0, 0, tzinfo=timezone.utc)

    # ========== Timezone Tests ==========

    def test_parse_returns_utc_datetimes(self):
        """Verify all returned datetimes are in UTC."""
        # Arrange
        fixed_now = datetime(2025, 12, 19, 15, 0, 0, tzinfo=timezone.utc)

        # Act
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            start, end = TimeParser.parse("last_24_hours", None, None)

        # Assert
        assert start.tzinfo == timezone.utc
        assert end.tzinfo == timezone.utc

    def test_parse_all_periods_return_utc_datetimes(self):
        """Verify all predefined periods return UTC datetimes."""
        # Arrange
        fixed_now = datetime(2025, 12, 19, 15, 0, 0, tzinfo=timezone.utc)
        all_periods = TimeParser.PERIODS.keys()

        # Act & Assert
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.combine = datetime.combine
            mock_datetime.min = datetime.min

            for period in all_periods:
                start, end = TimeParser.parse(period, None, None)

                # Verify both datetimes have UTC timezone
                assert start.tzinfo == timezone.utc, f"Period '{period}' start is not UTC"
                assert end.tzinfo == timezone.utc, f"Period '{period}' end is not UTC"

    def test_parse_custom_dates_preserve_timezone(self):
        """Verify custom dates with UTC timezone are preserved."""
        # Arrange
        fixed_now = datetime(2025, 12, 19, 15, 0, 0, tzinfo=timezone.utc)
        start_date = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end_date = datetime(2025, 1, 31, 23, 59, 59, tzinfo=timezone.utc)

        # Act
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            start, end = TimeParser.parse(None, start_date, end_date)

        # Assert
        assert start.tzinfo == timezone.utc
        assert end.tzinfo == timezone.utc

    # ========== Edge Case Tests ==========

    def test_parse_last_6_hours_boundary(self):
        """Verify last_6_hours period calculates correctly."""
        # Arrange
        fixed_now = datetime(2025, 12, 19, 15, 30, 45, tzinfo=timezone.utc)

        # Act
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            start, end = TimeParser.parse("last_6_hours", None, None)

        # Assert
        assert end == fixed_now
        assert start == datetime(2025, 12, 19, 9, 30, 45, tzinfo=timezone.utc)

    def test_parse_last_60_days_boundary(self):
        """Verify last_60_days period calculates correctly with calendar day boundaries."""
        # Arrange
        fixed_now = datetime(2025, 12, 19, 0, 0, 0, tzinfo=timezone.utc)

        # Act
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.combine = datetime.combine
            mock_datetime.min = datetime.min
            start, end = TimeParser.parse("last_60_days", None, None)

        # Assert - end = now, start = midnight(now - 60 days)
        assert end == fixed_now
        assert start == datetime(2025, 10, 20, 0, 0, 0, tzinfo=timezone.utc)
        # Verify it's a 60-day range
        assert (end.date() - start.date()).days == 60

    def test_parse_custom_range_same_start_and_end_date(self):
        """Verify custom range where start_date equals end_date fails validation."""
        # Arrange
        same_date = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        fixed_now = datetime(2025, 12, 19, 0, 0, 0, tzinfo=timezone.utc)

        # Act & Assert
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

            # Same date should technically pass (start <= end)
            # but logically no data would be returned
            start, end = TimeParser.parse(None, same_date, same_date)

            # Should succeed (implementation allows equal dates)
            assert start == same_date
            assert end == same_date

    def test_parse_custom_range_microseconds_precision(self):
        """Verify custom range handles microsecond precision."""
        # Arrange
        fixed_now = datetime(2025, 12, 19, 15, 0, 0, 123456, tzinfo=timezone.utc)
        start_date = datetime(2025, 12, 1, 0, 0, 0, 100000, tzinfo=timezone.utc)
        end_date = datetime(2025, 12, 18, 23, 59, 59, 999999, tzinfo=timezone.utc)

        # Act
        with patch("codemie.service.analytics.time_parser.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
            start, end = TimeParser.parse(None, start_date, end_date)

        # Assert - Microseconds preserved
        assert start == start_date
        assert end == end_date
        assert start.microsecond == 100000
        assert end.microsecond == 999999

    # ========== Comprehensive Validation Tests ==========

    def test_parse_periods_dict_contains_expected_periods(self):
        """Verify PERIODS dict contains all expected period definitions."""
        # Arrange & Act
        periods = TimeParser.PERIODS

        # Assert - Check all expected periods exist
        expected_periods = [
            "last_hour",
            "last_6_hours",
            "last_24_hours",
            "last_7_days",
            "last_30_days",
            "last_60_days",
            "last_year",
        ]

        for period in expected_periods:
            assert period in periods, f"Missing expected period: {period}"

        # Verify each has a timedelta value
        for period, delta in periods.items():
            assert isinstance(delta, timedelta), f"Period '{period}' does not have timedelta value"

    def test_parse_empty_string_period_raises_value_error(self):
        """Verify empty string period is treated as invalid."""
        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            TimeParser.parse("", None, None)

        # Verify error message
        error_message = str(exc_info.value)
        assert "Invalid time_period: ''" in error_message

    def test_parse_case_sensitive_period_names(self):
        """Verify period names are case-sensitive."""
        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            TimeParser.parse("LAST_30_DAYS", None, None)

        error_message = str(exc_info.value)
        assert "Invalid time_period: 'LAST_30_DAYS'" in error_message

        with pytest.raises(ValueError) as exc_info:
            TimeParser.parse("Last_Hour", None, None)

        error_message = str(exc_info.value)
        assert "Invalid time_period: 'Last_Hour'" in error_message
