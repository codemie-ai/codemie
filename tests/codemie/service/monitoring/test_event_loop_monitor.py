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

"""Event-loop lag: a blocking call is seen from outside, as a timed sleep that wakes late (EPMCDME-15537)."""

import asyncio
import contextlib
import time
from unittest.mock import patch

import pytest

from codemie.service.monitoring import event_loop_monitor
from codemie.service.monitoring.event_loop_monitor import monitor_event_loop_lag
from codemie.service.monitoring.metrics_constants import EVENT_LOOP_LAG_METRIC

INTERVAL_SECONDS = 0.01
BLOCK_SECONDS = 0.3
WARN_SECONDS = 0.1
#: A threshold no test block can reach, for the case that must record without warning.
UNREACHABLE_WARN_SECONDS = 60.0


async def _block_the_loop_under_the_monitor(warn_seconds):
    """Run the monitor across one blocking call and return what it recorded and logged."""
    with (
        patch.object(event_loop_monitor, "BaseMonitoringService") as monitoring,
        patch.object(event_loop_monitor, "logger") as monitor_logger,
    ):
        monitor = asyncio.create_task(monitor_event_loop_lag(INTERVAL_SECONDS, warn_seconds))
        await asyncio.sleep(0)  # the monitor starts its timed sleep
        time.sleep(BLOCK_SECONDS)  # a blocking call holding the loop
        await asyncio.sleep(INTERVAL_SECONDS * 2)  # the monitor wakes, late, and records
        monitor.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await monitor
    return monitoring, monitor_logger


@pytest.mark.asyncio
async def test_a_blocking_call_is_recorded_as_lag_and_warned_about():
    monitoring, monitor_logger = await _block_the_loop_under_the_monitor(WARN_SECONDS)

    recorded = monitoring.record_duration_metric.call_args_list
    assert {call.args[0] for call in recorded} == {EVENT_LOOP_LAG_METRIC}
    assert all(call.kwargs == {"per_user": False} for call in recorded), "lag is the worker's, not a user's"
    # The block began after the monitor's sleep did, so it overran that sleep by all but one interval.
    assert max(call.args[1] for call in recorded) >= BLOCK_SECONDS - INTERVAL_SECONDS
    monitor_logger.warning.assert_called()


@pytest.mark.asyncio
async def test_a_lag_below_the_threshold_is_recorded_but_not_warned_about():
    monitoring, monitor_logger = await _block_the_loop_under_the_monitor(UNREACHABLE_WARN_SECONDS)

    monitoring.record_duration_metric.assert_called()
    monitor_logger.warning.assert_not_called()
