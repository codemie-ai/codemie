# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from __future__ import annotations

from unittest.mock import patch

import pytest

from codemie_tools.data_management.code_executor.tool_calling_limits import (
    MAX_TOOL_CALLING_SECONDS,
    clamp_tool_calling_timeout,
)

_TL = "codemie_tools.data_management.code_executor.tool_calling_limits"


@pytest.mark.parametrize("raw", [None, "120", True, [1], object()])
def test_non_numeric_value_means_tool_calling_is_off_without_warning(raw: object) -> None:
    with patch(f"{_TL}.logger.warning") as warn:
        assert clamp_tool_calling_timeout(raw) is None
    warn.assert_not_called()


@pytest.mark.parametrize("raw", [0, 0.0, -5, float("inf"), float("nan")])
def test_non_positive_or_non_finite_value_turns_tool_calling_off_with_warning(raw: float) -> None:
    with patch(f"{_TL}.logger.warning") as warn:
        assert clamp_tool_calling_timeout(raw) is None
    warn.assert_called_once()


@pytest.mark.parametrize("raw", [1, 120, 120.0, MAX_TOOL_CALLING_SECONDS])
def test_value_within_the_maximum_is_returned_as_float_without_warning(raw: float) -> None:
    with patch(f"{_TL}.logger.warning") as warn:
        result = clamp_tool_calling_timeout(raw)
    assert result == float(raw)
    assert isinstance(result, float)
    warn.assert_not_called()


def test_value_above_the_maximum_is_clamped_with_warning() -> None:
    with patch(f"{_TL}.logger.warning") as warn:
        assert clamp_tool_calling_timeout(1e12) == MAX_TOOL_CALLING_SECONDS
    warn.assert_called_once()
