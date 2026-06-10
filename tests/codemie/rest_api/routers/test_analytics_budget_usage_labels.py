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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.rest_api.security.user import User


@pytest.mark.asyncio
async def test_budget_usage_uses_username_as_subject_label():
    """Budget stats always use username as the subject label, regardless of email."""
    from codemie.rest_api.routers.analytics import get_user_budget_usage
    from codemie.service.analytics.handlers.budget_usage_service import _get_key_spending_columns

    mock_user = User(
        id="test-user-id",
        username="john_doe",
        email="john.doe@corp.com",
        project_names=[],
        admin_project_names=[],
    )

    mock_rows = [
        {
            "project_name": "john_doe",
            "current_spending": 15.5,
            "budget_reset_at": "2026-04-01T00:00:00Z",
            "time_until_reset": None,
            "budget_limit": 100.0,
            "total": 15.5,
        },
    ]

    mock_session = AsyncMock()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    captured_label = {}

    async def capture_get_budget_usage(session, subject_user_id, subject_label):
        captured_label["value"] = subject_label
        return _get_key_spending_columns(), mock_rows

    with patch("codemie.clients.postgres.get_async_session", return_value=mock_ctx):
        with patch(
            "codemie.service.analytics.handlers.budget_usage_service.BudgetUsageService.get_budget_usage",
            side_effect=capture_get_budget_usage,
        ):
            await get_user_budget_usage(user=mock_user, user_id=None)

    assert (
        captured_label["value"] == "john_doe"
    ), f"Expected username 'john_doe' as subject_label, got {captured_label['value']!r}"


@pytest.mark.asyncio
async def test_budget_usage_uses_username_even_when_email_differs():
    """When email != username (client deployment), username is still used — not email."""
    from codemie.rest_api.routers.analytics import get_user_budget_usage
    from codemie.service.analytics.handlers.budget_usage_service import _get_key_spending_columns

    mock_user = User(
        id="test-user-id",
        username="jsmith",
        email="john.smith@client.org",
        project_names=[],
        admin_project_names=[],
    )

    mock_rows = []
    mock_session = AsyncMock()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    captured_label = {}

    async def capture_get_budget_usage(session, subject_user_id, subject_label):
        captured_label["value"] = subject_label
        return _get_key_spending_columns(), mock_rows

    with patch("codemie.clients.postgres.get_async_session", return_value=mock_ctx):
        with patch(
            "codemie.service.analytics.handlers.budget_usage_service.BudgetUsageService.get_budget_usage",
            side_effect=capture_get_budget_usage,
        ):
            await get_user_budget_usage(user=mock_user, user_id=None)

    assert captured_label["value"] == "jsmith"
    assert captured_label["value"] != "john.smith@client.org"
