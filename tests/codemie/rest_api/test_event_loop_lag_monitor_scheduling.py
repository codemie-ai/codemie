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

"""The lifespan schedules the event-loop lag monitor unless its interval is 0 (EPMCDME-15537)."""

import asyncio
from unittest.mock import MagicMock, patch

from codemie.rest_api import main

INTERVAL_SECONDS = 2.0
WARN_SECONDS = 0.75


def _schedule(interval_seconds):
    tasks: list[asyncio.Task] = []
    created = MagicMock(return_value="task-handle")
    with (
        patch.object(main.config, "EVENT_LOOP_LAG_MONITOR_INTERVAL_SECONDS", interval_seconds),
        patch.object(main.config, "EVENT_LOOP_LAG_WARN_SECONDS", WARN_SECONDS),
        patch.object(main.asyncio, "create_task", created),
        # A plain mock, so no coroutine is created that nothing awaits.
        patch(
            "codemie.service.monitoring.event_loop_monitor.monitor_event_loop_lag", new_callable=MagicMock
        ) as monitor,
    ):
        main._schedule_event_loop_lag_monitor(tasks)
    return tasks, created, monitor


def test_the_lag_monitor_is_scheduled_as_a_background_task():
    tasks, created, monitor = _schedule(INTERVAL_SECONDS)

    monitor.assert_called_once_with(INTERVAL_SECONDS, WARN_SECONDS)
    assert created.call_args.args[0] is monitor.return_value
    assert created.call_args.kwargs["name"] == "event_loop_lag_monitor"
    assert tasks == ["task-handle"], "in the tasks list the shutdown cancels"


def test_a_zero_interval_schedules_nothing():
    tasks, created, monitor = _schedule(0)

    created.assert_not_called()
    monitor.assert_not_called()
    assert tasks == []
