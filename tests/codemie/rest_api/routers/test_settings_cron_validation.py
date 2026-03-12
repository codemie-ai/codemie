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

import pytest
from fastapi import status
from unittest.mock import MagicMock

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.settings.settings_request_validator import validate_cron_expression
from codemie.rest_api.models.settings import SettingRequest


class TestScheduleCredentialValidation:
    """Test cases for schedule credential validation functionality."""

    def _create_mock_request(self, schedule_value):
        """Helper method to create a mock SettingRequest with schedule credential."""
        mock_credential = MagicMock()
        mock_credential.key = "schedule"
        mock_credential.value = schedule_value

        mock_request = MagicMock(spec=SettingRequest)
        mock_request.credential_values = [mock_credential]

        return mock_request

    def test_validate_schedule_credential_valid_expressions(self):
        """Test that valid cron expressions pass validation."""
        valid_expressions = [
            "0 9 * * *",  # Every day at 9 AM
            "0 9 * * MON-FRI",  # Weekdays at 9 AM
            "0 * * * *",  # Every hour (minimum frequency)
            "0 0 1 * *",  # First day of every month
            "0 0 * * 0",  # Every Sunday at midnight
            "30 14 * * 1-5",  # 2:30 PM on weekdays
            "0 2 * * SUN",  # 2 AM every Sunday
            "0 */2 * * *",  # Every 2 hours
            "@daily",  # Special expressions
            "@weekly",
            "@monthly",
            "@yearly",
        ]

        for expression in valid_expressions:
            # Should not raise any exception
            try:
                mock_request = self._create_mock_request(expression)
                validate_cron_expression(mock_request)
            except Exception as e:
                pytest.fail(f"Valid cron expression '{expression}' raised an exception: {e}")

    def test_validate_schedule_credential_invalid_expressions(self):
        """Test that invalid cron expressions raise appropriate exceptions."""
        invalid_expressions = [
            "invalid",  # Completely invalid
            "* * * *",  # Missing field
            "0 9 * * MON-INVALID",  # Invalid day name
        ]

        for expression in invalid_expressions:
            with pytest.raises(ExtendedHTTPException) as exc_info:
                mock_request = self._create_mock_request(expression)
                validate_cron_expression(mock_request)

            assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
            assert "Invalid cron expression" in exc_info.value.message
            assert expression in exc_info.value.details

    def test_validate_schedule_credential_empty_string(self):
        """Test that empty string is handled appropriately."""
        with pytest.raises(ExtendedHTTPException) as exc_info:
            mock_request = self._create_mock_request("")
            validate_cron_expression(mock_request)

        assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Invalid schedule format" in exc_info.value.message
        assert "must be a valid cron expression string" in exc_info.value.details

    def test_validate_schedule_credential_whitespace_only(self):
        """Test that whitespace-only expressions are handled appropriately."""
        whitespace_expressions = ["   ", "\t", "\n"]

        for expression in whitespace_expressions:
            with pytest.raises(ExtendedHTTPException) as exc_info:
                mock_request = self._create_mock_request(expression)
                validate_cron_expression(mock_request)

            assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
            assert "Invalid cron expression" in exc_info.value.message
            assert "cannot be empty" in exc_info.value.details

    def test_validate_schedule_credential_none_value(self):
        """Test that None schedule value is allowed (doesn't raise exception)."""
        mock_request = self._create_mock_request(None)
        # Should not raise any exception for None values
        try:
            validate_cron_expression(mock_request)
        except Exception as e:
            pytest.fail(f"None schedule value raised an exception: {e}")

    def test_validate_schedule_credential_whitespace_handling(self):
        """Test that cron expressions with leading/trailing whitespace are handled correctly."""
        expressions_with_whitespace = [
            "  0 9 * * *  ",
            "\t0 9 * * *\t",
            "\n0 9 * * *\n",
        ]

        for expression in expressions_with_whitespace:
            # Should not raise any exception as whitespace should be stripped
            try:
                mock_request = self._create_mock_request(expression)
                validate_cron_expression(mock_request)
            except Exception as e:
                pytest.fail(f"Cron expression with whitespace '{repr(expression)}' raised an exception: {e}")

    def test_validate_schedule_credential_no_schedule(self):
        """Test that requests without schedule credential raise appropriate exceptions."""
        mock_request = MagicMock(spec=SettingRequest)
        mock_request.credential_values = []  # No schedule credential

        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_cron_expression(mock_request)

        assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Cron expression is missing" in exc_info.value.message

    def test_validate_schedule_credential_non_string_value(self):
        """Test that non-string schedule values raise appropriate exceptions."""
        non_string_values = [123, [], {}, True]

        for value in non_string_values:
            with pytest.raises(ExtendedHTTPException) as exc_info:
                mock_request = self._create_mock_request(value)
                validate_cron_expression(mock_request)

            assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
            assert "Invalid schedule format" in exc_info.value.message
            assert "must be a valid cron expression string" in exc_info.value.details

    def test_validate_schedule_credential_frequency_too_high(self):
        """Test that schedules running more frequently than hourly are rejected."""
        too_frequent_expressions = [
            "* * * * *",  # Every minute
            "*/5 * * * *",  # Every 5 minutes
            "*/15 * * * *",  # Every 15 minutes
            "*/30 * * * *",  # Every 30 minutes
            "0,30 * * * *",  # Every 30 minutes (at :00 and :30)
        ]

        for expression in too_frequent_expressions:
            with pytest.raises(ExtendedHTTPException) as exc_info:
                mock_request = self._create_mock_request(expression)
                validate_cron_expression(mock_request)

            assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
            assert "runs too frequently" in exc_info.value.message
            assert "at most once per hour" in exc_info.value.details

    def test_validate_schedule_credential_minimum_hourly_accepted(self):
        """Test that hourly schedules (minimum frequency) are accepted."""
        hourly_expressions = [
            "0 * * * *",  # Every hour at minute 0
            "30 * * * *",  # Every hour at minute 30
            "15 * * * *",  # Every hour at minute 15
        ]

        for expression in hourly_expressions:
            try:
                mock_request = self._create_mock_request(expression)
                validate_cron_expression(mock_request)
            except Exception as e:
                pytest.fail(f"Hourly cron expression '{expression}' raised an exception: {e}")


