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

"""Exercise admin operations through the actual LiteLLM create-or-update path."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from codemie.enterprise.litellm.budget_provider_adapter import LiteLLMBudgetEnforcementProvider
from codemie.repository.project_budget_repository import project_member_budget_assignment_repository as repository
from codemie.service.budget.budget_models import build_shared_project_budget_id, build_override_project_budget_id
from codemie.service.budget.project_budget_service import ProjectBudgetService


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["rebalance", "override"])
@pytest.mark.parametrize("project, category", [("project-a", "platform"), ("project-b", "cli")])
async def test_unused_member_is_provisioned_once_in_explicit_project(mocker, action, project, category):
    enterprise = pytest.importorskip("codemie_enterprise.litellm.service")
    budget_id = f"{project}-{category}"
    shared_id = build_shared_project_budget_id(budget_id)
    override_id = build_override_project_budget_id(budget_id, "new-user")

    def ref(user):
        return f"codemie:project:{project}:category:{category}:user:{user}"

    # Existing usage, including this user's unrelated project/global accounts, must be preserved.
    customers = {
        ref("existing-user"): {"user_id": ref("existing-user"), "budget_id": shared_id, "spend": 7.0},
        "new-user": {"user_id": "new-user", "budget_id": "global", "spend": 3.0},
        "codemie:project:unrelated:category:platform:user:new-user": {
            "user_id": "codemie:project:unrelated:category:platform:user:new-user",
            "budget_id": "unrelated",
            "spend": 5.0,
        },
    }
    original_customers = {key: dict(value) for key, value in customers.items()}
    budgets = {shared_id: {"budget_id": shared_id, "max_budget": 50.0}}
    created_customers = []

    def get(path, *, params):
        assert path == "/customer/info"
        user_id = params["end_user_id"]
        if user_id not in customers:
            request = httpx.Request("GET", "http://litellm.test/customer/info")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError(f"end-user {user_id} does not exist in db", request=request, response=response)
        return dict(customers[user_id])

    def post(path, *, data):
        if path == "/budget/info":
            return [dict(budgets[key]) for key in data["budgets"] if key in budgets]
        if path in {"/budget/new", "/budget/update"}:
            budgets[data["budget_id"]] = dict(data)
            return dict(data)
        assert path == "/customer/new"
        assert data["user_id"] not in customers, "duplicate customer creation"
        created_customers.append(data["user_id"])
        customers[data["user_id"]] = dict(data)
        return dict(data)

    client = object.__new__(enterprise.LiteLLMService)
    client.api_client = MagicMock(get=MagicMock(side_effect=get), post=MagicMock(side_effect=post))
    provider = LiteLLMBudgetEnforcementProvider(service=client)
    budget = SimpleNamespace(
        budget_id=budget_id,
        max_budget=100.0,
        soft_budget=80.0,
        budget_duration="30d",
        budget_reset_at=None,
        budget_category=category,
        created_by="admin",
    )
    allocations = [
        SimpleNamespace(
            id=user,
            user_id=user,
            project_name=project,
            budget_category=category,
            project_budget_id=budget_id,
            allocation_mode="equal",
            allocated_max_budget=50.0,
            allocated_soft_budget=40.0,
            shared_budget_id=shared_id,
            effective_budget_id=shared_id,
            override_budget_id=None,
            provider_metadata={"raw": {"provider_member_ref": ref(user)}} if user == "existing-user" else None,
            sync_status="ok" if user == "existing-user" else "pending_provider_sync",
        )
        for user in ("existing-user", "new-user")
    ]
    by_id = {row.id: row for row in allocations}

    async def update(_session, allocation_id=None, user_id=None, **values):
        row = by_id[allocation_id or user_id]
        for key, value in values.items():
            setattr(row, key, value)
        return row

    service = ProjectBudgetService()
    session = AsyncMock()
    mocker.patch.object(
        service,
        "get_project_budget",
        AsyncMock(
            return_value=(
                budget,
                SimpleNamespace(project_name=project, budget_category=category),
                allocations,
            )
        ),
    )
    mocker.patch.object(repository, "get_active_by_budget_id", AsyncMock(return_value=allocations))
    for method in (
        "update_allocation",
        "update_member_override",
        "update_member_budget_routing",
        "update_provider_metadata",
    ):
        mocker.patch.object(repository, method, AsyncMock(side_effect=update))
    mocker.patch.object(
        service, "_ensure_override_child_budget", AsyncMock(return_value=SimpleNamespace(budget_id=override_id))
    )
    mocker.patch.object(service, "_ensure_shared_child_budget_after_resync", AsyncMock())
    mocker.patch.object(service, "_persist_child_budget_provider_state", AsyncMock())
    mocker.patch("codemie.service.budget.project_budget_service.get_active_provider", return_value=provider)
    mocker.patch("codemie.service.settings.settings.SettingsService.get_enforce_member_spend_limits", return_value=True)
    mocker.patch("codemie.service.budget.project_budget_service.activity_event_repository.async_insert", AsyncMock())

    if action == "override":
        await service.override_member_allocation(
            session,
            budget_id,
            "new-user",
            10.0,
            8.0,
            "manual override",
            "admin",
        )
    else:
        await service.rebalance_project_budget(session, budget_id, "admin")
    # Repeating the operation must update the same customer rather than create a duplicate.
    await service.rebalance_project_budget(session, budget_id, "admin")

    assert created_customers == [ref("new-user")]
    assert customers[ref("new-user")]["budget_id"] == (override_id if action == "override" else shared_id)
    for key, original in original_customers.items():
        assert customers[key] == original
    for row in allocations:
        assert row.sync_status == "ok"
        assert row.provider_metadata["raw"]["provider_member_ref"] == ref(row.user_id)
        assert row.provider_metadata["raw"]["provider_budget_id"] == row.effective_budget_id
    session.commit.assert_not_called()
