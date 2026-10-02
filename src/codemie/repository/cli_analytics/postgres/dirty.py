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

"""Which rollup slices new rows invalidate: the contract between ingest and the refresher.

Ingest queues a `(day, session)` key with a bitmask of rollup families; the refresher
recomputes those families for that slice from raw rows (see rollups.py).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from enum import IntFlag

from codemie.repository.cli_analytics.postgres.otlp import DecodedBatch, HookRow, LogRow, UsageRow
from codemie.repository.cli_analytics.vocabulary import ROLLUP_METRICS, EventKind, SpanKind


class RollupFamily(IntFlag):
    LOG_FACTS = 1  # cost_daily, slash commands in invocations_hourly, session_skills
    DIMENSIONS = 2  # session_dims
    SPAN_FACTS = 4  # turns_daily, tool_facts_daily, session_files_daily, invocations_hourly
    METRIC_FACTS = 8  # lines_daily, active_time_daily
    USAGE_FACTS = 16  # usage rollups (usage_requests rows, api_request logs, subagent usage hooks)
    SESSION = 32  # per-session rollups, marked for every record that carries a session id


_LOG_KINDS = frozenset({EventKind.API_REQUEST, EventKind.USER_PROMPT, EventKind.SKILL_ACTIVATED})
_SPAN_KINDS = frozenset({SpanKind.TOOL, SpanKind.TOOL_EXECUTION, SpanKind.INTERACTION})
_ROLLUP_METRICS = frozenset(ROLLUP_METRICS)
# A tool span and its execution span can fall on both sides of midnight; the tool's hourly
# success is recomputed on its own day, so a span this close to midnight also marks the day before.
_CROSS_MIDNIGHT = timedelta(hours=1)

# Hook types that mark SPAN_FACTS, on their own day and the day of `ts - 1 h`, like spans.
_SPAN_HOOK_TYPES = frozenset(
    {
        "agent.tool.start",
        "agent.tool.end",
        "agent.tool.error",
        "agent.prompt.submit",
        "agent.subagent.start",
        "agent.skill.dispatch",
    }
)
_SUBAGENT_USAGE = "agent.subagent.usage"
# An api_request without a request_id this close to midnight also marks the neighbouring day (bounds
# inclusive): the window in which the recompute matches it with a transcript request by fingerprint.
_MIDNIGHT = timedelta(seconds=5)
# The lines of one transcript request can lie on both sides of midnight; the recompute reads a
# day's usage_requests rows this far past its edges (rollups.py), and a usage_requests row this
# close to midnight also marks the neighbouring day (bounds inclusive). The recompute also pairs a
# transcript request and an OTel record by request_id across midnight when both lie this close to
# it, so an api_request with a request_id marks the neighbouring day within the same reach.
# One constant for all three.
USAGE_REQUEST_REACH = timedelta(hours=1)

DirtyKeys = dict[tuple[date, str], int]


def _mark(keys: DirtyKeys, day: date, session_id: str, family: RollupFamily) -> None:
    keys[(day, session_id)] = keys.get((day, session_id), 0) | int(family)


def _neighbour(day: date, days: int) -> date | None:
    """The day `days` away from `day`; None past the calendar's first or last day. A client may date
    an event anywhere in years 1 to 9999, and there is no neighbouring day to mark beyond them."""
    try:
        return day + timedelta(days=days)
    except OverflowError:
        return None


def _mark_near_midnight(keys: DirtyKeys, day: date, ts: datetime, session_id: str, mask: int, reach: timedelta) -> None:
    """Mark `mask` on `day`, and on the neighbouring day when `ts` is within `reach` of that midnight."""
    mask = int(mask)
    keys[(day, session_id)] = keys.get((day, session_id), 0) | mask
    since_midnight = ts - datetime.combine(day, time.min, tzinfo=timezone.utc)
    if since_midnight <= reach:
        neighbour = _neighbour(day, -1)
    elif timedelta(days=1) - since_midnight <= reach:
        neighbour = _neighbour(day, 1)
    else:
        return
    if neighbour is not None:
        keys[(neighbour, session_id)] = keys.get((neighbour, session_id), 0) | mask


def _mark_log(keys: DirtyKeys, log: LogRow) -> None:
    mask = 0
    if log.event_kind in _LOG_KINDS:
        mask |= RollupFamily.LOG_FACTS
    if log.event_kind == EventKind.API_REQUEST:
        mask |= RollupFamily.USAGE_FACTS
    if log.session_id:
        mask |= RollupFamily.SESSION
    if not mask:
        return
    mask = int(mask)  # a plain int: an IntFlag is iterable and asyncpg would encode it as a nested array
    if log.event_kind == EventKind.API_REQUEST:
        reach = USAGE_REQUEST_REACH if log.request_id else _MIDNIGHT
        _mark_near_midnight(keys, log.day, log.ts, log.session_id, mask, reach)
    else:
        keys[(log.day, log.session_id)] = keys.get((log.day, log.session_id), 0) | mask


def _mark_usage(keys: DirtyKeys, row: UsageRow) -> None:
    mask = RollupFamily.LOG_FACTS | RollupFamily.USAGE_FACTS
    if row.session_id:
        mask |= RollupFamily.SESSION
    _mark_near_midnight(keys, row.day, row.ts, row.session_id, int(mask), USAGE_REQUEST_REACH)


def _mark_hook(keys: DirtyKeys, hook: HookRow) -> None:
    if not hook.session_id:
        return
    _mark(keys, hook.day, hook.session_id, RollupFamily.DIMENSIONS | RollupFamily.SESSION)
    if hook.event_type in _SPAN_HOOK_TYPES:
        _mark(keys, hook.day, hook.session_id, RollupFamily.SPAN_FACTS)
        if hook.ts - datetime.combine(hook.day, time.min, tzinfo=timezone.utc) < _CROSS_MIDNIGHT:
            previous = _neighbour(hook.day, -1)
            if previous is not None:
                _mark(keys, previous, hook.session_id, RollupFamily.SPAN_FACTS)
    elif hook.event_type == _SUBAGENT_USAGE:
        _mark(keys, hook.day, hook.session_id, RollupFamily.USAGE_FACTS)


def dirty_keys(batch: DecodedBatch) -> DirtyKeys:
    """The (day, session) slices whose rollups must be recomputed after storing `batch`."""
    keys: DirtyKeys = {}
    for log in batch.logs:
        _mark_log(keys, log)
    for row in batch.usage:
        _mark_usage(keys, row)
    for hook in batch.hooks:
        _mark_hook(keys, hook)
    for span in batch.spans:
        if span.span_kind in _SPAN_KINDS:
            _mark(keys, span.day, span.session_id, RollupFamily.SPAN_FACTS)
            _mark(keys, (span.ts - _CROSS_MIDNIGHT).date(), span.session_id, RollupFamily.SPAN_FACTS)
        if span.session_id:
            _mark(keys, span.day, span.session_id, RollupFamily.SESSION)
    for point in batch.metrics:
        if point.metric_name in _ROLLUP_METRICS:
            _mark(keys, point.day, point.session_id, RollupFamily.METRIC_FACTS)
        if point.session_id:
            _mark(keys, point.day, point.session_id, RollupFamily.SESSION)
    return keys
