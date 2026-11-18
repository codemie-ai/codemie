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

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs.config import PredefinedBudgetConfig
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.user.user_management_service import UserManagementService


_SERVICE = "codemie.service.user.user_management_service"
_BUDGET = "codemie.service.budget.budget_service"
_GATE = "codemie.service.budget.feature_gate"


@pytest.fixture
def creation_flow():
    with (
        patch(f"{_SERVICE}.user_repository") as mock_user_repository,
        patch(f"{_SERVICE}.UserManagementService.get_user_with_relationships") as mock_get_user,
        patch(f"{_SERVICE}.UserManagementService.create_local_user") as mock_create,
        patch(f"{_SERVICE}.UserManagementService._validate_role_change_permissions") as mock_validate,
        patch("codemie.clients.postgres.get_session") as mock_get_session,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_validate.return_value = (False, False)
        mock_create.return_value = SimpleNamespace(id="user-1")
        mock_get_user.return_value = SimpleNamespace(id="user-1")
        mock_user_repository.get_by_id.return_value = None
        yield SimpleNamespace(session=mock_session)


@pytest.fixture
def flow_mocks(creation_flow):
    with patch(f"{_BUDGET}.budget_service") as mock_budget_service:
        yield SimpleNamespace(session=creation_flow.session, budget_service=mock_budget_service)


def _create(email="user@example.com", username="user1"):
    return UserManagementService.create_local_user_with_flow(
        email=email,
        username=username,
        password="secret",
        actor_user_id="admin-1",
    )


def test_create_local_user_with_flow_provisions_with_username_not_email(flow_mocks):
    _create(email="alice@corp.com", username="alice_corp")

    flow_mocks.budget_service.schedule_default_budget_provisioning.assert_called_once_with("user-1", "alice_corp")


def test_create_local_user_with_flow_runs_provisioning_after_commit(flow_mocks):
    order = []
    flow_mocks.session.commit.side_effect = lambda: order.append("commit")
    flow_mocks.budget_service.schedule_default_budget_provisioning.side_effect = lambda *a, **k: order.append(
        "provision"
    )

    result = _create()

    assert result.id == "user-1"
    assert order == ["commit", "provision"]


@pytest.mark.asyncio
async def test_create_local_user_with_flow_leaves_default_budget_assignments_for_the_new_user(creation_flow):
    assignments = {}
    both_written = asyncio.Event()

    async def get_budget_id(session, user_id, category):
        return assignments.get((user_id, category))

    async def upsert_assignment(session, user_id, category, budget_id, assigned_by):
        assignments[(user_id, category)] = budget_id
        if len(assignments) == 2:
            both_written.set()

    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    session_cm.__aexit__ = AsyncMock(return_value=False)
    provider = SimpleNamespace(
        provision_category_customer=AsyncMock(side_effect=lambda **kw: f"mirrored-{kw['budget_id']}")
    )
    predefined = [
        PredefinedBudgetConfig(budget_id="platform-default", name="p", max_budget=10.0, budget_category="platform"),
        PredefinedBudgetConfig(
            budget_id="premium-default", name="m", max_budget=10.0, budget_category="premium_models"
        ),
    ]

    with (
        patch(f"{_GATE}.is_budget_management_enabled", return_value=True),
        patch(f"{_GATE}.is_budget_enforcement_active", return_value=True),
        patch(f"{_BUDGET}.budget_config", SimpleNamespace(predefined_budgets=predefined)),
        patch(f"{_BUDGET}.get_active_provider", return_value=provider),
        patch(f"{_BUDGET}._budget_assignment_cache", {}),
        patch(
            f"{_BUDGET}.budget_repository",
            SimpleNamespace(
                get_user_category_budget_id=get_budget_id,
                upsert_user_category_assignment=upsert_assignment,
            ),
        ),
        patch("codemie.clients.postgres.get_async_session", return_value=session_cm),
        patch("codemie.core.event_loop.get_main_event_loop", return_value=None),
    ):
        result = _create(username="alice_corp")
        await asyncio.wait_for(both_written.wait(), timeout=2)

    assert result.id == "user-1"
    assert assignments == {
        ("user-1", BudgetCategory.PLATFORM): "mirrored-platform-default",
        ("user-1", BudgetCategory.PREMIUM_MODELS): "mirrored-premium-default",
    }
    assert {c.kwargs["username"] for c in provider.provision_category_customer.await_args_list} == {"alice_corp"}
