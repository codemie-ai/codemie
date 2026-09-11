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

from unittest.mock import AsyncMock, patch

import pytest

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.budget.project_budget_service import ProjectBudgetService


# ==================== _ensure_project_is_active ====================


@pytest.mark.asyncio
async def test_ensure_project_is_active_passes_when_active():
    """is_active=True → no exception."""
    session = AsyncMock()
    with patch("codemie.service.budget.project_budget_service.project_enrichment_repository") as mock_repo:
        mock_repo.get_is_active_for_project = AsyncMock(return_value=True)
        await ProjectBudgetService._ensure_project_is_active(session, "proj-ok")

    mock_repo.get_is_active_for_project.assert_called_once_with(session, "proj-ok")


@pytest.mark.asyncio
async def test_ensure_project_is_active_raises_422_when_inactive():
    """is_active=False → 422 with 'inactive' message."""
    session = AsyncMock()
    with patch("codemie.service.budget.project_budget_service.project_enrichment_repository") as mock_repo:
        mock_repo.get_is_active_for_project = AsyncMock(return_value=False)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await ProjectBudgetService._ensure_project_is_active(session, "proj-inactive")

    assert exc_info.value.code == 422
    assert "inactive" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_ensure_project_is_active_raises_422_when_no_enrichment():
    """is_active=None (no enrichment) → 422 with 'no enrichment' message."""
    session = AsyncMock()
    with patch("codemie.service.budget.project_budget_service.project_enrichment_repository") as mock_repo:
        mock_repo.get_is_active_for_project = AsyncMock(return_value=None)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await ProjectBudgetService._ensure_project_is_active(session, "proj-orphan")

    assert exc_info.value.code == 422
    assert "enrichment" in exc_info.value.message.lower()


# ==================== create_project_budget integration ====================


def _make_payload(project_name: str = "proj-x"):
    from types import SimpleNamespace
    from codemie.service.budget.budget_enums import BudgetCategory

    return SimpleNamespace(
        budget_id="bgt",
        project_name=project_name,
        budget_category=BudgetCategory.PLATFORM,
        name="Test Budget",
        description=None,
        soft_budget=10.0,
        max_budget=100.0,
        budget_duration="30d",
        allocation_mode="equal",
        models=None,
    )


@pytest.mark.asyncio
async def test_create_project_budget_blocked_when_no_enrichment():
    """create_project_budget raises 422 before touching DB if no enrichment."""
    session = AsyncMock()
    service = ProjectBudgetService()
    config.BUDGET_STOP_ENABLED = True

    try:
        with (
            patch.object(
                ProjectBudgetService,
                "_ensure_project_exists",
                new=AsyncMock(),
            ),
            patch("codemie.service.budget.project_budget_service.project_enrichment_repository") as mock_repo,
        ):
            mock_repo.get_is_active_for_project = AsyncMock(return_value=None)

            with pytest.raises(ExtendedHTTPException) as exc_info:
                await service.create_project_budget(session, _make_payload(), actor_id="admin-1")

        assert exc_info.value.code == 422
        assert "enrichment" in exc_info.value.message.lower()
        session.execute.assert_not_called()
    finally:
        config.BUDGET_STOP_ENABLED = False


@pytest.mark.asyncio
async def test_create_project_budget_blocked_when_project_inactive():
    """create_project_budget raises 422 before touching DB if project inactive."""
    session = AsyncMock()
    service = ProjectBudgetService()
    config.BUDGET_STOP_ENABLED = True

    try:
        with (
            patch.object(
                ProjectBudgetService,
                "_ensure_project_exists",
                new=AsyncMock(),
            ),
            patch("codemie.service.budget.project_budget_service.project_enrichment_repository") as mock_repo,
        ):
            mock_repo.get_is_active_for_project = AsyncMock(return_value=False)

            with pytest.raises(ExtendedHTTPException) as exc_info:
                await service.create_project_budget(session, _make_payload(), actor_id="admin-1")

        assert exc_info.value.code == 422
        assert "inactive" in exc_info.value.message.lower()
        session.execute.assert_not_called()
    finally:
        config.BUDGET_STOP_ENABLED = False


@pytest.mark.asyncio
async def test_create_project_budget_proceeds_past_enrichment_guard_when_active():
    """create_project_budget passes the enrichment guard and continues to DB steps."""
    session = AsyncMock()
    service = ProjectBudgetService()
    config.BUDGET_STOP_ENABLED = True

    try:
        with (
            patch.object(
                ProjectBudgetService,
                "_ensure_project_exists",
                new=AsyncMock(),
            ),
            patch("codemie.service.budget.project_budget_service.project_enrichment_repository") as mock_repo,
            patch(
                "codemie.service.budget.project_budget_service.project_budget_assignment_repository"
            ) as mock_pba_repo,
        ):
            mock_repo.get_is_active_for_project = AsyncMock(return_value=True)
            mock_pba_repo.get_active_by_project_category = AsyncMock(return_value=None)
            from types import SimpleNamespace
            from codemie.repository.budget_repository import BudgetRepository

            existing = SimpleNamespace(deleted_at=None)
            with patch.object(BudgetRepository, "get_by_name", new=AsyncMock(return_value=existing)):
                with pytest.raises(ExtendedHTTPException) as exc_info:
                    await service.create_project_budget(session, _make_payload(), actor_id="admin-1")

        # Enrichment guard passed — error is about duplicate name (409), not enrichment (422)
        assert exc_info.value.code == 409
    finally:
        config.BUDGET_STOP_ENABLED = False
