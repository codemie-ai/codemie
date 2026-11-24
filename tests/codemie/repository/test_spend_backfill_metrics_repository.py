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

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from elastic_transport import ApiResponseMeta
from elastic_transport import ConnectionError as ESConnectionError
from elasticsearch import ApiError

from codemie.configs.config import config
from codemie.repository.spend_backfill_metrics_repository import (
    LEDGER_ATTRIBUTES_MAPPING,
    UpdateStatus,
    VersionCheckedUpdate,
    MetricDoc,
    MetricsIndexUnavailableError,
    SpendBackfillMetricsRepository,
    UnsettledDocsQuery,
)

START = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
END = datetime(2026, 11, 20, 11, 55, tzinfo=UTC)
HIT_MILLIS = 1_763_640_000_000


def _api_error(status: int = 500) -> ApiError:
    meta = ApiResponseMeta(status=status, http_version="1.1", headers={}, duration=0.0, node=MagicMock())
    return ApiError(message="boom", meta=meta, body={})


@pytest.fixture
def client():
    client = MagicMock()
    client.open_point_in_time = AsyncMock(return_value={"id": "pit-1"})
    client.close_point_in_time = AsyncMock()
    client.search = AsyncMock(return_value={"timed_out": False, "_shards": {"failed": 0}, "hits": {"hits": []}})
    client.bulk = AsyncMock(return_value={"errors": False, "items": []})
    client.indices.put_mapping = AsyncMock(return_value={"acknowledged": True})
    return client


@pytest.fixture
def repository(client):
    with patch("codemie.repository.spend_backfill_metrics_repository.ElasticSearchClient") as mock_client_cls:
        mock_client_cls.is_configured.return_value = True
        mock_client_cls.get_async_client.return_value = client
        yield SpendBackfillMetricsRepository()


async def _find(repository, **overrides) -> list[MetricDoc]:
    arguments = {
        "metric_name": "routing_call_usage",
        "start": START,
        "end": END,
        "is_end_inclusive": True,
        "projects": (),
        "settlement_version": 1,
        "source_fields": ["attributes.llm_run_id"],
        "size": 50,
        "search_after": None,
    }
    arguments.update(overrides)
    return await repository.find_unsettled_docs(UnsettledDocsQuery(**arguments), pit_id="pit-1", timeout=9.0)


def test_ledger_mapping_stores_counts_as_long_status_as_keyword_text_and_the_rest_as_float():
    assert LEDGER_ATTRIBUTES_MAPPING["ledger_settlement"] == {
        "type": "text",
        "fields": {"keyword": {"type": "keyword"}},
    }
    assert LEDGER_ATTRIBUTES_MAPPING["ledger_pending_reason"] == LEDGER_ATTRIBUTES_MAPPING["ledger_settlement"]
    assert LEDGER_ATTRIBUTES_MAPPING["ledger_settled_at"] == {"type": "date"}
    counts = {"ledger_settlement_version", "ledger_rows"}
    texts = {"ledger_settlement", "ledger_pending_reason"}
    money = set(LEDGER_ATTRIBUTES_MAPPING) - counts - texts - {"ledger_settled_at"}
    assert all(LEDGER_ATTRIBUTES_MAPPING[name] == {"type": "long"} for name in counts)
    assert all(LEDGER_ATTRIBUTES_MAPPING[name] == {"type": "float"} for name in money)
    assert {"ledger_spend_usd", "ledger_attempts", "original_cost_usd", "classifier_cost_usd_estimated"} <= money
    assert len(money) == 10


@pytest.mark.asyncio
async def test_open_point_in_time_disallows_partial_results_and_yields_the_pit_id(repository, client):
    async with repository.open_point_in_time(timeout=7.0) as pit_id:
        assert pit_id == "pit-1"

    client.open_point_in_time.assert_awaited_once_with(
        index=config.ELASTIC_METRICS_INDEX, keep_alive="2m", allow_partial_search_results=False, request_timeout=7.0
    )
    assert client.close_point_in_time.await_args.kwargs["id"] == "pit-1"


@pytest.mark.asyncio
async def test_open_point_in_time_rejects_failed_shards_and_closes_the_pit(repository, client):
    client.open_point_in_time.return_value = {"id": "pit-1", "_shards": {"failed": 1}}

    with pytest.raises(MetricsIndexUnavailableError):
        async with repository.open_point_in_time(timeout=7.0):
            pass

    client.close_point_in_time.assert_awaited_once()


