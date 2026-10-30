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

"""Unit tests for AnalyticsFilterParams naive-datetime normalization."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from codemie.rest_api.routers.analytics._common import AnalyticsFilterParams, AnalyticsQueryParams


def test_analytics_filter_params_normalizes_naive_dates_to_utc():
    """Naive start_date/end_date get UTC tzinfo attached instead of staying naive."""
    result = AnalyticsFilterParams(start_date=datetime(2026, 1, 1), end_date=datetime(2026, 1, 2))

    assert result.start_date.tzinfo == timezone.utc
    assert result.end_date.tzinfo == timezone.utc


def test_analytics_filter_params_rejects_malformed_date_string():
    """A malformed date string already 422s via Pydantic coercion (confirming existing behavior)."""
    with pytest.raises(ValidationError):
        AnalyticsQueryParams(start_date="not-a-date")
