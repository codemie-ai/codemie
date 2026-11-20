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

"""What counts as real work at the end of a session, in one place.

The rollup (the stored end of a session) and the reader (the end it reports) both stop at the last record of real
work. A lifecycle hook, Claude Code's own startup record (event kind OTHER), a built-in command typed at a reopen
(`/exit`), a span of kind OTHER, a metric other than lines of code, and a zero-value lines-of-code point are not real
work: a reopen that does nothing, or a zero-delta metric point sent after the last real edit, must leave no trace in
the end -- the same rule the admission predicate applies to membership (EPMCDME-16496 AC4). Both consumers take
the rule from here, so a trailing zero-value point neither admits a session nor extends its `ended_at`/duration.
Everything here is built from vocabulary constants, never from input.
"""

from __future__ import annotations

from collections.abc import Callable

from codemie.repository.cli_analytics.vocabulary import (
    ACTIVITY_HOOK_TYPES,
    BUILTIN_COMMAND_NAMES,
    LINES_METRICS,
    EventKind,
    SpanKind,
)


def text_array(values: tuple[str, ...]) -> str:
    """A SQL text[] literal of code constants."""
    return "ARRAY[" + ", ".join("'" + v.replace("'", "''") + "'" for v in values) + "]::text[]"


# A local command (`/cost`, `/status`, `/exit`) is typed into a client and answered by the client itself: a prompt
# record naming a command that has no model request on its span is not a turn, the rule `_REAL_TURN` of the rollup
# applies. The record and the request are linked by the span id and sit within an hour of each other.
_LINK_WINDOW = "interval '3600 seconds'"
_REQUEST_KINDS = f"{int(EventKind.API_REQUEST)}, {int(EventKind.API_ERROR)}"


def _near_span_log(alias: str, row: str) -> str:
    """A log row `alias` of the session and the span of `row`, within the link window of its time."""
    return (
        f"{alias}.session_id = {row}.session_id AND {alias}.span_id = {row}.span_id "
        f"AND {alias}.ts >= {row}.ts - {_LINK_WINDOW} AND {alias}.ts <= {row}.ts + {_LINK_WINDOW}"
    )


def _manual_compaction(a: str, *, span: bool = False) -> str:
    # A request span has its own id, while its API log and the compaction marker
    # name the parent turn. NULL-safe equality preserves clients without trace context.
    span_id = f"{a}.parent_span_id" if span else f"{a}.span_id"
    return (
        f"EXISTS (SELECT 1 FROM log_events cm WHERE cm.session_id = {a}.session_id "  # noqa: S608
        f"AND cm.span_id IS NOT DISTINCT FROM {span_id} "
        f"AND cm.trace_id IS NOT DISTINCT FROM nullif({a}.trace_id, ''::bytea) "
        f"AND cm.ts >= {a}.ts AND cm.ts <= {a}.ts + {_LINK_WINDOW} "
        "AND cm.event_name = 'compaction' AND cm.attrs->>'trigger' = 'manual')"
    )


def real_model_request(a: str) -> str:
    """The model-request evidence shared by activity filters and turn counting."""
    manual = _manual_compaction(a)
    return f"({a}.event_kind IN ({_REQUEST_KINDS}) AND ({a}.query_source IS DISTINCT FROM 'compact' OR {manual}))"


def _compact_span(a: str) -> str:
    return (
        f"({a}.span_kind = {int(SpanKind.LLM_REQUEST)} "
        f"AND coalesce({a}.attrs->>'query_source_safe', {a}.attrs->>'query_source') = 'compact')"
    )


def idle_compaction_at(session_id: str, timestamp: str) -> str:
    """Retained raw evidence that the exact stored end is an excluded compact log or span."""
    log = (
        "EXISTS (SELECT 1 FROM log_events ic "  # noqa: S608
        f"WHERE ic.session_id = {session_id} AND ic.ts = {timestamp} "
        f"AND ic.event_kind IN ({_REQUEST_KINDS}) AND ic.query_source = 'compact' "
        f"AND NOT {real_model_request('ic')})"
    )
    span = (
        "EXISTS (SELECT 1 FROM spans cs "  # noqa: S608
        f"WHERE cs.session_id = {session_id} AND cs.ts = {timestamp} "
        f"AND {_compact_span('cs')} AND NOT {_manual_compaction('cs', span=True)})"
    )
    return f"({log} OR {span})"


def _no_model_request(row: str) -> str:
    return (
        f"NOT EXISTS (SELECT 1 FROM log_events rq WHERE {_near_span_log('rq', row)} "  # noqa: S608 - code constants
        f"AND {real_model_request('rq')})"
    )


def _is_command_prompt(alias: str) -> str:
    return f"({alias}.event_kind = {int(EventKind.USER_PROMPT)} AND btrim(coalesce({alias}.command_name, '')) <> '')"


def _log_filter(a: str) -> str:
    return (
        f" AND {a}.event_kind <> {int(EventKind.OTHER)}"
        f" AND coalesce({a}.command_name, '') <> ALL({text_array(BUILTIN_COMMAND_NAMES)})"
        f" AND ({a}.event_kind NOT IN ({_REQUEST_KINDS}) OR {real_model_request(a)})"
        f" AND NOT ({_is_command_prompt(a)} AND {_no_model_request(a)})"
    )


def _typed_prompt(alias: str) -> str:
    return f"({alias}.event_kind = {int(EventKind.USER_PROMPT)} AND btrim(coalesce({alias}.command_name, '')) = '')"


def _span_filter(a: str) -> str:
    """Spans of any kind but OTHER. An interaction span is a turn only on evidence stored on its own span (a model
    request or a typed prompt): a local command has neither, and a span exported before its records, or whose command
    record carries no span id, has no evidence yet. A compact LLM request is activity only on a manual marker
    matching its parent turn; ordinary LLM request spans keep their existing behavior without a log."""
    evidence = (
        f"EXISTS (SELECT 1 FROM log_events ev WHERE {_near_span_log('ev', a)} "  # noqa: S608 - code constants
        f"AND ({real_model_request('ev')} OR {_typed_prompt('ev')}))"
    )
    return (
        f" AND {a}.span_kind <> {int(SpanKind.OTHER)}"
        f" AND ({a}.span_kind <> {int(SpanKind.INTERACTION)} OR {evidence})"
        f" AND (NOT coalesce({_compact_span(a)}, false) OR {_manual_compaction(a, span=True)})"
    )


_FILTERS: dict[str, Callable[[str], str]] = {
    "hook_events": lambda a: f" AND {a}.event_type = ANY({text_array(ACTIVITY_HOOK_TYPES)})",
    # Every model request is work, whatever else the session did.
    "usage_requests": lambda _alias: "",
    "log_events": _log_filter,
    "spans": _span_filter,
    # Lines of code only (an active_time point is sent while a reopen is typed into), and only a nonzero delta: a
    # zero-value point only says "lines are known", not that a line changed.
    "metric_points": lambda a: f" AND {a}.metric_name = ANY({text_array(LINES_METRICS)}) AND {a}.value <> 0",
}


def real_work_filter(table: str, alias: str) -> str:
    """The ` AND ...` predicate that keeps only the real-work rows of `table` (aliased `alias`); a table without a
    definition is refused, not left unfiltered."""
    return _FILTERS[table](alias)
