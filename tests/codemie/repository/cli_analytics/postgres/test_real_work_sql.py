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

"""One definition of "real work" at the end of a session, shared by the rollup and the reader."""

from __future__ import annotations

import pytest

from codemie.repository.cli_analytics.postgres import real_work_sql, reader, rollups
from codemie.repository.cli_analytics.vocabulary import (
    ACTIVITY_HOOK_TYPES,
    BUILTIN_COMMAND_NAMES,
    LINES_METRICS,
    EventKind,
    SpanKind,
)

_TABLES = (
    ("hook_events", "h"),
    ("usage_requests", "u"),
    ("log_events", "l"),
    ("spans", "s"),
    ("metric_points", "m"),
)


def _array(values: tuple[str, ...]) -> str:
    return "ARRAY[" + ", ".join(f"'{v}'" for v in values) + "]::text[]"


def test_each_table_keeps_only_its_real_work_rows():
    f = real_work_sql.real_work_filter
    assert f("hook_events", "h") == f" AND h.event_type = ANY({_array(ACTIVITY_HOOK_TYPES)})"
    assert f("usage_requests", "u") == ""
    log_filter = f("log_events", "l")
    assert log_filter.startswith(
        f" AND l.event_kind <> {int(EventKind.OTHER)}"
        f" AND coalesce(l.command_name, '') <> ALL({_array(BUILTIN_COMMAND_NAMES)})"
    )
    # A command prompt (any name) with no model request on its span is not a turn.
    assert f" AND NOT ((l.event_kind = {int(EventKind.USER_PROMPT)} AND btrim(coalesce(l.command_name, '')) <> '')" in (
        log_filter
    )
    assert f"rq.event_kind IN ({int(EventKind.API_REQUEST)}, {int(EventKind.API_ERROR)})" in log_filter
    assert "rq.span_id = l.span_id" in log_filter
    span_filter = f("spans", "s")
    assert span_filter.startswith(
        f" AND s.span_kind <> {int(SpanKind.OTHER)} AND (s.span_kind <> {int(SpanKind.INTERACTION)} OR EXISTS ("
    )
    # An interaction span counts only on evidence on its own span: a model request or a typed prompt, never a guess.
    assert "ev.span_id = s.span_id" in span_filter
    assert f"ev.event_kind IN ({int(EventKind.API_REQUEST)}, {int(EventKind.API_ERROR)})" in span_filter
    assert (
        f"(ev.event_kind = {int(EventKind.USER_PROMPT)} AND btrim(coalesce(ev.command_name, '')) = '')" in span_filter
    )
    assert "NOT EXISTS" not in span_filter
    # Only lines of code: an active_time point is sent while a reopen is typed into.
    assert f("metric_points", "m") == f" AND m.metric_name = ANY({_array(LINES_METRICS)})"


def test_a_table_without_a_definition_is_refused_not_left_unfiltered():
    with pytest.raises(KeyError):
        real_work_sql.real_work_filter("session_dims", "d")


def test_the_array_literal_doubles_a_quote():
    assert real_work_sql.text_array(("a'b", "c")) == "ARRAY['a''b', 'c']::text[]"


@pytest.mark.parametrize(("table", "alias"), _TABLES)
def test_the_rollup_fallback_range_ends_at_the_shared_definition(table, alias):
    assert (
        f"FROM {table} {alias} WHERE {alias}.session_id = k.session_id{real_work_sql.real_work_filter(table, alias)}) AS hi"
        in (rollups._RAW_RANGE)
    )


def test_the_reader_end_probes_use_the_shared_definition_and_the_start_probes_do_not():
    start, end = reader._activity_range("s")
    for table, alias in (("log_events", "le"), ("spans", "sp")):
        assert real_work_sql.real_work_filter(table, alias) in end
        assert real_work_sql.real_work_filter(table, alias) not in start
