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

"""OTLP and hook decoding for the PostgreSQL adapter: pure functions, no database."""

from __future__ import annotations

import base64
import json
from datetime import date, datetime, timedelta, timezone

from unittest.mock import MagicMock, patch

import pytest
from google.protobuf import json_format

from codemie.repository.cli_analytics import vocabulary as v
from codemie.repository.cli_analytics.ports import InvalidTelemetryPayloadError, UnsupportedTelemetryContentTypeError
from codemie.repository.cli_analytics.postgres import otlp
from codemie.repository.cli_analytics.postgres.otlp import (
    MAX_COST_USD,
    MAX_TOKENS,
    DecodedBatch,
    HookRow,
    UsageRow,
    as_str,
    ch_float,
    ch_uint,
    clean,
    decode_hook_events,
    decode_otlp,
    go_float,
    usage_bool,
    usage_int,
)
from tests.codemie.repository.cli_analytics.support import otlp_builders as b
from tests.codemie.repository.cli_analytics.support.hook_event_builders import (
    as_otlp_log,
    git_snapshot,
    minimal,
    session_env,
    session_summary,
    skill_dispatch,
    subagent_usage,
    usage_request,
    without,
)

T = datetime(2026, 9, 23, 10, 15, 30, 123456, tzinfo=timezone.utc)
RESOURCE = {"service.name": "claude-code", "service.version": "2.0.1", "user.email": "res@example.com"}
TRACE = bytes.fromhex("0af7651916cd43dd8448eb211c80319c")
SPAN_ID = bytes.fromhex("b7ad6b7169203331")


def _api_request(**overrides) -> dict:
    attrs = {
        "event.name": "api_request",
        "session.id": "sess-1",
        "prompt.id": "prompt-1",
        "user.email": "dev@example.com",
        "user.id": "u-hash",
        "organization.id": "org-1",
        "terminal.type": "vscode",
        "model": "claude-sonnet-4-5",
        "query_source": "repl_main_thread",
        "cost_usd": 0.0125,
        "input_tokens": 1200,
        "output_tokens": 340,
        "cache_read_tokens": 55000,
        "cache_creation_tokens": 800,
        "event.sequence": 7,
    }
    attrs.update(overrides)
    return attrs


def _logs_body(records, resource=RESOURCE) -> bytes:
    return b.logs_request(resource, records).SerializeToString()


# ── pcommon AsString() and lenient numeric parsing ─────────────────────────


@pytest.mark.parametrize(
    ("value", "rendered"),
    [
        (5.0, "5"),
        (0.1, "0.1"),
        (1e21, "1000000000000000000000"),
        (-0.0, "-0"),
        (float("nan"), "NaN"),
        (float("inf"), "+Inf"),
        (float("-inf"), "-Inf"),
        (1.5e-7, "0.00000015"),
    ],
)
def test_doubles_render_in_their_shortest_decimal_form(value: float, rendered: str) -> None:
    assert go_float(value) == rendered


@pytest.mark.parametrize(
    ("value", "rendered"),
    [
        (None, ""),
        ("text", "text"),
        (True, "true"),
        (False, "false"),
        (42, "42"),
        (2.0, "2"),
        (["a", 1], '["a",1]'),
        ({"b": 1, "a": 2}, '{"a":2,"b":1}'),
    ],
)
def test_attribute_values_render_like_as_string(value, rendered):
    assert as_str(value) == rendered


@pytest.mark.parametrize(
    ("value", "parsed"),
    [(1200, (1200, True)), ("77", (77, True)), (None, (0, True)), ("", (0, True)), ("12.5", (0, False))]
    + [("-1", (0, False)), ("abc", (0, False)), (str(2**64), (0, False))],
)
def test_unsigned_parsing_matches_to_uint64_or_zero(value, parsed):
    assert ch_uint(value) == parsed


@pytest.mark.parametrize(
    ("value", "parsed"),
    [(0.0125, (0.0125, True)), ("3.5", (3.5, True)), (7, (7.0, True)), (None, (0.0, True)), ("n/a", (0.0, False))],
)
def test_float_parsing_matches_to_float64_or_zero(value, parsed):
    assert ch_float(value) == parsed


HIGH, LOW = chr(0xD83D), chr(0xDE00)  # the UTF-16 halves of U+1F600, as JSON escapes decode them
REPLACEMENT = "\N{REPLACEMENT CHARACTER}"


def test_clean_strips_nul_and_replaces_lone_surrogates():
    assert clean("a\x00b") == "ab"
    assert clean(f"broken {HIGH} emoji") == f"broken {REPLACEMENT} emoji"
    assert clean(f"pair {HIGH}{LOW} kept") == "pair \U0001f600 kept"
    assert clean(None) is None


# ── logs ──────────────────────────────────────────────────────────────────────


def test_api_request_log_becomes_a_typed_row():
    batch = decode_otlp(
        "logs", _logs_body([b.log_record(T, _api_request(), trace_id=TRACE, span_id=SPAN_ID)]), "application/x-protobuf"
    )

    (row,) = batch.logs
    assert row.ts == T
    assert row.day == date(2026, 9, 23)
    assert row.session_id == "sess-1"
    assert row.event_kind == v.EventKind.API_REQUEST
    assert row.event_name == "api_request"
    assert row.prompt_id == "prompt-1"
    assert row.user_email == "dev@example.com"
    assert (row.model, row.query_source) == ("claude-sonnet-4-5", "repl_main_thread")
    assert (row.cost_usd, row.input_tokens, row.output_tokens) == (0.0125, 1200, 340)
    assert (row.cache_read_tokens, row.cache_creation_tokens) == (55000, 800)
    assert (row.trace_id, row.span_id) == (TRACE, SPAN_ID)
    assert row.severity == 9
    assert json.loads(row.attrs) == {"event.sequence": 7}


