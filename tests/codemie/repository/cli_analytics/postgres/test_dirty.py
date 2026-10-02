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

import pytest

from codemie.repository.cli_analytics.postgres.dirty import RollupFamily, dirty_keys
from codemie.repository.cli_analytics.postgres.otlp import DecodedBatch, decode_hook_events, decode_otlp
from tests.codemie.repository.cli_analytics.support import hook_event_builders as hb
from tests.codemie.repository.cli_analytics.support import otlp_builders as b

NOON = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
JUST_AFTER_MIDNIGHT = datetime(2026, 9, 23, 0, 20, tzinfo=timezone.utc)
D, PREVIOUS, NEXT = date(2026, 9, 23), date(2026, 9, 22), date(2026, 9, 24)


def _logs(*records):
    return decode_otlp("logs", b.logs_request({}, list(records)).SerializeToString(), "")


def _spans(*spans):
    return decode_otlp("traces", b.traces_request({}, list(spans)).SerializeToString(), "")


def _span(name: str, ts: datetime, session: str = "s1"):
    return b.span(
        name, trace_id=b"t" * 16, span_id=ts.isoformat().encode()[:8], start=ts, end=ts, attrs={"session.id": session}
    )


def test_rollup_families_are_distinct_bits():
    assert [int(f) for f in RollupFamily] == [1, 2, 4, 8, 16, 32]


def test_cost_prompt_and_skill_logs_invalidate_log_facts():
    batch = _logs(
        b.log_record(NOON, {"event.name": "api_request", "session.id": "s1"}),
        b.log_record(NOON, {"event.name": "user_prompt", "session.id": "s2"}),
        b.log_record(NOON, {"event.name": "skill_activated", "session.id": "s3"}),
        b.log_record(NOON, {"event.name": "tool_result", "session.id": "s4"}),
    )

    assert dirty_keys(batch) == {
        (D, "s1"): 49,  # LOG_FACTS 1 + USAGE_FACTS 16 + SESSION 32
        (D, "s2"): 33,  # LOG_FACTS 1 + SESSION 32
        (D, "s3"): 33,  # LOG_FACTS 1 + SESSION 32
        (D, "s4"): 32,  # tool_result is no log fact; the session id alone gives SESSION 32
    }


def test_logs_without_a_session_still_count_for_cost():
    # The cost rollup keeps records without a session.
    batch = _logs(b.log_record(NOON, {"event.name": "api_request"}))

    assert dirty_keys(batch) == {(D, ""): 17}  # LOG_FACTS 1 + USAGE_FACTS 16, no SESSION


def test_hook_events_invalidate_the_session_dimensions():
    batch = decode_hook_events(
        [{"type": "agent.session.start", "session_id": "s1", "timestamp": NOON.isoformat()}], "", 0
    )

    assert dirty_keys(batch) == {(D, "s1"): 34}  # DIMENSIONS 2 + SESSION 32


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

    assert dirty_keys(batch) == {
        (D, "s1"): 36,  # SPAN_FACTS 4 + SESSION 32
        (D, "s9"): 32,  # an llm_request span is no span fact; the session id gives SESSION 32
    }


def test_spans_in_the_first_hour_also_invalidate_the_previous_day():
    # A tool call started before midnight finds its execution span after midnight.
    batch = _spans(_span("claude_code.tool.execution", JUST_AFTER_MIDNIGHT))

    assert dirty_keys(batch) == {
        (D, "s1"): 36,  # SPAN_FACTS 4 + SESSION 32
        (PREVIOUS, "s1"): 4,  # SPAN_FACTS only
    }


def test_only_rollup_metrics_invalidate_metric_facts():
    lines = b.sum_metric("claude_code.lines_of_code.count", [b.number_point(NOON, 3, {"session.id": "s1"})])
    other = b.sum_metric("claude_code.token.usage", [b.number_point(NOON, 3, {"session.id": "s2"})])
    batch = decode_otlp("metrics", b.metrics_request({}, [lines, other]).SerializeToString(), "")

    assert dirty_keys(batch) == {
        (D, "s1"): 40,  # METRIC_FACTS 8 + SESSION 32
        (D, "s2"): 32,  # token.usage is no rollup metric; the session id gives SESSION 32
    }