@pytest.mark.asyncio
async def test_open_point_in_time_translates_api_error(repository, client):
    client.open_point_in_time.side_effect = _api_error()

    with pytest.raises(MetricsIndexUnavailableError):
        async with repository.open_point_in_time(timeout=7.0):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize(("closed_at", "close_timeout"), [(120.0, 10.0), (140.0, 5.0)], ids=["remaining", "floor"])
async def test_open_point_in_time_closes_pit_within_the_remaining_budget(repository, client, closed_at, close_timeout):
    clock = iter([100.0, closed_at])

    with patch("codemie.repository.spend_backfill_metrics_repository.monotonic", side_effect=lambda: next(clock)):
        async with repository.open_point_in_time(timeout=30.0):
            pass

    assert client.open_point_in_time.await_args.kwargs["request_timeout"] == 30.0
    assert client.close_point_in_time.await_args.kwargs["request_timeout"] == pytest.approx(close_timeout)


@pytest.mark.asyncio
async def test_open_point_in_time_closes_pit_when_body_raises(repository, client):
    with pytest.raises(RuntimeError):
        async with repository.open_point_in_time(timeout=5.0):
            raise RuntimeError("boom")

    client.close_point_in_time.assert_awaited_once()
    assert client.close_point_in_time.await_args.kwargs["id"] == "pit-1"


@pytest.mark.asyncio
async def test_open_point_in_time_keeps_body_error_when_close_fails(repository, client):
    client.close_point_in_time.side_effect = ESConnectionError("refused")

    with pytest.raises(RuntimeError, match="boom"):
        async with repository.open_point_in_time(timeout=5.0):
            raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_find_unsettled_docs_filters_metric_time_range_projects_and_older_versions(repository, client):
    await _find(repository, projects=("alpha", "beta"), settlement_version=3)

    filters = client.search.await_args.kwargs["body"]["query"]["bool"]["filter"]
    assert {"term": {"metric_name.keyword": "routing_call_usage"}} in filters
    assert {"range": {"@timestamp": {"gte": "2026-11-18T12:00:00.000Z", "lte": "2026-11-20T11:55:00.000Z"}}} in filters
    assert {"terms": {"attributes.project.keyword": ["alpha", "beta"]}} in filters
    assert {
        "bool": {
            "should": [
                {"bool": {"must_not": [{"exists": {"field": "attributes.ledger_settlement_version"}}]}},
                {"range": {"attributes.ledger_settlement_version": {"lt": 3}}},
            ],
            "minimum_should_match": 1,
        }
    } in filters


@pytest.mark.asyncio
async def test_find_unsettled_docs_uses_lt_for_an_exclusive_end_and_no_project_filter_without_projects(
    repository, client
):
    await _find(repository, is_end_inclusive=False)

    filters = client.search.await_args.kwargs["body"]["query"]["bool"]["filter"]
    assert {"range": {"@timestamp": {"gte": "2026-11-18T12:00:00.000Z", "lt": "2026-11-20T11:55:00.000Z"}}} in filters
    assert not [clause for clause in filters if "terms" in clause]


@pytest.mark.asyncio
async def test_find_unsettled_docs_sorts_pages_and_returns_typed_docs(repository, client):
    raw_hit = {
        "_index": "i-1",
        "_id": "a",
        "_seq_no": 3,
        "_primary_term": 1,
        "sort": [HIT_MILLIS, 2],
        "_source": {"attributes": {"llm_run_id": "run-1"}},
    }
    client.search.return_value = {"timed_out": False, "_shards": {"failed": 0}, "hits": {"hits": [raw_hit]}}

    docs = await _find(repository, search_after=[1, 2])

    assert docs == [
        MetricDoc(
            index="i-1",
            id="a",
            seq_no=3,
            primary_term=1,
            timestamp=datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=HIT_MILLIS),
            sort_values=[HIT_MILLIS, 2],
            attributes={"llm_run_id": "run-1"},
        )
    ]
    kwargs = client.search.await_args.kwargs
    assert kwargs["request_timeout"] == 9.0
    assert kwargs["allow_partial_search_results"] is False
    assert "index" not in kwargs
    body = kwargs["body"]
    assert body["size"] == 50
    assert body["_source"] == ["attributes.llm_run_id"]
    assert body["pit"]["id"] == "pit-1"
    assert body["sort"] == [{"@timestamp": "asc"}, {"_shard_doc": "asc"}]
    assert body["seq_no_primary_term"] is True
    assert body["search_after"] == [1, 2]


