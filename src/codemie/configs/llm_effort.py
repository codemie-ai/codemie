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

"""Effort levels and their mapping to provider-specific constructor kwargs.

Every helper clamps to the model's declared ceiling and returns an empty dict when nothing should be
sent, so callers can merge the result unconditionally and never raise.
"""

from enum import Enum
from typing import Any


class EffortLevel(str, Enum):
    """Reasoning effort a request asks for, in ascending order."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


# Rank order for clamp_effort_level — higher is "more effort".
EFFORT_RANK: dict[str, int] = {level: rank for rank, level in enumerate(EffortLevel)}

# Gemini thinking budgets (tokens) per effort level; Gemini ceilings stop at "high".
GEMINI_THINKING_BUDGETS: dict[str, int] = {EffortLevel.LOW: 1024, EffortLevel.MEDIUM: 2048, EffortLevel.HIGH: 4096}


# DIAL's Bedrock and Vertex adapters reject reasoning_effort above "high" (HTTP 400) until they ship
# ai-dial-sdk with xhigh/max support: https://github.com/epam/ai-dial-sdk/pull/478. Drop the cap then.
DIAL_ADAPTER_EFFORT_LEVELS: list[str] = [EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH]


def clamp_effort_level(requested: str | None, allowed: list[str] | None) -> str | None:
    """Clamp a requested effort level down to the highest level in `allowed` whose rank is
    <= the requested level's rank. Never jumps up over a gap in `allowed` to a higher level.
    Returns None when `requested`/`allowed` is falsy or no level in `allowed` qualifies."""
    if not requested or not allowed:
        return None
    requested_rank = EFFORT_RANK.get(requested)
    if requested_rank is None:
        return None
    candidates = [level for level in allowed if EFFORT_RANK.get(level, -1) <= requested_rank]
    if not candidates:
        return None
    return EffortLevel(max(candidates, key=lambda level: EFFORT_RANK[level])).value


def openai_reasoning_effort_kwargs(
    effort: str | None, allowed: list[str] | None, *, cap_at_dial_adapter_levels: bool = False
) -> dict[str, str]:
    """`reasoning_effort` for models behind an OpenAI-compatible client (Azure, DIAL).

    Claude and Gemini reach DIAL through its Bedrock and Vertex adapters, so their level is capped to what
    those adapters accept.
    """
    clamped = clamp_effort_level(effort, allowed)
    if clamped is not None and cap_at_dial_adapter_levels:
        clamped = clamp_effort_level(clamped, DIAL_ADAPTER_EFFORT_LEVELS)
    return {"reasoning_effort": clamped} if clamped is not None else {}


def gemini_thinking_kwargs(effort: str | None, allowed: list[str] | None) -> dict[str, int]:
    """`thinking_budget` for Gemini on ChatVertexAI."""
    clamped = clamp_effort_level(effort, allowed)
    budget = GEMINI_THINKING_BUDGETS.get(clamped) if clamped is not None else None
    return {"thinking_budget": budget} if budget is not None else {}


def anthropic_effort_kwargs(effort: str | None, allowed: list[str] | None) -> dict[str, str]:
    """`effort` for Claude on ChatAnthropic."""
    clamped = clamp_effort_level(effort, allowed)
    return {"effort": clamped} if clamped is not None else {}


def bedrock_request_fields_with_effort(
    fields: dict[str, Any], effort: str | None, allowed: list[str] | None
) -> dict[str, Any]:
    """Bedrock `additional_model_request_fields` with Anthropic's `output_config.effort` merged in.

    Bedrock-hosted Claude has no first-class effort kwarg, so the effort goes through the passthrough,
    keeping any other `output_config` keys already set from client_headers.
    """
    clamped = clamp_effort_level(effort, allowed)
    if clamped is None:
        return fields
    existing = fields.get("output_config")
    output_config = dict(existing) if isinstance(existing, dict) else {}
    return {**fields, "output_config": {**output_config, "effort": clamped}}