def test_session_scoped_attributes_are_stored_once_per_session():
    records = [b.log_record(T, _api_request()), b.log_record(T, _api_request(**{"event.sequence": 8}))]

    batch = decode_otlp("logs", _logs_body(records), "application/x-protobuf")

    assert batch.session_attrs == {
        "sess-1": {"user.id": "u-hash", "organization.id": "org-1", "terminal.type": "vscode"}
    }
    assert all("user.id" not in json.loads(r.attrs) for r in batch.logs)


def test_resource_is_deduplicated_by_its_attributes_in_any_order():
    reordered = dict(reversed(list(RESOURCE.items())))
    first = decode_otlp("logs", _logs_body([b.log_record(T, _api_request())]), "application/x-protobuf")
    second = decode_otlp("logs", _logs_body([b.log_record(T, _api_request())], reordered), "application/x-protobuf")

    (rid,) = first.resources
    assert set(second.resources) == {rid}
    assert first.logs[0].resource_id == rid
    service_name, attrs_json = first.resources[rid]
    assert service_name == "claude-code"
    assert json.loads(attrs_json) == RESOURCE


def test_session_id_falls_back_to_the_underscore_attribute_and_prompt_id_to_prompt_dot_id():
    attrs = _api_request()
    del attrs["session.id"]
    attrs["session_id"] = "sess-9"
    del attrs["prompt.id"]
    attrs["prompt_id"] = "p-9"

    (row,) = decode_otlp("logs", _logs_body([b.log_record(T, attrs)]), "application/x-protobuf").logs

    assert (row.session_id, row.prompt_id) == ("sess-9", "p-9")


def test_unparseable_numbers_become_zero_and_keep_their_original_value():
    (row,) = decode_otlp(
        "logs", _logs_body([b.log_record(T, _api_request(input_tokens="lots"))]), "application/x-protobuf"
    ).logs

    assert row.input_tokens == 0
    assert json.loads(row.attrs)["input_tokens"] == "lots"


