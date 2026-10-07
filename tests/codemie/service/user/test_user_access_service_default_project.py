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

"""Tests for UserAccessService.set_default_project / clear_default_project (EPMCDME-15110)."""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.user.user_access_service import UserAccessService


class TestSetDefaultProject:
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_sets_default_for_existing_membership(self, mock_user_repo, mock_app_repo, mock_upr, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="shared")
        mock_upr.set_default.return_value = MagicMock(user_id="user-1", project_name="proj-a", is_default=True)

        result = UserAccessService.set_default_project(
            user_id="user-1",
            project_name="proj-a",
            actor=MagicMock(id="admin-1", is_admin_or_maintainer=True),
        )

        assert result == {"message": "Default project set successfully"}
        mock_upr.set_default.assert_called_once_with(mock_session, "user-1", "proj-a")

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_rejects_when_not_a_member(self, mock_user_repo, mock_app_repo, mock_upr, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="shared")
        mock_upr.set_default.return_value = None  # repository: membership doesn't exist

        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.set_default_project(
                user_id="user-1",
                project_name="proj-not-a-member",
                actor=MagicMock(id="admin-1", is_admin_or_maintainer=True),
            )

        assert exc_info.value.code == 404
        mock_upr.set_default.assert_called_once()

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_rejects_unknown_user(self, mock_user_repo, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.set_default_project(user_id="ghost", project_name="proj-a", actor=MagicMock(id="admin-1"))

        assert exc_info.value.code == 404

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_allows_personal_project_membership(self, mock_user_repo, mock_app_repo, mock_upr, mock_get_session):
        """Story AC: any of the user's projects can be the default — the personal one included."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="personal", created_by="user-1")
        mock_upr.set_default.return_value = MagicMock(project_name="alice@example.com", is_default=True)

        result = UserAccessService.set_default_project(
            user_id="user-1",
            project_name="alice@example.com",
            actor=MagicMock(id="admin-1", is_admin_or_maintainer=True),
        )

        assert result == {"message": "Default project set successfully"}
        mock_upr.set_default.assert_called_once_with(mock_session, "user-1", "alice@example.com")

    @patch("codemie.service.user.authentication_service.invalidate_user_from_cache")
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_invalidates_auth_cache_after_change(self, mock_user_repo, mock_upr, mock_get_session, mock_invalidate):
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_upr.set_default.return_value = MagicMock(is_default=True)

        UserAccessService.set_default_project(user_id="user-1", project_name="proj-a", actor=MagicMock(id="admin-1"))

        mock_invalidate.assert_called_once_with("user-1")


class TestClearDefaultProject:
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_clears_default(self, mock_user_repo, mock_app_repo, mock_upr, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="shared")
        mock_upr.clear_default.return_value = MagicMock(user_id="user-1", project_name="proj-a", is_default=False)

        result = UserAccessService.clear_default_project(
            user_id="user-1", project_name="proj-a", actor=MagicMock(id="admin-1", is_admin_or_maintainer=True)
        )

        assert result == {"message": "Default project cleared successfully"}

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_rejects_when_not_a_member(self, mock_user_repo, mock_app_repo, mock_upr, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="shared")
        mock_upr.clear_default.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.clear_default_project(
                user_id="user-1", project_name="proj-not-a-member", actor=MagicMock(id="admin-1")
            )

        assert exc_info.value.code == 404


class TestSetDefaultProjectConcurrencyConflicts:
    """CR-001: concurrent-write conflicts must return a clean HTTP error, not a raw 500."""

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_integrity_error_on_set_returns_409(self, mock_user_repo, mock_app_repo, mock_upr, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="shared")
        mock_upr.set_default.side_effect = IntegrityError(
            "stmt",
            "params",
            Exception('duplicate key value violates unique constraint "uix_user_projects_one_default"'),
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.set_default_project(
                user_id="user-1", project_name="proj-a", actor=MagicMock(id="admin-1", is_admin_or_maintainer=True)
            )

        assert exc_info.value.code == 409
        mock_session.rollback.assert_called_once()

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_unrelated_integrity_error_is_not_reported_as_concurrency(self, mock_user_repo, mock_upr, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_upr.set_default.side_effect = IntegrityError("stmt", "params", Exception("fk_user_projects_user"))

        with pytest.raises(IntegrityError):
            UserAccessService.set_default_project(user_id="user-1", project_name="proj-a", actor=MagicMock(id="a"))

        mock_session.rollback.assert_called_once()

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_stale_data_error_on_set_returns_404(self, mock_user_repo, mock_app_repo, mock_upr, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="shared")
        mock_upr.set_default.side_effect = StaleDataError("row concurrently deleted")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.set_default_project(
                user_id="user-1", project_name="proj-a", actor=MagicMock(id="admin-1", is_admin_or_maintainer=True)
            )

        assert exc_info.value.code == 404
        mock_session.rollback.assert_called_once()

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    def test_object_deleted_error_on_clear_returns_404(self, mock_user_repo, mock_app_repo, mock_upr, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="shared")
        mock_upr.clear_default.side_effect = ObjectDeletedError.__new__(ObjectDeletedError)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserAccessService.clear_default_project(
                user_id="user-1", project_name="proj-a", actor=MagicMock(id="admin-1", is_admin_or_maintainer=True)
            )

        assert exc_info.value.code == 404
        mock_session.rollback.assert_called_once()


class TestDefaultProjectAuditLogging:
    """CR-002: audit log lines must record which project was set/cleared."""

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    @patch("codemie.service.user.user_access_service.logger")
    def test_set_default_logs_project_name(
        self, mock_logger, mock_user_repo, mock_app_repo, mock_upr, mock_get_session
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="shared")
        mock_upr.set_default.return_value = MagicMock(user_id="user-1", project_name="proj-a", is_default=True)

        UserAccessService.set_default_project(
            user_id="user-1", project_name="proj-a", actor=MagicMock(id="admin-1", is_admin_or_maintainer=True)
        )

        log_message = mock_logger.info.call_args[0][0]
        assert "project_name='proj-a'" in log_message

    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.user_access_service.user_project_repository")
    @patch("codemie.service.user.user_access_service.application_repository")
    @patch("codemie.service.user.user_access_service.user_repository")
    @patch("codemie.service.user.user_access_service.logger")
    def test_clear_default_logs_project_name(
        self, mock_logger, mock_user_repo, mock_app_repo, mock_upr, mock_get_session
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_by_id.return_value = MagicMock(id="user-1")
        mock_app_repo.get_by_name.return_value = MagicMock(project_type="shared")
        mock_upr.clear_default.return_value = MagicMock(user_id="user-1", project_name="proj-a", is_default=False)

        UserAccessService.clear_default_project(
            user_id="user-1", project_name="proj-a", actor=MagicMock(id="admin-1", is_admin_or_maintainer=True)
        )

        log_message = mock_logger.info.call_args[0][0]
        assert "project_name='proj-a'" in log_message
