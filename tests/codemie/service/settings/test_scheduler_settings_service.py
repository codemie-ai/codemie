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

"""Tests for SchedulerSettingsService cron expression validation."""

import pytest
from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.settings.scheduler_settings_service import validate_cron_expression


@pytest.mark.parametrize(
    "cron_expression",
    ["0 * * * *", "0 9 * * *", "0 0 * * 0", "0 9 * * MON-FRI"],
)
def test_validate_cron_expression_valid(cron_expression):
    """Test that valid cron expressions pass validation."""
    validate_cron_expression(cron_expression)


@pytest.mark.parametrize("cron_expression", [None, "", "   "])
def test_validate_cron_expression_none_or_empty(cron_expression):
    """Test that None and empty strings are allowed (signal schedule deletion)."""
    validate_cron_expression(cron_expression)


def test_validate_cron_expression_invalid_type():
    """Test that non-string types raise errors."""
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_cron_expression(123)

    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
    assert "Cron expression must be a string" in exc_info.value.message


@pytest.mark.parametrize("cron_expression", ["invalid", "* * * *", "60 0 * * *"])
def test_validate_cron_expression_invalid_format(cron_expression):
    """Test that invalid cron formats raise errors."""
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_cron_expression(cron_expression)

    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST


@pytest.mark.parametrize(
    "cron_expression",
    ["* * * * *", "*/15 * * * *", "*/30 * * * *"],
)
def test_validate_cron_expression_too_frequent(cron_expression):
    """Test that expressions running more frequently than hourly are rejected."""
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_cron_expression(cron_expression)

    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
    assert "too frequently" in exc_info.value.message
    assert "once per hour" in exc_info.value.details


def test_validate_cron_expression_exactly_hourly():
    """Test that hourly expressions are valid (boundary case)."""
    validate_cron_expression("0 * * * *")
