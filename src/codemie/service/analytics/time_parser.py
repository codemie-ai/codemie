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

"""Time period parsing for analytics queries.

This module converts time period strings and custom date ranges into datetime objects
for use in Elasticsearch queries.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class TimeParser:
    """Parses time period parameters into datetime ranges for analytics queries."""

    # Predefined time periods mapping to timedeltas
    PERIODS: dict[str, timedelta] = {
        "last_hour": timedelta(hours=1),
        "last_6_hours": timedelta(hours=6),
        "last_24_hours": timedelta(hours=24),
        "last_7_days": timedelta(days=7),
        "last_30_days": timedelta(days=30),
        "last_60_days": timedelta(days=60),
        "last_year": timedelta(days=365),
    }

    # Day-based periods that should use calendar day boundaries
    DAY_BASED_PERIODS = {"last_7_days", "last_30_days", "last_60_days", "last_year"}

    @staticmethod
    def _get_valid_periods_message() -> str:
        """Get formatted list of valid time periods."""
        return ", ".join(TimeParser.PERIODS.keys())

    @staticmethod
    def _get_start_of_day(dt: datetime) -> datetime:
        """Get start of day (00:00:00) for a given datetime.

        Args:
            dt: Input datetime

        Returns:
            Datetime truncated to start of day (00:00:00) in UTC
        """
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def _get_end_of_day(dt: datetime) -> datetime:
        """Get end of day (23:59:59.999999) for a given datetime.

        Args:
            dt: Input datetime

        Returns:
            Datetime set to end of day (23:59:59.999999) in UTC
        """
        return dt.replace(hour=23, minute=59, second=59, microsecond=999999)

    @staticmethod
    def _raise_invalid_period_error(period: str) -> None:
        """Raise ValueError for invalid time period.

        Args:
            period: The invalid period string

        Raises:
            ValueError: Always raises with formatted error message
        """
        valid_periods = TimeParser._get_valid_periods_message()
        raise ValueError(f"Invalid time_period: '{period}'. Valid options are: {valid_periods}")

    @staticmethod
    def _parse_predefined_period(time_period: str) -> tuple[datetime, datetime]:
        """Parse predefined time period into start/end datetimes."""
        delta = TimeParser.PERIODS.get(time_period)
        if not delta:
            TimeParser._raise_invalid_period_error(time_period)
        end = datetime.now(timezone.utc)
        start = end - delta
        logger.debug(
            f"Parsed predefined time period: period={time_period}, "
            f"duration_hours={(end - start).total_seconds() / 3600:.2f}, "
            f"start={start.isoformat()}, end={end.isoformat()}"
        )
        return start, end

    @staticmethod
    def _parse_custom_range(start_date: datetime | None, end_date: datetime | None) -> tuple[datetime, datetime]:
        """Parse custom date range with defaults for missing values."""
        now = datetime.now(timezone.utc)

        # Calculate final_start with clear logic
        if start_date:
            final_start = start_date
        elif end_date:
            final_start = end_date - timedelta(days=30)
        else:
            final_start = now - timedelta(days=30)

        # Calculate final_end
        final_end = end_date if end_date else now

        # Validate date range
        if final_start > final_end:
            raise ValueError("start_date must be before end_date")
        if final_end > now:
            raise ValueError("end_date cannot be in the future")

        logger.debug(
            f"Parsed custom date range: "
            f"duration_days={(final_end - final_start).days}, "
            f"start={final_start.isoformat()}, end={final_end.isoformat()}"
        )
        return final_start, final_end

    @staticmethod
    def _get_default_range() -> tuple[datetime, datetime]:
        """Get default 30-day time range."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=30)
        logger.debug(f"Using default time period (last 30 days): start={start.isoformat()}, end={end.isoformat()}")
        return start, end

    @staticmethod
    def parse(
        time_period: str | None, start_date: datetime | None, end_date: datetime | None
    ) -> tuple[datetime, datetime]:
        """Parse time period into start/end datetimes.

        For day-based periods (last_7_days, last_30_days, etc.), uses calendar day boundaries
        to ensure the correct number of complete days are returned. For hour-based periods,
        uses elapsed time from the current moment.

        Args:
            time_period: Predefined period string (e.g., 'last_30_days')
            start_date: Custom range start datetime
            end_date: Custom range end datetime

        Returns:
            Tuple of (start_datetime, end_datetime)

        Raises:
            ValueError: If time_period is invalid or date range is invalid
        """
        # Case 1: Predefined time period provided
        if time_period is not None and time_period != "":
            delta = TimeParser.PERIODS.get(time_period)
            if delta is None:
                TimeParser._raise_invalid_period_error(time_period)

            now = datetime.now(timezone.utc)

            # For day-based periods: end = now, start = midnight UTC of (now - N days)
            # Matches Kibana behaviour: lte=now, gte=midnight(now - N*days)
            if time_period in TimeParser.DAY_BASED_PERIODS:
                end = now
                start = TimeParser._get_start_of_day(now - delta)
                logger.debug(
                    f"Parsed predefined time period (Kibana-style): period={time_period}, "
                    f"calendar_days={delta.days}, "
                    f"start={start.isoformat()}, end={end.isoformat()}"
                )
            else:
                # For hour-based periods, use elapsed time
                end = now
                start = end - delta
                logger.debug(
                    f"Parsed predefined time period (elapsed time): period={time_period}, "
                    f"duration_hours={(end - start).total_seconds() / 3600:.2f}, "
                    f"start={start.isoformat()}, end={end.isoformat()}"
                )
            return start, end

        # Case 2: Explicitly handle empty string
        if time_period == "":
            TimeParser._raise_invalid_period_error("")

        # Case 3: Custom date range provided
        if start_date or end_date:
            return TimeParser._parse_custom_range(start_date, end_date)

        # Case 4: Default to last 30 days (Kibana-style: end=now, start=midnight(now-30d))
        now = datetime.now(timezone.utc)
        end = now
        start = TimeParser._get_start_of_day(now - timedelta(days=30))
        logger.debug(
            f"Using default time period (last 30 days, Kibana-style): start={start.isoformat()}, end={end.isoformat()}"
        )
        return start, end
