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

"""The decoder reports every explicit project value and the wire record count (EPMCDME-16025)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from google.protobuf import json_format

from codemie.repository.cli_analytics.postgres.otlp import DecodedBatch, decode_hook_events, decode_otlp
from tests.codemie.repository.cli_analytics.support import otlp_builders as b
from tests.codemie.repository.cli_analytics.support.hook_event_builders import as_otlp_log, usage_request

T = datetime(2026, 9, 23, 10, 15, 30, tzinfo=timezone.utc)
KEY = "codemie_project_name"
TRACE = bytes.fromhex("0af7651916cd43dd8448eb211c80319c")
SPAN_ID = bytes.fromhex("b7ad6b7169203331")
FORMATS = ["protobuf", "json"]


def decode(signal: str, request, fmt: str) -> DecodedBatch:
    if fmt == "protobuf":
        return decode_otlp(signal, request.SerializeToString(), "application/x-protobuf")
    return decode_otlp(signal, json.dumps(json_format.MessageToDict(request)).encode(), "application/json")


def res(project: str | None = None) -> dict:
    return {"service.name": "claude-code", **({KEY: project} if project is not None else {})}


def log(project: str | None = None, **attrs):
    return b.log_record(
        T, {"event.name": "api_request", "session.id": "s", **({KEY: project} if project else {}), **attrs}
    )


def tspan(project: str | None = None):
    return b.span(
        "claude_code.tool",
        trace_id=TRACE,
        span_id=SPAN_ID,
        start=T,
        end=T,
        attrs={"session.id": "s", **({KEY: project} if project else {})},
    )


def point(project: str | None = None, value: float = 1.0):
    return b.number_point(T, value, {KEY: project} if project else {})


# ── explicit projects ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize(
    ("signal", "request_"),
    [
        ("logs", b.logs_request(res("p1"), [log()])),
        ("traces", b.traces_request(res("p1"), [tspan()])),
        ("metrics", b.metrics_request(res("p1"), [b.sum_metric("m", [point()])])),
    ],
    ids=["logs", "traces", "metrics"],
)
def test_resource_attribute_is_collected(signal, request_, fmt):
    assert decode(signal, request_, fmt).explicit_projects == {"p1"}


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize(
    "record",
    [
        log("p2"),
        b.log_record(T, {"event.name": "tool_result", "session.id": "s", KEY: "p2", "event_type": "agent.tool.end"}),
        as_otlp_log(usage_request(**{KEY: "p2"}), T),
    ],
    ids=["plain-log", "hook-log", "usage-log"],
)
def test_log_record_attribute_is_collected(record, fmt):
    assert decode("logs", b.logs_request(res(), [record]), fmt).explicit_projects == {"p2"}


@pytest.mark.parametrize("fmt", FORMATS)
def test_span_attribute_is_collected(fmt):
    assert decode("traces", b.traces_request(res(), [tspan("p3")]), fmt).explicit_projects == {"p3"}


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize(
    "metric",
    [
        b.sum_metric("m", [point("p4")]),
        b.gauge_metric("m", [point("p4")]),
        b.histogram_metric("m", {KEY: "p4"}),
        b.exponential_histogram_metric("m", {KEY: "p4"}),
        b.summary_metric("m", {KEY: "p4"}),
        b.sum_metric("m", [point("p4", float("nan"))]),
    ],
    ids=["sum", "gauge", "histogram", "exp-histogram", "summary", "nan-sum"],
)
def test_metric_point_attribute_is_collected_even_for_skipped_points(metric, fmt):
    assert decode("metrics", b.metrics_request(res(), [metric]), fmt).explicit_projects == {"p4"}


@pytest.mark.parametrize("fmt", FORMATS)
def test_mixed_values_across_sources_are_all_reported(fmt):
    request = b.logs_request(res("p1"), [log("p2"), log("p2")])

    assert decode("logs", request, fmt).explicit_projects == {"p1", "p2"}


@pytest.mark.parametrize("fmt", FORMATS)
def test_values_are_kept_as_received_and_blank_ones_ignored(fmt):
    request = b.logs_request(res("  "), [log("  p1  "), log("")])

    assert decode("logs", request, fmt).explicit_projects == {"  p1  "}


@pytest.mark.parametrize("fmt", FORMATS)
def test_no_project_anywhere_gives_an_empty_set(fmt):
    assert decode("logs", b.logs_request(res(), [log()]), fmt).explicit_projects == set()
    assert decode("traces", b.traces_request(res(), [tspan()]), fmt).explicit_projects == set()
    assert decode("metrics", b.metrics_request(res(), [b.sum_metric("m", [point()])]), fmt).explicit_projects == set()


def test_ndjson_hook_and_usage_events_are_collected():
    events = [
        {"type": "agent.tool.end", "session_id": "s1", KEY: "p5"},
        {**usage_request(), KEY: "p6"},
        {"type": "agent.tool.end", "session_id": "s1"},
    ]

    batch = decode_hook_events(events, "dev@example.com", b.ns(T))

    assert batch.explicit_projects == {"p5", "p6"}


def test_ndjson_event_skipped_for_nesting_still_reports_its_project():
    deep: list = []
    node = deep
    for _ in range(100_000):
        node.append([])
        node = node[0]
    events = [{"type": "agent.tool.start", "session_id": "s1", KEY: "p7", "tool_input": deep}]

    batch = decode_hook_events(events, "dev@example.com", b.ns(T))

    assert batch.hooks == []
    assert batch.explicit_projects == {"p7"}


# ── wire count ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", FORMATS)
def test_wire_count_of_logs_and_spans(fmt):
    assert decode("logs", b.logs_request(res(), [log(), log(), log()]), fmt).wire_count == 3
    assert decode("traces", b.traces_request(res(), [tspan(), tspan()]), fmt).wire_count == 2


@pytest.mark.parametrize("fmt", FORMATS)
def test_wire_count_covers_every_metric_kind_and_unusable_points(fmt):
    metrics = [
        b.sum_metric("a", [point(), point(value=float("nan"))]),
        b.gauge_metric("b", [point()]),
        b.histogram_metric("c"),
        b.exponential_histogram_metric("d"),
        b.summary_metric("e"),
        b.Metric(name="no-data"),
    ]

    batch = decode("metrics", b.metrics_request(res(), metrics), fmt)

    assert batch.wire_count == 6  # 2 + 1 + 1 + 1 + 1, the metric without data adds none
    assert len(batch.metrics) == 2  # only the finite sum and gauge points are stored


def test_wire_count_of_hook_events_includes_skipped_ones():
    deep: list = []
    node = deep
    for _ in range(100_000):
        node.append([])
        node = node[0]
    events = [
        {"type": "agent.tool.end", "session_id": "s1"},
        {"type": "agent.tool.start", "session_id": "s1", "tool_input": deep},
        "not-an-object",
    ]

    assert decode_hook_events(events, "dev@example.com", b.ns(T)).wire_count == 2