def test_missing_numbers_stay_null():
    attrs = _api_request()
    for key in ("cost_usd", "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
        del attrs[key]

    (row,) = decode_otlp("logs", _logs_body([b.log_record(T, attrs)]), "application/x-protobuf").logs

    assert (row.cost_usd, row.input_tokens, row.cache_read_tokens) == (None, None, None)


def test_record_without_timestamp_uses_the_observed_time():
    record = b.log_record(None, _api_request(), observed=T)

    (row,) = decode_otlp("logs", _logs_body([record]), "application/x-protobuf").logs

    assert row.ts == T


def test_unknown_event_names_keep_kind_zero():
    (row,) = decode_otlp(
        "logs",
        _logs_body([b.log_record(T, _api_request(**{"event.name": "brand_new_event"}))]),
        "application/x-protobuf",
    ).logs

    assert (row.event_kind, row.event_name) == (v.EventKind.OTHER, "brand_new_event")


def test_skill_and_command_names_are_typed_columns():
    records = [
        b.log_record(T, {"event.name": "skill_activated", "session.id": "s", "skill.name": "superpowers:tdd"}),
        b.log_record(T, {"event.name": "user_prompt", "session.id": "s", "command_name": "compact"}),
    ]

    skill, prompt = decode_otlp("logs", _logs_body(records), "application/x-protobuf").logs

    assert (skill.event_kind, skill.skill_name) == (v.EventKind.SKILL_ACTIVATED, "superpowers:tdd")
    assert (prompt.event_kind, prompt.command_name) == (v.EventKind.USER_PROMPT, "compact")


def test_nul_never_reaches_a_row():
    # Protobuf strings are valid UTF-8, so NUL is what can arrive here; unpaired
    # surrogates only arrive through JSON (see the OTLP/JSON and hook tests).
    attrs = _api_request(model="claude\x00-x", **{"session.id": "s\x001"})
    attrs["weird\x00key"] = "v\x00"

    (row,) = decode_otlp("logs", _logs_body([b.log_record(T, attrs)]), "application/x-protobuf").logs

    assert (row.model, row.session_id) == ("claude-x", "s1")
    assert json.loads(row.attrs)["weirdkey"] == "v"


def test_log_carrying_an_event_type_is_a_hook_event():
    hook = {"event_type": "agent.session.start", "session_id": "s-h", "cwd": "org/repo", "user.email": "a@b.c"}

    batch = decode_otlp("logs", _logs_body([b.log_record(T, hook)]), "application/x-protobuf")

    assert batch.logs == []
    (row,) = batch.hooks
    assert (row.event_type, row.session_id, row.cwd, row.user_email) == (
        "agent.session.start",
        "s-h",
        "org/repo",
        "a@b.c",
    )
    assert row.ts == T


def test_concatenated_export_requests_merge_into_one_batch():
    # The plugin proxy joins several exports of a spool into one body.
    body = _logs_body([b.log_record(T, _api_request())]) + _logs_body(
        [b.log_record(T, _api_request(**{"session.id": "sess-2"}))]
    )

    batch = decode_otlp("logs", body, "application/x-protobuf")

    assert [r.session_id for r in batch.logs] == ["sess-1", "sess-2"]


def test_identical_records_share_an_identity_and_different_records_do_not():
    same = decode_otlp("logs", _logs_body([b.log_record(T, _api_request())] * 2), "application/x-protobuf")
    other = decode_otlp(
        "logs", _logs_body([b.log_record(T, _api_request(**{"event.sequence": 99}))]), "application/x-protobuf"
    )

    assert same.logs[0].h == same.logs[1].h
    assert other.logs[0].h != same.logs[0].h


# ── traces ────────────────────────────────────────────────────────────────────


def _tool_span(**attrs) -> bytes:
    base = {"session.id": "sess-1", "tool_name": "Edit", "tool_use_id": "toolu_1", "file_path": "src/a.py"}
    base.update(attrs)
    sp = b.span(
        "claude_code.tool",
        trace_id=TRACE,
        span_id=SPAN_ID,
        start=T,
        end=T.replace(second=31),
        attrs=base,
        parent_span_id=b"\x01" * 8,
        status_code=1,
    )
    return b.traces_request(RESOURCE, [sp]).SerializeToString()


def test_tool_span_becomes_a_typed_row():
    (row,) = decode_otlp(
        "traces", _tool_span(success="true", subagent_type="", skill_name=""), "application/x-protobuf"
    ).spans

    assert (row.ts, row.duration_ns) == (T, 1_000_000_000)
    assert (row.trace_id, row.span_id, row.parent_span_id) == (TRACE, SPAN_ID, b"\x01" * 8)
    assert (row.span_kind, row.span_name) == (v.SpanKind.TOOL, "claude_code.tool")
    assert (row.session_id, row.tool_name, row.tool_use_id, row.file_path) == ("sess-1", "Edit", "toolu_1", "src/a.py")
    assert (row.success, row.status_code) == ("true", 1)
    assert (row.subagent_type, row.skill_name) == (None, None)


def test_span_email_falls_back_to_the_resource():
    (row,) = decode_otlp("traces", _tool_span(), "application/x-protobuf").spans

    assert row.user_email == "res@example.com"


def test_span_identity_is_trace_and_span_id():
    first = decode_otlp("traces", _tool_span(success="true"), "application/x-protobuf").spans[0]
    changed = decode_otlp("traces", _tool_span(success="false"), "application/x-protobuf").spans[0]

    assert first.h == changed.h


def test_span_success_boolean_is_rendered_like_as_string():
    (row,) = decode_otlp("traces", _tool_span(success=True), "application/x-protobuf").spans

    assert row.success == "true"


# ── metrics ───────────────────────────────────────────────────────────────────


def test_sum_and_gauge_points_are_stored_and_histograms_skipped():
    point_attrs = {"session.id": "sess-1", "user.email": "dev@example.com", "model": "m", "type": "added"}
    body = b.metrics_request(
        RESOURCE,
        [
            b.sum_metric("claude_code.lines_of_code.count", [b.number_point(T, 12, point_attrs, start=T)]),
            b.gauge_metric("some.gauge", [b.number_point(T, 0.5, {"session.id": "sess-1"})]),
            b.histogram_metric("some.histogram"),
        ],
    ).SerializeToString()

    lines, gauge = decode_otlp("metrics", body, "application/x-protobuf").metrics

    assert (lines.metric_name, lines.value, lines.session_id, lines.type) == (
        "claude_code.lines_of_code.count",
        12.0,
        "sess-1",
        "added",
    )
    assert (lines.user_email, lines.model, lines.start_ts) == ("dev@example.com", "m", T)
    assert (lines.temporality, lines.is_monotonic) == (1, True)
    assert (gauge.metric_name, gauge.value, gauge.temporality, gauge.is_monotonic) == ("some.gauge", 0.5, 0, False)


# ── JSON, content types, errors ───────────────────────────────────────────────


def _hex_ids_json(request) -> bytes:
    """OTLP/JSON as senders produce it: ids in hex, not protobuf-JSON base64."""
    data = json_format.MessageToDict(request)

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("traceId", "spanId", "parentSpanId") and isinstance(value, str):
                    node[key] = base64.b64decode(value).hex()
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return json.dumps(data).encode()


def test_json_traces_with_hex_ids_decode_like_protobuf():
    sp = b.span("claude_code.tool", trace_id=TRACE, span_id=SPAN_ID, start=T, end=T, attrs={"session.id": "s"})
    request = b.traces_request(RESOURCE, [sp])

    from_json = decode_otlp("traces", _hex_ids_json(request), "application/json; charset=utf-8").spans
    from_pb = decode_otlp("traces", request.SerializeToString(), "application/x-protobuf").spans

    assert from_json == from_pb


def test_json_lone_surrogates_are_replaced_before_parsing():
    request = b.logs_request(RESOURCE, [b.log_record(T, _api_request(model="PLACEHOLDER"))])
    body = json.dumps(json_format.MessageToDict(request)).replace("PLACEHOLDER", "\\ud83d cut").encode()

    (row,) = decode_otlp("logs", body, "application/json").logs

    assert row.model == f"{REPLACEMENT} cut"


@pytest.mark.parametrize("content_type", ["application/x-protobuf", "application/protobuf", ""])
def test_protobuf_media_types(content_type):
    assert decode_otlp("logs", _logs_body([b.log_record(T, _api_request())]), content_type).logs


def test_unsupported_media_type_is_rejected():
    with pytest.raises(UnsupportedTelemetryContentTypeError):
        decode_otlp("logs", b"x", "text/plain")


@pytest.mark.parametrize(
    ("body", "content_type"),
    [(b"\xff\xff\xff\x00garbage", "application/x-protobuf"), (b"{not json", "application/json")]
    + [(b"[1, 2]", "application/json"), (b'{"resourceLogs": "wrong"}', "application/json")],
)
def test_undecodable_bodies_are_invalid_payloads(body, content_type):
    with pytest.raises(InvalidTelemetryPayloadError):
        decode_otlp("logs", body, content_type)


def test_empty_body_is_an_empty_batch():
    batch = decode_otlp("metrics", b"", "application/x-protobuf")

    assert batch.record_count == 0


# ── hook NDJSON ───────────────────────────────────────────────────────────────


def _hook(**overrides) -> dict:
    event = {
        "type": "agent.prompt.submit",
        "timestamp": "2026-09-23T10:15:30.123456Z",
        "session_id": "sess-1",
        "prompt_id": "p1",
        "developer_name": "dev",
        "codemie_project_name": "proj",
        "cwd": "org/repo",
        "git_branch": "main",
        "prompt_body": "fix the bug",
    }
    event.update(overrides)
    return event


def test_hook_event_becomes_a_typed_row_with_the_sender_email():
    batch = decode_hook_events([_hook(extra_field={"x": 1})], "jwt@example.com", received_at_ns=0)

    (row,) = batch.hooks
    assert row.ts == T
    assert (row.event_type, row.session_id, row.prompt_id, row.user_email) == (
        "agent.prompt.submit",
        "sess-1",
        "p1",
        "jwt@example.com",
    )
    assert (row.developer_name, row.codemie_project_name, row.cwd, row.git_branch) == (
        "dev",
        "proj",
        "org/repo",
        "main",
    )
    assert row.prompt_body == "fix the bug"
    assert row.tool_name is None
    assert json.loads(row.attrs) == {"extra_field": {"x": 1}}


def test_hook_row_columns_follow_the_vocabulary():
    assert HookRow._fields[7:-1] == v.HOOK_TEXT_FIELDS


def test_hook_payload_cannot_override_the_authenticated_sender():
    (row,) = decode_hook_events([_hook(user_email="spoofed@example.com")], "jwt@example.com", 0).hooks

    assert row.user_email == "jwt@example.com"
    assert json.loads(row.attrs) == {"user_email": "spoofed@example.com"}


def test_hook_shaped_log_keeps_unknown_attributes_apart_from_typed_columns():
    hook = {"event_type": "agent.tool.start", "session_id": "s", "user.email": "a@b.c", "user_email": "x@y.z"}

    (row,) = decode_otlp("logs", _logs_body([b.log_record(T, hook)]), "application/x-protobuf").hooks

    assert row.user_email == "a@b.c"
    assert json.loads(row.attrs) == {"user_email": "x@y.z"}


def test_hook_identity_ignores_the_sender():
    # The proxy re-sends a spool with whatever cookie it holds at that moment.
    first = decode_hook_events([_hook()], "a@example.com", 0).hooks[0]
    again = decode_hook_events([_hook()], "b@example.com", 0).hooks[0]

    assert first.h == again.h


def test_hook_values_under_typed_keys_are_stored_as_text() -> None:
    (row,) = decode_hook_events([_hook(tool_input={"command": "ls"}, reason=None, source=3)], "", 0).hooks

    assert (row.tool_input, row.reason, row.source) == ('{"command": "ls"}', None, "3")


def test_hook_without_a_timestamp_uses_the_receive_time():
    received = b.ns(T)

    (row,) = decode_hook_events([_hook(timestamp=None)], "", received).hooks

    assert row.ts == T


def test_hook_text_is_sanitised():
    (row,) = decode_hook_events([_hook(prompt_body=f"split {HIGH}", tool_output="a\x00b")], "", 0).hooks

    assert (row.prompt_body, row.tool_output) == (f"split {REPLACEMENT}", "ab")


def test_non_object_hook_lines_are_skipped():
    batch = decode_hook_events([["not", "an", "event"], "text", _hook()], "", 0)

    assert len(batch.hooks) == 1


def test_hook_with_integers_beyond_64_bits_still_decodes():
    (row,) = decode_hook_events([_hook(huge=2**70)], "", 0).hooks

    assert json.loads(row.attrs) == {"huge": 2**70}


def test_decoding_is_deterministic_for_the_ledger():
    body = _logs_body([b.log_record(T, _api_request())])

    assert (
        decode_otlp("logs", body, "application/x-protobuf").logs[0].h
        == otlp.decode_otlp("logs", body, "application/x-protobuf").logs[0].h
    )


# ── values PostgreSQL cannot store (one bad value must not block a client's spool) ──


def test_unsigned_values_beyond_bigint_are_unparseable():
    assert ch_uint(str(2**63 - 1)) == (2**63 - 1, True)
    assert ch_uint(str(2**63)) == (0, False)
    assert ch_uint(2**64 - 1) == (0, False)


@pytest.mark.parametrize("value", ["NaN", "nan", "inf", "-Infinity", "1e999", float("nan"), float("inf")])
def test_non_finite_floats_are_unparseable(value):
    assert ch_float(value) == (0.0, False)


def test_enum_values_beyond_smallint_are_stored_as_zero():
    record = b.log_record(T, _api_request())
    record.severity_number = 40_000  # an open proto3 enum carries any int32
    span = b.span("claude_code.tool", trace_id=TRACE, span_id=SPAN_ID, start=T, end=T, attrs={}, status_code=70_000)
    metric = b.sum_metric("claude_code.lines_of_code.count", [b.number_point(T, 3, {"type": "added"})])
    metric.sum.aggregation_temporality = 99_999

    (log,) = decode_otlp("logs", _logs_body([record]), "application/x-protobuf").logs
    (spn,) = decode_otlp("traces", b.traces_request(RESOURCE, [span]).SerializeToString(), "").spans
    (point,) = decode_otlp("metrics", b.metrics_request(RESOURCE, [metric]).SerializeToString(), "").metrics

    assert (log.severity, spn.status_code, point.temporality) == (0, 0, 0)


def test_a_span_duration_beyond_bigint_is_stored_as_zero():
    span = b.span("claude_code.tool", trace_id=TRACE, span_id=SPAN_ID, start=T, end=T, attrs={})
    span.start_time_unix_nano, span.end_time_unix_nano = 0, 2**64 - 1

    (row,) = decode_otlp("traces", b.traces_request(RESOURCE, [span]).SerializeToString(), "").spans

    assert row.duration_ns == 0


# ── implausible values: stored as sent, a few would overflow the sums behind every dashboard ──


def test_token_counts_no_request_comes_near_count_as_unparseable():
    attrs = _api_request(input_tokens=str(2**62), output_tokens=MAX_TOKENS + 1, cache_read_tokens=MAX_TOKENS)

    (row,) = decode_otlp("logs", _logs_body([b.log_record(T, attrs)]), "application/x-protobuf").logs

    assert (row.input_tokens, row.output_tokens, row.cache_read_tokens) == (0, 0, MAX_TOKENS)
    kept = json.loads(row.attrs)  # the original stays, as for any unparseable value
    assert (kept["input_tokens"], kept["output_tokens"]) == (str(2**62), MAX_TOKENS + 1)


@pytest.mark.parametrize("cost", [1e300, -1e300, MAX_COST_USD * 2])
def test_a_cost_no_request_comes_near_counts_as_unparseable(cost):
    (row,) = decode_otlp(
        "logs", _logs_body([b.log_record(T, _api_request(cost_usd=cost))]), "application/x-protobuf"
    ).logs

    assert row.cost_usd == 0.0
    assert json.loads(row.attrs)["cost_usd"] == cost


@pytest.mark.parametrize(("digits", "stored"), [("9" * 5000, 0), ("0" * 5000 + "1", 1)])
def test_digit_strings_int_would_refuse_never_fail_the_request(digits, stored):
    # int() refuses more than 4,300 digits, leading zeros included; "0…01" is still read as 1.
    (row,) = decode_otlp(
        "logs", _logs_body([b.log_record(T, _api_request(input_tokens=digits))]), "application/x-protobuf"
    ).logs

    assert row.input_tokens == stored


@pytest.mark.parametrize(("days", "kept"), [(29, True), (31, False)])
def test_a_span_longer_than_any_real_one_is_stored_with_zero_duration(days, kept):
    span = b.span("claude_code.tool", trace_id=TRACE, span_id=SPAN_ID, start=T, end=T + timedelta(days=days), attrs={})

    (row,) = decode_otlp("traces", b.traces_request(RESOURCE, [span]).SerializeToString(), "").spans

    assert row.duration_ns == (days * 86_400 * 10**9 if kept else 0)


def test_non_finite_metric_points_are_skipped():
    points = [b.number_point(T, float("nan"), {}), b.number_point(T, float("-inf"), {}), b.number_point(T, 2.5, {})]
    metric = b.sum_metric("claude_code.active_time.total", points)

    rows = decode_otlp("metrics", b.metrics_request(RESOURCE, [metric]).SerializeToString(), "").metrics

    assert [r.value for r in rows] == [2.5]


def test_a_json_request_nested_beyond_the_recursion_limit_is_an_invalid_payload():
    body = (
        '{"resourceLogs": [{"resource": {"attributes": [{"key": "k", "value": '
        + "[" * 100_000
        + "]" * 100_000
        + "}]}}]}"
    ).encode()

    with pytest.raises(InvalidTelemetryPayloadError):
        decode_otlp("logs", body, "application/json")


def test_a_value_a_decoder_cannot_convert_is_an_invalid_payload_not_a_server_error():
    # Any ValueError left in a decoder (a conversion Python refuses) must not answer 500.
    with (
        patch.dict(otlp._DECODERS, {"logs": MagicMock(side_effect=ValueError("cannot convert"))}),
        pytest.raises(InvalidTelemetryPayloadError),
    ):
        decode_otlp("logs", _logs_body([b.log_record(T, _api_request())]), "application/x-protobuf")


def test_a_hook_event_nested_beyond_the_recursion_limit_is_skipped():
    deep: list = []
    node = deep
    for _ in range(100_000):
        node.append([])
        node = node[0]
    events = [
        {"type": "agent.tool.start", "session_id": "s1", "tool_input": deep},
        {"type": "agent.tool.end", "session_id": "s1"},
    ]

    rows = decode_hook_events(events, "dev@example.com", b.ns(T)).hooks

    assert [r.event_type for r in rows] == ["agent.tool.end"]


# ── record identity ───────────────────────────────────────────────────────────


def test_record_identity_does_not_depend_on_where_its_parts_split():
    assert otlp.record_hash(b"ab", b"c") != otlp.record_hash(b"a", b"bc")


def test_spans_without_ids_are_told_apart_by_their_content():
    # Spans are identified by trace and span id; without them every span would be a duplicate of the first.
    first = b.span("claude_code.tool", trace_id=b"", span_id=b"", start=T, end=T, attrs={"tool_name": "Read"})
    second = b.span("claude_code.tool", trace_id=b"", span_id=b"", start=T, end=T, attrs={"tool_name": "Edit"})

    rows = decode_otlp("traces", b.traces_request(RESOURCE, [first, second]).SerializeToString(), "").spans

    assert len({r.h for r in rows}) == 2


def test_a_span_resent_with_other_attributes_is_the_same_span():
    first = b.span("claude_code.tool", trace_id=TRACE, span_id=SPAN_ID, start=T, end=T, attrs={"tool_name": "Read"})
    again = b.span("claude_code.tool", trace_id=TRACE, span_id=SPAN_ID, start=T, end=T, attrs={"tool_name": "Edit"})

    rows = decode_otlp("traces", b.traces_request(RESOURCE, [first, again]).SerializeToString(), "").spans

    assert rows[0].h == rows[1].h


def test_a_hook_timestamp_outside_the_datetime_range_is_stored_at_arrival():
    event = {"type": "agent.session.start", "session_id": "s1", "timestamp": "9999-12-31T23:59:59-01:00"}

    (row,) = decode_hook_events([event], "", b.ns(T)).hooks

    assert row.ts == T


# ── client text that becomes an index key (a btree row holds at most ~2.7 kB) ──

HUGE = "\N{EMOJI MODIFIER FITZPATRICK TYPE-1-2}" * 5_000  # 4 bytes per character


def _fits(text: str | None, limit: int) -> bool:
    return text is None or len(text.encode("utf-8")) <= limit


def test_oversized_index_keys_of_logs_are_cut_to_fit():
    attrs = _api_request(**{"session.id": HUGE, "user.email": HUGE, "model": HUGE, "query_source": HUGE})
    skill = {"event.name": "skill_activated", "session.id": HUGE, "skill.name": HUGE}
    command = {"event.name": "user_prompt", "session.id": HUGE, "command_name": HUGE}

    rows = decode_otlp("logs", _logs_body([b.log_record(T, a) for a in (attrs, skill, command)]), "").logs

    for row in rows:
        for text in (row.session_id, row.user_email, row.model, row.query_source, row.skill_name, row.command_name):
            assert _fits(text, otlp.MAX_KEY_BYTES)
        assert row.session_id  # cut, not dropped


def test_oversized_index_keys_of_spans_and_metrics_are_cut_to_fit():
    attrs = {
        k: HUGE
        for k in ("session.id", "user.email", "tool_name", "tool_use_id", "subagent_type", "skill_name", "success")
    }
    span = b.span(
        "claude_code.tool", trace_id=TRACE, span_id=SPAN_ID, start=T, end=T, attrs={**attrs, "file_path": HUGE}
    )
    metric = b.sum_metric(
        "claude_code.lines_of_code.count",
        [b.number_point(T, 3, {"session.id": HUGE, "model": HUGE, "user.email": HUGE})],
    )

    (spn,) = decode_otlp("traces", b.traces_request(RESOURCE, [span]).SerializeToString(), "").spans
    (point,) = decode_otlp("metrics", b.metrics_request(RESOURCE, [metric]).SerializeToString(), "").metrics

    for text in (
        spn.session_id,
        spn.user_email,
        spn.tool_name,
        spn.tool_use_id,
        spn.subagent_type,
        spn.skill_name,
        spn.success,
    ):
        assert _fits(text, otlp.MAX_KEY_BYTES)
    assert _fits(spn.file_path, otlp.MAX_PATH_BYTES) and len(spn.file_path) > 100
    assert _fits(point.session_id, otlp.MAX_KEY_BYTES) and _fits(point.model, otlp.MAX_KEY_BYTES)


def test_an_oversized_hook_session_id_is_cut_to_fit():
    (row,) = decode_hook_events([{"type": "agent.session.start", "session_id": HUGE}], "", b.ns(T)).hooks

    assert _fits(row.session_id, otlp.MAX_KEY_BYTES) and row.session_id


def test_a_hook_event_with_a_huge_integer_and_non_finite_numbers_is_stored_as_valid_json():
    # orjson cannot take the integer, and the fallback must still write JSON that jsonb accepts.
    event = {"type": "agent.tool.start", "session_id": "s1", "big": 2**70, "ratio": float("nan"), "inf": float("-inf")}

    (row,) = decode_hook_events([event], "", b.ns(T)).hooks

    def reject(constant):
        raise ValueError(constant)

    attrs = json.loads(row.attrs, parse_constant=reject)  # NaN / Infinity are not JSON
    assert attrs == {"big": 2**70, "ratio": None, "inf": None}


@pytest.mark.parametrize(
    "value, expected",
    [
        (12, (12, False)),
        ("12", (12, False)),
        ("1000000000", (1000000000, False)),  # 10^9 is exactly the cap
        (1000000001, (0, True)),  # above the cap
        ("99999999999999999999", (0, True)),  # above 2^63, still (0, True)
        (None, (None, False)),
        ("", (None, False)),
        ("abc", (None, True)),
        ("-1", (None, True)),
        (-1, (None, True)),
        ("12.5", (None, True)),
        (12.5, (None, True)),
        (True, (None, True)),
        ([1], (None, True)),
        ({"a": 1}, (None, True)),
    ],
)
def test_usage_int_parses_digits_and_flags_the_rest(value: object, expected: object) -> None:
    assert usage_int(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (True, (True, False)),
        ("TRUE", (True, False)),
        ("true", (True, False)),
        ("False", (False, False)),
        (None, (None, False)),
        ("", (None, False)),
        (1, (None, True)),
        (0, (None, True)),
        ("yes", (None, True)),
    ],
)
def test_usage_bool_parses_only_true_and_false(value: object, expected: object) -> None:
    assert usage_bool(value) == expected


