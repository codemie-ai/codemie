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

"""Unit tests for ProjectService.delete_project and ProjectService.update_project."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.service.project.project_service import ProjectService


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _make_app(name: str, project_type: str = "shared") -> Application:
    return Application(
        id=name,
        name=name,
        description="Test project",
        project_type=project_type,
        date=datetime.now(),
        update_date=datetime.now(),
    )


def _zero_counts(project_name: str) -> dict[str, dict]:
    return {
        project_name: {
            "assistants_count": 0,
            "workflows_count": 0,
            "integrations_count": 0,
            "datasources_count": 0,
            "skills_count": 0,
            "budgets_count": 0,
            "budget_groups_count": 0,
        }
    }


def _counts_with(project_name: str, **overrides) -> dict[str, dict]:
    base = {
        "assistants_count": 0,
        "workflows_count": 0,
        "integrations_count": 0,
        "datasources_count": 0,
        "skills_count": 0,
        "budgets_count": 0,
        "budget_groups_count": 0,
    }
    base.update(overrides)
    return {project_name: base}


# ---------------------------------------------------------------------------
# Tests for ProjectService.delete_project
# ---------------------------------------------------------------------------


class TestProjectServiceDeleteProject:
    """Tests for ProjectService.delete_project."""

    @patch("codemie.service.project.project_service.application_repository")
    def test_personal_project_raises_403(self, mock_app_repo):
        """delete_project raises 403 for personal projects regardless of resources."""
        mock_session = MagicMock()

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.delete_project(
                session=mock_session,
                project_name="user@example.com",
                project_type=Application.ProjectType.PERSONAL,
                actor_id="user-1",
                action="DELETE /v1/projects/user@example.com",
            )

        assert exc_info.value.code == 403
        assert exc_info.value.message == ProjectService.ERRORS.PERSONAL_DELETE
        mock_app_repo.get_project_entity_counts_bulk.assert_not_called()
        mock_app_repo.delete_by_name.assert_not_called()

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_assigned_users_raises_409(self, mock_app_repo, mock_upr):
        """delete_project raises 409 when project has assigned users."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = [MagicMock(), MagicMock()]

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.delete_project(
                session=mock_session,
                project_name="my-project",
                project_type=Application.ProjectType.SHARED,
                actor_id="user-1",
                action="DELETE /v1/projects/my-project",
            )

        assert exc_info.value.code == 409
        assert "my-project" in exc_info.value.message
        assert exc_info.value.message == ProjectService.ERRORS.HAS_ASSIGNED_USERS.format(name="my-project")
        assert exc_info.value.details == "Assigned users: 2"
        mock_app_repo.get_project_entity_counts_bulk.assert_not_called()
        mock_app_repo.delete_by_name.assert_not_called()

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_one_assigned_user_raises_409(self, mock_app_repo, mock_upr):
        """delete_project raises 409 even with a single assigned user."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = [MagicMock()]

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.delete_project(
                session=mock_session,
                project_name="my-project",
                project_type=Application.ProjectType.SHARED,
                actor_id="user-1",
                action="DELETE /v1/projects/my-project",
            )

        assert exc_info.value.code == 409
        assert exc_info.value.message == ProjectService.ERRORS.HAS_ASSIGNED_USERS.format(name="my-project")
        assert exc_info.value.details == "Assigned users: 1"
        mock_app_repo.get_project_entity_counts_bulk.assert_not_called()
        mock_app_repo.delete_by_name.assert_not_called()

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_assistants_raises_409(self, mock_app_repo, mock_upr):
        """delete_project raises 409 when project has assistants."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = []
        mock_app_repo.get_project_entity_counts_bulk.return_value = _counts_with("my-project", assistants_count=3)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.delete_project(
                session=mock_session,
                project_name="my-project",
                project_type=Application.ProjectType.SHARED,
                actor_id="user-1",
                action="DELETE /v1/projects/my-project",
            )

        assert exc_info.value.code == 409
        assert "my-project" in exc_info.value.message
        assert "deleted" in exc_info.value.message
        mock_app_repo.delete_by_name.assert_not_called()

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_workflows_raises_409(self, mock_app_repo, mock_upr):
        """delete_project raises 409 when project has workflows."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = []
        mock_app_repo.get_project_entity_counts_bulk.return_value = _counts_with("my-project", workflows_count=2)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.delete_project(
                session=mock_session,
                project_name="my-project",
                project_type=Application.ProjectType.SHARED,
                actor_id="user-1",
                action="DELETE /v1/projects/my-project",
            )

        assert exc_info.value.code == 409
        mock_app_repo.delete_by_name.assert_not_called()

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_skills_raises_409(self, mock_app_repo, mock_upr):
        """delete_project raises 409 when project has skills."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = []
        mock_app_repo.get_project_entity_counts_bulk.return_value = _counts_with("my-project", skills_count=1)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.delete_project(
                session=mock_session,
                project_name="my-project",
                project_type=Application.ProjectType.SHARED,
                actor_id="user-1",
                action="DELETE /v1/projects/my-project",
            )

        assert exc_info.value.code == 409
        mock_app_repo.delete_by_name.assert_not_called()

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_datasources_raises_409(self, mock_app_repo, mock_upr):
        """delete_project raises 409 when project has datasources."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = []
        mock_app_repo.get_project_entity_counts_bulk.return_value = _counts_with("my-project", datasources_count=5)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.delete_project(
                session=mock_session,
                project_name="my-project",
                project_type=Application.ProjectType.SHARED,
                actor_id="user-1",
                action="DELETE /v1/projects/my-project",
            )

        assert exc_info.value.code == 409
        mock_app_repo.delete_by_name.assert_not_called()

    @patch("codemie.service.project.project_service.Settings")
    @patch("codemie.service.project.project_service.SettingsService")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_integrations_deletes_them_then_succeeds(
        self, mock_app_repo, mock_upr, mock_settings_service, mock_settings
    ):
        """delete_project auto-deletes project integrations instead of blocking on them."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = []
        mock_app_repo.get_project_entity_counts_bulk.return_value = _counts_with("my-project", integrations_count=2)
        setting_1 = MagicMock(id="setting-1")
        setting_2 = MagicMock(id="setting-2")
        mock_settings.get_by_project_names.return_value = [setting_1, setting_2]

        ProjectService.delete_project(
            session=mock_session,
            project_name="my-project",
            project_type=Application.ProjectType.SHARED,
            actor_id="user-1",
            action="DELETE /v1/projects/my-project",
        )

        mock_settings.get_by_project_names.assert_called_once_with(["my-project"])
        assert mock_settings_service.delete_setting.call_count == 2
        mock_settings_service.delete_setting.assert_any_call("setting-1")
        mock_settings_service.delete_setting.assert_any_call("setting-2")
        mock_app_repo.delete_by_name.assert_called_once_with(mock_session, "my-project")

    @patch("codemie.service.project.project_service.Settings")
    @patch("codemie.service.project.project_service.SettingsService")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_zero_integrations_skips_settings_lookup(
        self, mock_app_repo, mock_upr, mock_settings_service, mock_settings
    ):
        """delete_project does not touch Settings/SettingsService when integrations_count is 0."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = []
        mock_app_repo.get_project_entity_counts_bulk.return_value = _zero_counts("my-project")

        ProjectService.delete_project(
            session=mock_session,
            project_name="my-project",
            project_type=Application.ProjectType.SHARED,
            actor_id="user-1",
            action="DELETE /v1/projects/my-project",
        )

        mock_settings.get_by_project_names.assert_not_called()
        mock_settings_service.delete_setting.assert_not_called()
        mock_app_repo.delete_by_name.assert_called_once_with(mock_session, "my-project")

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_budgets_raises_409(self, mock_app_repo, mock_upr):
        """delete_project raises 409 when project has active budgets."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = []
        mock_app_repo.get_project_entity_counts_bulk.return_value = _counts_with("my-project", budgets_count=2)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.delete_project(
                session=mock_session,
                project_name="my-project",
                project_type=Application.ProjectType.SHARED,
                actor_id="user-1",
                action="DELETE /v1/projects/my-project",
            )

        assert exc_info.value.code == 409
        assert "budgets" in exc_info.value.details
        mock_app_repo.delete_by_name.assert_not_called()

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_active_budget_group_raises_409(self, mock_app_repo, mock_upr):
        """An active budget group blocks deletion even once all its budgets are gone."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = []
        mock_app_repo.get_project_entity_counts_bulk.return_value = _counts_with("my-project", budget_groups_count=1)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.delete_project(
                session=mock_session,
                project_name="my-project",
                project_type=Application.ProjectType.SHARED,
                actor_id="user-1",
                action="DELETE /v1/projects/my-project",
            )

        assert exc_info.value.code == 409
        assert "budget_groups" in exc_info.value.details
        mock_app_repo.delete_by_name.assert_not_called()

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_resources_details_include_non_zero_counts(self, mock_app_repo, mock_upr):
        """delete_project 409 details include non-zero resource counts."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = []
        mock_app_repo.get_project_entity_counts_bulk.return_value = _counts_with(
            "my-project", assistants_count=2, workflows_count=1
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.delete_project(
                session=mock_session,
                project_name="my-project",
                project_type=Application.ProjectType.SHARED,
                actor_id="user-1",
                action="DELETE /v1/projects/my-project",
            )

        assert exc_info.value.code == 409
        # details should mention the non-zero counts
        assert exc_info.value.details is not None
        assert "assistants_count" in exc_info.value.details
        assert "workflows_count" in exc_info.value.details

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_empty_project_calls_delete_and_succeeds(self, mock_app_repo, mock_upr):
        """delete_project calls delete_by_name when project has no resources."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = []
        mock_app_repo.get_project_entity_counts_bulk.return_value = _zero_counts("my-project")

        ProjectService.delete_project(
            session=mock_session,
            project_name="my-project",
            project_type=Application.ProjectType.SHARED,
            actor_id="user-1",
            action="DELETE /v1/projects/my-project",
        )

        mock_app_repo.delete_by_name.assert_called_once_with(mock_session, "my-project")

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_creator_only_allows_deletion(self, mock_app_repo, mock_upr):
        """delete_project succeeds when the sole assigned user is the creator."""
        mock_session = MagicMock()
        creator_record = MagicMock()
        creator_record.user_id = "creator-1"
        mock_upr.get_by_project_name.return_value = [creator_record]
        mock_app_repo.get_project_entity_counts_bulk.return_value = _zero_counts("my-project")

        ProjectService.delete_project(
            session=mock_session,
            project_name="my-project",
            project_type=Application.ProjectType.SHARED,
            actor_id="creator-1",
            action="DELETE /v1/projects/my-project",
            creator_id="creator-1",
        )

        mock_app_repo.delete_by_name.assert_called_once_with(mock_session, "my-project")

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_creator_plus_other_user_raises_409(self, mock_app_repo, mock_upr):
        """delete_project raises 409 with count 1 when creator + 1 other user are assigned."""
        mock_session = MagicMock()
        creator_record = MagicMock()
        creator_record.user_id = "creator-1"
        other_record = MagicMock()
        other_record.user_id = "other-user"
        mock_upr.get_by_project_name.return_value = [creator_record, other_record]

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.delete_project(
                session=mock_session,
                project_name="my-project",
                project_type=Application.ProjectType.SHARED,
                actor_id="creator-1",
                action="DELETE /v1/projects/my-project",
                creator_id="creator-1",
            )

        assert exc_info.value.code == 409
        assert exc_info.value.details == "Assigned users: 1"
        mock_app_repo.delete_by_name.assert_not_called()

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_no_creator_id_counts_all_assigned_users(self, mock_app_repo, mock_upr):
        """delete_project without creator_id counts all assigned users (backward compat)."""
        mock_session = MagicMock()
        user_record = MagicMock()
        user_record.user_id = "some-user"
        mock_upr.get_by_project_name.return_value = [user_record]

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.delete_project(
                session=mock_session,
                project_name="my-project",
                project_type=Application.ProjectType.SHARED,
                actor_id="actor-1",
                action="DELETE /v1/projects/my-project",
            )

        assert exc_info.value.code == 409
        assert exc_info.value.details == "Assigned users: 1"

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_empty_counts_dict_calls_delete(self, mock_app_repo, mock_upr):
        """delete_project treats missing project-name key in counts dict as zero resources."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = []
        # Simulate bulk returning no entry for the project
        mock_app_repo.get_project_entity_counts_bulk.return_value = {}

        ProjectService.delete_project(
            session=mock_session,
            project_name="ghost-project",
            project_type=Application.ProjectType.SHARED,
            actor_id="user-1",
            action="DELETE /v1/projects/ghost-project",
        )

        mock_app_repo.delete_by_name.assert_called_once_with(mock_session, "ghost-project")

    @patch("codemie.service.project.project_service.project_budget_group_repository")
    @patch("codemie.service.project.project_service.budget_repository")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_budgets_and_groups_are_detached_before_delete(self, mock_app_repo, mock_upr, mock_budgets, mock_groups):
        """delete_project detaches budgets and budget groups, then removes the project row."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = []
        mock_app_repo.get_project_entity_counts_bulk.return_value = _zero_counts("my-project")
        mock_budgets.clear_project_on_deleted_budgets.return_value = []
        mock_groups.clear_project_on_deleted_groups.return_value = []

        ProjectService.delete_project(
            session=mock_session,
            project_name="my-project",
            project_type=Application.ProjectType.SHARED,
            actor_id="user-1",
            action="DELETE /v1/projects/my-project",
        )

        mock_budgets.clear_project_on_deleted_budgets.assert_called_once_with(mock_session, "my-project")
        mock_groups.clear_project_on_deleted_groups.assert_called_once_with(mock_session, "my-project")
        mock_app_repo.delete_by_name.assert_called_once_with(mock_session, "my-project")

    @patch("codemie.service.project.project_service.project_budget_group_repository")
    @patch("codemie.service.project.project_service.budget_repository")
    @patch("codemie.service.project.project_service.activity_event_repository")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_project_delete_emits_affected_budgets_in_event(
        self, mock_app_repo, mock_upr, mock_activity, mock_budgets, mock_groups
    ):
        """delete_project's activity event records the detached budget and group ids."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = []
        mock_app_repo.get_project_entity_counts_bulk.return_value = _zero_counts("my-project")
        mock_budgets.clear_project_on_deleted_budgets.return_value = ["budget-1", "budget-2"]
        mock_groups.clear_project_on_deleted_groups.return_value = ["group-1"]

        ProjectService.delete_project(
            session=mock_session,
            project_name="my-project",
            project_type=Application.ProjectType.SHARED,
            actor_id="user-1",
            action="DELETE /v1/projects/my-project",
        )

        event = mock_activity.insert.call_args[0][0]
        assert event.attributes == {
            "affected_budgets": ["budget-1", "budget-2"],
            "affected_budget_groups": ["group-1"],
            "affected_integrations": [],
        }

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_delete_calls_bulk_with_correct_project_name(self, mock_app_repo, mock_upr):
        """delete_project passes project_name in list to get_project_entity_counts_bulk."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = []
        mock_app_repo.get_project_entity_counts_bulk.return_value = _zero_counts("analytics")

        ProjectService.delete_project(
            session=mock_session,
            project_name="analytics",
            project_type=Application.ProjectType.SHARED,
            actor_id="user-1",
            action="DELETE /v1/projects/analytics",
        )

        mock_app_repo.get_project_entity_counts_bulk.assert_called_once_with(mock_session, ["analytics"])

    @patch("codemie.service.project.project_service.ProjectAssignmentService")
    @patch("codemie.service.project.project_service.invalidate_user_from_cache")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_delete_cleans_up_creator_membership(self, mock_app_repo, mock_upr, mock_invalidate, mock_assignment_svc):
        mock_session = MagicMock()
        creator_record = MagicMock()
        creator_record.user_id = "creator-1"
        mock_upr.get_by_project_name.return_value = [creator_record]
        mock_app_repo.get_project_entity_counts_bulk.return_value = _zero_counts("my-project")

        ProjectService.delete_project(
            session=mock_session,
            project_name="my-project",
            project_type=Application.ProjectType.SHARED,
            actor_id="creator-1",
            action="DELETE /v1/projects/my-project",
            creator_id="creator-1",
        )

        mock_upr.remove_project.assert_called_once_with(mock_session, "creator-1", "my-project")
        mock_invalidate.assert_called_once_with("creator-1")
        mock_assignment_svc._sync_project_budget_member_removed.assert_called_once_with(
            mock_session, "my-project", "creator-1"
        )

    @patch("codemie.service.project.project_service.ProjectAssignmentService")
    @patch("codemie.service.project.project_service.project_budget_group_repository")
    @patch("codemie.service.project.project_service.budget_repository")
    @patch("codemie.service.project.project_service.activity_event_repository")
    @patch("codemie.service.project.project_service.invalidate_user_from_cache")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_null_creator_id_skips_membership_cleanup(
        self,
        mock_app_repo,
        mock_upr,
        mock_invalidate,
        mock_activity,
        mock_budgets,
        mock_groups,
        mock_assignment_svc,
    ):
        """Legacy projects with created_by=NULL pass creator_id=None; delete_project must
        complete successfully without calling remove_project or invalidate_user_from_cache."""
        mock_session = MagicMock()
        mock_upr.get_by_project_name.return_value = []
        mock_app_repo.get_project_entity_counts_bulk.return_value = _zero_counts("my-project")
        mock_budgets.clear_project_on_deleted_budgets.return_value = []
        mock_groups.clear_project_on_deleted_groups.return_value = []

        ProjectService.delete_project(
            session=mock_session,
            project_name="my-project",
            project_type=Application.ProjectType.SHARED,
            actor_id="actor-1",
            action="DELETE /v1/projects/my-project",
        )

        mock_upr.remove_project.assert_not_called()
        mock_invalidate.assert_not_called()
        mock_assignment_svc._sync_project_budget_member_removed.assert_not_called()
        mock_app_repo.delete_by_name.assert_called_once_with(mock_session, "my-project")


# ---------------------------------------------------------------------------
# Tests for ProjectService.update_project
# ---------------------------------------------------------------------------


class TestProjectServiceUpdateProject:
    """Tests for ProjectService.update_project (PATCH: description + cost center)."""

    def _make_super_admin(self) -> MagicMock:
        user = MagicMock()
        user.is_admin = True
        user.is_maintainer = False
        user.id = "admin-1"
        user.is_admin_or_maintainer = True
        return user

    def _make_regular_user(self) -> MagicMock:
        user = MagicMock()
        user.is_admin = False
        user.is_maintainer = False
        user.id = "user-1"
        user.is_admin_or_maintainer = False
        return user

    def _make_pure_auditor(self) -> MagicMock:
        user = MagicMock()
        user.is_admin = False
        user.is_maintainer = False
        user.is_auditor = True
        user.id = "auditor-1"
        user.is_admin_or_maintainer = False
        return user

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_non_admin_non_project_admin_raises_403(self, mock_get_session, mock_app_repo, mock_upr):
        """update_project raises 403 when caller is neither a super admin nor a project admin."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_app_repo.get_by_name.return_value = _make_app("my-project")
        mock_upr.is_admin.return_value = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.update_project(
                user=self._make_regular_user(),
                project_name="my-project",
                description="new desc",
            )

        assert exc_info.value.code == 403
        mock_upr.is_admin.assert_called_once_with(mock_session, "user-1", "my-project")

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_pure_auditor_raises_403(self, mock_get_session, mock_app_repo, mock_upr):
        """EPMCDME-10930 spec 5.2: a pure auditor (read-only role) must not gain write
        access to project updates — is_auditor grants no exception to the existing
        super-admin-or-project-admin gate."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_app_repo.get_by_name.return_value = _make_app("my-project")
        mock_upr.is_admin.return_value = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.update_project(
                user=self._make_pure_auditor(),
                project_name="my-project",
                description="new desc",
            )

        assert exc_info.value.code == 403

    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_project_admin_can_update(self, mock_get_session, mock_app_repo, mock_upr, mock_cc_service):
        """update_project succeeds when caller is a project admin (not a super admin)."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        project = _make_app("my-project")
        project.cost_center_id = None
        mock_app_repo.get_by_name.return_value = project
        mock_upr.is_admin.return_value = True
        updated = _make_app("my-project")
        updated.description = "new desc"
        mock_app_repo.update_project.return_value = updated

        result = ProjectService.update_project(
            user=self._make_regular_user(),
            project_name="my-project",
            description="new desc",
        )

        assert result is updated
        mock_upr.is_admin.assert_called_once_with(mock_session, "user-1", "my-project")

    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_project_not_found_raises_404(self, mock_get_session, mock_app_repo):
        """update_project raises 404 when project does not exist."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_app_repo.get_by_name.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.update_project(
                user=self._make_super_admin(),
                project_name="missing",
                description="desc",
            )

        assert exc_info.value.code == 404

    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_description_calls_update_project(self, mock_get_session, mock_app_repo, mock_cc_service):
        """update_project updates description when description is provided."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        project = _make_app("my-project")
        project.cost_center_id = None
        mock_app_repo.get_by_name.return_value = project
        updated = _make_app("my-project")
        updated.description = "new desc"
        mock_app_repo.update_project.return_value = updated

        result = ProjectService.update_project(
            user=self._make_super_admin(),
            project_name="my-project",
            description="new desc",
        )

        mock_app_repo.update_project.assert_called_once_with(
            mock_session,
            project,
            name=None,
            display_name=None,
            description="new desc",
            clear_description=False,
            cost_center_id=None,
            chargeback_enabled=None,
            chargeback_attribution=None,
        )
        assert result is updated

    @patch("codemie.service.project.project_service.SettingsService")
    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_persists_budget_tracking_flag(
        self,
        mock_get_session,
        mock_app_repo,
        mock_cc_service,
        mock_settings_service,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        project = _make_app("my-project")
        project.cost_center_id = None
        mock_app_repo.get_by_name.return_value = project
        mock_app_repo.update_project.return_value = project

        ProjectService.update_project(
            user=self._make_super_admin(),
            project_name="my-project",
            enforce_member_spend_limits=True,
        )

        mock_settings_service.set_enforce_member_spend_limits.assert_called_once_with("my-project", True)

    @patch("codemie.service.project.project_service.SettingsService")
    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_disables_budget_tracking_flag(
        self,
        mock_get_session,
        mock_app_repo,
        mock_cc_service,
        mock_settings_service,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        project = _make_app("my-project")
        project.cost_center_id = None
        mock_app_repo.get_by_name.return_value = project
        mock_app_repo.update_project.return_value = project

        ProjectService.update_project(
            user=self._make_super_admin(),
            project_name="my-project",
            enforce_member_spend_limits=False,
        )

        mock_settings_service.set_enforce_member_spend_limits.assert_called_once_with("my-project", False)

    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_cost_center_calls_ensure_exists(self, mock_get_session, mock_app_repo, mock_cc_service):
        """update_project calls cost_center_service when cost_center_id is provided."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        project = _make_app("my-project")
        project.cost_center_id = None
        mock_app_repo.get_by_name.return_value = project
        cost_center = MagicMock()
        cost_center.id = "cc-1"
        mock_cc_service.ensure_exists_for_project.return_value = cost_center
        mock_app_repo.update_project.return_value = project

        from uuid import UUID

        cc_id = UUID("12345678-1234-5678-1234-567812345678")
        ProjectService.update_project(
            user=self._make_super_admin(),
            project_name="my-project",
            cost_center_id=cc_id,
        )

        mock_cc_service.ensure_exists_for_project.assert_called_once_with(mock_session, cc_id)
        mock_app_repo.update_project.assert_called_once_with(
            mock_session,
            project,
            name=None,
            display_name=None,
            description=None,
            clear_description=False,
            cost_center_id="cc-1",
            chargeback_enabled=None,
            chargeback_attribution=None,
        )

    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_clear_cost_center_passes_none(self, mock_get_session, mock_app_repo, mock_cc_service):
        """update_project passes cost_center_id=None when clear_cost_center=True."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        project = _make_app("my-project")
        project.cost_center_id = "existing-cc"
        mock_app_repo.get_by_name.return_value = project
        mock_app_repo.update_project.return_value = project

        ProjectService.update_project(
            user=self._make_super_admin(),
            project_name="my-project",
            clear_cost_center=True,
        )

        mock_app_repo.update_project.assert_called_once_with(
            mock_session,
            project,
            name=None,
            display_name=None,
            description=None,
            clear_description=False,
            cost_center_id=None,
            chargeback_enabled=None,
            chargeback_attribution=None,
        )

    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_clear_display_name_clears_existing_value(self, mock_get_session, mock_app_repo, mock_cc_service):
        """update_project passes display_name=None when clear_display_name=True,
        clearing an existing value (EPMCDME-13486). display_name=None alone (without
        the flag) must NOT clear an existing value - it means 'leave unchanged'."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        project = _make_app("my-project")
        project.display_name = "Existing Display Name"
        mock_app_repo.get_by_name.return_value = project
        mock_app_repo.update_project.return_value = project

        ProjectService.update_project(
            user=self._make_super_admin(),
            project_name="my-project",
            clear_display_name=True,
        )

        mock_app_repo.update_project.assert_called_once_with(
            mock_session,
            project,
            name=None,
            display_name=None,
            description=None,
            clear_description=False,
            cost_center_id=None,
            chargeback_enabled=None,
            chargeback_attribution=None,
        )

    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_omitted_display_name_preserves_existing_value(self, mock_get_session, mock_app_repo, mock_cc_service):
        """update_project keeps the existing display_name when display_name=None and
        clear_display_name=False (i.e. the field was simply not sent) (EPMCDME-13486)."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        project = _make_app("my-project")
        project.display_name = "Existing Display Name"
        mock_app_repo.get_by_name.return_value = project
        mock_app_repo.update_project.return_value = project

        ProjectService.update_project(
            user=self._make_super_admin(),
            project_name="my-project",
            description="new description",
        )

        mock_app_repo.update_project.assert_called_once_with(
            mock_session,
            project,
            name=None,
            display_name="Existing Display Name",
            description="new description",
            clear_description=False,
            cost_center_id=None,
            chargeback_enabled=None,
            chargeback_attribution=None,
        )

    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_whitespace_only_display_name_clears_instead_of_persisting_empty(
        self, mock_get_session, mock_app_repo, mock_cc_service
    ):
        """A whitespace-only display_name (without clear_display_name) must resolve to
        None, not an empty string - persisting '' would defeat search_by_name's
        COALESCE(display_name, name), which only falls back to name on NULL (EPMCDME-13486)."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        project = _make_app("my-project")
        project.display_name = "Existing Display Name"
        mock_app_repo.get_by_name.return_value = project
        mock_app_repo.update_project.return_value = project

        ProjectService.update_project(
            user=self._make_super_admin(),
            project_name="my-project",
            display_name="   ",
        )

        mock_app_repo.update_project.assert_called_once_with(
            mock_session,
            project,
            name=None,
            display_name=None,
            description=None,
            clear_description=False,
            cost_center_id=None,
            chargeback_enabled=None,
            chargeback_attribution=None,
        )

    @patch("codemie.service.project.project_service.SettingsService")
    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_enforcement_flag_triggers_immediate_member_resync(
        self,
        mock_get_session,
        mock_app_repo,
        mock_cc_service,
        mock_settings_service,
    ):
        """Toggling enforce_member_spend_limits calls bulk resync."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        project = _make_app("my-project")
        project.cost_center_id = None
        mock_app_repo.get_by_name.return_value = project
        mock_app_repo.update_project.return_value = project

        with patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.resync_project_member_allocations_sync"
        ) as mock_resync:
            ProjectService.update_project(
                user=self._make_super_admin(),
                project_name="my-project",
                enforce_member_spend_limits=True,
            )

        mock_resync.assert_called_once_with(project_name="my-project", enforce_limit=True)

    @patch("codemie.service.project.project_service.SettingsService")
    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_enforcement_flag_resync_failure_does_not_raise(
        self,
        mock_get_session,
        mock_app_repo,
        mock_cc_service,
        mock_settings_service,
    ):
        """If resync fails, flag is still saved and no exception propagates."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        project = _make_app("my-project")
        project.cost_center_id = None
        mock_app_repo.get_by_name.return_value = project
        mock_app_repo.update_project.return_value = project

        with patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.resync_project_member_allocations_sync",
            side_effect=RuntimeError("resync exploded"),
        ):
            result = ProjectService.update_project(
                user=self._make_super_admin(),
                project_name="my-project",
                enforce_member_spend_limits=False,
            )

        assert result is project
        mock_settings_service.set_enforce_member_spend_limits.assert_called_once_with("my-project", False)

    @patch("codemie.service.project.project_service.activity_event_repository")
    @patch("codemie.service.project.project_service.logger")
    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_logs_on_success(
        self,
        mock_get_session,
        mock_app_repo,
        mock_cc_service,
        mock_logger,
        mock_activity,
    ):
        """update_project emits a logger.info containing 'project_updated' on success."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        project = _make_app("my-project")
        project.cost_center_id = None
        mock_app_repo.get_by_name.return_value = project
        updated = _make_app("my-project")
        updated.description = "new desc"
        mock_app_repo.update_project.return_value = updated
        mock_activity.insert = MagicMock()

        ProjectService.update_project(
            user=self._make_super_admin(),
            project_name="my-project",
            description="new desc",
        )

        mock_logger.info.assert_called_once()
        assert "project_updated" in mock_logger.info.call_args[0][0]


# ---------------------------------------------------------------------------
# Regression: delete-then-same-name-recreate must not inherit membership
# ---------------------------------------------------------------------------


class TestProjectServiceDeleteRecreate:
    """Regression: deleting and recreating a same-named project must not inherit membership."""

    @patch("codemie.service.project.project_service.ProjectAssignmentService")
    @patch("codemie.service.project.project_service.invalidate_user_from_cache")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    def test_old_creator_membership_removed_before_name_reuse(
        self, mock_app_repo, mock_upr, mock_invalidate, mock_assignment_svc
    ):
        """After delete_project, the creator's UserProject row is removed so
        a new project with the same name starts with clean membership."""
        mock_session = MagicMock()
        creator_record = MagicMock()
        creator_record.user_id = "original-admin"
        mock_upr.get_by_project_name.return_value = [creator_record]
        mock_app_repo.get_project_entity_counts_bulk.return_value = _zero_counts("alpha")

        ProjectService.delete_project(
            session=mock_session,
            project_name="alpha",
            project_type=Application.ProjectType.SHARED,
            actor_id="original-admin",
            action="DELETE /v1/projects/alpha",
            creator_id="original-admin",
        )

        mock_upr.remove_project.assert_called_once_with(mock_session, "original-admin", "alpha")
        mock_invalidate.assert_called_once_with("original-admin")


class TestUpdateProjectClearDescription:
    """Tests for clear_description parameter in ProjectService.update_project (EPMCDME-14336)."""

    def _make_super_admin(self):
        from codemie.rest_api.security.user import User

        return User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)

    @patch("codemie.service.project.project_service.activity_event_repository")
    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_clear_description_removes_value(
        self, mock_get_session, mock_app_repo, mock_cc_service, mock_activity
    ):
        """clear_description=True forwards clear_description=True to the repository."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        project = _make_app("my-project")
        project.description = "has a description"
        mock_app_repo.get_by_name.return_value = project
        cleared = _make_app("my-project")
        cleared.description = None
        mock_app_repo.update_project.return_value = cleared

        ProjectService.update_project(
            user=self._make_super_admin(),
            project_name="my-project",
            clear_description=True,
        )

        call_kwargs = mock_app_repo.update_project.call_args.kwargs
        assert call_kwargs.get("clear_description") is True
        assert call_kwargs.get("description") is None

    @patch("codemie.service.project.project_service.activity_event_repository")
    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_omitted_description_unchanged(
        self, mock_get_session, mock_app_repo, mock_cc_service, mock_activity
    ):
        """Omitting description (no clear) does not pass description to repository."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        project = _make_app("my-project")
        project.description = "stays"
        mock_app_repo.get_by_name.return_value = project
        mock_app_repo.update_project.return_value = project

        ProjectService.update_project(
            user=self._make_super_admin(),
            project_name="my-project",
        )

        call_kwargs = mock_app_repo.update_project.call_args.kwargs
        assert call_kwargs.get("description") is None
        assert call_kwargs.get("clear_description") is False

    @patch("codemie.service.project.project_service.activity_event_repository")
    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_whitespace_description_treated_as_clear(
        self, mock_get_session, mock_app_repo, mock_cc_service, mock_activity
    ):
        """Whitespace-only description normalizes to None and triggers an explicit clear (clear_description=True)."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        project = _make_app("my-project")
        project.description = "old desc"
        mock_app_repo.get_by_name.return_value = project
        cleared = _make_app("my-project")
        cleared.description = None
        mock_app_repo.update_project.return_value = cleared

        ProjectService.update_project(
            user=self._make_super_admin(),
            project_name="my-project",
            description="   ",
        )

        call_kwargs = mock_app_repo.update_project.call_args.kwargs
        assert call_kwargs.get("description") is None
        assert call_kwargs.get("clear_description") is True
