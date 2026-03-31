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
        }
    }


def _counts_with(project_name: str, **overrides) -> dict[str, dict]:
    base = {
        "assistants_count": 0,
        "workflows_count": 0,
        "integrations_count": 0,
        "datasources_count": 0,
        "skills_count": 0,
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

    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_assistants_raises_409(self, mock_app_repo):
        """delete_project raises 409 when project has assistants."""
        mock_session = MagicMock()
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

    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_workflows_raises_409(self, mock_app_repo):
        """delete_project raises 409 when project has workflows."""
        mock_session = MagicMock()
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

    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_skills_raises_409(self, mock_app_repo):
        """delete_project raises 409 when project has skills."""
        mock_session = MagicMock()
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

    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_datasources_raises_409(self, mock_app_repo):
        """delete_project raises 409 when project has datasources."""
        mock_session = MagicMock()
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

    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_integrations_raises_409(self, mock_app_repo):
        """delete_project raises 409 when project has integrations."""
        mock_session = MagicMock()
        mock_app_repo.get_project_entity_counts_bulk.return_value = _counts_with("my-project", integrations_count=1)

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

    @patch("codemie.service.project.project_service.application_repository")
    def test_project_with_resources_details_include_non_zero_counts(self, mock_app_repo):
        """delete_project 409 details include non-zero resource counts."""
        mock_session = MagicMock()
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

    @patch("codemie.service.project.project_service.application_repository")
    def test_empty_project_calls_delete_and_succeeds(self, mock_app_repo):
        """delete_project calls delete_by_name when project has no resources."""
        mock_session = MagicMock()
        mock_app_repo.get_project_entity_counts_bulk.return_value = _zero_counts("my-project")

        ProjectService.delete_project(
            session=mock_session,
            project_name="my-project",
            project_type=Application.ProjectType.SHARED,
            actor_id="user-1",
            action="DELETE /v1/projects/my-project",
        )

        mock_app_repo.delete_by_name.assert_called_once_with(mock_session, "my-project")

    @patch("codemie.service.project.project_service.application_repository")
    def test_empty_counts_dict_calls_delete(self, mock_app_repo):
        """delete_project treats missing project-name key in counts dict as zero resources."""
        mock_session = MagicMock()
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

    @patch("codemie.service.project.project_service.application_repository")
    def test_delete_calls_bulk_with_correct_project_name(self, mock_app_repo):
        """delete_project passes project_name in list to get_project_entity_counts_bulk."""
        mock_session = MagicMock()
        mock_app_repo.get_project_entity_counts_bulk.return_value = _zero_counts("analytics")

        ProjectService.delete_project(
            session=mock_session,
            project_name="analytics",
            project_type=Application.ProjectType.SHARED,
            actor_id="user-1",
            action="DELETE /v1/projects/analytics",
        )

        mock_app_repo.get_project_entity_counts_bulk.assert_called_once_with(mock_session, ["analytics"])


# ---------------------------------------------------------------------------
# Tests for ProjectService.update_project
# ---------------------------------------------------------------------------


class TestProjectServiceUpdateProject:
    """Tests for ProjectService.update_project (PATCH: description + cost center)."""

    def _make_super_admin(self) -> MagicMock:
        user = MagicMock()
        user.is_admin = True
        user.id = "admin-1"
        return user

    def _make_regular_user(self) -> MagicMock:
        user = MagicMock()
        user.is_admin = False
        user.id = "user-1"
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
            description="new desc",
            cost_center_id=None,
        )
        assert result is updated

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
            description=None,
            cost_center_id="cc-1",
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
            description=None,
            cost_center_id=None,
        )
