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

"""Tests for effort -> provider kwarg mapping."""

import pytest

from codemie.configs.llm_effort import (
    GEMINI_THINKING_BUDGETS,
    gemini_thinking_kwargs,
    openai_reasoning_effort_kwargs,
)

LEVELS = ["low", "medium", "high"]


def test_openai_reasoning_effort_clamped():
    assert openai_reasoning_effort_kwargs("max", LEVELS) == {"reasoning_effort": "high"}


def test_openai_reasoning_effort_keeps_max_when_uncapped():
    assert openai_reasoning_effort_kwargs("max", [*LEVELS, "xhigh", "max"]) == {"reasoning_effort": "max"}


@pytest.mark.parametrize(("effort", "expected"), [("max", "high"), ("xhigh", "high"), ("medium", "medium")])
def test_openai_reasoning_effort_capped_for_dial_adapters(effort, expected):
    allowed = [*LEVELS, "xhigh", "max"]
    assert openai_reasoning_effort_kwargs(effort, allowed, cap_at_dial_adapter_levels=True) == {
        "reasoning_effort": expected
    }


@pytest.mark.parametrize(("effort", "allowed"), [(None, LEVELS), ("high", None), ("bogus", LEVELS)])
def test_openai_reasoning_effort_empty_when_unresolved(effort, allowed):
    assert openai_reasoning_effort_kwargs(effort, allowed) == {}


@pytest.mark.parametrize(("effort", "budget"), sorted(GEMINI_THINKING_BUDGETS.items()))
def test_gemini_thinking_budget(effort, budget):
    assert gemini_thinking_kwargs(effort, LEVELS) == {"thinking_budget": budget}


def test_gemini_max_clamps_to_high_budget():
    assert gemini_thinking_kwargs("max", LEVELS) == {"thinking_budget": 4096}


@pytest.mark.parametrize(("effort", "allowed"), [(None, LEVELS), ("high", None)])
def test_gemini_no_budget_when_unresolved(effort, allowed):
    assert gemini_thinking_kwargs(effort, allowed) == {}