class TestScheduleCredentialParametrized:
    """Test cases using parametrized testing for different schedule credential scenarios."""

    def _create_mock_request(self, schedule_value):
        """Helper method to create a mock SettingRequest with schedule credential."""
        mock_credential = MagicMock()
        mock_credential.key = "schedule"
        mock_credential.value = schedule_value

        mock_request = MagicMock(spec=SettingRequest)
        mock_request.credential_values = [mock_credential]

        return mock_request

    @pytest.mark.parametrize(
        "expression,expected_valid",
        [
            # Valid expressions (hourly or less frequent)
            ("0 9 * * *", True),
            ("0 9 * * MON-FRI", True),
            ("0 * * * *", True),  # Every hour (minimum frequency)
            ("0 0 1 * *", True),
            ("0 0 * * 0", True),
            ("30 14 * * 1-5", True),
            ("0 2 * * SUN", True),
            ("0 */2 * * *", True),
            ("5 4 * * sun", True),
            ("@daily", True),
            ("@weekly", True),
            ("@monthly", True),
            ("@yearly", True),
            # Invalid expressions
            (None, True),  # None is allowed
            ("*/15 * * * *", False),  # Too frequent (every 15 minutes)
            ("* * * * * *", False),  # Too frequent (every second with 6 fields)
            ("invalid", False),
            ("* * * *", False),
            ("0 9 * * MON-INVALID", False),
            ("", False),
            ("   ", False),
        ],
    )
    def test_schedule_credential_validation(self, expression, expected_valid):
        """Test schedule credential validation with various inputs."""
        mock_request = self._create_mock_request(expression)

        if expected_valid:
            try:
                validate_cron_expression(mock_request)
            except Exception as e:
                pytest.fail(f"Expected valid schedule credential '{expression}' raised exception: {e}")
        else:
            with pytest.raises(ExtendedHTTPException):
                validate_cron_expression(mock_request)