# --- sdlc-analytics events on /event-hooks (usage_requests routing, event_id hash) ---

# Captured on the unmodified branch (before UsageRow existed), re-captured after the codemie_cli_version rename:
# decode_hook_events([without(skill_dispatch(), "event_id")], "jwt@example.com", 0).hooks[0].h
OLD_FORMAT_SKILL_DISPATCH_H = 2558847118315968949


def _decode(*events: dict[str, object], email: str = "jwt@example.com") -> DecodedBatch:
    return decode_hook_events(list(events), email, 0)


def test_usage_request_goes_to_usage_only() -> None:
    batch = _decode(usage_request())

    assert batch.hooks == []
    assert len(batch.usage) == 1
    assert batch.record_count == 1


def test_usage_request_fills_typed_columns_and_keeps_the_rest_in_attrs() -> None:
    (row,) = _decode(usage_request(unknown_key={"a": [1]})).usage

    assert isinstance(row, UsageRow)
    assert row.ts == datetime(2026, 9, 29, 10, 59, 41, 512000, tzinfo=timezone.utc)
    assert row.day == date(2026, 9, 29)
    assert (row.session_id, row.user_email) == ("3f6c0a52", "jwt@example.com")
    assert (row.request_id, row.message_id, row.model_raw, row.model) == (
        "req_011CTx9aB",
        "msg_01H7Yq",
        "claude-opus-5-5-20260101",
        "claude-opus-5-5",
    )
    assert (row.input_tokens, row.cache_creation_1h_tokens, row.cache_read_tokens) == (12, 2500, 48000)
    assert (row.output_tokens, row.thinking_tokens, row.web_search_requests) == (295, 289, 0)
    assert (row.scope_kind, row.scope_name, row.agent_id, row.agent_type) == ("main", None, None, None)
    assert (row.stop_reason, row.is_api_error, row.git_branch) == ("tool_use", False, "feature/EPMCDME-15302")
    attrs = json.loads(row.attrs)
    assert attrs["event_id"] == "usage:3f6c0a52:req_011CTx9aB"
    assert attrs["schema_version"] == 2
    assert attrs["unknown_key"] == {"a": [1]}
    for gone in ("type", "timestamp", "session_id", "input_tokens", "model", "is_api_error", "request_id"):
        assert gone not in attrs


