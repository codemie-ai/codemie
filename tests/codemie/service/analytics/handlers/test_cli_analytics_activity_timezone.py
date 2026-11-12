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

"""Activity buckets each session in the viewer's IANA zone, with that session's own DST offset."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from tests.codemie.repository.cli_analytics.support.bounded import bounded_to_itself
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler

MADRID = ZoneInfo("Europe/Madrid")
MON, WED, SAT, SUN = 0, 2, 5, 6


def _flt() -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=datetime(2026, 10, 1), end_dt=datetime(2026, 11, 30))


async def _activity(starts: list[datetime], tz=None) -> dict:
    repo = MagicMock()
    repo.get_session_start_times = AsyncMock(return_value=[{"started_at": s} for s in starts])
    handler = LocalAnalyticsHandler(bounded_to_itself(repo))
    return await (handler.get_activity(_flt()) if tz is None else handler.get_activity(_flt(), tz))


def _cells(data: dict) -> list[tuple[int, int, int]]:
    return [(wd, h, v) for wd, row in enumerate(data["heat"]) for h, v in enumerate(row) if v]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("utc_start", "weekday", "hour"),
    [
        # CET (UTC+1): 2026-11-11 07:37Z -> Wed 08:xx
        (datetime(2026, 11, 11, 7, 37, tzinfo=timezone.utc), WED, 8),
        # CEST (UTC+2): 2026-10-21 21:40Z -> Wed 23:xx (QA's failing case: was Wed 22:00)
        (datetime(2026, 10, 21, 21, 40, tzinfo=timezone.utc), WED, 23),
        # Midnight rollover in CET: Sat 23:30Z -> Sun 00:xx
        (datetime(2026, 11, 7, 23, 30, tzinfo=timezone.utc), SUN, 0),
        # Midnight rollover in CEST: Sat 22:15Z -> Sun 00:xx
        (datetime(2026, 10, 17, 22, 15, tzinfo=timezone.utc), SUN, 0),
        # DST end day (2026-10-25 01:00Z, clocks go 03:00 CEST -> 02:00 CET)
        (datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc), SUN, 2),  # 02:30 CEST
        (datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc), SUN, 2),  # 02:30 CET
        (datetime(2026, 10, 25, 2, 30, tzinfo=timezone.utc), SUN, 3),  # 03:30 CET
    ],
)
async def test_session_is_bucketed_with_its_own_utc_offset(utc_start, weekday, hour):
    data = await _activity([utc_start], MADRID)

    assert _cells(data) == [(weekday, hour, 1)]
    assert data["by_weekday"][weekday] == 1
    assert data["by_hour"][hour] == 1


@pytest.mark.asyncio
async def test_sessions_from_different_dst_periods_land_on_their_local_hours():
    # Both sessions started at 09:00 local time, one in CEST and one in CET.
    data = await _activity(
        [datetime(2026, 10, 19, 7, 0, tzinfo=timezone.utc), datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)], MADRID
    )

    assert _cells(data) == [(MON, 9, 2)]


@pytest.mark.asyncio
async def test_without_a_zone_buckets_stay_in_utc_and_naive_times_are_utc():
    data = await _activity([datetime(2026, 10, 21, 21, 40, tzinfo=timezone.utc), datetime(2026, 10, 21, 21, 5)])

    assert _cells(data) == [(WED, 21, 2)]


@pytest.mark.asyncio
async def test_naive_start_times_are_read_as_utc_before_conversion():
    data = await _activity([datetime(2026, 10, 21, 21, 40)], MADRID)

    assert _cells(data) == [(WED, 23, 1)]
