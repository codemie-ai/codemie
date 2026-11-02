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

"""Personal-project constraints on the Users-management membership endpoints.

The personal-project guard lives in ProjectAssignmentService, covered by
tests/codemie/service/project/test_project_assignment_service.py::TestPersonalProjectGuard.
These tests pin that UserAccessService propagates its 403/404 unchanged, without committing
or invalidating the auth cache.
"""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.user.user_access_service import UserAccessService

PAS_PATH = "codemie.service.project.project_assignment_service.project_assignment_service"
PERSONAL = "alice@example.com"

_OPERATIONS = [
    pytest.param(
        "assign_user_to_project",
        lambda actor: UserAccessService.grant_project_access("user-123", PERSONAL, False, actor),
        id="grant",
    ),
    pytest.param(
        "update_user_project_role",
        lambda actor: UserAccessService.update_user_project_access("user-123", PERSONAL, True, actor),
        id="update",
    ),
    pytest.param(
        "remove_user_from_project",
        lambda actor: UserAccessService.revoke_project_access("user-123", PERSONAL, actor),
        id="revoke",
    ),
]


@pytest.mark.parametrize(
    ("code", "message"),
    [
        pytest.param(403, "Cannot modify membership of a personal project", id="owner-or-admin-403"),
        pytest.param(404, "Project not found", id="unrelated-caller-404"),
    ],
)
@pytest.mark.parametrize(("pas_method", "call"), _OPERATIONS)
@patch("codemie.clients.postgres.get_session")
@patch("codemie.service.user.user_access_service.application_repository")
@patch(PAS_PATH)
def test_personal_project_guard_propagates_without_commit(
    mock_pas, mock_app_repo, mock_get_session, pas_method, call, code, message
):
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_app_repo.get_by_name.return_value = MagicMock(project_type="personal", created_by="other-user")
    getattr(mock_pas, pas_method).side_effect = ExtendedHTTPException(code=code, message=message)
    actor = MagicMock(id="admin-456", is_admin=False, is_maintainer=False, is_admin_or_maintainer=False)

    with patch.object(UserAccessService, "_invalidate_auth_cache") as mock_invalidate:
        with pytest.raises(ExtendedHTTPException) as exc_info:
            call(actor)

    assert exc_info.value.code == code
    assert exc_info.value.message == message
    getattr(mock_pas, pas_method).assert_called_once()
    mock_session.commit.assert_not_called()
    mock_invalidate.assert_not_called()
