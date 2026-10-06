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

from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import MAX_PAYLOAD_BYTES
from codemie_tools.data_management.code_executor.tool_calling_limits import (
    DEFAULT_MAX_PARALLEL_CALLS,
    DEFAULT_RUN_TIMEOUT_SECONDS,
    MAX_RUN_TIMEOUT_SECONDS,
    RunBudget,
    ToolCallingSettings,
    normalize_max_parallel_calls,
    normalize_run_timeout,
)

_TL = "codemie_tools.data_management.code_executor.tool_calling_limits"


def test_default_is_120_and_ceiling_is_480() -> None:
    assert DEFAULT_RUN_TIMEOUT_SECONDS == 120.0
    assert MAX_RUN_TIMEOUT_SECONDS == 480.0


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "abc",
        "",
        True,
        False,
        0,
        0.0,
        -5,
        "-1",
        float("nan"),
        float("inf"),
        "inf",
        "nan",
        [30],
        {"a": 1},
        10**400,
        -(10**400),
        object(),
    ],
)
def test_invalid_value_falls_back_to_the_default_with_a_warning(raw: object) -> None:
    with patch(f"{_TL}.logger.warning") as warn:
        assert normalize_run_timeout(raw) == DEFAULT_RUN_TIMEOUT_SECONDS
    warn.assert_called_once()


@pytest.mark.parametrize("raw", [1, 45, 120, 120.0, "120", "45", MAX_RUN_TIMEOUT_SECONDS])
def test_value_within_the_ceiling_is_returned_as_float_without_warning(raw: float | int | str) -> None:
    with patch(f"{_TL}.logger.warning") as warn:
        result = normalize_run_timeout(raw)
    assert result == float(raw)
    assert isinstance(result, float)
    warn.assert_not_called()


@pytest.mark.parametrize("raw", [601, 3600, "9999", 1e12])
def test_value_above_the_ceiling_is_clamped_with_a_warning(raw: float | int | str) -> None:
    with patch(f"{_TL}.logger.warning") as warn:
        assert normalize_run_timeout(raw) == MAX_RUN_TIMEOUT_SECONDS
    warn.assert_called_once()


def test_normalizing_is_idempotent() -> None:
    once = normalize_run_timeout(1e12)
    with patch(f"{_TL}.logger.warning") as warn:
        assert normalize_run_timeout(once) == once
    warn.assert_not_called()


def test_default_parallel_calls_is_five() -> None:
    assert DEFAULT_MAX_PARALLEL_CALLS == 5


@pytest.mark.parametrize(
    "raw", [None, "abc", "", True, False, 0, -3, "-1", "0", 0.5, float("nan"), "inf", [2], 10**400]
)
def test_invalid_parallel_calls_fall_back_to_the_default_with_a_warning(raw: object) -> None:
    with patch(f"{_TL}.logger.warning") as warn:
        assert normalize_max_parallel_calls(raw, 10) == DEFAULT_MAX_PARALLEL_CALLS
    warn.assert_called_once()


@pytest.mark.parametrize(("raw", "expected"), [(1, 1), (2, 2), ("3", 3), (5.0, 5), (10, 10), (7.9, 7)])
def test_parallel_calls_within_the_ceiling_are_returned_as_whole_numbers_without_warning(
    raw: object, expected: int
) -> None:
    with patch(f"{_TL}.logger.warning") as warn:
        result = normalize_max_parallel_calls(raw, 10)
    assert result == expected
    assert isinstance(result, int)
    warn.assert_not_called()


@pytest.mark.parametrize("raw", [11, 500, "999", 1e9])
def test_parallel_calls_above_the_process_limit_are_lowered_to_it_with_a_warning(raw: object) -> None:
    with patch(f"{_TL}.logger.warning") as warn:
        assert normalize_max_parallel_calls(raw, 10) == 10
    warn.assert_called_once()


def test_the_default_is_itself_held_to_a_low_process_limit() -> None:
    assert normalize_max_parallel_calls(None, 3) == 3


def test_a_nonsense_process_limit_still_allows_one_call() -> None:
    assert normalize_max_parallel_calls(5, 0) == 1


class TestToolCallingSettings:
    def test_defaults(self) -> None:
        settings = ToolCallingSettings()

        assert settings.run_timeout_seconds == DEFAULT_RUN_TIMEOUT_SECONDS
        assert settings.max_payload_bytes == MAX_PAYLOAD_BYTES
        assert settings.max_parallel_calls == DEFAULT_MAX_PARALLEL_CALLS

    def test_is_frozen(self) -> None:
        with pytest.raises(AttributeError):
            ToolCallingSettings().max_parallel_calls = 2  # type: ignore[misc]

    @pytest.mark.parametrize(
        ("execution_timeout", "run_timeout", "expected"),
        [(30.0, 120.0, 120.0), (300.0, 120.0, 300.0), (30.0, 30.0, 30.0)],
    )
    def test_the_run_budget_is_the_larger_of_the_script_limit_and_the_run_limit(
        self, execution_timeout: float, run_timeout: float, expected: float
    ) -> None:
        settings = ToolCallingSettings(run_timeout_seconds=run_timeout)

        assert settings.run_budget_seconds(execution_timeout) == expected

    def test_sdk_config_carries_the_exchange_folder_the_budget_and_the_cap(self) -> None:
        settings = ToolCallingSettings(run_timeout_seconds=90.0, max_payload_bytes=1000)

        config = settings.sdk_config(".codemie_bridge/1-abc", 30.0)

        assert config == {"exchange_dir": ".codemie_bridge/1-abc", "run_seconds": 90.0, "max_payload_bytes": 1000}


class TestRunBudget:
    def test_starting_now_ends_after_its_length(self) -> None:
        with patch(f"{_TL}.time.monotonic", return_value=1000.0):
            budget = RunBudget.starting_now(180.0)

        assert budget.seconds == 180.0
        assert budget.deadline == 1180.0

    def test_time_left_counts_down(self) -> None:
        budget = RunBudget(seconds=100.0, deadline=1100.0)

        with patch(f"{_TL}.time.monotonic", return_value=1040.0):
            assert budget.time_left() == 60.0
        with patch(f"{_TL}.time.monotonic", return_value=1200.0):
            assert budget.time_left() == -100.0
