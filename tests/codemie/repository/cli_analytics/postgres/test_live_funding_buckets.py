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

"""Live-Postgres funding and personal-billed buckets through the real reader and handler (EPMCDME-16849 items 6-8).

A personal-billed session stored under a work project is in `personal_billed` and out of the spend total; a private
session stays out of every work-project read; the read scope is the existing super-admin/project-admin one.
Skipped when TEST_LOCAL_PG_URL is unreachable (same harness as test_live_owner_scope)."""

# ruff: noqa: F811  (the `case` fixture is imported)

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs.llm_config import CostConfig
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.postgres.test_live_owner_scope import (  # noqa: F401 (fixtures)
    OWNER,
    _scope,
    case,
)

_CATALOG = "codemie.service.analytics.handlers.coding_agent_pricing.llm_service.get_all_llm_model_info"
_MODEL = "claude-opus-5-5"
_SESSION_COST = 110e-6  # the seeded 100 input + 10 output tokens at $1 per 1M tokens


def _catalog():
    model = MagicMock(base_name=_MODEL, deployment_name=_MODEL, cost=CostConfig(input=1e-6, output=1e-6))
    return patch(_CATALOG, return_value=[model])


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in {"funding", "personal_billed"}}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


async def _seed(case):
    corp = await case.seed("corp", sub="corporate", origin="profile", owner=OWNER)
    billed = await case.seed("billed", sub="personal", origin="linked_folder", owner=OWNER)
    private = await case.seed("private", sub="personal", origin="personal", owner=OWNER)
    async with case.engine.acquire() as conn:
        await conn.execute("UPDATE session_dims SET funding_route = 'api_based' WHERE session_id = $1", corp)
        await conn.execute("UPDATE session_dims SET funding_route = 'subscription' WHERE session_id = $1", billed)
        await conn.execute("UPDATE session_dims SET funding_route = 'subscription' WHERE session_id = $1", private)
    return corp, billed, private


@pytest.mark.asyncio
@pytest.mark.parametrize("admin", ["super", "project"])
async def test_personal_billed_is_its_own_bucket_and_private_rows_stay_out(case, admin):
    await _seed(case)
    scope = (
        _scope("exclude", "super@x.com", unrestricted=True)
        if admin == "super"
        else _scope("exclude", "pa@x.com", projects=(case.project,))
    )
    with _catalog():
        data, _ = await LocalAnalyticsHandler(case.reader).get_cost(case.f(scope))

    assert data["kpis"]["total_sessions"] == 2  # corp + billed; the private session is not read
    assert data["kpis"]["total_cost_usd"] == pytest.approx(_SESSION_COST)  # spend: corp only
    assert data["personal_billed"]["session_count"] == 1
    assert data["personal_billed"]["cost_usd"] == pytest.approx(_SESSION_COST)
    assert data["funding"]["api_based"]["session_count"] == 1
    assert data["funding"]["subscription"]["session_count"] == 1  # the private subscription session is absent
    assert data["funding"]["unknown"]["session_count"] == 0
    total = sum(b["cost_usd"] for b in data["funding"].values())
    assert data["kpis"]["total_cost_usd"] + data["personal_billed"]["cost_usd"] == pytest.approx(total)


@pytest.mark.asyncio
async def test_the_bucket_rows_change_no_existing_field_on_a_real_dataset(case):
    await _seed(case)
    f = case.f(_scope("exclude", "super@x.com", unrestricted=True))
    handler = LocalAnalyticsHandler(case.reader)
    with _catalog():
        with_buckets, _ = await handler.get_cost(f)
        with patch.object(case.reader, "get_session_funding_costs", AsyncMock(return_value=[])):
            baseline, _ = await handler.get_cost(f)

    assert _strip(with_buckets) == _strip(baseline)


@pytest.mark.asyncio
@pytest.mark.parametrize("viewer", ["ordinary", "owner", "other_project_admin"])
async def test_no_member_or_owner_reads_the_work_project_buckets(case, viewer):
    await _seed(case)
    scope = {
        "ordinary": _scope("exclude", "ordinary@x.com"),
        "owner": _scope("exclude", OWNER),  # the owner of all three sessions, not an admin
        "other_project_admin": _scope("exclude", "pa@x.com", projects=("someone-elses-project",)),
    }[viewer]
    with _catalog():
        data, _ = await LocalAnalyticsHandler(case.reader).get_cost(case.f(scope))

    assert data["personal_billed"]["session_count"] == 0
    assert all(b["session_count"] == 0 for b in data["funding"].values())
