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

"""OTLP export requests and plugin hook events → typed PostgreSQL rows (pure, no I/O).

Stored values follow these rules:
- promoted string columns render an attribute as text: a string as is (bytes as base64),
  a bool as "true"/"false", a double in shortest decimal form (5.0 is "5"), and a map or
  list as compact JSON with sorted keys (see `as_str`);
- numeric columns parse leniently (an unparseable value becomes 0), and an
  unparseable original stays in the residual `attrs` JSON;
- a log record's `session_id` is `session.id`, else `session_id`, and its `prompt_id` falls
  back to `prompt.id` (spans and metrics read `session.id` only, by design);
  a span without `user.email` takes the resource's.
- An OTel log record carrying `event_type` is a hook event (it feeds the hook-event table).

Every string is sanitised for PostgreSQL, which rejects NUL and unpaired surrogates in
`text` and `jsonb`. Both occur in real plugin payloads: the plugin cuts hook text at a
UTF-16 length, which can split an emoji.

Each row carries `(day, h)`: the record's UTC day and a 64-bit hash of its identity, the
key of the idempotency ledger. The identity is the record itself, never the request,
because the plugin re-sends a growing batch after any failure.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, NamedTuple

import orjson
from google.protobuf import json_format
from google.protobuf.message import DecodeError, Message
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from codemie.repository.cli_analytics.hook_events import safe_str, timestamp_to_ns
from codemie.repository.cli_analytics.ports import (
    InvalidTelemetryPayloadError,
    OtlpSignal,
    UnsupportedTelemetryContentTypeError,
)
from codemie.repository.cli_analytics.vocabulary import (
    EVENT_KIND,
    HOOK_KNOWN_KEYS,
    HOOK_TEXT_FIELDS,
    SESSION_SCOPED_KEYS,
    SPAN_KIND,
    EventKind,
    SpanKind,
)

logger = logging.getLogger(__name__)

JSON_MEDIA_TYPE = "application/json"
PROTOBUF_MEDIA_TYPES = frozenset({"", "application/x-protobuf", "application/protobuf"})
_SESSION_ID_KEY = "session.id"
_USER_EMAIL_KEY = "user.email"

_REQUEST_TYPES: dict[str, type[Message]] = {
    "logs": ExportLogsServiceRequest,
    "metrics": ExportMetricsServiceRequest,
    "traces": ExportTraceServiceRequest,
}
_HEX_ID_KEYS = frozenset({"traceId", "spanId", "parentSpanId", "trace_id", "span_id", "parent_span_id"})
# Column ranges: a value outside them is stored as 0, not rejected, because one unstorable
# value would fail the whole request and the plugin would re-send it forever.
_BIGINT_LIMIT = 2**63
_SMALLINT_LIMIT = 2**15
# For the same reason, client text that becomes part of an index or primary key is cut to fit
# a btree row (about 2.7 kB, all key and INCLUDE columns together). Real values are far shorter.
MAX_KEY_BYTES = 256
MAX_PATH_BYTES = 1024
# Plausibility ceilings: a larger value counts as unparseable (0, the original kept in attrs).
# No real request comes near them, and they keep every sum behind the dashboards and rollups
# far inside bigint's range, whatever a client sends.
MAX_TOKENS = 10**9  # per API request and token kind
MAX_COST_USD = 1e6  # per API request
MAX_SPAN_DURATION_NS = 30 * 86_400 * 10**9


# ── rows ──────────────────────────────────────────────────────────────────────


class LogRow(NamedTuple):
    day: date
    h: int
    ts: datetime
    session_id: str
    event_kind: int
    event_name: str | None
    prompt_id: str | None
    user_email: str | None
    model: str | None
    query_source: str | None
    request_id: str | None
    skill_name: str | None
    command_name: str | None
    trace_id: bytes | None
    span_id: bytes | None
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_creation_tokens: int | None
    severity: int
    resource_id: int
    attrs: str | None


class HookRow(NamedTuple):
    day: date
    h: int
    ts: datetime
    session_id: str
    event_type: str
    prompt_id: str | None
    user_email: str | None
    developer_name: str | None
    codemie_project_name: str | None
    cwd: str | None
    git_branch: str | None
    repo_remote: str | None
    permission_mode: str | None
    source: str | None
    effort: str | None
    tool_name: str | None
    tool_use_id: str | None
    tool_input: str | None
    tool_output: str | None
    error_message: str | None
    error_type: str | None
    reason: str | None
    agent_id: str | None
    agent_type: str | None
    trigger: str | None
    denial_reason: str | None
    notification_type: str | None
    prompt_body: str | None
    skill_name: str | None
    attrs: str | None


class SpanRow(NamedTuple):
    day: date
    h: int
    ts: datetime
    duration_ns: int
    trace_id: bytes
    span_id: bytes
    parent_span_id: bytes | None
    span_kind: int
    span_name: str
    session_id: str
    user_email: str | None
    tool_name: str | None
    tool_use_id: str | None
    file_path: str | None
    subagent_type: str | None
    skill_name: str | None
    success: str | None
    status_code: int
    resource_id: int
    attrs: str | None


class MetricRow(NamedTuple):
    day: date
    h: int
    ts: datetime
    start_ts: datetime | None
    metric_name: str
    value: float
    session_id: str
    user_email: str | None
    model: str | None
    type: str | None
    temporality: int
    is_monotonic: bool
    resource_id: int
    attrs: str | None


class UsageRow(NamedTuple):
    day: date
    h: int
    ts: datetime
    session_id: str
    user_email: str | None
    request_id: str | None
    message_id: str | None
    agent_id: str | None
    agent_type: str | None
    scope_kind: str | None
    scope_name: str | None
    model_raw: str | None
    model: str | None
    speed: str | None
    inference_geo: str | None
    service_tier: str | None
    input_tokens: int | None
    cache_creation_5m_tokens: int | None
    cache_creation_1h_tokens: int | None
    cache_read_tokens: int | None
    output_tokens: int | None
    thinking_tokens: int | None
    web_search_requests: int | None
    web_fetch_requests: int | None
    stop_reason: str | None
    is_api_error: bool | None
    git_branch: str | None
    attrs: str | None


@dataclass
class DecodedBatch:
    logs: list[LogRow] = field(default_factory=list)
    hooks: list[HookRow] = field(default_factory=list)
    usage: list[UsageRow] = field(default_factory=list)
    spans: list[SpanRow] = field(default_factory=list)
    metrics: list[MetricRow] = field(default_factory=list)
    # resource_id -> (service.name, attributes as JSON)
    resources: dict[int, tuple[str | None, str]] = field(default_factory=dict)
    # session_id -> session-constant attributes (user.id, organization.id, ...)
    session_attrs: dict[str, dict[str, Any]] = field(default_factory=lambda: defaultdict(dict))

    @property
    def record_count(self) -> int:
        return len(self.logs) + len(self.hooks) + len(self.usage) + len(self.spans) + len(self.metrics)


# ── value rendering ───────────────────────────────────────────────────────────


def any_to_py(av: Any) -> Any:
    which = av.WhichOneof("value")
    if which == "string_value":
        return av.string_value
    if which == "bool_value":
        return av.bool_value
    if which == "int_value":
        return av.int_value
    if which == "double_value":
        return av.double_value
    if which == "array_value":
        return [any_to_py(x) for x in av.array_value.values]
    if which == "kvlist_value":
        return {kv.key: any_to_py(kv.value) for kv in av.kvlist_value.values}
    if which == "bytes_value":
        return base64.b64encode(av.bytes_value).decode()
    return None


def attrs_to_py(key_values: Any) -> dict[str, Any]:
    return {kv.key: any_to_py(kv.value) for kv in key_values}


def go_float(x: float) -> str:
    """A double as text: the shortest decimal that reads back as `x`, never in exponent form
    (5.0 is "5", 1e21 is "1000000000000000000000"); NaN and infinities as "NaN", "+Inf", "-Inf"."""
    if math.isnan(x):
        return "NaN"
    if x in (float("inf"), float("-inf")):
        return "+Inf" if x > 0 else "-Inf"
    return format(Decimal(repr(x)).normalize(), "f")


def _finite(value: Any) -> Any:
    """NaN and infinities as null, as orjson writes them: JSON has no such numbers."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, list):
        return [_finite(v) for v in value]
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    return value


