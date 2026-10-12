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
"""Tests for the bounded retry helper."""

from unittest.mock import patch

import pytest

from codemie_tools.ms_exchange import retry


class _Transient(Exception):
    response_status_code = 503


def test_total_budget_stops_retries_before_max_attempts():
    calls = []

    def fn():
        calls.append(1)
        raise _Transient

    clock = iter([0.0, 40.0, 80.0, 120.0, 160.0])
    with patch.object(retry.time, "sleep"), patch.object(retry.time, "monotonic", lambda: next(clock)):
        with pytest.raises(_Transient):
            retry.call_with_retry(fn)
    assert len(calls) < retry.MAX_RETRIES + 1
