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

"""The ledger's session factory times the connection wait (EPMCDME-15537).

A slow figure is a slow query or a long queue for a connection, and only this wait tells them apart.
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.enterprise.litellm import spend_ledger
from codemie.service.monitoring.metrics_constants import SPEND_LEDGER_CONNECTION_WAIT_METRIC

CHECKOUT_STARTED = 10.0
CHECKOUT_SECONDS = 0.25


def _open_ledger_session(session, pool_size, checkout_error=None):
    """Open the ledger's session over a fake one, with the clock and the metric stubbed.

    Returns what the open produced - the session, or the error the checkout raised - and the metric.
    """
    clock = MagicMock()
    clock.monotonic.side_effect = [CHECKOUT_STARTED, CHECKOUT_STARTED + CHECKOUT_SECONDS]

    async def _checkout():
        assert clock.monotonic.call_count == 1, "the wait is timed from before the checkout to after it"
        if checkout_error is not None:
            raise checkout_error

    session.connection = AsyncMock(side_effect=_checkout)

    @asynccontextmanager
    async def _fake_ledger_session():
        yield session

    async def _open():
        try:
            async with spend_ledger._ledger_session() as opened:
                return opened
        except (TimeoutError, asyncio.CancelledError) as error:
            return error

    with (
        patch("codemie.clients.postgres.get_ledger_async_session", _fake_ledger_session),
        patch.object(spend_ledger, "time", clock),
        patch.object(spend_ledger.config, "SPEND_LEDGER_POOL_SIZE", pool_size),
        patch.object(spend_ledger, "BaseMonitoringService") as monitoring,
    ):
        opened = asyncio.run(_open())
    return opened, monitoring


@pytest.mark.parametrize(("pool_size", "pool"), [(0, "shared"), (4, "dedicated")])
def test_the_connection_wait_is_recorded_under_the_pool_it_was_drawn_from(pool_size, pool):
    session = MagicMock()

    opened, monitoring = _open_ledger_session(session, pool_size)

    assert opened is session
    session.connection.assert_awaited_once()
    monitoring.record_duration_metric.assert_called_once_with(
        SPEND_LEDGER_CONNECTION_WAIT_METRIC,
        CHECKOUT_SECONDS,
        attributes={"pool": pool, "outcome": "ok"},
        per_user=False,
    )


def test_a_failed_checkout_is_timed_as_failed_and_still_raises():
    """A pool timeout is the clearest sign of a queue, so the wait that ended in one is recorded."""
    session = MagicMock()
    pool_timeout = TimeoutError("pool exhausted")

    raised, monitoring = _open_ledger_session(session, pool_size=4, checkout_error=pool_timeout)

    assert raised is pool_timeout
    monitoring.record_duration_metric.assert_called_once_with(
        SPEND_LEDGER_CONNECTION_WAIT_METRIC,
        CHECKOUT_SECONDS,
        attributes={"pool": "dedicated", "outcome": "failed"},
        per_user=False,
    )


def test_a_cancelled_checkout_is_timed_as_cancelled_and_still_raises():
    """An abandoned wait is not a failed checkout, so it never inflates the failed series."""
    session = MagicMock()
    cancelled = asyncio.CancelledError()

    raised, monitoring = _open_ledger_session(session, pool_size=0, checkout_error=cancelled)

    assert raised is cancelled
    monitoring.record_duration_metric.assert_called_once_with(
        SPEND_LEDGER_CONNECTION_WAIT_METRIC,
        CHECKOUT_SECONDS,
        attributes={"pool": "shared", "outcome": "cancelled"},
        per_user=False,
    )


def test_the_ledger_is_composed_against_the_timed_session():
    ledger = spend_ledger.build_spend_logs_ledger(MagicMock(name="coverage"))

    assert ledger._session_factory is spend_ledger._ledger_session
