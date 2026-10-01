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

from datetime import date, timedelta
from enum import IntFlag

from codemie.repository.cli_analytics.postgres.otlp import DecodedBatch
from codemie.repository.cli_analytics.vocabulary import ROLLUP_METRICS, EventKind, SpanKind


class RollupFamily(IntFlag):
    LOG_FACTS = 1  # cost_daily, slash commands in invocations_hourly, session_skills
    DIMENSIONS = 2  # session_dims
    SPAN_FACTS = 4  # turns_daily, tool_facts_daily, session_files_daily, invocations_hourly
    METRIC_FACTS = 8  # lines_daily, active_time_daily


_LOG_KINDS = frozenset({EventKind.API_REQUEST, EventKind.USER_PROMPT, EventKind.SKILL_ACTIVATED})
_SPAN_KINDS = frozenset({SpanKind.TOOL, SpanKind.TOOL_EXECUTION, SpanKind.INTERACTION})
_ROLLUP_METRICS = frozenset(ROLLUP_METRICS)
# A tool span and its execution span can fall on both sides of midnight; the tool's hourly
# success is recomputed on its own day, so a span this close to midnight also marks the day before.
_CROSS_MIDNIGHT = timedelta(hours=1)

DirtyKeys = dict[tuple[date, str], int]


def _mark(keys: DirtyKeys, day: date, session_id: str, family: RollupFamily) -> None:
    keys[(day, session_id)] = keys.get((day, session_id), 0) | int(family)


def dirty_keys(batch: DecodedBatch) -> DirtyKeys:
    """The (day, session) slices whose rollups must be recomputed after storing `batch`."""
    keys: DirtyKeys = {}
    for log in batch.logs:
        if log.event_kind in _LOG_KINDS:
            _mark(keys, log.day, log.session_id, RollupFamily.LOG_FACTS)
    for hook in batch.hooks:
        if hook.session_id:
            _mark(keys, hook.day, hook.session_id, RollupFamily.DIMENSIONS)
    for span in batch.spans:
        if span.span_kind in _SPAN_KINDS:
            _mark(keys, span.day, span.session_id, RollupFamily.SPAN_FACTS)
            _mark(keys, (span.ts - _CROSS_MIDNIGHT).date(), span.session_id, RollupFamily.SPAN_FACTS)
    for point in batch.metrics:
        if point.metric_name in _ROLLUP_METRICS:
            _mark(keys, point.day, point.session_id, RollupFamily.METRIC_FACTS)
    return keys
