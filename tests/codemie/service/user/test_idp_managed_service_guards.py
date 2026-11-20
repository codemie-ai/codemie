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

"""Tests for IDP-managed guards in service layer.

Covers:
- AuthenticationService.create_user_from_idp is NOT blocked when IDP_MANAGED_ENABLED=True (first login must work)
  and skips the personal (email) project in that mode
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.user.authentication_service import AuthenticationService


class TestCreateUserFromIdp:
    """First-login user creation must keep working when IDP_MANAGED_ENABLED=True."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "idp_managed, expected_projects",
        [
            pytest.param(True, ["proj-a", "proj-b"], id="idp-managed-no-personal-project"),
            pytest.param(False, ["proj-a", "proj-b", "jdoe@example.com"], id="not-managed-personal-project"),
        ],
    )
    async def test_creates_user_and_bootstraps_projects(self, idp_managed, expected_projects):
        user_id = "8f14e45f-ceea-467a-9575-0f3a1f4e5b6c"
        idp_user = SimpleNamespace(
            id=user_id,
            username="jdoe",
            name="Jane Doe",
            email="jdoe@example.com",
            picture="",
            user_type="regular",
            roles=[],
            project_names=["proj-a", "proj-b"],
            admin_project_names=["proj-a"],
            knowledge_bases=[],
        )
        created = SimpleNamespace(id=user_id, email="jdoe@example.com", user_type="regular")

        with (
            patch("codemie.service.user.authentication_service.config") as mock_config,
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch("codemie.service.user.authentication_service.activity_event_repository") as mock_activity_repo,
            patch("codemie.service.user.authentication_service.user_project_repository") as mock_project_repo,
            patch.object(AuthenticationService, "_ensure_projects_exist", new=AsyncMock()) as mock_ensure,
        ):
            mock_config.IDP_MANAGED_ENABLED = idp_managed
            mock_config.IDP_PROVIDER = "keycloak"
            mock_config.ADMIN_USER_ID = "admin-id"
            mock_config.ADMIN_ROLE_NAME = "admin"
            mock_config.USER_PROJECT_LIMIT = 10
            mock_user_repo.acreate = AsyncMock(return_value=created)
            mock_activity_repo.async_insert = AsyncMock()
            mock_project_repo.aadd_project = AsyncMock()
            session = MagicMock()

            result = await AuthenticationService.create_user_from_idp(session=session, idp_user=idp_user)

        assert result is created
        mock_ensure.assert_awaited_once_with(expected_projects)
        assert [call.args[2:] for call in mock_project_repo.aadd_project.await_args_list] == [
            ("proj-a", True),
            ("proj-b", False),
        ]
