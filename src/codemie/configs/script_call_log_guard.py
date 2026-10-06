# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""Log guard for tool runs on behalf of a workspace script call.

While a tool runs for a script call, tool arguments and results must never reach the logs, even when the tool logs
them itself. The handler sets ``script_call_active`` around the tool run (via ``suppress_call_logging``), and then:

- a **record factory** redacts every record created meanwhile: it keeps its level, logger name and time, and loses its
  message, arguments and exception text. It is marked ``script_call_redacted``. The factory works on the record
  itself, so it covers every handler, including one added after start-up, and records of third-party libraries;
- a **handler filter** drops the redacted records below WARNING, so a call leaves no INFO or DEBUG noise, while a
  warning or an error still shows that something went wrong (without its text);
- platform **metrics** are the exception: ``send_log_metric`` writes inside ``allow_metric_record()``, and the factory
  leaves such a record untouched, so a usage metric a tool emits during the call still reaches the log.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager

script_call_active: contextvars.ContextVar[bool] = contextvars.ContextVar("script_call_active", default=False)
_metric_record: contextvars.ContextVar[bool] = contextvars.ContextVar("script_call_metric_record", default=False)

REDACTED_MESSAGE: str = "[log message withheld during a script tool call]"
_REDACTED_ATTRIBUTE: str = "script_call_redacted"
_GUARDED_ATTRIBUTE: str = "_script_call_guarded"

type RecordFactory = Callable[..., logging.LogRecord]


@contextmanager
def suppress_call_logging() -> Iterator[None]:
    """Mark the current context as running a script call; log records are redacted meanwhile."""
    token = script_call_active.set(True)
    try:
        yield
    finally:
        script_call_active.reset(token)


@contextmanager
def allow_metric_record() -> Iterator[None]:
    """Mark the records made inside as platform metrics: the guard leaves them untouched."""
    token = _metric_record.set(True)
    try:
        yield
    finally:
        _metric_record.reset(token)


def guarded_record_factory(factory: RecordFactory) -> RecordFactory:
    """``factory`` wrapped so that a record created during a script call is redacted (see the module docstring).

    Idempotent: a factory that is already guarded is returned as it is.
    """
    if getattr(factory, _GUARDED_ATTRIBUTE, False):
        return factory

    def make_record(
        name: str,
        level: int,
        fn: str,
        lno: int,
        msg: object,
        args: object,
        exc_info: object,
        func: str | None = None,
        sinfo: str | None = None,
        **kwargs: object,
    ) -> logging.LogRecord:
        if script_call_active.get() and not _metric_record.get():
            record = factory(name, level, fn, lno, REDACTED_MESSAGE, (), None, func, None, **kwargs)
            setattr(record, _REDACTED_ATTRIBUTE, True)
            return record
        return factory(name, level, fn, lno, msg, args, exc_info, func, sinfo, **kwargs)

    setattr(make_record, _GUARDED_ATTRIBUTE, True)
    return make_record


class ScriptCallLogFilter(logging.Filter):
    """Drops the records redacted during a script call when they are below WARNING."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not (getattr(record, _REDACTED_ATTRIBUTE, False) and record.levelno < logging.WARNING)


def _add_filter_once(handler: logging.Handler) -> None:
    if not any(isinstance(existing, ScriptCallLogFilter) for existing in handler.filters):
        handler.addFilter(ScriptCallLogFilter())


def install_script_call_log_guard() -> None:
    """Guard the current record factory and add the filter to every handler that exists now (idempotent)."""
    logging.setLogRecordFactory(guarded_record_factory(logging.getLogRecordFactory()))
    loggers: list[logging.Logger] = [logging.getLogger()]
    loggers.extend(
        candidate
        for candidate in list(logging.root.manager.loggerDict.values())
        if isinstance(candidate, logging.Logger)
    )
    for target in loggers:
        for handler in target.handlers:
            _add_filter_once(handler)
