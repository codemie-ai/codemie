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

"""Admin user views report the effective default project, stored or derived (EPMCDME-15738)."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.models.user_management import UserDB, UserProject
from codemie.service.user.user_management_service import UserManagementService

EMAIL = "alice@example.com"


def _user(user_id: str = "target_user", email: str = EMAIL) -> UserDB:
    return UserDB(
        id=user_id,
        username=user_id,
        email=email,
        name="Alice",
        user_type="regular",
        is_active=True,
        is_admin=False,
        auth_source="local",
        email_verified=True,
        project_limit=3,
    )


def _memberships(*rows: tuple[str, bool], user_id: str = "target_user") -> list[UserProject]:
    return [
        UserProject(user_id=user_id, project_name=name, is_project_admin=False, is_default=is_default)
        for name, is_default in rows
    ]


def _defaults(projects) -> dict[str, bool]:
    return {p.name: p.is_default for p in projects}


class TestUserDetailEffectiveDefault:
    @pytest.mark.parametrize(
        "rows,expected_default",
        [
            ((("zeta", False), (EMAIL, False), ("alpha", False)), EMAIL),
            ((("zeta", False), ("alpha", False)), "alpha"),
            ((("zeta", True), (EMAIL, False), ("alpha", False)), "zeta"),
        ],
        ids=["personal-fallback", "alphabetical-fallback", "stored-default"],
    )
    @patch("codemie.repository.user_project_repository.user_project_repository.get_by_user_id")
    @patch("codemie.repository.user_project_repository.user_project_repository.get_visible_projects_for_user")
    @patch("codemie.service.user.user_management_service.user_repository")
    def test_exactly_one_project_is_default(
        self, mock_user_repo, mock_get_visible, mock_get_all, rows, expected_default
    ):
        memberships = _memberships(*rows)
        mock_user_repo.get_by_id.return_value = _user()
        mock_user_repo.get_user_knowledge_bases.return_value = []
        mock_get_visible.return_value = memberships
        mock_get_all.return_value = memberships

        result = UserManagementService.get_user_with_relationships(
            MagicMock(), "target_user", "admin", is_admin=True, is_project_admin=False
        )

        defaults = _defaults(result.projects)
        assert [name for name, is_default in defaults.items() if is_default] == [expected_default]

    @patch("codemie.repository.user_project_repository.user_project_repository.get_by_user_id")
    @patch("codemie.repository.user_project_repository.user_project_repository.get_admin_visible_projects_for_user")
    @patch("codemie.service.user.user_management_service.user_repository")
    def test_hidden_personal_project_still_wins(self, mock_user_repo, mock_get_admin_visible, mock_get_all):
        """A project admin cannot see the personal project, so no visible row may claim the default."""
        mock_user_repo.get_by_id.return_value = _user()
        mock_user_repo.get_user_knowledge_bases.return_value = []
        mock_get_admin_visible.return_value = _memberships(("alpha", False))
        mock_get_all.return_value = _memberships(("alpha", False), (EMAIL, False))

        result = UserManagementService.get_user_with_relationships(
            MagicMock(), "target_user", "project_admin", is_admin=False, is_project_admin=True
        )

        assert _defaults(result.projects) == {"alpha": False}


class TestListUsersEffectiveDefault:
    @patch("codemie.repository.user_project_repository.user_project_repository.filter_visible_projects_from_map")
    @patch("codemie.service.user.user_management_service.user_repository")
    def test_each_user_gets_one_effective_default(self, mock_user_repo, mock_filter_visible):
        alice, bob = _user("alice", EMAIL), _user("bob", "bob@example.com")
        projects_map = {
            "alice": _memberships(("zeta", False), (EMAIL, False), user_id="alice"),
            "bob": _memberships(("zeta", False), ("alpha", False), user_id="bob"),
        }
        mock_user_repo.count_users.return_value = 2
        mock_user_repo.query_users.return_value = [alice, bob]
        mock_user_repo.fetch_projects_map.return_value = projects_map
        mock_user_repo.fetch_budget_assignments_map.return_value = {}
        mock_filter_visible.return_value = projects_map

        result = UserManagementService.list_users(MagicMock(), "admin", is_project_admin=True)

        by_id = {item.id: _defaults(item.projects) for item in result.data}
        assert by_id == {
            "alice": {"zeta": False, EMAIL: True},
            "bob": {"zeta": False, "alpha": True},
        }

    @patch("codemie.repository.user_project_repository.user_project_repository.filter_visible_projects_from_map")
    @patch("codemie.service.user.user_management_service.user_repository")
    def test_default_is_decided_before_visibility_filtering(self, mock_user_repo, mock_filter_visible):
        alice = _user("alice", EMAIL)
        mock_user_repo.count_users.return_value = 1
        mock_user_repo.query_users.return_value = [alice]
        mock_user_repo.fetch_projects_map.return_value = {
            "alice": _memberships(("alpha", False), (EMAIL, False), user_id="alice")
        }
        mock_user_repo.fetch_budget_assignments_map.return_value = {}
        mock_filter_visible.return_value = {"alice": _memberships(("alpha", False), user_id="alice")}

        result = UserManagementService.list_users(MagicMock(), "project_admin", is_project_admin=True)

        assert _defaults(result.data[0].projects) == {"alpha": False}