@pytest.mark.parametrize(
    ("event", "event_type"),
    [
        (subagent_usage(), "agent.subagent.usage"),
        (session_summary(), "agent.session.summary"),
        (session_env(), "agent.session.env"),
        (skill_dispatch(), "agent.skill.dispatch"),
        (git_snapshot(), "agent.git.snapshot"),
    ],
)
def test_other_sdlc_events_go_to_hooks_only(event: dict[str, object], event_type: str) -> None:
    batch = _decode({**event, "future_field": True})

    assert batch.usage == []
    (row,) = batch.hooks
    assert row.event_type == event_type
    attrs = json.loads(row.attrs)
    assert attrs["future_field"] is True
    assert attrs["schema_version"] == 2
    assert attrs["event_id"] == event["event_id"]


def test_subagent_usage_keeps_json_types_in_attrs_and_fills_typed_columns() -> None:
    (row,) = _decode(subagent_usage()).hooks

    assert (row.agent_id, row.agent_type, row.tool_use_id) == ("agent-7", "Explore", "toolu_01Abc")
    attrs = json.loads(row.attrs)
    assert attrs["api_calls"] == 4
    assert attrs["tools"] == {"Read": {"calls": 6, "errors": 0}, "Bash": {"calls": 3, "errors": 1}}
    assert attrs["commands"] == ["review"]
    assert "agent_id" not in attrs


