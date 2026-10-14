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

"""Unit tests for ProjectService.update_project_ownership (EPMCDME-15951)."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User
from codemie.service.project.project_service import ProjectService


@pytest.fixture
def regular_user() -> User:
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id="user-1", username="user1", email="user1@example.com", is_admin=False)


class TestUpdateProjectOwnership:
    @patch("codemie.service.project.project_service.invalidate_user_from_cache")
    @patch("codemie.service.project.project_service.activity_event_repository")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_ownership_success_updates_created_by(
        self,
        mock_get_session,
        mock_application_repository,
        mock_user_repository,
        mock_user_project_repository,
        mock_activity,
        mock_invalidate_cache,
        regular_user,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        project = MagicMock()
        project.name = "my-project"
        project.created_by = "original-owner"
        project.deleted_at = None
        project.display_name = "My Project"
        project.cost_center_id = "cost-center-1"
        mock_application_repository.get_by_name.return_value = project

        new_owner = MagicMock(id="new-owner", username="new-owner-username")
        previous_owner = MagicMock(id="original-owner", username="original-owner-username")
        mock_user_repository.get_active_by_id.return_value = new_owner
        mock_user_repository.get_by_id.return_value = previous_owner
        mock_user_project_repository.get_by_user_and_project.return_value = None

        updated_project = MagicMock()
        updated_project.name = "my-project"
        updated_project.created_by = "new-owner"
        mock_application_repository.update_project.return_value = updated_project

        result = ProjectService.update_project_ownership(
            user=regular_user,
            project_name="my-project",
            new_owner_id="new-owner",
        )

        assert result.created_by == "new-owner"
        mock_application_repository.update_project.assert_called_once_with(
            mock_session,
            project,
            display_name="My Project",
            cost_center_id="cost-center-1",
            created_by="new-owner",
        )
        mock_user_repository.get_by_id.assert_called_once_with(mock_session, "original-owner")
        mock_activity.insert.assert_called_once()
        event = mock_activity.insert.call_args[0][0]
        assert event.attributes == {
            "previous_owner_username": "original-owner-username",
            "new_owner_username": "new-owner-username",
        }
        mock_session.commit.assert_called_once()
        mock_invalidate_cache.assert_any_call("original-owner")
        mock_invalidate_cache.assert_any_call("new-owner")

    @patch("codemie.service.project.project_service.invalidate_user_from_cache")
    @patch("codemie.service.project.project_service.activity_event_repository")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_ownership_event_attributes_use_usernames_not_ids(
        self,
        mock_get_session,
        mock_application_repository,
        mock_user_repository,
        mock_user_project_repository,
        mock_activity,
        mock_invalidate_cache,
        regular_user,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        project = MagicMock()
        project.name = "my-project"
        project.created_by = "original-owner-id"
        project.deleted_at = None
        mock_application_repository.get_by_name.return_value = project

        new_owner = MagicMock(id="new-owner-id", username="new-owner-username")
        previous_owner = MagicMock(id="original-owner-id", username="original-owner-username")
        mock_user_repository.get_active_by_id.return_value = new_owner
        mock_user_repository.get_by_id.return_value = previous_owner
        mock_user_project_repository.get_by_user_and_project.return_value = MagicMock()
        mock_application_repository.update_project.return_value = project

        ProjectService.update_project_ownership(
            user=regular_user,
            project_name="my-project",
            new_owner_id="new-owner-id",
        )

        event = mock_activity.insert.call_args[0][0]
        assert event.attributes["previous_owner_username"] == "original-owner-username"
        assert event.attributes["new_owner_username"] == "new-owner-username"
        assert "original-owner-id" not in event.attributes.values()
        assert "new-owner-id" not in event.attributes.values()

    @patch("codemie.service.project.project_service.invalidate_user_from_cache")
    @patch("codemie.service.project.project_service.activity_event_repository")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_ownership_preserves_display_name_and_cost_center(
        self,
        mock_get_session,
        mock_application_repository,
        mock_user_repository,
        mock_user_project_repository,
        mock_activity,
        mock_invalidate_cache,
        regular_user,
    ):
        """Ownership transfer must not blank display_name or detach cost_center_id."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        project = MagicMock()
        project.name = "my-project"
        project.created_by = "original-owner"
        project.deleted_at = None
        project.display_name = "Existing Display Name"
        project.cost_center_id = "existing-cost-center"
        mock_application_repository.get_by_name.return_value = project

        new_owner = MagicMock(id="new-owner", username="new-owner-username")
        mock_user_repository.get_active_by_id.return_value = new_owner
        mock_user_repository.get_by_id.return_value = MagicMock(username="original-owner-username")
        mock_user_project_repository.get_by_user_and_project.return_value = MagicMock()
        mock_application_repository.update_project.return_value = project

        ProjectService.update_project_ownership(
            user=regular_user,
            project_name="my-project",
            new_owner_id="new-owner",
        )

        mock_application_repository.update_project.assert_called_once_with(
            mock_session,
            project,
            display_name="Existing Display Name",
            cost_center_id="existing-cost-center",
            created_by="new-owner",
        )

    @patch("codemie.service.project.project_service.invalidate_user_from_cache")
    @patch("codemie.service.project.project_service.activity_event_repository")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_ownership_adds_new_owner_as_project_member(
        self,
        mock_get_session,
        mock_application_repository,
        mock_user_repository,
        mock_user_project_repository,
        mock_activity,
        mock_invalidate_cache,
        regular_user,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        project = MagicMock()
        project.name = "my-project"
        project.created_by = "original-owner"
        project.deleted_at = None
        mock_application_repository.get_by_name.return_value = project

        new_owner = MagicMock(id="new-owner")
        mock_user_repository.get_active_by_id.return_value = new_owner
        mock_user_project_repository.get_by_user_and_project.return_value = None
        mock_application_repository.update_project.return_value = project

        ProjectService.update_project_ownership(
            user=regular_user,
            project_name="my-project",
            new_owner_id="new-owner",
        )

        mock_user_project_repository.get_by_user_and_project.assert_called_once_with(
            mock_session, "new-owner", "my-project"
        )
        mock_user_project_repository.add_project.assert_called_once_with(
            session=mock_session,
            user_id="new-owner",
            project_name="my-project",
            is_project_admin=True,
        )

    @patch("codemie.service.project.project_service.invalidate_user_from_cache")
    @patch("codemie.service.project.project_service.activity_event_repository")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_ownership_skips_membership_add_when_already_member(
        self,
        mock_get_session,
        mock_application_repository,
        mock_user_repository,
        mock_user_project_repository,
        mock_activity,
        mock_invalidate_cache,
        regular_user,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        project = MagicMock()
        project.name = "my-project"
        project.created_by = "original-owner"
        project.deleted_at = None
        mock_application_repository.get_by_name.return_value = project

        new_owner = MagicMock(id="new-owner")
        mock_user_repository.get_active_by_id.return_value = new_owner
        mock_user_project_repository.get_by_user_and_project.return_value = MagicMock()
        mock_application_repository.update_project.return_value = project

        ProjectService.update_project_ownership(
            user=regular_user,
            project_name="my-project",
            new_owner_id="new-owner",
        )

        mock_user_project_repository.add_project.assert_not_called()

    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_ownership_personal_project_raises_403(
        self,
        mock_get_session,
        mock_application_repository,
        regular_user,
    ):
        from codemie.core.models import Application

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        project = MagicMock()
        project.name = "my-personal-project"
        project.created_by = regular_user.id
        project.deleted_at = None
        project.project_type = Application.ProjectType.PERSONAL
        mock_application_repository.get_by_name.return_value = project

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.update_project_ownership(
                user=regular_user,
                project_name="my-personal-project",
                new_owner_id="new-owner",
            )

        assert exc_info.value.code == 403
        mock_application_repository.update_project.assert_not_called()
        mock_session.commit.assert_not_called()

    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_ownership_project_not_found_raises_404(
        self,
        mock_get_session,
        mock_application_repository,
        regular_user,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_application_repository.get_by_name.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.update_project_ownership(
                user=regular_user,
                project_name="missing-project",
                new_owner_id="new-owner",
            )

        assert exc_info.value.code == 404

    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_ownership_user_not_found_raises_404(
        self,
        mock_get_session,
        mock_application_repository,
        mock_user_repository,
        regular_user,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        project = MagicMock()
        project.name = "my-project"
        project.created_by = "original-owner"
        project.deleted_at = None
        mock_application_repository.get_by_name.return_value = project
        mock_user_repository.get_active_by_id.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.update_project_ownership(
                user=regular_user,
                project_name="my-project",
                new_owner_id="nonexistent-user",
            )

        assert exc_info.value.code == 404
        mock_application_repository.update_project.assert_not_called()
        mock_session.commit.assert_not_called()

    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_ownership_deactivated_user_raises_404(
        self,
        mock_get_session,
        mock_application_repository,
        mock_user_repository,
        regular_user,
    ):
        """A deactivated/soft-deleted account must never be set as the new owner (CR-002)."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        project = MagicMock()
        project.name = "my-project"
        project.created_by = "original-owner"
        project.deleted_at = None
        mock_application_repository.get_by_name.return_value = project

        # get_active_by_id returns None for a deactivated user, even though the account exists.
        mock_user_repository.get_active_by_id.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.update_project_ownership(
                user=regular_user,
                project_name="my-project",
                new_owner_id="deactivated-user",
            )

        assert exc_info.value.code == 404
        mock_user_repository.get_active_by_id.assert_called_once_with(mock_session, "deactivated-user")
        mock_application_repository.update_project.assert_not_called()
        mock_session.commit.assert_not_called()
