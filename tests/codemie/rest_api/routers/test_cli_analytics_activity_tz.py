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

"""`/activity?tz=`: the IANA zone reaches the handler; an unknown zone is a 422, no zone means UTC."""

from __future__ import annotations

from datetime import timezone
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from tests.codemie.rest_api.routers.test_cli_analytics_owner_sessions import BASE, SUPER, make  # noqa: F401

EMPTY = {"heat": [[0] * 24 for _ in range(7)], "by_hour": [0] * 24, "by_weekday": [0] * 7}


def _client(make):  # noqa: F811
    client, env = make(SUPER())
    env.handler.get_activity = AsyncMock(return_value=EMPTY)
    return client, env


def test_iana_zone_is_passed_to_the_handler(make):  # noqa: F811
    client, env = _client(make)

    resp = client.get(f"{BASE}/activity", params={"tz": "Europe/Madrid"})

    assert resp.status_code == 200
    assert env.handler.get_activity.await_args.args[1] == ZoneInfo("Europe/Madrid")


def test_missing_zone_buckets_in_utc(make):  # noqa: F811
    client, env = _client(make)

    assert client.get(f"{BASE}/activity").status_code == 200
    assert env.handler.get_activity.await_args.args[1] == timezone.utc


@pytest.mark.parametrize(
    "tz", ["Mars/Olympus", "../../etc/passwd", "UTC+2", "Europe", "America/Argentina", "posixrules", "localtime", ""]
)
def test_unknown_zone_is_rejected(make, tz):  # noqa: F811
    client, env = _client(make)

    resp = client.get(f"{BASE}/activity", params={"tz": tz})

    if tz == "":
        assert resp.status_code == 200  # an empty value is the same as no value
        return
    assert resp.status_code == 422
    env.handler.get_activity.assert_not_awaited()


def test_host_localtime_alias_is_rejected_even_where_the_zone_list_has_it(make, monkeypatch):  # noqa: F811
    # On Linux /usr/share/zoneinfo/localtime links to /etc/localtime, so available_timezones() lists it;
    # it would bucket in the server's own zone.
    import codemie.rest_api.routers.cli_analytics as router_module

    monkeypatch.setattr(router_module, "available_timezones", lambda: {"UTC", "Europe/Madrid", "localtime"})
    router_module._iana_zones.cache_clear()
    try:
        client, env = _client(make)

        assert client.get(f"{BASE}/activity", params={"tz": "localtime"}).status_code == 422
        assert client.get(f"{BASE}/activity", params={"tz": "Europe/Madrid"}).status_code == 200
    finally:
        router_module._iana_zones.cache_clear()
