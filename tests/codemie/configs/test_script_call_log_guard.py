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

from __future__ import annotations

import logging
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from codemie.configs.logger import log_config, logger as app_logger
from codemie.configs.script_call_log_guard import (
    REDACTED_MESSAGE,
    ScriptCallLogFilter,
    allow_metric_record,
    guarded_record_factory,
    install_script_call_log_guard,
    script_call_active,
    suppress_call_logging,
)

SECRET = "SECRET-ARGUMENT-VALUE"


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    @property
    def messages(self) -> list[str]:
        return [record.getMessage() for record in self.records]

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def capture() -> Iterator[tuple[logging.Logger, _ListHandler]]:
    handler = _ListHandler()
    test_logger = logging.getLogger("codemie.test_script_call_log_guard")
    test_logger.setLevel(logging.DEBUG)
    test_logger.propagate = False
    test_logger.addHandler(handler)
    try:
        yield test_logger, handler
    finally:
        test_logger.removeHandler(handler)


def _filters_of_type(handler: logging.Handler) -> list[logging.Filter | object]:
    return [f for f in handler.filters if isinstance(f, ScriptCallLogFilter)]


def test_script_call_active_defaults_to_false() -> None:
    assert script_call_active.get() is False


def test_the_app_record_factory_builds_on_a_guarded_factory() -> None:
    """The app's own factory replaces the global one on every ``set_logging_info``, so the guard sits underneath it."""
    import importlib

    logger_module = importlib.import_module("codemie.configs.logger")  # the package attribute ``logger`` is a Logger

    assert getattr(logger_module.old_factory, "_script_call_guarded", False) is True


def test_a_record_made_during_a_call_keeps_level_logger_and_time_but_loses_its_text(
    capture: tuple[logging.Logger, _ListHandler],
) -> None:
    test_logger, handler = capture

    with suppress_call_logging():
        test_logger.warning("response body %s", SECRET)

    (record,) = handler.records
    assert record.levelno == logging.WARNING
    assert record.name == "codemie.test_script_call_log_guard"
    assert record.created > 0
    assert record.getMessage() == REDACTED_MESSAGE
    assert record.args == ()
    assert SECRET not in record.getMessage()


def test_exception_text_of_a_record_made_during_a_call_is_removed(
    capture: tuple[logging.Logger, _ListHandler],
) -> None:
    test_logger, handler = capture

    with suppress_call_logging():
        try:
            raise ValueError(SECRET)
        except ValueError:
            test_logger.exception("failed")

    (record,) = handler.records
    assert record.exc_info is None
    assert record.exc_text is None
    assert SECRET not in repr(record.__dict__)


def test_a_handler_added_after_the_guard_was_installed_still_gets_no_text(
    capture: tuple[logging.Logger, _ListHandler],
) -> None:
    """The factory redacts the record itself, so it does not matter which handlers exist."""
    test_logger, _ = capture
    late = _ListHandler()  # no filter at all
    test_logger.addHandler(late)
    try:
        with suppress_call_logging():
            test_logger.error("late handler %s", SECRET)
    finally:
        test_logger.removeHandler(late)

    assert late.messages == [REDACTED_MESSAGE]


def test_a_third_party_logger_is_covered_too(capture: tuple[logging.Logger, _ListHandler]) -> None:
    library = logging.getLogger("some.third.party.library")
    handler = _ListHandler()
    library.setLevel(logging.DEBUG)
    library.propagate = False
    library.addHandler(handler)
    try:
        with suppress_call_logging():
            library.error("connection to %s failed", SECRET)
    finally:
        library.removeHandler(handler)

    assert handler.messages == [REDACTED_MESSAGE]


def test_records_outside_a_call_are_untouched(capture: tuple[logging.Logger, _ListHandler]) -> None:
    test_logger, handler = capture

    test_logger.info("before %s", "call")
    with suppress_call_logging():
        test_logger.info("during")
    test_logger.info("after %s", "call")

    assert handler.messages == ["before call", REDACTED_MESSAGE, "after call"]
    assert script_call_active.get() is False