def test_envelope_only_usage_request_leaves_every_other_column_null() -> None:
    (row,) = _decode(minimal("agent.usage.request", "s1", "2026-09-29T10:00:00Z")).usage

    assert row.session_id == "s1"
    assert row.ts == datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
    others = row._replace(day=None, h=None, ts=None, session_id=None, user_email=None)
    assert all(value is None for value in others)


def test_otlp_envelope_only_usage_request_leaves_attrs_null() -> None:
    (row,) = _decode_logs(minimal("agent.usage.request", "s1", "2026-09-29T10:00:00Z")).usage

    assert row.session_id == "s1"
    others = row._replace(day=None, h=None, ts=None, session_id=None, user_email=None)
    assert all(value is None for value in others)


def test_otlp_usage_request_keeps_a_non_empty_prompt_id_in_attrs() -> None:
    event = {**minimal("agent.usage.request", "s1", "2026-09-29T10:00:00Z"), "prompt_id": "p-7"}

    (row,) = _decode_logs(event).usage

    assert json.loads(row.attrs) == {"prompt_id": "p-7"}


def test_usage_request_digit_string_is_stored_as_integer() -> None:
    (row,) = _decode(usage_request(input_tokens="12")).usage

    assert row.input_tokens == 12
    assert "input_tokens" not in json.loads(row.attrs)


