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

"""One rule for every grant: unknown user is 404, deactivated user is 409."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.user.active_user import require_active_user
from codemie.service.user.user_management_service import UserManagementService


def _repo(active=None, existing=None):
    repo = MagicMock()
    repo.get_active_by_id.return_value = active
    repo.get_by_id.return_value = existing
    return repo


def test_active_user_is_returned():
    user = MagicMock()

    assert require_active_user(MagicMock(), "u1", _repo(active=user, existing=user)) is user


def test_unknown_user_is_404():
    with pytest.raises(ExtendedHTTPException) as exc_info:
        require_active_user(MagicMock(), "ghost", _repo())

    assert exc_info.value.code == 404
    assert exc_info.value.message == "User not found"


def test_deactivated_user_is_409():
    with pytest.raises(ExtendedHTTPException) as exc_info:
        require_active_user(MagicMock(), "u1", _repo(existing=MagicMock(is_active=False)))

    assert exc_info.value.code == 409
    assert exc_info.value.message == "User is deactivated"


@pytest.mark.parametrize("role", ["is_admin", "is_maintainer", "is_auditor"])
def test_platform_role_grant_to_deactivated_user_is_409(role):
    repo = _repo(existing=MagicMock(is_active=False))

    with (
        patch("codemie.clients.postgres.get_session"),
        patch("codemie.service.user.user_management_service.user_repository", repo),
        pytest.raises(ExtendedHTTPException) as exc_info,
    ):
        UserManagementService.update_user_fields("u1", "admin-1", **{role: True})

    assert exc_info.value.code == 409
    assert exc_info.value.message == "User is deactivated"
