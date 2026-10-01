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

from codemie.repository.cli_analytics import vocabulary as v


def test_supported_span_names_map_to_canonical_kinds():
    assert v.SPAN_KIND == {
        "claude_code.tool": v.SpanKind.TOOL,
        "claude_code.tool.execution": v.SpanKind.TOOL_EXECUTION,
        "claude_code.interaction": v.SpanKind.INTERACTION,
        "claude_code.llm_request": v.SpanKind.LLM_REQUEST,
        "cursor.tool": v.SpanKind.TOOL,
        "cursor.tool.execution": v.SpanKind.TOOL_EXECUTION,
        "cursor.interaction": v.SpanKind.INTERACTION,
    }


def test_claude_code_log_events_map_to_canonical_kinds():
    assert v.EVENT_KIND["api_request"] == v.EventKind.API_REQUEST
    assert v.EVENT_KIND["user_prompt"] == v.EventKind.USER_PROMPT
    assert v.EVENT_KIND["skill_activated"] == v.EventKind.SKILL_ACTIVATED


def test_unknown_names_have_kind_zero():
    assert v.SpanKind.OTHER == 0
    assert v.EventKind.OTHER == 0


def test_hook_attribute_keys_are_the_router_contract():
    # The ClickHouse path forwards exactly these keys as OTel log attributes, in this order.
    assert v.HOOK_ATTRIBUTE_KEYS[0] == "event_type"
    assert len(v.HOOK_ATTRIBUTE_KEYS) == 25
    assert set(v.HOOK_ATTRIBUTE_KEYS) == {"event_type", "session_id", "prompt_id", *v.HOOK_TEXT_FIELDS}


def test_known_hook_keys_cover_every_typed_column():
    assert {"type", "timestamp", "session_id", "prompt_id", *v.HOOK_TEXT_FIELDS} <= v.HOOK_KNOWN_KEYS


def test_rollup_metrics_cover_lines_and_active_time_for_supported_harnesses():
    assert v.ROLLUP_METRICS == (
        "claude_code.lines_of_code.count",
        "cursor.lines_of_code.count",
        "claude_code.active_time.total",
        "cursor.active_time.total",
    )


def test_dimension_hook_types_exclude_skill_dispatch():
    # ClickHouse's hook_events table (and so every dimension) sees only these 13 types.
    assert len(v.DIMENSION_HOOK_TYPES) == 13
    assert v.SKILL_DISPATCH_EVENT not in v.DIMENSION_HOOK_TYPES
