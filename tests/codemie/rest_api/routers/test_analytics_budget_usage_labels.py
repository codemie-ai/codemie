# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

from unittest.mock import patch

import pytest

from codemie.rest_api.security.user import User


@pytest.mark.asyncio
async def test_budget_usage_falls_back_to_username_when_email_missing():
    """Personal budget rows should still have a stable label when email is blank."""
    from codemie.enterprise.litellm.models import UserKeysSpending
    from codemie.rest_api.routers.analytics import get_user_budget_usage

    mock_user = User(
        id="test-user-id",
        username="maksim_yuzva@epam.com",
        email="",
        project_names=[],
        admin_project_names=[],
    )

    mock_personal_spending = {
        "total_spend": 15.5,
        "max_budget": 100.0,
        "budget_reset_at": "2026-04-01T00:00:00Z",
    }
    mock_premium_spending = {
        "total_spend": 1.25,
        "max_budget": 5.0,
        "budget_reset_at": "2026-04-02T00:00:00Z",
    }
    mock_cli_spending = {
        "total_spend": 3.75,
        "max_budget": 20.0,
        "budget_reset_at": "2026-04-03T00:00:00Z",
    }
    mock_keys_spending = UserKeysSpending(user_keys=[], project_keys=[])

    with patch("codemie.enterprise.litellm.dependencies.get_customer_spending", return_value=mock_personal_spending):
        with patch(
            "codemie.enterprise.litellm.dependencies.get_proxy_customer_spending",
            return_value=mock_cli_spending,
        ):
            with patch(
                "codemie.enterprise.litellm.dependencies.get_premium_customer_spending",
                return_value=mock_premium_spending,
            ):
                with patch("codemie.enterprise.litellm.dependencies.is_premium_models_enabled", return_value=True):
                    with patch(
                        "codemie.enterprise.litellm.dependencies.get_user_keys_spending",
                        return_value=mock_keys_spending,
                    ):
                        response = await get_user_budget_usage(user=mock_user)

    rows = response["data"]["rows"]
    assert rows[0]["project_name"] == mock_user.username
    assert rows[1]["project_name"] == f"{mock_user.username} (premium)"
    assert rows[2]["project_name"] == f"{mock_user.username} (cli)"
