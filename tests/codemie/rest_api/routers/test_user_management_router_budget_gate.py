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

"""budgetManagement gate on the user budget routes (EPMCDME-14536)."""

from unittest.mock import patch

import pytest

from codemie.configs.customer_config import CustomerConfig
from codemie.core.exceptions import ExtendedHTTPException
from codemie.enterprise.litellm.budget_categories import BudgetCategory
from codemie.rest_api.routers.user_management_router import (
    BulkUserBudgetSetRequest,
    UserBudgetAssignRequest,
    UserBudgetResetRequest,
    bulk_set_user_budgets,
    get_user_budgets,
    reset_user_budget_spending,
    set_user_budgets,
)
from codemie.rest_api.security.user import User

ROUTER = "codemie.rest_api.routers.user_management_router"


def _admin() -> User:
    return User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)


def _route_calls(user: User) -> dict:
    return {
        "bulk_put": lambda: bulk_set_user_budgets(
            data=BulkUserBudgetSetRequest(user_ids=["u-2"], assignments={BudgetCategory.PLATFORM: None}),
            user=user,
            _=None,
        ),
        "get": lambda: get_user_budgets(user_id="u-2", user=user, _=None),
        "reset_post": lambda: reset_user_budget_spending(
            user_id="u-2", data=UserBudgetResetRequest(), user=user, _=None
        ),
        "put": lambda: set_user_budgets(
            user_id="u-2",
            data=UserBudgetAssignRequest(assignments={BudgetCategory.PLATFORM: None}),
            user=user,
            _=None,
        ),
    }


@pytest.mark.anyio
@pytest.mark.parametrize("route", ["bulk_put", "get", "reset_post", "put"])
async def test_user_budget_route_is_403_when_budget_management_is_disabled(route):
    with (
        patch(f"{ROUTER}.config") as mock_config,
        patch(f"{ROUTER}.require_litellm_enabled"),
        patch(f"{ROUTER}.budget_service") as mock_budget_service,
        patch(f"{ROUTER}.budget_repository") as mock_budget_repository,
        patch(f"{ROUTER}.get_async_session") as mock_session,
        patch.object(CustomerConfig, "is_feature_enabled", return_value=False),
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True

        with pytest.raises(ExtendedHTTPException) as raised:
            await _route_calls(_admin())[route]()

    assert raised.value.code == 403
    mock_session.assert_not_called()
    assert not mock_budget_service.mock_calls
    assert not mock_budget_repository.mock_calls