def json_text(value: Any, sort_keys: bool = False) -> str:
    """Compact JSON; orjson for speed, the standard library for integers beyond 64 bits."""
    try:
        return orjson.dumps(value, option=orjson.OPT_SORT_KEYS if sort_keys else 0).decode()
    except TypeError:
        return json.dumps(
            _finite(value), sort_keys=sort_keys, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )


def as_str(value: Any) -> str:
    """An attribute value as text: '' for none, a string as is, "true"/"false", an integer in
    decimal, a double by `go_float`, anything else as compact JSON with sorted keys."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return go_float(value)
    return json_text(value, sort_keys=True)


def ch_uint(value: Any, limit: int = _BIGINT_LIMIT - 1) -> tuple[int, bool]:
    """The value's text (`as_str`) as a non-negative integer, else 0; the flag tells whether the
    value was parseable or absent.

    Values above `limit` (by default a signed bigint's, the column type) count as unparseable.
    """
    text = as_str(value)
    if text.isascii() and text.isdigit():
        # Measured before int(), which refuses more than 4,300 digits, leading zeros included.
        digits = text.lstrip("0") or "0"
        if len(digits) <= len(str(limit)) and int(digits) <= limit:
            return int(digits), True
    return 0, text == ""


def ch_float(value: Any, limit: float | None = None) -> tuple[float, bool]:
    """The value's text (`as_str`) as a float, else 0.0; the flag tells whether the value was
    parseable or absent.

    NaN and infinities count as unparseable: summed into a rollup they would make every
    total of the window NaN, which a JSON response cannot carry. So do values beyond ±`limit`.
    """
    text = as_str(value)
    try:
        number = float(text)
    except ValueError:
        return 0.0, text == ""
    if not math.isfinite(number) or (limit is not None and abs(number) > limit):
        return 0.0, False
    return number, True


def _tokens(value: Any) -> tuple[int | float, bool]:
    return ch_uint(value, MAX_TOKENS)


def usage_int(value: Any) -> tuple[int | None, bool]:
    """Typed usage_requests count: (stored value, keep the original in attrs).

    Absent gives (None, False); ASCII digits within MAX_TOKENS are stored; digits above it store 0
    and keep the original; anything else stores NULL and keeps the original.
    """
    text = as_str(value)
    if text == "":
        return None, False
    if text.isascii() and text.isdigit():
        number, parsed = ch_uint(text, MAX_TOKENS)
        return (number, False) if parsed else (0, True)
    return None, True


def usage_bool(value: Any) -> tuple[bool | None, bool]:
    """Typed usage_requests flag: only a bool or a case-insensitive "true"/"false" string is parsed."""
    if isinstance(value, bool):
        return value, False
    if value is None or value == "":
        return None, False
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true", False
    return None, True


def _cost(value: Any) -> tuple[int | float, bool]:
    return ch_float(value, MAX_COST_USD)


def _duration_ns(start_ns: int, end_ns: int) -> int:
    """end - start; a span longer than any real one, or ending long before it starts, counts as 0."""
    duration = end_ns - start_ns
    return duration if abs(duration) <= MAX_SPAN_DURATION_NS else 0


def _smallint(value: int) -> int:
    """An open protobuf enum (any int32) as a smallint column value."""
    return value if -_SMALLINT_LIMIT <= value < _SMALLINT_LIMIT else 0


def clean(text: str | None) -> str | None:
    """Make a string storable in PostgreSQL: drop NUL, replace unpaired surrogates with U+FFFD."""
    if text is None:
        return None
    if "\x00" in text:
        text = text.replace("\x00", "")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        # Re-pairs escaped surrogate pairs and replaces the unpaired ones, as Go's decoder does.
        text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return text


def clean_json(value: Any) -> Any:
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, list):
        return [clean_json(x) for x in value]
    if isinstance(value, dict):
        return {clean(k): clean_json(x) for k, x in value.items()}
    return value


def _fit(text: str, limit: int) -> str:
    """`text` cut to at most `limit` UTF-8 bytes, on a character boundary."""
    encoded = text.encode("utf-8")
    return text if len(encoded) <= limit else encoded[:limit].decode("utf-8", "ignore")


def _session_key(text: str) -> str:
    return _fit(clean(text) or "", MAX_KEY_BYTES)


def _opt(text: str) -> str | None:
    """Optional text column: '' is stored as NULL (queries test `<> ''`, which NULL never passes)."""
    return clean(text) if text else None


def _jsonb(attrs: dict[str, Any]) -> str | None:
    return json_text(clean_json(attrs)) if attrs else None


def ns_to_datetime(ns: int) -> datetime:
    """UTC datetime with microsecond precision (PostgreSQL's), truncating nanoseconds."""
    return datetime.fromtimestamp(ns // 1_000_000_000, tz=timezone.utc).replace(
        microsecond=(ns % 1_000_000_000) // 1000
    )


def record_hash(*parts: bytes) -> int:
    """64-bit identity for the idempotency ledger, as a signed bigint.

    Each part is length-prefixed, so ("ab", "c") and ("a", "bc") are different records.
    """
    digest = hashlib.blake2b(digest_size=8)
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return int.from_bytes(digest.digest(), "big", signed=True)


# ── request parsing ───────────────────────────────────────────────────────────


def _media_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _hex_ids_to_base64(node: Any) -> None:
    """OTLP/JSON carries trace and span ids in hex; protobuf JSON expects base64."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _HEX_ID_KEYS and isinstance(value, str):
                # Not hex: leave it to the protobuf JSON parser (which reads base64).
                with contextlib.suppress(ValueError):
                    node[key] = base64.b64encode(bytes.fromhex(value)).decode()
            else:
                _hex_ids_to_base64(value)
    elif isinstance(node, list):
        for item in node:
            _hex_ids_to_base64(item)


def parse_request(signal: OtlpSignal, body: bytes, content_type: str) -> Message:
    """The OTLP export request in `body`, protobuf or JSON."""
    message = _REQUEST_TYPES[signal]()
    media_type = _media_type(content_type)
    try:
        if media_type in PROTOBUF_MEDIA_TYPES:
            # Concatenated requests merge into one: their repeated fields append (protobuf merge rules).
            message.ParseFromString(body)
        elif media_type == JSON_MEDIA_TYPE:
            document = clean_json(json.loads(body))
            if not isinstance(document, dict):
                raise ValueError("an OTLP/JSON request must be an object")
            _hex_ids_to_base64(document)
            json_format.ParseDict(document, message, ignore_unknown_fields=True)
        else:
            raise UnsupportedTelemetryContentTypeError(f"Unsupported content type {content_type!r}")
    except (DecodeError, json_format.ParseError, ValueError) as exc:
        raise InvalidTelemetryPayloadError(f"Cannot decode OTLP {signal} request: {exc}") from exc
    except RecursionError as exc:
        raise InvalidTelemetryPayloadError(f"OTLP {signal} request is nested too deeply") from exc
    return message


# ── decoding ──────────────────────────────────────────────────────────────────


def _resource(resource_attrs: Any, batch: DecodedBatch) -> tuple[int, dict[str, Any]]:
    attrs = attrs_to_py(resource_attrs)
    resource_id = record_hash(json_text(clean_json(attrs), sort_keys=True).encode())
    if resource_id not in batch.resources:
        batch.resources[resource_id] = (_opt(as_str(attrs.get("service.name"))), _jsonb(attrs) or "{}")
    return resource_id, attrs


def _split_session_attrs(attrs: dict[str, Any], session_id: str, batch: DecodedBatch) -> None:
    """Move session-constant attributes out of the row into the per-session record."""
    if not session_id:
        return
    found = {k: attrs.pop(k) for k in SESSION_SCOPED_KEYS if k in attrs}
    if found:
        batch.session_attrs[session_id].update(clean_json(found))


def _pop_text(attrs: dict[str, Any], key: str) -> str | None:
    return _opt(as_str(attrs.pop(key, None)))


def _pop_key(attrs: dict[str, Any], key: str, limit: int = MAX_KEY_BYTES) -> str | None:
    """An optional text column that an index or a rollup primary key contains."""
    text = _pop_text(attrs, key)
    return _fit(text, limit) if text is not None else None


def _hook_row(
    ts: datetime,
    h: int,
    identity: tuple[str, str, str, str],
    texts: dict[str, str],
    extras: dict[str, Any],
) -> HookRow:
    """`identity` is (session_id, event_type, prompt_id, user_email); `texts` the typed hook
    fields; `extras` everything else, kept apart so no payload key can replace a typed value."""
    session_id, event_type, prompt_id, user_email = identity
    return HookRow(
        ts.date(), h, ts, _session_key(session_id), clean(event_type) or "", _opt(prompt_id), _opt(user_email),
        *(_opt(texts.get(name, "")) for name in HOOK_TEXT_FIELDS), _jsonb(extras),
    )  # fmt: skip


USAGE_REQUEST_EVENT = "agent.usage.request"
_USAGE_TEXT_FIELDS = (
    "request_id", "message_id", "agent_id", "agent_type", "scope_kind", "scope_name", "model_raw", "model",
    "speed", "inference_geo", "service_tier",
)  # fmt: skip
_USAGE_INT_FIELDS = (
    "input_tokens", "cache_creation_5m_tokens", "cache_creation_1h_tokens", "cache_read_tokens", "output_tokens",
    "thinking_tokens", "web_search_requests", "web_fetch_requests",
)  # fmt: skip
_USAGE_ENVELOPE = ("type", "timestamp", "session_id")


def _usage_text(value: Any) -> str | None:
    return _opt(_fit(clean(as_str(value)) or "", MAX_KEY_BYTES))


def _usage_row(ts: datetime, h: int, session_id: str, user_email: str, fields: dict[str, Any]) -> UsageRow:
    """A usage_requests row. Typed keys leave `fields` unless the parser keeps the original
    (a rejected or capped value); the rest, minus the envelope, is `attrs`."""
    rest = {k: v for k, v in fields.items() if k not in _USAGE_ENVELOPE}
    if rest.get("prompt_id") == "":
        del rest["prompt_id"]  # an empty prompt_id is never stored, as on the OTLP path
    text = {name: _usage_text(rest.pop(name, None)) for name in _USAGE_TEXT_FIELDS}
    numbers: dict[str, int | None] = {}
    for name in _USAGE_INT_FIELDS:
        original = rest.pop(name, None)
        numbers[name], keep = usage_int(original)
        if keep:
            rest[name] = original
    original = rest.pop("is_api_error", None)
    is_api_error, keep = usage_bool(original)
    if keep:
        rest["is_api_error"] = original
    stop_reason = _usage_text(rest.pop("stop_reason", None))
    git_branch = _usage_text(rest.pop("git_branch", None))
    return UsageRow(
        ts.date(), h, ts, _session_key(session_id), _usage_text(user_email),
        text["request_id"], text["message_id"], text["agent_id"], text["agent_type"], text["scope_kind"],
        text["scope_name"], text["model_raw"], text["model"], text["speed"], text["inference_geo"],
        text["service_tier"], *(numbers[name] for name in _USAGE_INT_FIELDS), stop_reason, is_api_error,
        git_branch, _jsonb(rest),
    )  # fmt: skip


def _event_identity(type_: str, session_key: str, event_id: str) -> int | None:
    """Record hash of the (type, session, event_id) triple as stored; None without an event_id."""
    event_id = clean(event_id) or ""
    if not event_id:
        return None
    return record_hash((clean(type_) or "").encode(), _session_key(session_key).encode(), event_id.encode())


def _hook_log(attrs: dict[str, Any], ts: datetime, h: int, record_ns: int, batch: DecodedBatch) -> bool:
    """A hook event delivered as an OTel log: stored like /event-hooks stores it; False when it is not one."""
    if not as_str(attrs.get("event_type")):
        return False
    raw_session = as_str(attrs.pop(_SESSION_ID_KEY, None)) or as_str(attrs.pop("session_id", None))
    event_type = as_str(attrs.pop("event_type"))
    by_event_id = _event_identity(event_type, raw_session, as_str(attrs.get("event_id")))
    if by_event_id is not None:
        h = by_event_id
    is_usage = clean(event_type) == USAGE_REQUEST_EVENT
    if by_event_id is not None or is_usage:
        # The event's own time, as on /event-hooks; the record time when it does not parse.
        ts = ns_to_datetime(timestamp_to_ns({"timestamp": attrs.pop("timestamp", None)}, record_ns))
    prompt_id = as_str(attrs.pop("prompt_id", None)) or as_str(attrs.pop("prompt.id", None))
    user_email = as_str(attrs.pop(_USER_EMAIL_KEY, None))
    if is_usage:
        batch.usage.append(
            _usage_row(ts, h, raw_session, user_email, attrs | ({"prompt_id": prompt_id} if prompt_id else {}))
        )
        return True
    texts = {name: as_str(attrs.pop(name, None)) for name in HOOK_TEXT_FIELDS}
    batch.hooks.append(_hook_row(ts, h, (raw_session, event_type, prompt_id, user_email), texts, attrs))
    return True


def _pop_log_numbers(attrs: dict[str, Any]) -> dict[str, float | int]:
    """The log's cost and token counts; each parsed one leaves attrs."""
    numbers: dict[str, float | int] = {}
    for key, parse in (
        ("cost_usd", _cost),
        ("input_tokens", _tokens),
        ("output_tokens", _tokens),
        ("cache_read_tokens", _tokens),
        ("cache_creation_tokens", _tokens),
    ):
        if key in attrs:
            numbers[key], parsed = parse(attrs[key])
            if parsed:
                del attrs[key]  # an unparseable original stays in attrs
    return numbers


def _log_from_record(record: Any, attrs: dict[str, Any], h: int, resource_id: int, batch: DecodedBatch) -> None:
    record_ns = record.time_unix_nano or record.observed_time_unix_nano
    ts = ns_to_datetime(record_ns)
    if _hook_log(attrs, ts, h, record_ns, batch):
        return

    session_id = _session_key(as_str(attrs.pop(_SESSION_ID_KEY, None)) or as_str(attrs.get("session_id")))
    _split_session_attrs(attrs, session_id, batch)
    event_name = as_str(attrs.pop("event.name", None))
    attrs.pop("event.timestamp", None)  # the record's own timestamp
    prompt_id = as_str(attrs.get("prompt_id")) or as_str(attrs.pop("prompt.id", None))
    numbers = _pop_log_numbers(attrs)
    batch.logs.append(
        LogRow(
            day=ts.date(),
            h=h,
            ts=ts,
            session_id=session_id,
            event_kind=int(EVENT_KIND.get(event_name, EventKind.OTHER)),
            event_name=_opt(event_name),
            prompt_id=_opt(prompt_id),
            user_email=_pop_key(attrs, _USER_EMAIL_KEY),
            model=_pop_key(attrs, "model"),
            query_source=_pop_key(attrs, "query_source"),
            request_id=_pop_key(attrs, "request_id"),
            skill_name=_pop_key(attrs, "skill.name"),
            command_name=_pop_key(attrs, "command_name"),
            trace_id=record.trace_id or None,
            span_id=record.span_id or None,
            cost_usd=numbers.get("cost_usd"),
            input_tokens=numbers.get("input_tokens"),
            output_tokens=numbers.get("output_tokens"),
            cache_read_tokens=numbers.get("cache_read_tokens"),
            cache_creation_tokens=numbers.get("cache_creation_tokens"),
            severity=_smallint(record.severity_number),
            resource_id=resource_id,
            attrs=_jsonb(attrs),
        )
    )


def decode_logs(request: ExportLogsServiceRequest) -> DecodedBatch:
    batch = DecodedBatch()
    for resource_logs in request.resource_logs:
        resource_id, _ = _resource(resource_logs.resource.attributes, batch)
        resource_key = resource_id.to_bytes(8, "big", signed=True)
        for scope_logs in resource_logs.scope_logs:
            scope = scope_logs.scope.name.encode()
            for record in scope_logs.log_records:
                h = record_hash(resource_key, scope, record.SerializeToString())
                _log_from_record(record, attrs_to_py(record.attributes), h, resource_id, batch)
    return batch


def _span_identity(span: Any, resource_key: bytes, scope: bytes) -> int:
    """A span is its trace and span id; one without them (not valid OTLP) is its whole content."""
    if span.trace_id and span.span_id:
        return record_hash(span.trace_id, span.span_id)
    return record_hash(resource_key, scope, span.SerializeToString())


def decode_traces(request: ExportTraceServiceRequest) -> DecodedBatch:
    batch = DecodedBatch()
    for resource_spans in request.resource_spans:
        resource_id, resource_attrs = _resource(resource_spans.resource.attributes, batch)
        resource_key = resource_id.to_bytes(8, "big", signed=True)
        resource_email = as_str(resource_attrs.get(_USER_EMAIL_KEY))
        for scope_spans in resource_spans.scope_spans:
            scope = scope_spans.scope.name.encode()
            for span in scope_spans.spans:
                ts = ns_to_datetime(span.start_time_unix_nano)
                attrs = attrs_to_py(span.attributes)
                session_id = _session_key(as_str(attrs.pop(_SESSION_ID_KEY, None)))
                _split_session_attrs(attrs, session_id, batch)
                attrs.pop("span.type", None)
                batch.spans.append(
                    SpanRow(
                        day=ts.date(),
                        h=_span_identity(span, resource_key, scope),
                        ts=ts,
                        duration_ns=_duration_ns(span.start_time_unix_nano, span.end_time_unix_nano),
                        trace_id=span.trace_id,
                        span_id=span.span_id,
                        parent_span_id=span.parent_span_id or None,
                        span_kind=int(SPAN_KIND.get(span.name, SpanKind.OTHER)),
                        span_name=clean(span.name) or "",
                        session_id=session_id,
                        user_email=_opt(
                            _fit(as_str(attrs.pop(_USER_EMAIL_KEY, None)) or resource_email, MAX_KEY_BYTES)
                        ),
                        tool_name=_pop_key(attrs, "tool_name"),
                        tool_use_id=_pop_key(attrs, "tool_use_id"),
                        file_path=_pop_key(attrs, "file_path", MAX_PATH_BYTES),
                        subagent_type=_pop_key(attrs, "subagent_type"),
                        skill_name=_pop_key(attrs, "skill_name"),
                        success=_pop_key(attrs, "success"),
                        status_code=_smallint(span.status.code),
                        resource_id=resource_id,
                        attrs=_jsonb(attrs),
                    )
                )
    return batch


def decode_metrics(request: ExportMetricsServiceRequest) -> DecodedBatch:
    """Sum and gauge data points; histograms and summaries feed no endpoint and are skipped."""
    batch = DecodedBatch()
    for resource_metrics in request.resource_metrics:
        _decode_resource_metrics(resource_metrics, batch)
    return batch


def _decode_resource_metrics(resource_metrics: Any, batch: DecodedBatch) -> None:
    resource_id, _ = _resource(resource_metrics.resource.attributes, batch)
    resource_key = resource_id.to_bytes(8, "big", signed=True)
    for scope_metrics in resource_metrics.scope_metrics:
        for metric in scope_metrics.metrics:
            _decode_metric(metric, resource_id, resource_key, batch)


def _decode_metric(metric: Any, resource_id: int, resource_key: bytes, batch: DecodedBatch) -> None:
    data_kind = metric.WhichOneof("data")
    if data_kind not in ("sum", "gauge"):
        return

    data = getattr(metric, data_kind)
    if data_kind == "sum":
        temporality = _smallint(data.aggregation_temporality)
        monotonic = data.is_monotonic
    else:
        temporality = 0
        monotonic = False

    for point in data.data_points:
        _append_metric_point(point, metric.name, resource_id, resource_key, temporality, monotonic, batch)


def _append_metric_point(
    point: Any,
    metric_name: str,
    resource_id: int,
    resource_key: bytes,
    temporality: int,
    monotonic: bool,
    batch: DecodedBatch,
) -> None:
    value = float(point.as_int) if point.WhichOneof("value") == "as_int" else point.as_double
    if not math.isfinite(value):
        return  # NaN or infinity: no counter or gauge reading can use it

    ts = ns_to_datetime(point.time_unix_nano)
    attrs = attrs_to_py(point.attributes)
    session_id = _session_key(as_str(attrs.pop(_SESSION_ID_KEY, None)))
    _split_session_attrs(attrs, session_id, batch)
    batch.metrics.append(
        MetricRow(
            day=ts.date(),
            h=record_hash(resource_key, metric_name.encode(), point.SerializeToString()),
            ts=ts,
            start_ts=ns_to_datetime(point.start_time_unix_nano) if point.start_time_unix_nano else None,
            metric_name=clean(metric_name) or "",
            value=value,
            session_id=session_id,
            user_email=_pop_key(attrs, _USER_EMAIL_KEY),
            model=_pop_key(attrs, "model"),
            type=_pop_text(attrs, "type"),
            temporality=temporality,
            is_monotonic=monotonic,
            resource_id=resource_id,
            attrs=_jsonb(attrs),
        )
    )


_DECODERS = {"logs": decode_logs, "metrics": decode_metrics, "traces": decode_traces}


def decode_otlp(signal: OtlpSignal, body: bytes, content_type: str) -> DecodedBatch:
    """Typed rows for one OTLP/HTTP export request."""
    request = parse_request(signal, body, content_type)
    try:
        return _DECODERS[signal](request)
    except RecursionError as exc:  # attribute values nested deeper than the parser's own limits
        raise InvalidTelemetryPayloadError(f"OTLP {signal} request is nested too deeply") from exc
    except ValueError as exc:  # a value Python refuses to convert: the payload, not the server
        raise InvalidTelemetryPayloadError(f"OTLP {signal} request carries a value that cannot be stored") from exc


def decode_hook_events(events: list[Any], user_email: str, received_at_ns: int) -> DecodedBatch:
    """Typed rows for plugin hook events parsed from `/event-hooks` NDJSON.

    `user_email` (the authenticated sender) is stored but is not part of an event's
    identity: the proxy re-sends a spool with whatever login it holds at that moment.
    """
    batch = DecodedBatch()
    for raw in events:
        if not isinstance(raw, dict):
            continue
        try:
            row = _hook_event_row(raw, user_email, received_at_ns)
            (batch.usage if isinstance(row, UsageRow) else batch.hooks).append(row)
        except RecursionError:
            # Skipped like an unparseable NDJSON line; the rest of the batch is stored.
            logger.debug("cli_analytics: skipping a hook event nested too deeply to store")
    return batch


def _hook_event_row(raw: dict[str, Any], user_email: str, received_at_ns: int) -> HookRow | UsageRow:
    event = clean_json(raw)
    ts = ns_to_datetime(timestamp_to_ns(event, received_at_ns))
    session_id, event_type = safe_str(event.get("session_id")), safe_str(event.get("type"))
    by_event_id = _event_identity(event_type, session_id, safe_str(event.get("event_id")))
    if event_type == USAGE_REQUEST_EVENT:
        h = by_event_id if by_event_id is not None else record_hash(json_text(event, sort_keys=True).encode())
        return _usage_row(ts, h, session_id, user_email, event)
    identity = (
        safe_str(event.get("session_id")),
        safe_str(event.get("type")),
        safe_str(event.get("prompt_id")),
        user_email,
    )
    return _hook_row(
        ts,
        by_event_id if by_event_id is not None else record_hash(json_text(event, sort_keys=True).encode()),
        identity,
        {name: safe_str(event.get(name)) for name in HOOK_TEXT_FIELDS},
        {k: v for k, v in event.items() if k not in HOOK_KNOWN_KEYS},
    )