def test_usage_request_unparseable_count_is_null_and_keeps_original() -> None:
    (row,) = _decode(usage_request(input_tokens="abc")).usage

    assert row.input_tokens is None
    assert json.loads(row.attrs)["input_tokens"] == "abc"


def test_usage_request_count_above_the_cap_is_zero_and_keeps_original() -> None:
    (row,) = _decode(usage_request(output_tokens=2 * 10**9)).usage

    assert row.output_tokens == 0
    assert json.loads(row.attrs)["output_tokens"] == 2 * 10**9


def test_usage_request_unparseable_flag_is_null_and_keeps_original() -> None:
    (row,) = _decode(usage_request(is_api_error="yes")).usage

    assert row.is_api_error is None
    assert json.loads(row.attrs)["is_api_error"] == "yes"


def test_usage_request_text_is_cleaned_cut_and_empty_becomes_null() -> None:
    (row,) = _decode(usage_request(model="a\x00b", stop_reason="", speed="x" * 300)).usage

    assert row.model == "ab"
    assert row.stop_reason is None
    assert row.speed == "x" * 256


def test_usage_request_sender_is_cut_like_every_other_text_column() -> None:
    # On the OTLP route the sender is the client's `user.email` attribute: like the other text columns
    # it is cleaned and cut to 256 bytes on both routes.
    (hooked,) = _decode(usage_request(), email="a\x00" + "x" * 300).usage
    (logged,) = _decode_logs({**usage_request(), "user.email": "y" * 300}).usage

    assert hooked.user_email == "a" + "x" * 255
    assert logged.user_email == "y" * 256


def test_same_event_id_with_different_fields_or_email_gives_the_same_hash() -> None:
    first = _decode(usage_request(), email="a@example.com").usage[0]
    changed = _decode(usage_request(input_tokens=99, extra="x"), email="b@example.com").usage[0]

    assert first.h == changed.h


def test_event_id_hash_is_the_same_for_hook_events_with_a_different_field_set() -> None:
    first = _decode(session_summary()).hooks[0]
    changed = _decode({**session_summary(), "cwd": "/elsewhere"}, email="other@example.com").hooks[0]

    assert first.h == changed.h


