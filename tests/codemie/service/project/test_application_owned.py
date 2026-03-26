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

"""Unit tests for Application.is_owned_by / is_managed_by / is_shared_with and Ability.can integration."""

from unittest.mock import patch


from codemie.configs import config
from codemie.core.ability import Ability, Action
from codemie.core.models import Application
from codemie.rest_api.security.user import User


def make_app(project_type: str, created_by: str | None = "owner-1") -> Application:
    return Application(
        id="test-proj",
        name="test-proj",
        project_type=project_type,
        created_by=created_by,
    )


def make_user(
    user_id: str = "user-1",
    is_admin: bool = False,
    admin_project_names: list[str] | None = None,
    project_names: list[str] | None = None,
) -> User:
    return User(
        id=user_id,
        username="testuser",
        is_admin=is_admin,
        admin_project_names=admin_project_names or [],
        project_names=project_names or [],
    )


class TestApplicationIsOwnedBy:
    def test_creator_of_personal_project(self):
        app = make_app("personal", created_by="owner-1")
        user = make_user("owner-1")
        assert app.is_owned_by(user) is True

    def test_non_creator_of_personal_project(self):
        app = make_app("personal", created_by="owner-1")
        user = make_user("other-user")
        assert app.is_owned_by(user) is False

    def test_created_by_none_returns_false(self):
        app = make_app("shared", created_by=None)
        user = make_user("any-user")
        assert app.is_owned_by(user) is False

    def test_super_admin_who_is_not_creator(self):
        app = make_app("shared", created_by="owner-1")
        user = make_user("super-admin", is_admin=True)
        assert app.is_owned_by(user) is False

    def test_super_admin_who_is_also_creator(self):
        app = make_app("shared", created_by="super-admin")
        user = make_user("super-admin", is_admin=True)
        assert app.is_owned_by(user) is True


@patch.object(config, "ENV", "dev")
@patch.object(config, "ENABLE_USER_MANAGEMENT", True)
class TestApplicationIsManagedBy:
    def test_project_admin_of_project(self):
        app = make_app("shared", created_by="owner-1")
        user = make_user("admin-1", admin_project_names=["test-proj"])
        assert app.is_managed_by(user) is True

    def test_admin_of_different_project(self):
        app = make_app("shared", created_by="owner-1")
        user = make_user("admin-1", admin_project_names=["other-proj"])
        assert app.is_managed_by(user) is False

    def test_regular_member_of_shared_project(self):
        app = make_app("shared", created_by="owner-1")
        user = make_user("member-1", project_names=["test-proj"])
        assert app.is_managed_by(user) is False

    def test_super_admin_always_true(self):
        app = make_app("shared", created_by="owner-1")
        user = make_user("super-admin", is_admin=True)
        assert app.is_managed_by(user) is True

    def test_super_admin_personal_project(self):
        app = make_app("personal", created_by="owner-1")
        user = make_user("super-admin", is_admin=True)
        assert app.is_managed_by(user) is True


@patch.object(config, "ENV", "dev")
@patch.object(config, "ENABLE_USER_MANAGEMENT", True)
class TestApplicationIsSharedWith:
    def test_super_admin_shared_project(self):
        app = make_app("shared")
        user = make_user("super-admin", is_admin=True)
        assert app.is_shared_with(user) is True

    def test_super_admin_personal_project(self):
        app = make_app("personal")
        user = make_user("super-admin", is_admin=True)
        assert app.is_shared_with(user) is True

    def test_regular_member_of_shared_project(self):
        app = make_app("shared")
        user = make_user("member-1", project_names=["test-proj"])
        assert app.is_shared_with(user) is True

    def test_non_member_of_shared_project(self):
        app = make_app("shared")
        user = make_user("stranger", project_names=["other-proj"])
        assert app.is_shared_with(user) is False

    def test_personal_project_non_super_admin_returns_false(self):
        app = make_app("personal")
        user = make_user("member-1", project_names=["test-proj"])
        assert app.is_shared_with(user) is False

    def test_personal_project_creator_is_not_shared_with(self):
        # is_shared_with: personal + non-super-admin → False (is_owned_by handles creator access)
        app = make_app("personal", created_by="creator-1")
        user = make_user("creator-1", project_names=["test-proj"])
        assert app.is_shared_with(user) is False


@patch.object(config, "ENV", "dev")
@patch.object(config, "ENABLE_USER_MANAGEMENT", True)
class TestAbilityCanIntegration:
    """Integration tests: Ability.can() with Application using the full permission matrix."""

    def test_super_admin_can_read_write_delete(self):
        app = make_app("shared", created_by="owner-1")
        user = make_user("super-admin", is_admin=True)
        ability = Ability(user)
        assert ability.can(Action.READ, app) is True
        assert ability.can(Action.WRITE, app) is True
        assert ability.can(Action.DELETE, app) is True

    def test_project_admin_can_read_write_delete(self):
        app = make_app("shared", created_by="owner-1")
        user = make_user("admin-1", admin_project_names=["test-proj"])
        ability = Ability(user)
        assert ability.can(Action.READ, app) is True
        assert ability.can(Action.WRITE, app) is True
        assert ability.can(Action.DELETE, app) is True

    def test_regular_member_can_read_but_not_write_or_delete(self):
        app = make_app("shared", created_by="owner-1")
        user = make_user("member-1", project_names=["test-proj"])
        ability = Ability(user)
        assert ability.can(Action.READ, app) is True
        assert ability.can(Action.WRITE, app) is False
        assert ability.can(Action.DELETE, app) is False

    def test_personal_project_creator_can_read_write_delete(self):
        app = make_app("personal", created_by="creator-1")
        user = make_user("creator-1")
        ability = Ability(user)
        assert ability.can(Action.READ, app) is True
        assert ability.can(Action.WRITE, app) is True
        assert ability.can(Action.DELETE, app) is True

    def test_personal_project_non_creator_cannot_do_anything(self):
        app = make_app("personal", created_by="creator-1")
        user = make_user("other-user")
        ability = Ability(user)
        assert ability.can(Action.READ, app) is False
        assert ability.can(Action.WRITE, app) is False
        assert ability.can(Action.DELETE, app) is False

    def test_non_member_of_shared_project_cannot_do_anything(self):
        app = make_app("shared", created_by="owner-1")
        user = make_user("stranger")
        ability = Ability(user)
        assert ability.can(Action.READ, app) is False
        assert ability.can(Action.WRITE, app) is False
        assert ability.can(Action.DELETE, app) is False
