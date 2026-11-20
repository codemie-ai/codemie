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

"""In IDP managed mode the Admin role belongs to the identity provider; Maintainer, Auditor and user type stay editable."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.user_management import UserDB
from codemie.rest_api.security.user import User
from codemie.service.user.user_management_service import UserManagementService

_REPO = "codemie.service.user.user_management_service.user_repository"
_IDP_FLAG = "codemie.service.user.user_management_service.config.IDP_MANAGED_ENABLED"


def _user(user_id: str, **overrides) -> UserDB:
    defaults = {
        "id": user_id,
        "username": user_id,
        "email": f"{user_id}@example.com",
        "name": user_id,
        "user_type": "regular",
        "auth_source": "keycloak",
        "is_active": True,
        "is_admin": False,
        "is_maintainer": False,
        "email_verified": True,
        "project_limit": 3,
        "date": datetime.now(UTC),
        "update_date": datetime.now(UTC),
    }
    return UserDB(**{**defaults, **overrides})


def _actor() -> User:
    return User(id="maintainer-1", username="maintainer-1", name="Maintainer", email="maintainer@example.com")


class TestRejectIdpAdminChange:
    @pytest.mark.parametrize(
        "idp_managed, stored_admin, requested, rejected",
        [
            pytest.param(True, False, True, True, id="idp-promote-rejected"),
            pytest.param(True, True, False, True, id="idp-demote-rejected"),
            pytest.param(True, True, True, False, id="idp-echoing-the-current-value-is-fine"),
            pytest.param(True, False, False, False, id="idp-echoing-false-is-fine"),
            pytest.param(True, False, None, False, id="idp-flag-not-sent"),
            pytest.param(False, False, True, False, id="outside-idp-mode-promote-allowed"),
            pytest.param(False, True, False, False, id="outside-idp-mode-demote-allowed"),
        ],
    )
    def test_guard(self, idp_managed, stored_admin, requested, rejected):
        with patch(_IDP_FLAG, idp_managed), patch(_REPO) as repo:
            repo.get_by_id.return_value = _user("target", is_admin=stored_admin)

            if rejected:
                with pytest.raises(ExtendedHTTPException) as exc:
                    UserManagementService._reject_idp_admin_change(MagicMock(), "target", requested)
                assert exc.value.code == 403
                assert "identity provider" in exc.value.message
            else:
                UserManagementService._reject_idp_admin_change(MagicMock(), "target", requested)

    @pytest.mark.parametrize(
        "stored_maintainer, requested_maintainer, rejected",
        [
            pytest.param(False, True, False, id="becoming-a-maintainer-implies-admin"),
            pytest.param(True, None, False, id="already-a-maintainer-implies-admin"),
            pytest.param(False, None, True, id="not-a-maintainer-so-admin-change-rejected"),
            pytest.param(False, False, True, id="explicitly-not-a-maintainer-so-admin-change-rejected"),
            pytest.param(True, False, True, id="losing-maintainer-does-not-excuse-an-admin-change"),
        ],
    )
    def test_promoting_to_admin_is_allowed_only_together_with_maintainer(
        self, stored_maintainer, requested_maintainer, rejected
    ):
        """A client that sends is_admin=True along with Maintainer (the UI does) must not get a 403."""
        with patch(_IDP_FLAG, True), patch(_REPO) as repo:
            repo.get_by_id.return_value = _user("target", is_admin=False, is_maintainer=stored_maintainer)
            call = lambda: UserManagementService._reject_idp_admin_change(  # noqa: E731
                MagicMock(), "target", True, requested_maintainer
            )

            if rejected:
                with pytest.raises(ExtendedHTTPException) as exc:
                    call()
                assert exc.value.code == 403
            else:
                call()

    def test_unknown_target_is_left_to_the_normal_not_found_handling(self):
        with patch(_IDP_FLAG, True), patch(_REPO) as repo:
            repo.get_by_id.return_value = None

            UserManagementService._reject_idp_admin_change(MagicMock(), "ghost", True)


@patch(_IDP_FLAG, True)
@patch(_REPO)
@patch("codemie.clients.postgres.get_session")
class TestUpdateUserFieldsInIdpMode:
    def _wire(self, mock_get_session, repo, target):
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        maintainer = _user("maintainer-1", is_admin=True, is_maintainer=True)
        repo.get_by_id.side_effect = lambda _session, user_id: maintainer if user_id == "maintainer-1" else target
        repo.update.return_value = target

    def test_changing_is_admin_is_rejected_before_anything_is_written(self, mock_get_session, repo):
        self._wire(mock_get_session, repo, _user("target"))

        with pytest.raises(ExtendedHTTPException) as exc:
            UserManagementService.update_user_fields(user_id="target", actor_user=_actor(), is_admin=True)

        assert exc.value.code == 403
        repo.update.assert_not_called()

    def test_maintainer_and_auditor_stay_editable(self, mock_get_session, repo):
        self._wire(mock_get_session, repo, _user("target"))

        with patch.object(UserManagementService, "get_user_with_relationships"):
            UserManagementService.update_user_fields(
                user_id="target", actor_user=_actor(), is_maintainer=True, is_auditor=False
            )

        updates = repo.update.call_args.kwargs
        assert updates["is_maintainer"] is True
        # a maintainer is always an admin, whatever the identity provider says
        assert updates["is_admin"] is True

    def test_full_flag_set_with_maintainer_from_the_current_ui_is_accepted(self, mock_get_session, repo):
        """The UI sends is_admin=True together with is_maintainer=True; that must not hit the IDP admin guard."""
        self._wire(mock_get_session, repo, _user("target"))

        with patch.object(UserManagementService, "get_user_with_relationships"):
            UserManagementService.update_user_fields(
                user_id="target",
                actor_user=_actor(),
                is_admin=True,
                is_maintainer=True,
                is_auditor=False,
            )

        updates = repo.update.call_args.kwargs
        assert (updates["is_admin"], updates["is_maintainer"]) == (True, True)

    def test_auditor_alone_is_editable(self, mock_get_session, repo):
        self._wire(mock_get_session, repo, _user("target"))

        with patch.object(UserManagementService, "get_user_with_relationships"):
            UserManagementService.update_user_fields(user_id="target", actor_user=_actor(), is_auditor=True)

        assert repo.update.call_args.kwargs["is_auditor"] is True

    def test_user_type_stays_editable(self, mock_get_session, repo):
        self._wire(mock_get_session, repo, _user("target"))

        with patch.object(UserManagementService, "get_user_with_relationships"):
            UserManagementService.update_user_fields(user_id="target", actor_user=_actor(), user_type="external")

        assert repo.update.call_args.kwargs["user_type"] == "external"
