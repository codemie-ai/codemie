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

"""Settings of a run's script tool calls: one value, one validator per number, and the run's time budget."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass

from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import MAX_PAYLOAD_BYTES

logger = logging.getLogger(__name__)

DEFAULT_RUN_TIMEOUT_SECONDS: float = 120.0
MAX_RUN_TIMEOUT_SECONDS: float = 480.0
DEFAULT_MAX_PARALLEL_CALLS: int = 5


def _as_finite_float(raw: object) -> float | None:
    """A finite float from an int, float or numeric string; ``None`` for anything else (``bool`` included)."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    try:
        value = float(raw)
    except (ValueError, OverflowError):
        # OverflowError: an int too large for float, e.g. 10**400 written in the YAML
        return None
    return value if math.isfinite(value) else None


def normalize_run_timeout(raw: object) -> float:
    """The effective run limit in seconds for a run that has tool calling on.

    A missing, non-numeric, non-positive or non-finite value falls back to ``DEFAULT_RUN_TIMEOUT_SECONDS`` with a
    warning, so a bad setting never silently turns tool calling off; a value above ``MAX_RUN_TIMEOUT_SECONDS`` is
    clamped to it with a warning. Never raises. Whether tool calling is on at all is decided by the caller.
    """
    value = _as_finite_float(raw)
    if value is None or value <= 0:
        logger.warning(
            "tool_calling_timeout_invalid: type=%s, using_default=%.0fs, domain=code_executor",
            type(raw).__name__,
            DEFAULT_RUN_TIMEOUT_SECONDS,
        )
        return DEFAULT_RUN_TIMEOUT_SECONDS
    if value > MAX_RUN_TIMEOUT_SECONDS:
        logger.warning(
            "tool_calling_timeout_clamped: requested=%.3fs, max=%.3fs, domain=code_executor",
            value,
            MAX_RUN_TIMEOUT_SECONDS,
        )
        return MAX_RUN_TIMEOUT_SECONDS
    return value


def normalize_max_parallel_calls(raw: object, ceiling: int) -> int:
    """How many calls of one run the backend serves at once: a whole number from 1 to ``ceiling``.

    The same rules as :func:`normalize_run_timeout`: a missing or invalid value falls back to the default (itself
    held to ``ceiling``) and a value above ``ceiling`` is clamped to it, each with a warning. Never raises.
    """
    ceiling = max(1, ceiling)
    default = min(DEFAULT_MAX_PARALLEL_CALLS, ceiling)
    value = _as_finite_float(raw)
    if value is None or value < 1:
        logger.warning(
            "tool_calling_max_parallel_invalid: type=%s, using_default=%d, domain=code_executor",
            type(raw).__name__,
            default,
        )
        return default
    if value > ceiling:
        logger.warning(
            "tool_calling_max_parallel_clamped: requested=%.0f, max=%d, domain=code_executor", value, ceiling
        )
        return ceiling
    return int(value)


@dataclass(frozen=True)
class ToolCallingSettings:
    """What a run with tool calling on needs to know: the only value that is passed between the layers."""

    run_timeout_seconds: float = DEFAULT_RUN_TIMEOUT_SECONDS
    max_payload_bytes: int = MAX_PAYLOAD_BYTES
    max_parallel_calls: int = DEFAULT_MAX_PARALLEL_CALLS

    def run_budget_seconds(self, execution_timeout: float) -> float:
        """The time a run with tool calling may take: the larger of the script's own limit and the run limit."""
        return max(execution_timeout, self.run_timeout_seconds)

    def sdk_config(self, exchange_dir: str, execution_timeout: float) -> dict[str, object]:
        """The configuration the SDK gets in the sandbox (see ``codemie_runtime_sdk._configure``)."""
        return {
            "exchange_dir": exchange_dir,
            "run_seconds": self.run_budget_seconds(execution_timeout),
            "max_payload_bytes": self.max_payload_bytes,
        }


@dataclass(frozen=True)
class RunBudget:
    """The wall-clock budget of one Job: its length and the ``time.monotonic()`` instant it ends."""

    seconds: float
    deadline: float

    @classmethod
    def starting_now(cls, seconds: float) -> RunBudget:
        return cls(seconds=seconds, deadline=time.monotonic() + seconds)

    def time_left(self) -> float:
        return self.deadline - time.monotonic()