def test_filter_drops_the_redacted_records_below_warning_and_keeps_the_rest(
    capture: tuple[logging.Logger, _ListHandler],
) -> None:
    test_logger, handler = capture
    handler.addFilter(ScriptCallLogFilter())

    test_logger.info("before")
    with suppress_call_logging():
        test_logger.debug("debug-during")
        test_logger.info("info-during")
        test_logger.warning("warning-during")
        test_logger.error("error-during")
    test_logger.info("after")

    assert [(r.levelno, r.getMessage()) for r in handler.records] == [
        (logging.INFO, "before"),
        (logging.WARNING, REDACTED_MESSAGE),
        (logging.ERROR, REDACTED_MESSAGE),
        (logging.INFO, "after"),
    ]


def test_a_metric_record_is_untouched_even_during_a_call(capture: tuple[logging.Logger, _ListHandler]) -> None:
    test_logger, handler = capture
    handler.addFilter(ScriptCallLogFilter())

    with suppress_call_logging():
        with allow_metric_record():
            test_logger.info('{"metric_name": "tool_usage", "attributes": {"count": 1}}')
        test_logger.info("ordinary line %s", SECRET)

    assert len(handler.messages) == 1
    assert "tool_usage" in handler.messages[0]
    assert SECRET not in handler.messages[0]


def test_send_log_metric_reaches_the_log_during_a_call() -> None:
    from codemie.service.monitoring.base_monitoring_service import logger as metric_logger, send_log_metric

    handler = _ListHandler()
    handler.addFilter(ScriptCallLogFilter())
    metric_logger.addHandler(handler)
    previous = metric_logger.level
    metric_logger.setLevel(logging.DEBUG)
    try:
        with suppress_call_logging():
            send_log_metric("tool_usage_metric", {"count": 2})
            metric_logger.info("not a metric %s", SECRET)
    finally:
        metric_logger.removeHandler(handler)
        metric_logger.setLevel(previous)

    assert len(handler.messages) == 1
    assert "tool_usage_metric" in handler.messages[0]
    assert SECRET not in handler.messages[0]


def test_suppress_call_logging_resets_flag_when_body_raises() -> None:
    with pytest.raises(RuntimeError):
        with suppress_call_logging():
            raise RuntimeError("boom")

    assert script_call_active.get() is False


def test_guarding_a_factory_twice_returns_the_same_factory() -> None:
    once = guarded_record_factory(logging.getLogRecordFactory())

    assert guarded_record_factory(once) is once


def test_install_adds_the_filter_to_existing_handlers_idempotently(
    capture: tuple[logging.Logger, _ListHandler],
) -> None:
    _, handler = capture
    root_handler = _ListHandler()
    logging.getLogger().addHandler(root_handler)
    try:
        install_script_call_log_guard()
        install_script_call_log_guard()

        assert len(_filters_of_type(handler)) == 1
        assert len(_filters_of_type(root_handler)) == 1
    finally:
        logging.getLogger().removeHandler(root_handler)


def test_the_factory_survives_set_logging_info_which_resets_the_factory() -> None:
    from codemie.configs.logger import set_logging_info

    set_logging_info(uuid="u", user_id="1")
    capture_handler = _ListHandler()
    test_logger = logging.getLogger("codemie.test_script_call_log_guard.after_set_logging_info")
    test_logger.setLevel(logging.DEBUG)
    test_logger.propagate = False
    test_logger.addHandler(capture_handler)
    try:
        with suppress_call_logging():
            test_logger.error("secret %s", SECRET)
    finally:
        test_logger.removeHandler(capture_handler)

    assert capture_handler.messages == [REDACTED_MESSAGE]
    assert capture_handler.records[0].user_id == "1", "the app's own fields are still added"  # type: ignore[attr-defined]


def _production_handlers() -> list[logging.Handler]:
    """Handlers the app configures at import time: the ``default`` handler of each named app logger."""
    handlers = [handler for name in log_config.loggers for handler in logging.getLogger(name).handlers]
    assert handlers, "the app logging config must attach at least one handler"
    return handlers


def test_app_logger_module_installs_the_filter_on_the_real_handlers() -> None:
    for handler in _production_handlers():
        assert len(_filters_of_type(handler)) == 1


def test_record_from_the_real_app_logger_carries_no_text_during_a_script_call() -> None:
    handlers = _production_handlers()
    emitted: list[str] = []

    with patch.object(logging.StreamHandler, "emit", lambda self, record: emitted.append(record.getMessage())):
        with suppress_call_logging():
            app_logger.error("secret-during %s", SECRET)
        app_logger.error("visible-after")

    assert handlers
    assert not any(SECRET in message or "secret-during" in message for message in emitted)
    assert "visible-after" in emitted
