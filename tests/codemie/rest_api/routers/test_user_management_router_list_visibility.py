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

"""Who sees deactivated users in GET /v1/admin/users: the default for each audience."""

import pytest

from codemie.rest_api.models.user_management import UserListFilters
from codemie.rest_api.security.user import User
from codemie.service.user.user_management_service import UserManagementService


def _user(**fields) -> User:
    # model_construct skips the validator that makes every user an admin when ENV=local
    return User.model_construct(id="u1", **{"is_admin": False, "is_maintainer": False, **fields})


def _restrict(user: User, **filters) -> UserListFilters:
    return UserManagementService.restrict_deactivated_visibility(user, UserListFilters(**filters))


@pytest.mark.parametrize(
    "audience",
    [{"is_admin": True}, {"is_maintainer": True}, {"is_auditor": True}],
    ids=["admin", "maintainer", "auditor"],
)
def test_privileged_audiences_keep_the_inclusive_default(audience):
    user = _user(**audience)

    assert _restrict(user).is_active is None
    assert _restrict(user, is_active=False).is_active is False


def test_project_admin_filtering_by_own_projects_keeps_the_inclusive_default():
    user = _user(admin_project_names=["alpha", "beta"])

    assert _restrict(user, projects=["alpha", "beta"]).is_active is None
    assert _restrict(user, projects=["alpha"], is_active=False).is_active is False


@pytest.mark.parametrize("projects", [None, [], ["alpha", "other"]], ids=["no-filter", "empty", "foreign-project"])
def test_project_admin_outside_own_projects_gets_active_only(projects):
    user = _user(admin_project_names=["alpha"])

    assert _restrict(user, projects=projects).is_active is True


@pytest.mark.parametrize("user_type", ["regular", "external"])
@pytest.mark.parametrize("is_active", [None, False])
def test_regular_and_external_users_get_active_only(user_type, is_active):
    user = _user(user_type=user_type)

    assert _restrict(user, projects=["alpha"], is_active=is_active).is_active is True