def test_sessions_sharing_an_event_id_give_different_hashes() -> None:
    one = _decode(usage_request(event_id="shared")).usage[0]
    two = _decode(usage_request(event_id="shared", session_id="another")).usage[0]

    assert one.h != two.h


def test_types_sharing_an_event_id_give_different_hashes() -> None:
    usage = _decode(usage_request(event_id="shared")).usage[0]
    summary = _decode(session_summary(event_id="shared")).hooks[0]

    assert usage.h != summary.h


def test_empty_event_id_falls_back_to_the_content_hash() -> None:
    with_empty = _decode(usage_request(event_id="")).usage[0]
    with_other_content = _decode(usage_request(event_id="", input_tokens=99)).usage[0]

    assert with_empty.h != with_other_content.h


def test_old_format_event_keeps_its_hash_and_row() -> None:
    (row,) = _decode(without(skill_dispatch(), "event_id")).hooks

    assert row.h == OLD_FORMAT_SKILL_DISPATCH_H
    assert (row.session_id, row.event_type, row.user_email, row.skill_name) == (
        "3f6c0a52",
        "agent.skill.dispatch",
        "jwt@example.com",
        "brainstorming",
    )
    assert "event_id" not in json.loads(row.attrs)


def test_dimension_hook_types_are_unchanged() -> None:
    assert len(v.DIMENSION_HOOK_TYPES) == 13


# ── the six events as OTLP log records ────────────────────────────────────────

RECORD_T = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _decode_logs(event: dict[str, object], record_ts: datetime | None = RECORD_T) -> DecodedBatch:
    return otlp.decode_logs(b.logs_request({"service.name": "claude-code"}, [as_otlp_log(event, record_ts)]))


def test_otlp_usage_request_goes_to_usage_only_with_the_attribute_email() -> None:
    batch = _decode_logs({**usage_request(), "user.email": "otel@example.com"})

    assert (len(batch.usage), batch.hooks, batch.logs) == (1, [], [])
    assert batch.usage[0].user_email == "otel@example.com"


@pytest.mark.parametrize(
    "event", [session_summary(), session_env(), skill_dispatch(), git_snapshot(), subagent_usage()]
)
def test_otlp_other_new_events_go_to_hooks(event: dict[str, object]) -> None:
    batch = _decode_logs(event)

    assert (len(batch.hooks), batch.usage, batch.logs) == (1, [], [])


@pytest.mark.parametrize(
    "event, table, expected_ts",
    [
        (usage_request(), "usage", datetime(2026, 9, 29, 10, 59, 41, 512000, tzinfo=timezone.utc)),
        (session_summary(), "hooks", datetime(2026, 9, 29, 11, 30, tzinfo=timezone.utc)),
        (skill_dispatch(), "hooks", datetime(2026, 9, 29, 10, 5, tzinfo=timezone.utc)),
    ],
)
def test_one_event_through_both_paths_gives_equal_hash_and_time(
    event: dict[str, object], table: str, expected_ts: datetime
) -> None:
    via_hooks = getattr(decode_hook_events([event], "u@example.com", 0), table)[0]
    via_otlp = getattr(_decode_logs(event), table)[0]

    assert via_otlp.h == via_hooks.h
    assert (via_otlp.ts, via_otlp.day) == (via_hooks.ts, via_hooks.day)
    assert via_otlp.ts == expected_ts  # the timestamp field, not RECORD_T


def test_otlp_hook_without_event_id_keeps_the_record_time_and_content_hash() -> None:
    event = without(skill_dispatch(), "event_id")

    (row,) = _decode_logs(event).hooks

    assert row.ts == RECORD_T
    assert json.loads(row.attrs)["timestamp"] == "2026-09-29T10:05:00.000Z"
    # Captured once on 198bddcce, re-captured after the codemie_cli_version rename: otlp.decode_logs of this record, `.hooks[0].h`.
    assert row.h == -4943635364139866433


@pytest.mark.parametrize("event", [skill_dispatch(timestamp="not-a-time"), usage_request(timestamp="not-a-time")])
def test_otlp_unparseable_timestamp_falls_back_to_the_record_time(event: dict[str, object]) -> None:
    batch = _decode_logs(event)

    (row,) = batch.hooks + batch.usage
    assert row.ts == RECORD_T


def test_api_request_request_id_is_a_typed_column_and_not_in_attrs() -> None:
    (row,) = otlp.decode_logs(b.logs_request({}, [b.log_record(T, _api_request(request_id="req_1"))])).logs

    assert row.request_id == "req_1"
    assert "request_id" not in json.loads(row.attrs)


def test_api_request_empty_request_id_is_null() -> None:
    (row,) = otlp.decode_logs(b.logs_request({}, [b.log_record(T, _api_request(request_id=""))])).logs

    assert row.request_id is None


def test_log_events_tokens_still_store_unparseable_text_as_zero() -> None:
    (row,) = otlp.decode_logs(b.logs_request({}, [b.log_record(T, _api_request(input_tokens="abc"))])).logs

    assert row.input_tokens == 0


def test_empty_prompt_id_gives_equal_usage_attrs_on_both_paths() -> None:
    event = {**minimal("agent.usage.request", "s1", "2026-09-29T10:00:00Z"), "prompt_id": ""}

    (via_hooks,) = _decode(event).usage
    (via_otlp,) = _decode_logs(event).usage

    assert via_hooks.attrs == via_otlp.attrs
    assert via_hooks.attrs is None


def test_hooks_usage_request_keeps_a_non_empty_prompt_id_in_attrs() -> None:
    event = {**minimal("agent.usage.request", "s1", "2026-09-29T10:00:00Z"), "prompt_id": "p-7"}

    (row,) = _decode(event).usage

    assert json.loads(row.attrs) == {"prompt_id": "p-7"}
