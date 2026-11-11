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

"""How a failed ledger read is made visible - one place, so every reader of a ledger fails the same way.

The facade absorbs a ledger that raises, and an adapter that absorbs a failure of its own (one filter
of a batch) owes the same trace: an ERROR line carrying the traceback, and a count on the failure
metric naming the ledger and the read. Written twice, the two would drift into a ledger whose failures
are logged but not counted, or the other way round.
"""

from __future__ import annotations

from codemie.configs import logger
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import SPEND_LEDGER_READ_FAILURES_METRIC


def record_read_failure(ledger_name: str, read: str, message: str, error: BaseException, count: int = 1) -> None:
    """Log one failed read at ERROR and count `count` failures against the ledger and the read.

    **ERROR, not the WARNING a degraded lookup gets**: a ledger that raises is an outage of the only
    place the money lives, whereas a lost id set still leaves the residue label naming it. `count` is
    more than one only where one read stood for several that failed together - the log is still one
    line, because the same outage written fifty times is not fifty facts.
    """
    logger.error(message, exc_info=error)
    # A fresh dict per call: `send_count_metric` mutates what it is handed, adding the calling user
    # and the environment, so a shared literal would accumulate them.
    BaseMonitoringService.send_count_metric(
        name=SPEND_LEDGER_READ_FAILURES_METRIC,
        attributes={"ledger": ledger_name, "read": read},
        count=count,
    )
