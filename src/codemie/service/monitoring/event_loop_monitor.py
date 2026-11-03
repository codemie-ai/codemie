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

"""How long the event loop is held, by blocking calls or CPU saturation (EPMCDME-15537).

A synchronous call on the loop - or more ready work than the loop can run - stalls every request on
the worker, not just its own, and shows up in no request's own timing: each one merely looks slow.
Sampling how late a timed sleep wakes up is the one measurement that sees the stall from outside.
"""

import asyncio

from codemie.configs import logger
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import EVENT_LOOP_LAG_METRIC


async def monitor_event_loop_lag(interval_seconds: float, warn_seconds: float) -> None:
    """Record, every `interval_seconds`, how late the loop woke up; runs until cancelled.

    The lag is the wake-up beyond the requested sleep, which is time something else held the loop -
    a blocking call or CPU saturation; the lag alone cannot tell the two apart.
    It is clamped at zero because a clock that wakes marginally early held nobody up.
    """
    loop = asyncio.get_running_loop()
    while True:
        expected = loop.time() + interval_seconds
        await asyncio.sleep(interval_seconds)
        lag = max(0.0, loop.time() - expected)
        BaseMonitoringService.record_duration_metric(EVENT_LOOP_LAG_METRIC, lag, per_user=False)
        if lag >= warn_seconds:
            # Worded as an observation, not a diagnosis: CPU saturation delays the loop just as a
            # blocking call does, and the lag alone cannot tell the two apart.
            logger.warning(
                f"event_loop_monitor: the event loop ran {lag:.2f}s late; requests on this worker were delayed"
            )
