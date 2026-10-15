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

"""Tests for the personal-project budget creation guard.

Covers `_ensure_project_budget_eligible` directly and the two creation
entry points (`create_project_budget`, `create_project_budget_group`) that
call it, confirming personal projects are rejected before any DB writes.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.service.budget.project_budget_service import ProjectBudgetService


def _mock_session_returning(app) -> AsyncMock:
    """Build a session whose execute(select(Application)...) returns `app`."""
    scalars_mock = MagicMock()
    scalars_mock.first.return_value = app
    result_mock = MagicMock()
    result_mock.scalars.return_value = scalars_mock
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_mock)
    return session


def _make_application(project_type: str) -> SimpleNamespace:
    return SimpleNamespace(name="proj-x", deleted_at=None, project_type=project_type)


# ==================== _ensure_project_budget_eligible ====================


@pytest.mark.asyncio
async def test_ensure_project_budget_eligible_passes_for_shared_project():
    app = _make_application(Application.ProjectType.SHARED)
    session = _mock_session_returning(app)

    await ProjectBudgetService._ensure_project_budget_eligible(session, "proj-x")

    session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_project_budget_eligible_raises_404_when_project_missing():
    session = _mock_session_returning(None)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        await ProjectBudgetService._ensure_project_budget_eligible(session, "proj-missing")

    assert exc_info.value.code == 404
    assert "not found" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_ensure_project_budget_eligible_raises_403_for_personal_project():
    app = _make_application(Application.ProjectType.PERSONAL)
    session = _mock_session_returning(app)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        await ProjectBudgetService._ensure_project_budget_eligible(session, "proj-x")

    assert exc_info.value.code == 403
    assert "personal" in exc_info.value.message.lower()


# ==================== create_project_budget integration ====================


def _make_budget_payload(project_name: str = "proj-x"):
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
async def test_create_project_budget_blocked_for_personal_project():
    """create_project_budget raises 403 before touching DB further for a personal project."""
    app = _make_application(Application.ProjectType.PERSONAL)
    session = _mock_session_returning(app)
    service = ProjectBudgetService()

    with pytest.raises(ExtendedHTTPException) as exc_info:
        await service.create_project_budget(session, _make_budget_payload(), actor_id="admin-1")

    assert exc_info.value.code == 403
    assert "personal" in exc_info.value.message.lower()
    # Only the eligibility lookup ran — no further DB activity for the budget itself.
    session.execute.assert_called_once()


# ==================== create_project_budget_group integration ====================


def _make_group_payload(project_name: str = "proj-x"):
    return SimpleNamespace(
        project_name=project_name,
        name="Test Group",
        total_amount=100.0,
        budget_duration="30d",
        description=None,
        categories={"platform": SimpleNamespace(pct=100.0)},
        notification_owner_email=None,
        soft_limit_notify_once=False,
        soft_limit_notification_enabled=False,
    )


@pytest.mark.asyncio
async def test_create_project_budget_group_blocked_for_personal_project():
    """create_project_budget_group raises 403 before checking for an existing group."""
    app = _make_application(Application.ProjectType.PERSONAL)
    session = _mock_session_returning(app)
    service = ProjectBudgetService()

    with (
        patch("codemie.service.budget.project_budget_service.project_budget_group_repository") as mock_group_repo,
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await service.create_project_budget_group(session, _make_group_payload(), actor_id="admin-1")

        mock_group_repo.get_active_by_project.assert_not_called()

    assert exc_info.value.code == 403
    assert "personal" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_create_project_budget_group_proceeds_past_guard_for_shared_project():
    """A shared project passes the guard and reaches the existing-group check."""
    app = _make_application(Application.ProjectType.SHARED)
    session = _mock_session_returning(app)
    service = ProjectBudgetService()

    with patch("codemie.service.budget.project_budget_service.project_budget_group_repository") as mock_group_repo:
        existing = SimpleNamespace(id="existing-group-id")
        mock_group_repo.get_active_by_project = AsyncMock(return_value=existing)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await service.create_project_budget_group(session, _make_group_payload(), actor_id="admin-1")

    # Guard passed — error is about an already-active group (409), not personal (403).
    assert exc_info.value.code == 409
