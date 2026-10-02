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

"""Limit for a run's script tool calls: bounds the tool-calling timeout that widens the Job deadline."""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

# Upper bound for a run's tool-calling timeout; it feeds the Job's activeDeadlineSeconds and the slot hold time.
MAX_TOOL_CALLING_SECONDS: float = 3600.0


def clamp_tool_calling_timeout(raw: object) -> float | None:
    """Effective tool-calling limit for a run, or ``None`` when tool calling is off.

    A bad value never fails a run: a non-positive or non-finite one switches tool calling off with a warning,
    and one above ``MAX_TOOL_CALLING_SECONDS`` is clamped to it.
    """
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return None
    requested = float(raw)

    if not math.isfinite(requested) or requested <= 0:
        logger.warning(
            "tool_calling_disabled: reason=invalid_timeout, requested=%s, domain=code_executor",
            requested,
        )
        return None
    if requested > MAX_TOOL_CALLING_SECONDS:
        logger.warning(
            "tool_calling_timeout_clamped: requested=%.3fs, max=%.3fs, domain=code_executor",
            requested,
            MAX_TOOL_CALLING_SECONDS,
        )
        return MAX_TOOL_CALLING_SECONDS
    return requested
