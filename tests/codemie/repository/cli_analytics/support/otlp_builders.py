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

"""Builders for real OTLP export requests, shaped like Claude Code's telemetry."""

from __future__ import annotations

from datetime import datetime, timezone

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.logs.v1.logs_pb2 import LogRecord
from opentelemetry.proto.metrics.v1.metrics_pb2 import AggregationTemporality, Metric, NumberDataPoint
from opentelemetry.proto.trace.v1.trace_pb2 import Span

SCOPE = "com.anthropic.claude_code"


def ns(dt: datetime) -> int:
    """Nanoseconds since the epoch; a naive datetime is taken as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp()) * 1_000_000_000 + dt.microsecond * 1_000


def any_value(value: object) -> AnyValue:
    av = AnyValue()
    if isinstance(value, bool):
        av.bool_value = value
    elif isinstance(value, int):
        av.int_value = value
    elif isinstance(value, float):
        av.double_value = value
    elif isinstance(value, bytes):
        av.bytes_value = value
    elif isinstance(value, list):
        av.array_value.values.extend(any_value(v) for v in value)
    elif isinstance(value, dict):
        av.kvlist_value.values.extend(KeyValue(key=k, value=any_value(v)) for k, v in value.items())
    else:
        av.string_value = str(value)
    return av


def key_values(attrs: dict) -> list[KeyValue]:
    return [KeyValue(key=k, value=any_value(v)) for k, v in attrs.items()]


def log_record(ts: datetime | None, attrs: dict, *, trace_id=b"", span_id=b"", severity=9, observed=None) -> LogRecord:
    return LogRecord(
        time_unix_nano=ns(ts) if ts else 0,
        observed_time_unix_nano=ns(observed) if observed else 0,
        severity_number=severity,
        attributes=key_values(attrs),
        trace_id=trace_id,
        span_id=span_id,
    )


def logs_request(resource: dict, records: list[LogRecord], scope: str = SCOPE) -> ExportLogsServiceRequest:
    req = ExportLogsServiceRequest()
    rl = req.resource_logs.add()
    rl.resource.attributes.extend(key_values(resource))
    sl = rl.scope_logs.add()
    sl.scope.name = scope
    sl.log_records.extend(records)
    return req


def span(
    name: str,
    *,
    trace_id: bytes,
    span_id: bytes,
    start: datetime,
    end: datetime,
    attrs: dict,
    parent_span_id: bytes = b"",
    status_code: int = 0,
) -> Span:
    sp = Span(
        name=name,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        start_time_unix_nano=ns(start),
        end_time_unix_nano=ns(end),
        attributes=key_values(attrs),
    )
    sp.status.code = status_code
    return sp


def traces_request(resource: dict, spans: list[Span], scope: str = SCOPE) -> ExportTraceServiceRequest:
    req = ExportTraceServiceRequest()
    rs = req.resource_spans.add()
    rs.resource.attributes.extend(key_values(resource))
    ss = rs.scope_spans.add()
    ss.scope.name = scope
    ss.spans.extend(spans)
    return req


def number_point(ts: datetime, value: float | int, attrs: dict, start: datetime | None = None) -> NumberDataPoint:
    dp = NumberDataPoint(time_unix_nano=ns(ts), attributes=key_values(attrs))
    if start is not None:
        dp.start_time_unix_nano = ns(start)
    if isinstance(value, int):
        dp.as_int = value
    else:
        dp.as_double = value
    return dp


def sum_metric(name: str, points: list[NumberDataPoint], monotonic: bool = True) -> Metric:
    m = Metric(name=name)
    m.sum.aggregation_temporality = AggregationTemporality.AGGREGATION_TEMPORALITY_DELTA
    m.sum.is_monotonic = monotonic
    m.sum.data_points.extend(points)
    return m


def gauge_metric(name: str, points: list[NumberDataPoint]) -> Metric:
    m = Metric(name=name)
    m.gauge.data_points.extend(points)
    return m


def histogram_metric(name: str) -> Metric:
    m = Metric(name=name)
    dp = m.histogram.data_points.add()
    dp.count = 1
    return m


def metrics_request(resource: dict, metrics: list[Metric], scope: str = SCOPE) -> ExportMetricsServiceRequest:
    req = ExportMetricsServiceRequest()
    rm = req.resource_metrics.add()
    rm.resource.attributes.extend(key_values(resource))
    sm = rm.scope_metrics.add()
    sm.scope.name = scope
    sm.metrics.extend(metrics)
    return req
