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

"""Snapshot consistency, completeness checks and cleanup for grouped analytics."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from elasticsearch import NotFoundError

from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.metrics_aggregation_snapshot import MetricsAggregationSnapshot


@pytest.fixture
def client():
    client = MagicMock()
    client.options.return_value = client
    client.open_point_in_time = AsyncMock(return_value={"id": "pit-1"})
    client.close_point_in_time = AsyncMock()
    client.search = AsyncMock(return_value={"pit_id": "pit-2", "timed_out": False, "_shards": {"failed": 0}})
    return client


@pytest.mark.asyncio
async def test_search_uses_and_closes_the_latest_snapshot_id(client):
    async with MetricsAggregationSnapshot(client, "metrics-*") as snapshot:
        await snapshot.search({"size": 0})
        await snapshot.search({"size": 0})
    client.open_point_in_time.assert_awaited_once_with(
        index="metrics-*", keep_alive="1m", allow_partial_search_results=False
    )
    searches = client.search.call_args_list
    assert searches[0].kwargs["body"]["pit"]["id"] == "pit-1"
    assert searches[1].kwargs["body"]["pit"]["id"] == "pit-2"
    assert "index" not in searches[0].kwargs
    assert searches[0].kwargs["allow_partial_search_results"] is False
    client.close_point_in_time.assert_awaited_once_with(id="pit-2")


@pytest.mark.parametrize(
    "result",
    [
        {"timed_out": True, "_shards": {"failed": 0}},
        {"timed_out": False, "_shards": {"failed": 1}},
    ],
)
@pytest.mark.asyncio
async def test_partial_results_fail_closed_and_close_snapshot(client, result):
    client.search.return_value = result
    with pytest.raises(ExtendedHTTPException) as exc:
        async with MetricsAggregationSnapshot(client, "metrics-*") as snapshot:
            await snapshot.search({})
    assert exc.value.code == 503
    client.close_point_in_time.assert_awaited_once()


@pytest.mark.asyncio
async def test_snapshot_is_closed_when_second_query_fails(client):
    client.search.side_effect = [{"pit_id": "pit-2"}, RuntimeError("detail failure")]
    with pytest.raises(RuntimeError, match="detail failure"):
        async with MetricsAggregationSnapshot(client, "metrics-*") as snapshot:
            await snapshot.search({})
            await snapshot.search({})
    client.close_point_in_time.assert_awaited_once_with(id="pit-2")


@pytest.mark.asyncio
async def test_unconfigured_elasticsearch_fails_closed():
    with pytest.raises(ExtendedHTTPException) as exc:
        async with MetricsAggregationSnapshot(None, "metrics-*"):
            pytest.fail("An unavailable snapshot must not execute analytics")
    assert exc.value.code == 503


@pytest.mark.asyncio
async def test_expired_snapshot_fails_closed_and_is_closed(client):
    client.search.side_effect = NotFoundError("expired PIT", meta=MagicMock(status=404), body={})
    with pytest.raises(ExtendedHTTPException) as exc:
        async with MetricsAggregationSnapshot(client, "metrics-*") as snapshot:
            await snapshot.search({})
    assert exc.value.code == 503
    client.close_point_in_time.assert_awaited_once_with(id="pit-1")


@pytest.mark.asyncio
async def test_partial_snapshot_open_closes_its_pit(client):
    client.open_point_in_time.return_value = {"id": "pit-1", "_shards": {"failed": 1}}
    with pytest.raises(ExtendedHTTPException):
        async with MetricsAggregationSnapshot(client, "metrics-*"):
            pytest.fail("A partial PIT must not be used")
    client.close_point_in_time.assert_awaited_once_with(id="pit-1")