def test_families_of_one_key_are_combined():
    batch = _logs(b.log_record(NOON, {"event.name": "api_request", "session.id": "s1"}))
    batch.spans = _spans(_span("claude_code.tool", NOON)).spans

    assert dirty_keys(batch) == {(D, "s1"): 53}  # LOG_FACTS 1 + USAGE_FACTS 16 + SPAN_FACTS 4 + SESSION 32


def _hooks(*events: dict[str, object]) -> DecodedBatch:
    return decode_hook_events(list(events), "", 0)


def _at(hour: int, minute: int, second: int, day: int = 23) -> datetime:
    return datetime(2026, 9, day, hour, minute, second, tzinfo=timezone.utc)


def _api_request(ts: datetime) -> DecodedBatch:
    return _logs(b.log_record(ts, {"event.name": "api_request", "session.id": "s1"}))


def _usage(ts: datetime, session: str = "s1") -> DecodedBatch:
    return _hooks(hb.usage_request(timestamp=ts.isoformat(), session_id=session))


def test_a_usage_request_marks_log_usage_and_session_facts() -> None:
    assert dirty_keys(_usage(NOON)) == {(D, "s1"): 49}  # LOG_FACTS 1 + USAGE_FACTS 16 + SESSION 32


def test_a_usage_request_without_a_session_gets_no_session_bit() -> None:
    assert dirty_keys(_usage(NOON, session="")) == {(D, ""): 17}  # LOG_FACTS 1 + USAGE_FACTS 16


def test_a_user_prompt_with_a_session_marks_log_and_session_facts() -> None:
    batch = _logs(b.log_record(NOON, {"event.name": "user_prompt", "session.id": "s1"}))

    assert dirty_keys(batch) == {(D, "s1"): 33}  # LOG_FACTS 1 + SESSION 32


def test_a_subagent_usage_hook_marks_dimensions_usage_and_session() -> None:
    batch = _hooks(hb.subagent_usage(timestamp=NOON.isoformat()))

    assert dirty_keys(batch) == {(D, hb.SESSION): 50}  # DIMENSIONS 2 + USAGE_FACTS 16 + SESSION 32


def test_a_tool_end_hook_marks_span_facts_on_its_day_and_the_hour_before() -> None:
    batch = _hooks(hb.minimal("agent.tool.end", "s1", JUST_AFTER_MIDNIGHT.isoformat()))

    assert dirty_keys(batch) == {
        (D, "s1"): 38,  # DIMENSIONS 2 + SPAN_FACTS 4 + SESSION 32
        (PREVIOUS, "s1"): 4,  # SPAN_FACTS only
    }


@pytest.mark.parametrize(
    "event_type",
    [
        "agent.tool.start",
        "agent.tool.end",
        "agent.tool.error",
        "agent.prompt.submit",
        "agent.subagent.start",
        "agent.skill.dispatch",
    ],
)
def test_the_six_span_hook_types_mark_span_facts(event_type: str) -> None:
    batch = _hooks(hb.minimal(event_type, "s1", NOON.isoformat()))

    assert dirty_keys(batch) == {(D, "s1"): 38}  # DIMENSIONS 2 + SPAN_FACTS 4 + SESSION 32


@pytest.mark.parametrize("event_type", ["agent.session.summary", "agent.session.env", "agent.git.snapshot"])
def test_other_hook_types_mark_only_dimensions_and_session(event_type: str) -> None:
    batch = _hooks(hb.minimal(event_type, "s1", NOON.isoformat()))

    assert dirty_keys(batch) == {(D, "s1"): 34}  # DIMENSIONS 2 + SESSION 32


