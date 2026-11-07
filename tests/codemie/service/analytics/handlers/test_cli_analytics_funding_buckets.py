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

"""Funding and personal-billed estimate buckets next to the unchanged spend (EPMCDME-16849 items 7-8).

The fake reader of the spend-scope test answers the spend rows (`spend_only=True`, Personal-paid left out) and the
bucket rows (every session, its funding route and billing class). The bucket keys are additive: stripping them
gives exactly the response the handler built before. Each response is also rendered through the router's real
`_respond` / `_metadata`, so a field the response models do not declare would be missing from the JSON.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from codemie.rest_api.models.cli_analytics import (
    LocalAnalyticsCostResponse,
    LocalAnalyticsEfficiencyResponse,
    LocalAnalyticsOverviewResponse,
    LocalAnalyticsRepositoriesResponse,
    LocalAnalyticsUsersResponse,
)
from codemie.rest_api.routers.cli_analytics import _metadata, _respond
from tests.codemie.service.analytics.handlers.test_cli_analytics_spend_scope import (
    CORPORATE_COST,
    PERSONAL_COST,
    SESSIONS,
    catalog,
    handler_over,
    make_filter,
)

ROUTES = ("api_based", "subscription", "unknown")
BUCKET_KEYS = {"cost_usd", "session_count", "cost_incomplete", "unpriced_models"}
NEW_KEYS = {"funding", "personal_billed", "models_without_spend"}
UNPRICED = "mystery-model"

# (handler call, response model, rows that carry their own buckets)
ENDPOINTS = {
    "overview": (lambda h, f: h.get_overview(f), LocalAnalyticsOverviewResponse, ()),
    "cost": (lambda h, f: h.get_cost(f), LocalAnalyticsCostResponse, ("cost_by_user", "cost_by_model")),
    "users": (lambda h, f: h.get_users(f, None, None), LocalAnalyticsUsersResponse, ("rows",)),
    "repositories": (
        lambda h, f: h.get_repositories(f, 0, 50, include_branches=False),
        LocalAnalyticsRepositoriesResponse,
        ("rows",),
    ),
    "repositories_branches": (
        lambda h, f: h.get_repositories(f, 0, 50, include_branches=True),
        LocalAnalyticsRepositoriesResponse,
        ("rows",),
    ),
    "efficiency": (lambda h, f: h.get_efficiency(f), LocalAnalyticsEfficiencyResponse, ()),
}


def _bucket(cost, sessions, unpriced=()):
    return {
        "cost_usd": pytest.approx(cost),
        "session_count": sessions,
        "cost_incomplete": bool(unpriced),
        "unpriced_models": list(unpriced),
    }


def _strip(value):
    """The response without the additive bucket keys."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in NEW_KEYS}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


async def _call(name, sessions, *, no_bucket_rows=False):
    handler, _ = handler_over(sessions)
    if no_bucket_rows:
        handler._repo.get_session_funding_costs = AsyncMock(return_value=[])
    call, model, _ = ENDPOINTS[name]
    with catalog():
        result = await call(handler, make_filter())
    return result[0], result[1], model


def _rendered(data, pricer, model) -> dict:
    metadata = _metadata(0, None, pricer.unpriced, pricer.catalog_source, datetime(2026, 12, 31))
    return json.loads(_respond({"data": data, "metadata": metadata}, model).body)


def _spend(row: dict) -> float:
    return row["kpis"]["total_cost_usd"] if "kpis" in row else row["cost_usd"]


# (a) a personal-billed session is its own bucket and stays out of spend


@pytest.mark.asyncio
async def test_personal_billed_sessions_are_their_own_bucket_and_stay_out_of_spend():
    data, _, _ = await _call("cost", SESSIONS)

    assert data["kpis"]["total_cost_usd"] == pytest.approx(CORPORATE_COST)  # spend unchanged
    assert data["kpis"]["total_sessions"] == 3  # activity counts the personal-billed sessions
    assert data["personal_billed"] == _bucket(PERSONAL_COST, 2)
    assert data["funding"] == {
        "api_based": _bucket(CORPORATE_COST, 1),
        "subscription": _bucket(2.0, 1),
        "unknown": _bucket(1.0, 1),  # no claim (NULL) reads unknown
    }
    users = {r["developer_name"]: r for r in data["cost_by_user"]}
    assert users["bob"]["cost_usd"] == 0.0
    assert users["bob"]["personal_billed"] == _bucket(PERSONAL_COST, 2)
    assert users["alice"]["personal_billed"] == _bucket(0.0, 0)


# the shared-model fixture, plus a personal-only model and a personal session with a blank normalized model
EXTRA_MODELS = {
    **SESSIONS,
    "pers3": {**SESSIONS["pers1"], "model": UNPRICED},
    "pers4": {**SESSIONS["pers1"], "model": ""},
}