@pytest.mark.asyncio
async def test_find_unsettled_docs_omits_search_after_on_first_page(repository, client):
    await _find(repository)

    assert "search_after" not in client.search.await_args.kwargs["body"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "partial",
    [{"timed_out": True, "_shards": {"failed": 0}}, {"timed_out": False, "_shards": {"failed": 2}}],
)
async def test_find_unsettled_docs_rejects_partial_results(repository, client, partial):
    client.search.return_value = {**partial, "hits": {"hits": [{"_id": "a"}]}}

    with pytest.raises(MetricsIndexUnavailableError):
        await _find(repository)


@pytest.mark.asyncio
async def test_apply_ledger_mapping_sends_mapping_to_metrics_index(repository, client):
    await repository.apply_ledger_mapping()

    client.indices.put_mapping.assert_awaited_once()
    kwargs = client.indices.put_mapping.await_args.kwargs
    assert kwargs["index"] == config.ELASTIC_METRICS_INDEX
    assert kwargs["properties"] == {"attributes": {"properties": LEDGER_ATTRIBUTES_MAPPING}}


@pytest.mark.asyncio
async def test_update_docs_sends_concurrency_checked_partial_updates(repository, client):
    client.bulk.return_value = {"errors": True, "items": [{"update": {"status": 200}}, {"update": {"status": 409}}]}
    updates = [
        VersionCheckedUpdate(index="metrics-000007", id="a", seq_no=5, primary_term=2, attributes={"ledger_rows": 1}),
        VersionCheckedUpdate(index="metrics-000003", id="b", seq_no=9, primary_term=1, attributes={"ledger_rows": 2}),
    ]

    statuses = await repository.update_docs(updates, timeout=12.0)

    assert [item.status for item in statuses] == [200, 409]
    client.bulk.assert_awaited_once_with(
        operations=[
            {"update": {"_index": "metrics-000007", "_id": "a", "if_seq_no": 5, "if_primary_term": 2}},
            {"doc": {"attributes": {"ledger_rows": 1}}},
            {"update": {"_index": "metrics-000003", "_id": "b", "if_seq_no": 9, "if_primary_term": 1}},
            {"doc": {"attributes": {"ledger_rows": 2}}},
        ],
        refresh=False,
        request_timeout=12.0,
    )


@pytest.mark.asyncio
async def test_update_docs_returns_each_items_status_error_type_and_reason(repository, client):
    client.bulk.return_value = {
        "errors": True,
        "items": [
            {"update": {"status": 200}},
            {"update": {"status": 409}},
            {
                "update": {
                    "status": 400,
                    "error": {"type": "mapper_parsing_exception", "reason": "failed to parse field"},
                }
            },
        ],
    }
    updates = [VersionCheckedUpdate(index="i", id=str(n), seq_no=n, primary_term=1, attributes={}) for n in range(3)]

    assert await repository.update_docs(updates, timeout=1.0) == [
        UpdateStatus(200),
        UpdateStatus(409),
        UpdateStatus(400, "mapper_parsing_exception", "failed to parse field"),
    ]


@pytest.mark.asyncio
async def test_update_docs_without_updates_skips_elasticsearch(repository, client):
    assert await repository.update_docs([], timeout=1.0) == []

    client.bulk.assert_not_called()


@pytest.mark.asyncio
async def test_update_docs_translates_api_error_without_logging(repository, client):
    client.bulk.side_effect = _api_error()
    update = VersionCheckedUpdate(index="i", id="a", seq_no=1, primary_term=1, attributes={})

    with (
        patch("codemie.repository.spend_backfill_metrics_repository.logger") as logger,
        pytest.raises(MetricsIndexUnavailableError, match="bulk update failed"),
    ):
        await repository.update_docs([update], timeout=1.0)

    logger.warning.assert_not_called()
    logger.error.assert_not_called()


@pytest.mark.asyncio
async def test_unconfigured_client_raises():
    with patch("codemie.repository.spend_backfill_metrics_repository.ElasticSearchClient") as mock_client_cls:
        mock_client_cls.is_configured.return_value = False
        repository = SpendBackfillMetricsRepository()

    update = VersionCheckedUpdate(index="i", id="a", seq_no=1, primary_term=1, attributes={})
    with pytest.raises(MetricsIndexUnavailableError):
        await repository.update_docs([update], timeout=1.0)
    with pytest.raises(MetricsIndexUnavailableError):
        await repository.apply_ledger_mapping()
    with pytest.raises(MetricsIndexUnavailableError):
        await _find(repository)
    with pytest.raises(MetricsIndexUnavailableError):
        async with repository.open_point_in_time(timeout=1.0):
            pass