def test_a_usage_row_30_seconds_after_midnight_also_marks_the_previous_day() -> None:
    # 00:00:30 is 30 s after midnight, within the 1 h reach: a request whose earlier line lies on the
    # previous day is regrouped there, so that day gets the same bits as the row's own day.
    assert dirty_keys(_usage(_at(0, 0, 30))) == {(D, "s1"): 49, (PREVIOUS, "s1"): 49}  # 1 + 16 + 32 on both days


def test_a_usage_row_1_hour_after_midnight_also_marks_the_previous_day() -> None:
    # 01:00:00 - 00:00:00 = 1 h, the reach itself (bounds inclusive)
    assert dirty_keys(_usage(_at(1, 0, 0))) == {(D, "s1"): 49, (PREVIOUS, "s1"): 49}  # 1 + 16 + 32 on both days


def test_a_usage_row_1_hour_and_1_second_after_midnight_marks_only_its_day() -> None:
    # 01:00:01 - 00:00:00 = 1 h 1 s, past the 1 h reach
    assert dirty_keys(_usage(_at(1, 0, 1))) == {(D, "s1"): 49}  # 1 + 16 + 32


def test_a_usage_row_1_hour_before_midnight_also_marks_the_next_day() -> None:
    # 24:00:00 - 23:00:00 = 1 h, the reach itself (bounds inclusive)
    assert dirty_keys(_usage(_at(23, 0, 0))) == {(D, "s1"): 49, (NEXT, "s1"): 49}  # 1 + 16 + 32 on both days


def test_a_usage_row_1_hour_and_1_second_before_midnight_marks_only_its_day() -> None:
    # 24:00:00 - 22:59:59 = 1 h 1 s, past the 1 h reach
    assert dirty_keys(_usage(_at(22, 59, 59))) == {(D, "s1"): 49}  # 1 + 16 + 32


def test_an_api_request_keeps_the_5_second_midnight_rule() -> None:
    # the OTel matching window stays 5 s, so an api_request marks its neighbour day only within 5 s
    assert dirty_keys(_api_request(_at(0, 0, 5))) == {(D, "s1"): 49, (PREVIOUS, "s1"): 49}
    assert dirty_keys(_api_request(_at(0, 0, 6))) == {(D, "s1"): 49}
    assert dirty_keys(_api_request(_at(23, 59, 55))) == {(D, "s1"): 49, (NEXT, "s1"): 49}
    assert dirty_keys(_api_request(_at(23, 59, 54))) == {(D, "s1"): 49}  # each is 1 + 16 + 32


def _api_request_with_id(ts: datetime, request_id: str) -> DecodedBatch:
    return _logs(b.log_record(ts, {"event.name": "api_request", "session.id": "s1", "request_id": request_id}))


def test_an_api_request_with_a_request_id_30_seconds_after_midnight_also_marks_the_previous_day() -> None:
    # 00:00:30 - 00:00:00 = 30 s, within the 1 h reach of an identifier pair across midnight
    assert dirty_keys(_api_request_with_id(_at(0, 0, 30), "req-1")) == {(D, "s1"): 49, (PREVIOUS, "s1"): 49}
    # each is LOG_FACTS 1 + USAGE_FACTS 16 + SESSION 32 = 49


def test_an_api_request_with_a_request_id_1_hour_from_midnight_marks_the_neighbour_day() -> None:
    # 01:00:00 - 00:00:00 = 1 h and 24:00:00 - 23:00:00 = 1 h, the reach itself (bounds inclusive)
    assert dirty_keys(_api_request_with_id(_at(1, 0, 0), "req-1")) == {(D, "s1"): 49, (PREVIOUS, "s1"): 49}
    assert dirty_keys(_api_request_with_id(_at(23, 0, 0), "req-1")) == {(D, "s1"): 49, (NEXT, "s1"): 49}