@pytest.mark.parametrize("sessions", [SESSIONS, EXTRA_MODELS], ids=["shared-model", "personal-only+blank-model"])
@pytest.mark.parametrize("name", list(ENDPOINTS))
@pytest.mark.asyncio
async def test_the_bucket_rows_change_no_existing_field(name, sessions):
    """Golden compare: the existing fields are what the handler answers with no bucket rows at all."""
    with_buckets, _, _ = await _call(name, sessions)
    baseline, _, _ = await _call(name, sessions, no_bucket_rows=True)

    assert _strip(with_buckets) == _strip(baseline)


# (b) per-entity grouping consistency: spend + personal_billed == the priced total of every funding bucket


@pytest.mark.parametrize("name", list(ENDPOINTS))
@pytest.mark.asyncio
async def test_spend_plus_personal_billed_is_the_priced_total_of_each_entity(name):
    data, _, _ = await _call(name, SESSIONS)
    _, _, row_keys = ENDPOINTS[name]
    entities = [data] if "kpis" in data and "total_cost_usd" in data["kpis"] else []
    entities += [row for key in row_keys for row in data[key]]

    assert entities
    for entity in entities:
        priced_total = sum(entity["funding"][route]["cost_usd"] for route in ROUTES)
        assert _spend(entity) + entity["personal_billed"]["cost_usd"] == pytest.approx(priced_total)
    assert sum(data["funding"][route]["cost_usd"] for route in ROUTES) == pytest.approx(CORPORATE_COST + PERSONAL_COST)


# (c) an unpriced model flags only the buckets it is in, never a bare 0


@pytest.mark.asyncio
async def test_an_unpriced_model_flags_only_its_own_buckets():
    sessions = {**SESSIONS, "pers1": {**SESSIONS["pers1"], "model": UNPRICED}}
    data, pricer, model = await _call("cost", sessions)

    assert data["funding"]["subscription"] == _bucket(0.0, 1, [UNPRICED])
    assert data["funding"]["api_based"] == _bucket(CORPORATE_COST, 1)
    assert data["funding"]["unknown"] == _bucket(1.0, 1)
    assert data["personal_billed"] == _bucket(1.0, 2, [UNPRICED])
    # the spend pricer never saw the Personal-paid model: the spend metadata is complete
    body = _rendered(data, pricer, model)
    assert (body["metadata"]["unpriced_models"], body["metadata"]["cost_incomplete"]) == ([], False)
    # a model row's buckets describe that model alone: the priced Personal-paid session, not the unpriced one
    by_model = {r["model_name"]: r for r in body["data"]["cost_by_model"]}
    assert by_model["claude-haiku"]["personal_billed"] == _bucket(1.0, 1)


@pytest.mark.asyncio
async def test_a_personal_only_model_is_served_apart_from_cost_by_model_and_a_blank_model_is_never_a_row():
    data, pricer, model = await _call("cost", EXTRA_MODELS)

    body = _rendered(data, pricer, model)["data"]
    assert [r["model_name"] for r in body["cost_by_model"]] == ["claude-haiku"]  # spend membership unchanged
    assert [r["model_name"] for r in body["models_without_spend"]] == [UNPRICED]  # no blank model row
    row = body["models_without_spend"][0]
    assert row["cost_usd"] == 0.0
    assert row["personal_billed"] == _bucket(0.0, 1, [UNPRICED])
    assert row["funding"]["subscription"] == _bucket(0.0, 1, [UNPRICED])
    assert data["kpis"]["total_cost_usd"] == pytest.approx(CORPORATE_COST)  # spend unchanged


@pytest.mark.asyncio
async def test_an_unpriced_spend_model_marks_the_metadata_incomplete():
    sessions = {**SESSIONS, "corp": {**SESSIONS["corp"], "model": UNPRICED}}
    data, pricer, model = await _call("overview", sessions)

    body = _rendered(data, pricer, model)
    assert (body["metadata"]["unpriced_models"], body["metadata"]["cost_incomplete"]) == ([UNPRICED], True)
    assert body["data"]["funding"]["api_based"]["cost_incomplete"] is True
    assert body["data"]["personal_billed"] == _bucket(PERSONAL_COST, 2)


# (e) the router's real `_respond` keeps every new field, also on an empty selection


def _assert_buckets(entity: dict) -> None:
    assert set(entity["funding"]) == set(ROUTES)
    for bucket in (*entity["funding"].values(), entity["personal_billed"]):
        assert set(bucket) == BUCKET_KEYS


@pytest.mark.parametrize("sessions", [SESSIONS, {}], ids=["sessions", "empty"])
@pytest.mark.parametrize("name", list(ENDPOINTS))
@pytest.mark.asyncio
async def test_the_serialized_response_keeps_the_bucket_and_metadata_fields(name, sessions):
    data, pricer, model = await _call(name, sessions)
    _, _, row_keys = ENDPOINTS[name]

    body = _rendered(data, pricer, model)

    assert body["metadata"]["cost_incomplete"] is False
    _assert_buckets(body["data"])
    rows = [row for key in row_keys for row in body["data"][key]]
    assert bool(rows) == bool(sessions and row_keys)
    for row in rows:
        _assert_buckets(row)
    if not sessions:
        assert body["data"]["personal_billed"] == _bucket(0.0, 0)
