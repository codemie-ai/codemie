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

"""Live-Postgres checks for EPMCDME-16441: `get_session_cost_facts(include_costless=True)` lists sessions
without any cost_daily row, and leaves the default (inner-join) path alone.

Skipped when the local analytics Postgres named by `TEST_LOCAL_PG_URL` is unreachable.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest

from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler

from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from tests.codemie.repository.cli_analytics.postgres.test_live_session_subagent_cost import (
    _cleanup,
    _get_engine,
    _KNOWN_MODEL,
    _START,
    _USAGE,
    _lookup,
    _refresh,
    _seed_requests,
    _seed_start,
    _seed_totals,
    _window,
)

_PRICED = "test-epmcdme-16441-costless-priced"
_COSTLESS = "test-epmcdme-16441-costless-none"
_PRICED2 = _PRICED + "-2"
_COSTLESS2 = _COSTLESS + "-2"
_DETAIL = "test-epmcdme-16441-costless-detail"  # its own id: xdist runs the tests of this file in parallel


async def _seed(engine, with_costless: bool, costless_id: str = _COSTLESS, priced_id: str = _PRICED) -> None:
    await _cleanup(engine, priced_id)
    await _cleanup(engine, costless_id)
    await _seed_start(engine, priced_id)
    await _seed_requests(engine, priced_id)
    if with_costless:
        await _seed_start(engine, costless_id)
    await _refresh(engine)


@pytest.mark.asyncio
async def test_costless_session_listed_with_null_cost_only_when_opted_in():
    engine = await _get_engine()
    try:
        await _seed(engine, with_costless=True)
        reader = PostgresCliAnalyticsReader(engine)
        default = {r["session_id"]: r for r in await reader.get_session_cost_facts(_window())}
        relaxed = {r["session_id"]: r for r in await reader.get_session_cost_facts(_window(), include_costless=True)}

        assert _COSTLESS not in default
        assert _COSTLESS in relaxed
        assert relaxed[_COSTLESS]["cost_usd"] is None
        assert relaxed[_COSTLESS]["started_at"] is not None
        assert relaxed[_PRICED]["cost_usd"] == pytest.approx(0.10)
    finally:
        await _cleanup(engine, _PRICED)
        await _cleanup(engine, _COSTLESS)
        await engine.close()


@pytest.mark.asyncio
async def test_priced_facts_and_money_rows_are_unchanged_by_a_costless_session():
    engine = await _get_engine()
    try:
        reader = PostgresCliAnalyticsReader(engine)
        await _seed(engine, False, _COSTLESS2, _PRICED2)
        before = (
            [r for r in await reader.get_session_cost_facts(_window()) if r["session_id"] == _PRICED2],
            [r for r in await reader.get_session_model_costs(_window()) if r["session_id"] == _PRICED2],
        )
        await _seed(engine, True, _COSTLESS2, _PRICED2)
        after = (
            [r for r in await reader.get_session_cost_facts(_window()) if r["session_id"] == _PRICED2],
            [r for r in await reader.get_session_model_costs(_window()) if r["session_id"] == _PRICED2],
        )
        assert before == after
        relaxed = [
            r
            for r in await reader.get_session_cost_facts(_window(), include_costless=True)
            if r["session_id"] == _PRICED2
        ]
        assert relaxed == before[0]
    finally:
        await _cleanup(engine, _PRICED2)
        await _cleanup(engine, _COSTLESS2)
        await engine.close()


@pytest.mark.asyncio
async def test_costless_session_detail_resolves_instead_of_404():
    engine = await _get_engine()
    try:
        await _seed(engine, with_costless=True, costless_id=_DETAIL, priced_id=_DETAIL + '-p')
        reader = PostgresCliAnalyticsReader(engine)
        meta = await reader.get_session_detail_meta(_DETAIL)
        assert [r["session_id"] for r in meta] == [_DETAIL]
        assert await reader.get_session_detail_model_costs(_DETAIL) == []
        handler = LocalAnalyticsHandler(reader)
        detail, _, _ = await handler.get_session_detail(_DETAIL)
        assert detail is not None
        assert detail["cost_usd"] == 0.0
        assert detail["cost_available"] is False
    finally:
        await _cleanup(engine, _DETAIL + '-p')
        await _cleanup(engine, _DETAIL)
        await engine.close()


@pytest.mark.asyncio
async def test_kpis_and_priced_aggregates_are_unchanged_by_a_costless_session():
    engine = await _get_engine()
    priced, costless = _PRICED + "-kpi", _COSTLESS + "-kpi"
    try:
        reader = PostgresCliAnalyticsReader(engine)
        await _seed(engine, False, costless, priced)
        kpis_before = await reader.get_cost_kpis(_window())
        priced_before = [
            r
            for r in await reader.get_session_cost_facts(_window(), include_costless=True)
            if r["session_id"] == priced
        ]
        await _seed(engine, True, costless, priced)
        kpis_after = await reader.get_cost_kpis(_window())
        facts = await reader.get_session_cost_facts(_window(), include_costless=True)

        assert kpis_after == kpis_before
        assert [r for r in facts if r["session_id"] == priced] == priced_before
        assert any(r["session_id"] == costless for r in facts)

        # the seeded request carries no model, so the priced session gets one request of a catalog-priced model
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO log_events (ts, session_id, event_kind, model, cost_usd, input_tokens, output_tokens,"
                " severity, resource_id) VALUES ($1, $2, 1, $3, 0.01, 10, 5, 9, 1)",
                _START + timedelta(seconds=30),
                priced,
                _KNOWN_MODEL,
            )
        await _refresh(engine)
        with patch("codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config", _lookup):
            result, _, _ = await LocalAnalyticsHandler(reader).get_sessions(_window(), 0, 1000, "start_time", None)
        by_id = {s["trace_id"]: s for s in result["sessions"]}
        assert by_id[costless]["cost_available"] is False
        assert by_id[costless]["cost_usd"] == 0.0
        assert by_id[priced]["cost_available"] is True
    finally:
        await _cleanup(engine, priced)
        await _cleanup(engine, costless)
        await engine.close()


@pytest.mark.asyncio
async def test_session_whose_only_model_is_missing_from_the_catalog_is_cost_available_false():
    """A session with model rows but no catalog price is not free: list and detail answer false and $0.0."""
    engine = await _get_engine()
    unpriced, priced = "test-epmcdme-16441-unpriced-model", "test-epmcdme-16441-unpriced-model-ok"
    try:
        for sid in (unpriced, priced):
            await _cleanup(engine, sid)
            await _seed_start(engine, sid)
        await _seed_totals(engine, unpriced, [{**_USAGE, "model": "not-a-real-model-xyz"}])
        await _seed_totals(engine, priced, [_USAGE])
        await _refresh(engine)

        reader = PostgresCliAnalyticsReader(engine)
        handler = LocalAnalyticsHandler(reader)
        with patch("codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config", _lookup):
            result, _, _ = await handler.get_sessions(_window(), 0, 1000, "start_time", None)
            unpriced_detail, _, _ = await handler.get_session_detail(unpriced)
            priced_detail, _, _ = await handler.get_session_detail(priced)
        by_id = {s["trace_id"]: s for s in result["sessions"]}

        assert (by_id[unpriced]["cost_available"], by_id[unpriced]["cost_usd"]) == (False, 0.0)
        assert (unpriced_detail["cost_available"], unpriced_detail["cost_usd"]) == (False, 0.0)
        assert by_id[priced]["cost_available"] is True
        assert priced_detail["cost_available"] is True
    finally:
        await _cleanup(engine, unpriced)
        await _cleanup(engine, priced)
        await engine.close()