def test_an_api_request_with_a_request_id_past_the_1_hour_reach_marks_only_its_day() -> None:
    # 01:00:01 - 00:00:00 = 1 h 1 s and 24:00:00 - 22:59:59 = 1 h 1 s, past the 1 h reach
    assert dirty_keys(_api_request_with_id(_at(1, 0, 1), "req-1")) == {(D, "s1"): 49}
    assert dirty_keys(_api_request_with_id(_at(22, 59, 59), "req-1")) == {(D, "s1"): 49}  # 1 + 16 + 32


def test_an_api_request_without_a_request_id_marks_the_neighbour_day_only_within_5_seconds() -> None:
    # 00:00:30 is 30 s past midnight: past the 5 s window of a record without an identifier
    assert dirty_keys(_api_request(_at(0, 0, 30))) == {(D, "s1"): 49}  # 1 + 16 + 32
    # an empty request_id is no identifier: the 5 s window as well
    assert dirty_keys(_api_request_with_id(_at(0, 0, 30), "")) == {(D, "s1"): 49}


# The first and the last hour a client can date an event in: years 1 to 9999 are accepted as sent.
FIRST_HOUR = datetime(1, 1, 1, 0, 30, tzinfo=timezone.utc)
LAST_HOUR = datetime(9999, 12, 31, 23, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("ts", [FIRST_HOUR, LAST_HOUR], ids=["0001-01-01", "9999-12-31"])
def test_a_usage_row_at_the_edge_of_the_calendar_marks_only_its_own_day(ts: datetime) -> None:
    # 00:30 and 23:30 are within the 1 h reach of a midnight, but there is no day before 0001-01-01 and
    # none after 9999-12-31: the row's own day is marked and nothing raises inside the ingest transaction.
    assert dirty_keys(_usage(ts)) == {(ts.date(), "s1"): 49}  # LOG_FACTS 1 + USAGE_FACTS 16 + SESSION 32


@pytest.mark.parametrize("ts", [FIRST_HOUR, LAST_HOUR], ids=["0001-01-01", "9999-12-31"])
@pytest.mark.parametrize(
    "event_type",
    [
        "agent.tool.start",
        "agent.tool.end",
        "agent.tool.error",
        "agent.prompt.submit",
        "agent.subagent.start",
        "agent.skill.dispatch",
    ],
)
def test_a_span_hook_at_the_edge_of_the_calendar_marks_only_its_own_day(event_type: str, ts: datetime) -> None:
    batch = _hooks(hb.minimal(event_type, "s1", ts.isoformat()))

    assert dirty_keys(batch) == {(ts.date(), "s1"): 38}  # DIMENSIONS 2 + SPAN_FACTS 4 + SESSION 32


def test_the_midnight_rule_does_not_apply_to_other_log_events() -> None:
    batch = _logs(b.log_record(_at(0, 0, 1), {"event.name": "user_prompt", "session.id": "s1"}))

    assert dirty_keys(batch) == {(D, "s1"): 33}  # LOG_FACTS 1 + SESSION 32


def _metrics_batch() -> DecodedBatch:
    lines = b.sum_metric("claude_code.lines_of_code.count", [b.number_point(NOON, 3, {"session.id": "s1"})])
    return decode_otlp("metrics", b.metrics_request({}, [lines]).SerializeToString(), "")


@pytest.mark.parametrize(
    "batch",
    [
        _api_request(NOON),
        _api_request(_at(0, 0, 5)),
        _logs(b.log_record(NOON, {"event.name": "user_prompt", "session.id": "s1"})),
        _usage(NOON),
        _usage(_at(0, 0, 30)),
        _hooks(hb.subagent_usage(timestamp=NOON.isoformat())),
        _spans(_span("claude_code.tool", NOON)),
        _metrics_batch(),
    ],
    ids=["api_request", "api_request_midnight", "user_prompt", "usage", "usage_midnight", "hook", "span", "metric"],
)
def test_stored_masks_are_plain_ints(batch: DecodedBatch) -> None:
    # An IntFlag is iterable on Python 3.12, so asyncpg would encode it as a nested int4[] array.
    assert {type(v) for v in dirty_keys(batch).values()} == {int}
