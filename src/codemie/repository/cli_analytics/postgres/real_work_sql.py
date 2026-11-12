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
(`/exit`), a span of kind OTHER and a metric other than lines of code are not real work: a reopen that does nothing
must leave no trace in the end. Everything here is built from vocabulary constants, never from input.
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
_LINK_WINDOW = "interval '1 hour'"
_REQUEST_KINDS = f"{int(EventKind.API_REQUEST)}, {int(EventKind.API_ERROR)}"


def _near_span_log(alias: str, row: str) -> str:
    """A log row `alias` of the session and the span of `row`, within the link window of its time."""
    return (
        f"{alias}.session_id = {row}.session_id AND {alias}.span_id = {row}.span_id "
        f"AND {alias}.ts >= {row}.ts - {_LINK_WINDOW} AND {alias}.ts <= {row}.ts + {_LINK_WINDOW}"
    )


def _no_model_request(row: str) -> str:
    return (
        f"NOT EXISTS (SELECT 1 FROM log_events rq WHERE {_near_span_log('rq', row)} "  # noqa: S608 - code constants
        f"AND rq.event_kind IN ({_REQUEST_KINDS}))"
    )


def _is_command_prompt(alias: str) -> str:
    return f"({alias}.event_kind = {int(EventKind.USER_PROMPT)} AND btrim(coalesce({alias}.command_name, '')) <> '')"


def _log_filter(a: str) -> str:
    return (
        f" AND {a}.event_kind <> {int(EventKind.OTHER)}"
        f" AND coalesce({a}.command_name, '') <> ALL({text_array(BUILTIN_COMMAND_NAMES)})"
        f" AND NOT ({_is_command_prompt(a)} AND {_no_model_request(a)})"
    )


def _typed_prompt(alias: str) -> str:
    return f"({alias}.event_kind = {int(EventKind.USER_PROMPT)} AND btrim(coalesce({alias}.command_name, '')) = '')"


def _span_filter(a: str) -> str:
    """Spans of any kind but OTHER. An interaction span is a turn only on evidence stored on its own span (a model
    request or a typed prompt): a local command has neither, and a span exported before its records, or whose command
    record carries no span id, has no evidence yet. Evidence stored later is real work by itself, and the stored end
    never goes back, so a span must not count on a guess."""
    evidence = (
        f"EXISTS (SELECT 1 FROM log_events ev WHERE {_near_span_log('ev', a)} "  # noqa: S608 - code constants
        f"AND (ev.event_kind IN ({_REQUEST_KINDS}) OR {_typed_prompt('ev')}))"
    )
    return (
        f" AND {a}.span_kind <> {int(SpanKind.OTHER)}"
        f" AND ({a}.span_kind <> {int(SpanKind.INTERACTION)} OR {evidence})"
    )


_FILTERS: dict[str, Callable[[str], str]] = {
    "hook_events": lambda a: f" AND {a}.event_type = ANY({text_array(ACTIVITY_HOOK_TYPES)})",
    # Every model request is work, whatever else the session did.
    "usage_requests": lambda _alias: "",
    "log_events": _log_filter,
    "spans": _span_filter,
    # Lines of code only: an active_time point is sent while a reopen is typed into.
    "metric_points": lambda a: f" AND {a}.metric_name = ANY({text_array(LINES_METRICS)})",
}


def real_work_filter(table: str, alias: str) -> str:
    """The ` AND ...` predicate that keeps only the real-work rows of `table` (aliased `alias`); a table without a
    definition is refused, not left unfiltered."""
    return _FILTERS[table](alias)
