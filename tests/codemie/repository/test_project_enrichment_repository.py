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

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.core.models import ProjectEnrichment
from codemie.repository.project_enrichment_repository import ProjectEnrichmentRepository


def test_project_enrichment_model_has_is_active_field():
    e = ProjectEnrichment(application_id="proj-a", is_active=True)
    assert e.is_active is True
    assert e.application_id == "proj-a"


def test_project_enrichment_model_defaults_is_active_true():
    e = ProjectEnrichment(application_id="proj-b")
    assert e.is_active is True


def test_project_enrichment_cost_center_variant():
    cc_id = uuid.uuid4()
    e = ProjectEnrichment(cost_center_id=cc_id, is_active=False)
    assert e.cost_center_id == cc_id
    assert e.is_active is False


@pytest.mark.asyncio
async def test_get_inactive_application_ids_returns_ids():
    session = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = ["proj-x", "proj-y"]
    session.execute.return_value = execute_result

    repo = ProjectEnrichmentRepository()
    result = await repo.get_inactive_application_ids(session)

    assert result == ["proj-x", "proj-y"]


@pytest.mark.asyncio
async def test_get_inactive_application_ids_empty():
    session = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = []
    session.execute.return_value = execute_result

    repo = ProjectEnrichmentRepository()
    result = await repo.get_inactive_application_ids(session)

    assert result == []


@pytest.mark.asyncio
async def test_get_inactive_cost_center_names_returns_names():
    session = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = ["cc-a", "cc-b"]
    session.execute.return_value = execute_result

    repo = ProjectEnrichmentRepository()
    result = await repo.get_inactive_cost_center_names(session)

    assert result == ["cc-a", "cc-b"]


@pytest.mark.asyncio
async def test_get_active_application_ids_returns_ids():
    session = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = ["proj-a"]
    session.execute.return_value = execute_result

    repo = ProjectEnrichmentRepository()
    result = await repo.get_active_application_ids(session)

    assert result == ["proj-a"]


# ==================== get_is_active_for_project ====================


@pytest.mark.asyncio
async def test_get_is_active_for_project_returns_true_when_direct_enrichment_active():
    """Direct application enrichment with is_active=True → returns True."""
    session = AsyncMock()
    direct_result = MagicMock()
    direct_result.scalars.return_value.first.return_value = True
    session.execute.return_value = direct_result

    repo = ProjectEnrichmentRepository()
    result = await repo.get_is_active_for_project(session, "proj-a")

    assert result is True
    session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_is_active_for_project_returns_false_when_direct_enrichment_inactive():
    """Direct application enrichment with is_active=False → returns False."""
    session = AsyncMock()
    direct_result = MagicMock()
    direct_result.scalars.return_value.first.return_value = False
    session.execute.return_value = direct_result

    repo = ProjectEnrichmentRepository()
    result = await repo.get_is_active_for_project(session, "proj-a")

    assert result is False
    session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_is_active_for_project_falls_back_to_cost_center():
    """No direct enrichment → falls back to cost-center enrichment."""
    session = AsyncMock()

    direct_miss = MagicMock()
    direct_miss.scalars.return_value.first.return_value = None

    cc_hit = MagicMock()
    cc_hit.scalars.return_value.first.return_value = True

    session.execute.side_effect = [direct_miss, cc_hit]

    repo = ProjectEnrichmentRepository()
    result = await repo.get_is_active_for_project(session, "proj-no-direct")

    assert result is True
    assert session.execute.call_count == 2


@pytest.mark.asyncio
async def test_get_is_active_for_project_returns_none_when_no_enrichment():
    """No direct and no cost-center enrichment → returns None."""
    session = AsyncMock()

    miss = MagicMock()
    miss.scalars.return_value.first.return_value = None

    session.execute.side_effect = [miss, miss]

    repo = ProjectEnrichmentRepository()
    result = await repo.get_is_active_for_project(session, "proj-orphan")

    assert result is None
    assert session.execute.call_count == 2


@pytest.mark.asyncio
async def test_get_is_active_for_project_cost_center_inactive():
    """No direct enrichment, cost-center enrichment is_active=False → returns False."""
    session = AsyncMock()

    direct_miss = MagicMock()
    direct_miss.scalars.return_value.first.return_value = None

    cc_inactive = MagicMock()
    cc_inactive.scalars.return_value.first.return_value = False

    session.execute.side_effect = [direct_miss, cc_inactive]

    repo = ProjectEnrichmentRepository()
    result = await repo.get_is_active_for_project(session, "proj-cc-inactive")

    assert result is False
