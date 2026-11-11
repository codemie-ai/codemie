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

"""Opt-in regression against the real local Compose API and Elasticsearch."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests
from elasticsearch import AsyncElasticsearch

from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.metrics_aggregation_snapshot import MetricsAggregationSnapshot

pytestmark = [
    pytest.mark.live_stack,
    pytest.mark.skipif(os.getenv("RUN_LIVE_STACK_TESTS") != "1", reason="Requires the local Docker Compose stack"),
]

API = "http://localhost:8080"
ES = "http://localhost:9200"
FIELD = "attributes.execution_id.keyword"


def test_complete_workflow_usage_across_rotated_indexes(record_property):
    response = requests.post(
        f"{API}/v1/local-auth/login",
        json={"email": os.environ["LIVE_API_USERNAME"], "password": os.environ["LIVE_API_PASSWORD"]},
        timeout=30,
    )
    assert response.status_code == 200
    login = response.json()
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {login['access_token']}"
    user_id = login["user"]["id"]
    marker = uuid.uuid4().hex
    indices = [f"codemie_metrics_logs_16484_validation_{marker}_{suffix}" for suffix in ("old", "new")]
    created = []
    date = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    params = {
        "start_date": f"{date}T00:00:00Z",
        "end_date": f"{date}T23:59:59Z",
        "attributes": json.dumps({"validation_run": marker}),
        "users": user_id,
    }
    timings = {}

    def doc(execution_id, timestamp, input_tokens, output_tokens, cost, duration=10):
        return {
            "@timestamp": timestamp,
            "metric_name": "workflow_execution_total",
            "attributes": {
                "execution_id": execution_id,
                "user_id": user_id,
                "user_name": "Validation",
                "user_email": "validation@example.invalid",
                "project": "epmcdme-16484-validation",
                "workflow_name": "Cross-index validation",
                "status": "SUCCEEDED",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "money_spent": cost,
                "execution_time": duration,
                "run_attribute_keys": ["validation_run"],
                "run_attribute_pairs": [f"validation_run={marker}"],
            },
        }

    def index_doc(index, document):
        result = requests.post(f"{ES}/{index}/_doc", json=document, params={"refresh": "true"}, timeout=30)
        assert result.status_code == 201

    def query():
        return {
            "bool": {
                "filter": [
                    {"term": {"metric_name.keyword": "workflow_execution_total"}},
                    {"term": {"attributes.run_attribute_pairs.keyword": f"validation_run={marker}"}},
                ]
            }
        }

    def api_page(**paging):
        started = time.perf_counter()
        result = session.get(f"{API}/v1/analytics/workflows-executions", params={**params, **paging}, timeout=60)
        timings[str(paging)] = round((time.perf_counter() - started) * 1000, 2)
        assert result.status_code == 200, result.text[:300]
        return result.json()

    try:
        for index in indices:
            result = requests.put(
                f"{ES}/{index}",
                json={
                    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
                    "mappings": {"properties": {"@timestamp": {"type": "date"}}},
                },
                timeout=30,
            )
            assert result.status_code == 200
            created.append(index)
        index_doc(indices[0], doc("resumed-target", f"{date}T09:00:00Z", 100, 50, 1.0, 90))
        bulk = []
        for i in range(100):
            bulk.extend(
                [
                    json.dumps({"index": {"_index": indices[0]}}),
                    json.dumps(doc(f"competitor-{i:03}", f"{date}T10:{i // 60:02}:{i % 60:02}Z", 1, 2, 0.01)),
                ]
            )
        result = requests.post(
            f"{ES}/_bulk",
            data="\n".join(bulk) + "\n",
            headers={"Content-Type": "application/x-ndjson"},
            params={"refresh": "true"},
            timeout=30,
        )
        assert result.status_code == 200 and not result.json()["errors"]
        index_doc(indices[1], doc("resumed-target", f"{date}T12:00:00Z", 10, 5, 0.1))

        old = requests.post(
            f"{ES}/{','.join(indices)}/_search",
            json={
                "size": 0,
                "query": query(),
                "aggs": {
                    "executions": {
                        "terms": {"field": FIELD, "size": 1, "order": {"last_activity": "desc"}},
                        "aggs": {
                            "last_activity": {"max": {"field": "@timestamp"}},
                            "input_tokens": {"sum": {"field": "attributes.input_tokens"}},
                        },
                    }
                },
            },
            timeout=30,
        ).json()["aggregations"]["executions"]["buckets"][0]
        assert old["key"] == "resumed-target" and old["input_tokens"]["value"] == 10

        for per_page in (1, 20, 1000):
            result = api_page(page=0, per_page=per_page)
            row = result["data"]["rows"][0]
            assert row["execution_id"] == "resumed-target"
            assert (row["input_tokens"], row["output_tokens"], row["total_tokens"], row["usage_records"]) == (
                110,
                55,
                165,
                2,
            )
            assert row["money_spent"] == 1.1 or row["spend_unavailable"]
            assert row["execution_time"] == 90
            assert row["attributes"] == {"validation_run": marker}
            assert result["pagination"]["total_count"] == 101
            totals = result["data"]["totals"]
            assert (totals["input_tokens"], totals["output_tokens"]) == (210, 255)
            assert totals.get("money_spent") == 2.1 or totals["money_spent_spend_unavailable"]
        second = api_page(page=1, per_page=1)
        assert second["data"]["rows"][0]["execution_id"] != "resumed-target"
        empty = api_page(page=2, per_page=100)
        assert empty["data"]["rows"] == [] and empty["data"]["totals"]["input_tokens"] == 210
        invalid = session.get(
            f"{API}/v1/analytics/workflows-executions",
            params={**params, "attributes": '{"bad key": "value"}'},
            timeout=30,
        )
        assert invalid.status_code == 422

        async def check_snapshot():
            async with AsyncElasticsearch(ES) as client:
                async with MetricsAggregationSnapshot(client, ",".join(indices)) as snapshot:
                    first = await snapshot.search(
                        {"size": 0, "query": query(), "aggs": {"tokens": {"sum": {"field": "attributes.input_tokens"}}}}
                    )
                    await client.index(
                        index=indices[1],
                        document=doc("resumed-target", f"{date}T13:00:00Z", 999, 0, 0),
                        refresh=True,
                    )
                    second = await snapshot.search(
                        {"size": 0, "query": query(), "aggs": {"tokens": {"sum": {"field": "attributes.input_tokens"}}}}
                    )
                    assert first["aggregations"]["tokens"]["value"] == second["aggregations"]["tokens"]["value"] == 210
                    expired_id = snapshot._pit_id
                fresh = await client.search(
                    index=",".join(indices),
                    query=query(),
                    size=0,
                    aggregations={"tokens": {"sum": {"field": "attributes.input_tokens"}}},
                )
                assert fresh["aggregations"]["tokens"]["value"] == 1209
                with pytest.raises(ExtendedHTTPException) as exc:
                    snapshot._pit_id = expired_id
                    await snapshot.search({"size": 0})
                assert exc.value.code == 503

        asyncio.run(check_snapshot())
        record_property(
            "live_validation", json.dumps({"old_input_tokens": 10, "fixed_input_tokens": 110, "latency_ms": timings})
        )
    finally:
        for index in created:
            result = requests.delete(f"{ES}/{index}", timeout=30)
            assert result.status_code in (200, 404)
        session.close()
