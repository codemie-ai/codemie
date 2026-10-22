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

import logging
import re
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codemie.datasource import datasource_concurrency_manager as manager_module
from codemie.datasource.datasource_concurrency_manager import (
    _QUEUE_TIMEOUT_ERROR,
    DatasourceConcurrencyManager,
)

LOGGER_NAME = manager_module.logger.name
SLOT_PATTERN = re.compile(r"slot=([0-9a-f]{8}) datasource_id=(\S+) datasource=(\S+) operation=(\S+)")


@pytest.fixture(autouse=True)
def _capture_manager_logs(caplog):
    manager_logger = logging.getLogger(LOGGER_NAME)
    manager_logger.addHandler(caplog.handler)
    yield
    manager_logger.removeHandler(caplog.handler)


def _slot_records(caplog, marker):
    return [r for r in caplog.records if f"{marker}." in r.getMessage() and r.name == LOGGER_NAME]


def _make_manager(queue_timeout=5):
    return DatasourceConcurrencyManager(max_concurrent=1, enabled=True, queue_timeout=queue_timeout)


def _assert_semaphore_free(manager):
    assert manager._semaphore.acquire(blocking=False)
    manager._semaphore.release()


def _hold_semaphore(manager):
    manager._semaphore.acquire()
    return manager._semaphore.release


def test_run_logs_slot_acquired_and_released_with_shared_slot(caplog):
    manager = _make_manager()
    index_info = SimpleNamespace(id="ds-1", repo_name="repo-a")

    def do_work():
        return None

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        manager.run(do_work, index_info)

    acquired = _slot_records(caplog, "SlotAcquired")
    released = _slot_records(caplog, "SlotReleased")
    assert len(acquired) == 1
    assert len(released) == 1
    assert acquired[0].levelno == logging.INFO
    assert released[0].levelno == logging.INFO
    acquired_match = SLOT_PATTERN.search(acquired[0].getMessage())
    released_match = SLOT_PATTERN.search(released[0].getMessage())
    assert acquired_match.groups() == released_match.groups()
    assert acquired_match.group(2) == "ds-1"
    assert acquired_match.group(3) == "repo-a"
    assert acquired_match.group(4) == "do_work"


def test_run_prefers_explicit_datasource_name_over_repo_name(caplog):
    manager = _make_manager()
    index_info = SimpleNamespace(id="ds-1", repo_name="repo-a")

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        manager.run(lambda: None, index_info, datasource_name="explicit-name")

    message = _slot_records(caplog, "SlotAcquired")[0].getMessage()
    assert "datasource=explicit-name" in message


def test_run_without_index_info_logs_unknown_identity(caplog):
    manager = _make_manager()

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        manager.run(lambda: None)

    message = _slot_records(caplog, "SlotAcquired")[0].getMessage()
    assert "datasource_id=unknown datasource=unknown" in message


def test_run_with_explicit_name_and_no_index_info_logs_name_and_unknown_id(caplog):
    manager = _make_manager()

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        manager.run(lambda: None, datasource_name="backend-repo")

    message = _slot_records(caplog, "SlotAcquired")[0].getMessage()
    assert "datasource_id=unknown datasource=backend-repo" in message


def test_run_with_magic_mock_index_info_completes(caplog):
    manager = _make_manager()
    process_func = MagicMock(__name__="process")

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        manager.run(process_func, MagicMock())

    process_func.assert_called_once()
    assert len(_slot_records(caplog, "SlotAcquired")) == 1
    assert len(_slot_records(caplog, "SlotReleased")) == 1


def test_slot_acquired_is_logged_before_clearing_queued_state(caplog):
    manager = _make_manager()
    release_holder = _hold_semaphore(manager)
    index_info = MagicMock(id="ds-1", repo_name="repo-a")
    process_func = MagicMock(__name__="process")
    seen_acquired_logs = []

    def check_ordering(_index_info):
        seen_acquired_logs.append(len(_slot_records(caplog, "SlotAcquired")))

    threading.Timer(0.2, release_holder).start()
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with patch.object(manager, "_try_clear_queued", side_effect=check_ordering) as clear_queued:
            manager.run(process_func, index_info)

    clear_queued.assert_called_once_with(index_info)
    assert seen_acquired_logs == [1]
    process_func.assert_called_once()


def test_exception_while_clearing_queued_state_still_releases_slot(caplog):
    manager = _make_manager()
    release_holder = _hold_semaphore(manager)
    index_info = MagicMock(id="ds-1", repo_name="repo-a")
    process_func = MagicMock(__name__="process")

    threading.Timer(0.2, release_holder).start()
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with patch.object(manager, "_try_clear_queued", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                manager.run(process_func, index_info)

    process_func.assert_not_called()
    assert len(_slot_records(caplog, "SlotAcquired")) == 1
    assert len(_slot_records(caplog, "SlotReleased")) == 1
    _assert_semaphore_free(manager)


def test_process_func_exception_propagates_and_logs_release(caplog):
    manager = _make_manager()

    def failing():
        raise ValueError("work failed")

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(ValueError, match="work failed"):
            manager.run(failing, SimpleNamespace(id="ds-1", repo_name="repo-a"))

    assert len(_slot_records(caplog, "SlotReleased")) == 1
    _assert_semaphore_free(manager)


def test_slot_extraction_failure_still_releases_semaphore():
    manager = _make_manager()
    process_func = MagicMock(__name__="process")

    with patch.object(manager_module, "uuid") as fake_uuid:
        fake_uuid.uuid4.side_effect = RuntimeError("uuid failed")
        with pytest.raises(RuntimeError, match="uuid failed"):
            manager.run(process_func)

    process_func.assert_not_called()
    _assert_semaphore_free(manager)


def test_slot_acquired_logging_failure_still_releases_semaphore():
    manager = _make_manager()
    process_func = MagicMock(__name__="process")
    fake_logger = MagicMock()

    def failing_info(message, *args, **kwargs):
        if "SlotAcquired" in message:
            raise RuntimeError("log failed")

    fake_logger.info.side_effect = failing_info

    with patch.object(manager_module, "logger", fake_logger):
        with pytest.raises(RuntimeError, match="log failed"):
            manager.run(process_func)

    process_func.assert_not_called()
    _assert_semaphore_free(manager)


def test_queue_timeout_logs_resolved_name_and_marks_error(caplog):
    manager = _make_manager(queue_timeout=0.05)
    release_holder = _hold_semaphore(manager)
    index_info = MagicMock(id="ds-1", repo_name="repo-a")
    process_func = MagicMock(__name__="process")

    try:
        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            manager.run(process_func, index_info, datasource_name="explicit-name")
    finally:
        release_holder()

    process_func.assert_not_called()
    index_info.set_error.assert_called_once_with(_QUEUE_TIMEOUT_ERROR)
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("datasource=explicit-name" in m for m in errors)
    assert not _slot_records(caplog, "SlotAcquired")


def test_disabled_manager_runs_without_slot_logs(caplog):
    manager = DatasourceConcurrencyManager(max_concurrent=1, enabled=False)
    process_func = MagicMock(__name__="process")

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        manager.run(process_func, MagicMock())

    process_func.assert_called_once()
    assert not [r for r in caplog.records if "Slot" in r.getMessage()]
