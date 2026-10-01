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

"""Which rollup slices a batch of new rows invalidates."""

from __future__ import annotations

from datetime import date, datetime, timezone

from codemie.repository.cli_analytics.postgres.dirty import RollupFamily, dirty_keys
from codemie.repository.cli_analytics.postgres.otlp import decode_hook_events, decode_otlp
from tests.codemie.repository.cli_analytics.support import otlp_builders as b

NOON = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
JUST_AFTER_MIDNIGHT = datetime(2026, 9, 23, 0, 20, tzinfo=timezone.utc)
D, PREVIOUS = date(2026, 9, 23), date(2026, 9, 22)


def _logs(*records):
    return decode_otlp("logs", b.logs_request({}, list(records)).SerializeToString(), "")


def _spans(*spans):
    return decode_otlp("traces", b.traces_request({}, list(spans)).SerializeToString(), "")


def _span(name: str, ts: datetime, session: str = "s1"):
    return b.span(
        name, trace_id=b"t" * 16, span_id=ts.isoformat().encode()[:8], start=ts, end=ts, attrs={"session.id": session}
    )


def test_rollup_families_are_distinct_bits():
    assert [int(f) for f in RollupFamily] == [1, 2, 4, 8]


def test_cost_prompt_and_skill_logs_invalidate_log_facts():
    batch = _logs(
        b.log_record(NOON, {"event.name": "api_request", "session.id": "s1"}),
        b.log_record(NOON, {"event.name": "user_prompt", "session.id": "s2"}),
        b.log_record(NOON, {"event.name": "skill_activated", "session.id": "s3"}),
        b.log_record(NOON, {"event.name": "tool_result", "session.id": "s4"}),
    )

    assert dirty_keys(batch) == {(D, "s1"): 1, (D, "s2"): 1, (D, "s3"): 1}


def test_logs_without_a_session_still_count_for_cost():
    # ClickHouse's cost rollup keeps records without a session; so does this one.
    batch = _logs(b.log_record(NOON, {"event.name": "api_request"}))

    assert dirty_keys(batch) == {(D, ""): RollupFamily.LOG_FACTS}


def test_hook_events_invalidate_the_session_dimensions():
    batch = decode_hook_events(
        [{"type": "agent.session.start", "session_id": "s1", "timestamp": NOON.isoformat()}], "", 0
    )

    assert dirty_keys(batch) == {(D, "s1"): RollupFamily.DIMENSIONS}


def test_hook_events_without_a_session_invalidate_nothing():
    batch = decode_hook_events([{"type": "agent.session.start", "timestamp": NOON.isoformat()}], "", 0)

    assert dirty_keys(batch) == {}


def test_tool_execution_and_interaction_spans_invalidate_span_facts():
    batch = _spans(
        _span("claude_code.tool", NOON),
        _span("claude_code.tool.execution", NOON.replace(minute=1)),
        _span("claude_code.interaction", NOON.replace(minute=2)),
        _span("claude_code.llm_request", NOON.replace(minute=3), session="s9"),
    )

    assert dirty_keys(batch) == {(D, "s1"): RollupFamily.SPAN_FACTS}


def test_spans_in_the_first_hour_also_invalidate_the_previous_day():
    # A tool call started before midnight finds its execution span after midnight.
    batch = _spans(_span("claude_code.tool.execution", JUST_AFTER_MIDNIGHT))

    assert dirty_keys(batch) == {(D, "s1"): RollupFamily.SPAN_FACTS, (PREVIOUS, "s1"): RollupFamily.SPAN_FACTS}


def test_only_rollup_metrics_invalidate_metric_facts():
    lines = b.sum_metric("claude_code.lines_of_code.count", [b.number_point(NOON, 3, {"session.id": "s1"})])
    other = b.sum_metric("claude_code.token.usage", [b.number_point(NOON, 3, {"session.id": "s2"})])
    batch = decode_otlp("metrics", b.metrics_request({}, [lines, other]).SerializeToString(), "")

    assert dirty_keys(batch) == {(D, "s1"): RollupFamily.METRIC_FACTS}


def test_families_of_one_key_are_combined():
    batch = _logs(b.log_record(NOON, {"event.name": "api_request", "session.id": "s1"}))
    batch.spans = _spans(_span("claude_code.tool", NOON)).spans

    assert dirty_keys(batch) == {(D, "s1"): RollupFamily.LOG_FACTS | RollupFamily.SPAN_FACTS}
