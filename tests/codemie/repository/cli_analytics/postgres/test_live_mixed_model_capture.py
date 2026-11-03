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

"""Replay a real mixed Claude session through OTLP ingest, PostgreSQL rollups and the Session API.

Opt-in with TEST_LOCAL_PG_URL pointing at a disposable local PostgreSQL. Authentication and the feature flag
are test seams; storage, resolution, token pricing and HTTP response serialization are the real implementation.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI, Request

from codemie.configs.config import Config
from codemie.configs.llm_config import CostConfig
from codemie.repository.cli_analytics.factory import CliAnalyticsStorage
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.rest_api.routers import cli_analytics as api
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.coding_agent_pricing import llm_service

_DSN = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(
    not _DSN or urlsplit(_DSN).hostname not in {"localhost", "127.0.0.1", "::1"},
    reason="TEST_LOCAL_PG_URL must point to a disposable local PostgreSQL",
)


def _reference_cost(attrs: dict, rates: dict) -> Decimal:
    """Independent decimal arithmetic over captured counts and the captured catalog rates."""
    return sum(
        Decimal(str(attrs.get(tokens, 0))) * Decimal(str(rates[rate]))
        for tokens, rate in (
            ("input_tokens", "input"),
            ("output_tokens", "output"),
            ("cache_read_tokens", "cache_read_input_token_cost"),
            ("cache_creation_5m_tokens", "cache_creation_input_token_cost"),
            ("cache_creation_1h_tokens", "cache_creation_input_token_cost_above_1hr"),
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_producer_cost", [False, True], ids=["captured", "catalog-authority"])
async def test_real_mixed_bedrock_session_through_ingest_rollup_and_session_api(monkeypatch, wrong_producer_cost):
    fixture = json.loads((Path(__file__).parent / "data" / "mixed_bedrock_session.json").read_text())
    payload = fixture["otlp"]
    records = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    session = str(uuid.uuid4())
    started = datetime.now(timezone.utc) - timedelta(minutes=1)
    for index, record in enumerate(records):
        timestamp = started + timedelta(seconds=index)
        record["timeUnixNano"] = str(int(timestamp.timestamp()) * 1_000_000_000)
        record["observedTimeUnixNano"] = record["timeUnixNano"]
        for attr in record["attributes"]:
            if attr["key"] == "session.id":
                attr["value"] = {"stringValue": session}
            elif attr["key"] == "event.timestamp":
                attr["value"] = {"stringValue": timestamp.isoformat()}
            elif attr["key"] == "cost_usd" and wrong_producer_cost:
                # Keep the captured tokens: the API must still price them at the catalog, not sum this value.
                attr["value"] = {"doubleValue": 999.0}

    models = [SimpleNamespace(**{**m, "cost": CostConfig(**m["cost"])}) for m in fixture["catalog"]]
    monkeypatch.setattr(llm_service, "get_all_llm_model_info", lambda: models)
    monkeypatch.setattr(llm_service, "catalog_source", lambda: "litellm")
    costs = []
    for record in records:
        attrs = {a["key"]: next(iter(a["value"].values())) for a in record["attributes"]}
        assert attrs["cache_creation_tokens"] == attrs["cache_creation_5m_tokens"] + attrs["cache_creation_1h_tokens"]
        # Explicit fixture identity, independent of the production normalizer/resolver.
        catalog_name = "claude-haiku-4-5-20251001" if attrs["model"].startswith("anthropic.") else attrs["model"]
        rates = next(m["cost"] for m in fixture["catalog"] if m["base_name"] == catalog_name)
        costs.append(_reference_cost(attrs, rates))
    assert costs == [Decimal(value) for value in fixture["expected"]["event_costs_usd"]]
    assert sum(costs) == Decimal(fixture["expected"]["cost_usd"])

    settings = AnalyticsPgSettings.from_config(Config(PG_URL=_DSN, CLI_ANALYTICS_PG_URL=_DSN))
    engine = AnalyticsPgEngine(settings)
    try:
        runtime = PostgresAnalyticsRuntime(engine, settings)
        await runtime.start()
        ingestor = PostgresTelemetryIngestor(engine)
        result = await ingestor.ingest_otlp("logs", json.dumps(payload).encode(), "application/json")
        assert result.accepted == len(records)
        await runtime.refresh_rollups()
        storage = CliAnalyticsStorage(reader=PostgresCliAnalyticsReader(engine), ingestor=ingestor, runtime=runtime)
        monkeypatch.setattr(api, "get_cli_analytics_storage", lambda: storage)
        monkeypatch.setattr(api, "_ensure_enabled", lambda: None)

        def local_admin(request: Request) -> User:
            request.state.user = User(id="local-capture-admin", email="capture@example.test", is_admin=True)
            return request.state.user

        app = FastAPI()
        app.include_router(api.router)
        app.dependency_overrides[authenticate] = local_admin
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            response = await client.get(f"/v1/analytics/cli-analytics/sessions/{session}")
        assert response.status_code == 200, response.text
        body = response.json()
        detail = body["data"]
        expected = fixture["expected"]
        assert detail["cost_usd"] == pytest.approx(float(expected["cost_usd"]), abs=1e-12)
        assert detail["cache_read_cost_usd"] == pytest.approx(float(expected["cache_read_cost_usd"]), abs=1e-12)
        assert detail["cost_available"] is True
        assert detail["model_name"] == "claude-haiku-4-5-20251001, claude-sonnet-5-5"
        assert body["metadata"]["unpriced_models"] == []
        events = [event for event in detail["events"] if event["event_type"] == "api_request"]
        assert [event["model_name"] for event in events] == [
            "claude-haiku-4-5-20251001",
            "claude-haiku-4-5-20251001",
            "claude-sonnet-5-5",
            "claude-sonnet-5-5",
        ]
        assert [event["cost_usd"] for event in events] == pytest.approx([float(value) for value in costs], abs=1e-12)
        assert sum(event["cost_usd"] for event in events) == pytest.approx(detail["cost_usd"], abs=1e-12)
        for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
            assert detail[field] == expected[field]
        assert detail["bloat_pct"] == pytest.approx(
            round(float(expected["cache_read_cost_usd"]) / float(expected["cost_usd"]) * 100, 1)
        )
    finally:
        await engine.close()
