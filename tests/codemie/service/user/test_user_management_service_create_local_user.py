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

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from codemie.service.user.user_management_service import UserManagementService


@patch("codemie.service.user.user_management_service.logger")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.UserManagementService.create_local_user")
@patch("codemie.service.user.user_management_service.UserManagementService._validate_role_change_permissions")
@patch("codemie.clients.postgres.get_session")
def test_create_local_user_with_flow_logs_budget_provision_warning(
    mock_get_session,
    mock_validate_permissions,
    mock_create_local_user,
    mock_get_user_with_relationships,
    mock_user_repository,
    mock_logger,
):
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_validate_permissions.return_value = (False, False)
    mock_create_local_user.return_value = SimpleNamespace(id="user-1")
    mock_get_user_with_relationships.return_value = SimpleNamespace(id="user-1")
    mock_user_repository.get_by_id.return_value = None
    provider = SimpleNamespace(provision_global_user=AsyncMock(side_effect=RuntimeError("provider unavailable")))

    with patch("codemie.service.budget.provider_registry.get_active_provider", return_value=provider):
        result = UserManagementService.create_local_user_with_flow(
            email="user@example.com",
            username="user1",
            password="secret",
            actor_user_id="admin-1",
        )

    assert result.id == "user-1"
    mock_logger.warning.assert_called_once()


@patch("codemie.service.user.user_management_service.logger")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.UserManagementService.create_local_user")
@patch("codemie.service.user.user_management_service.UserManagementService._validate_role_change_permissions")
@patch("codemie.clients.postgres.get_session")
def test_create_local_user_with_flow_provisions_with_username_not_email(
    mock_get_session,
    mock_validate_permissions,
    mock_create_local_user,
    mock_get_user_with_relationships,
    mock_user_repository,
    mock_logger,
):
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_validate_permissions.return_value = (False, False)
    mock_create_local_user.return_value = SimpleNamespace(id="user-1")
    mock_get_user_with_relationships.return_value = SimpleNamespace(id="user-1")
    mock_user_repository.get_by_id.return_value = None
    provision_mock = AsyncMock()
    provider = SimpleNamespace(provision_global_user=provision_mock)

    with patch("codemie.service.budget.provider_registry.get_active_provider", return_value=provider):
        UserManagementService.create_local_user_with_flow(
            email="alice@corp.com",
            username="alice_corp",
            password="secret",
            actor_user_id="admin-1",
        )

    provision_mock.assert_awaited_once()
    call_kwargs = provision_mock.call_args.kwargs
    assert call_kwargs["username"] == "alice_corp"
    assert "user_email" not in call_kwargs
