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

"""Unit tests for ProjectService shared project creation."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User
from codemie.service.activity.activity_models import ProjectManagementEvent
from codemie.service.project.project_service import ProjectService


@pytest.fixture
def regular_user() -> User:
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id="user-1", username="user1", email="user1@example.com", is_admin=False)


@pytest.fixture
def super_admin_user() -> User:
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)


class TestProjectServiceCreateSharedProject:
    @patch("codemie.service.project.project_service.activity_event_repository")
    @patch("codemie.service.project.project_service.logger")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.get_session")
    def test_create_shared_project_success(
        self,
        mock_get_session,
        mock_cost_center_service,
        mock_user_repository,
        mock_application_repository,
        mock_user_project_repository,
        mock_logger,
        mock_activity,
        regular_user,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repository.get_active_by_id.return_value = MagicMock(project_limit=3)
        mock_cost_center_service.ensure_exists_for_project.return_value = None
        mock_application_repository.count_shared_projects_created_by_user.return_value = 1
        mock_application_repository.get_by_name_case_insensitive.return_value = None
        project = SimpleNamespace(
            name="data-pipeline",
            description="Analytics pipeline",
            project_type="shared",
            created_by="user-1",
            date=datetime(2026, 2, 10, tzinfo=UTC),
        )
        mock_application_repository.create.return_value = project
        mock_activity.insert = MagicMock()

        result = ProjectService.create_shared_project(
            user=regular_user,
            project_name="data-pipeline",
            description="Analytics pipeline",
        )

        assert result is project
        mock_application_repository.create.assert_called_once_with(
            session=mock_session,
            name="data-pipeline",
            display_name=None,
            description="Analytics pipeline",
            project_type="shared",
            created_by="user-1",
        )
        mock_user_project_repository.add_project.assert_called_once_with(
            session=mock_session,
            user_id="user-1",
            project_name="data-pipeline",
            is_project_admin=True,
        )
        mock_session.commit.assert_called_once()
        mock_logger.info.assert_called_once()
        assert mock_logger.info.call_args[0][0].startswith("project_created: project=")

    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_duplicate_error_uses_existing_project_casing(
        self,
        mock_get_session,
        mock_user_repository,
        mock_application_repository,
        regular_user,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repository.get_active_by_id.return_value = MagicMock(project_limit=3)
        mock_application_repository.count_shared_projects_created_by_user.return_value = 0
        mock_application_repository.get_by_name_case_insensitive.return_value = SimpleNamespace(name="my-project")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.create_shared_project(
                user=regular_user,
                project_name="my-project",
                description="desc",
            )

        assert exc_info.value.code == 409
        assert exc_info.value.message == "Project 'my-project' already exists. Please choose a different name."

    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_integrity_error_conflict_is_chained_and_uses_existing_casing(
        self,
        mock_get_session,
        mock_user_repository,
        mock_application_repository,
        regular_user,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repository.get_active_by_id.return_value = MagicMock(project_limit=3)
        mock_application_repository.count_shared_projects_created_by_user.return_value = 0
        mock_application_repository.get_by_name_case_insensitive.side_effect = [
            None,
            SimpleNamespace(name="my-project"),
        ]
        integrity_error = IntegrityError("stmt", "params", Exception("duplicate"))
        mock_application_repository.create.side_effect = integrity_error

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.create_shared_project(
                user=regular_user,
                project_name="my-project",
                description="desc",
            )

        assert exc_info.value.code == 409
        assert exc_info.value.message == "Project 'my-project' already exists. Please choose a different name."
        assert exc_info.value.__cause__ is integrity_error
        mock_session.rollback.assert_called_once()

    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_project_limit_reached_returns_403(
        self,
        mock_get_session,
        mock_user_repository,
        mock_application_repository,
        regular_user,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repository.get_active_by_id.return_value = MagicMock(project_limit=1)
        mock_application_repository.count_shared_projects_created_by_user.return_value = 1

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.create_shared_project(
                user=regular_user,
                project_name="new-project",
                description="desc",
            )

        assert exc_info.value.code == 403
        assert (
            exc_info.value.message
            == "Project creation limit reached (1/1). Contact administrator to increase your limit."
        )
        mock_application_repository.create.assert_not_called()

    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_grandfathered_limit_reached_returns_403_with_delete_guidance(
        self,
        mock_get_session,
        mock_user_repository,
        mock_application_repository,
        regular_user,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repository.get_active_by_id.return_value = MagicMock(project_limit=3)
        mock_application_repository.count_shared_projects_created_by_user.return_value = 5

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.create_shared_project(
                user=regular_user,
                project_name="legacy-heavy-user-project",
                description="desc",
            )

        assert exc_info.value.code == 403
        assert (
            exc_info.value.message
            == "Project creation limit reached (5/3). Delete 2 or more projects to create new ones."
        )
        mock_application_repository.create.assert_not_called()

    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_zero_limit_returns_403_with_zero_ratio_message(
        self,
        mock_get_session,
        mock_user_repository,
        mock_application_repository,
        regular_user,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repository.get_active_by_id.return_value = MagicMock(project_limit=0)
        mock_application_repository.count_shared_projects_created_by_user.return_value = 0

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.create_shared_project(
                user=regular_user,
                project_name="blocked-project",
                description="desc",
            )

        assert exc_info.value.code == 403
        assert (
            exc_info.value.message
            == "Project creation limit reached (0/0). Contact administrator to increase your limit."
        )
        mock_application_repository.create.assert_not_called()

    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_non_super_admin_with_null_limit_is_rejected(
        self,
        mock_get_session,
        mock_user_repository,
        mock_application_repository,
        regular_user,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repository.get_active_by_id.return_value = MagicMock(project_limit=None)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.create_shared_project(
                user=regular_user,
                project_name="corrupted-limit-project",
                description="desc",
            )

        assert exc_info.value.code == 403
        assert exc_info.value.message == "Invalid project limit configuration. Contact administrator."
        mock_application_repository.count_shared_projects_created_by_user.assert_not_called()
        mock_application_repository.create.assert_not_called()

    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_missing_active_user_returns_401_account_is_deactivated(
        self,
        mock_get_session,
        mock_user_repository,
        regular_user,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repository.get_active_by_id.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.create_shared_project(
                user=regular_user,
                project_name="my-project",
                description="desc",
            )

        assert exc_info.value.code == 401
        assert exc_info.value.message == "Account is deactivated"

    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_super_admin_bypasses_project_limit(
        self,
        mock_get_session,
        mock_user_repository,
        mock_application_repository,
        mock_user_project_repository,
        super_admin_user,
    ):
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_application_repository.get_by_name_case_insensitive.return_value = None
        project = SimpleNamespace(
            name="admin-project",
            description="desc",
            project_type="shared",
            created_by="admin-1",
            date=datetime(2026, 2, 10, tzinfo=UTC),
        )
        mock_application_repository.create.return_value = project

        result = ProjectService.create_shared_project(
            user=super_admin_user,
            project_name="admin-project",
            description="desc",
        )

        assert result is project
        mock_user_repository.get_active_by_id.assert_not_called()
        mock_application_repository.count_shared_projects_created_by_user.assert_not_called()
        mock_user_project_repository.add_project.assert_called_once()


@pytest.fixture
def service_account() -> User:
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id="svc-1", username="svc", email="svc@example.com", is_admin=False)


@patch("codemie.service.project.project_service.llm_service")
@patch("codemie.service.project.project_service.activity_event_repository")
@patch("codemie.service.project.project_service.user_project_repository")
@patch("codemie.service.project.project_service.application_repository")
@patch("codemie.service.project.project_service.cost_center_service")
class TestCreateSharedProjectInSession:
    @pytest.fixture(autouse=True)
    def _chat_model_check_passes(self):
        with patch.object(ProjectService, "ensure_chat_model_allowed"):
            yield

    @staticmethod
    def _prepare(mock_application_repository, mock_llm_service, models=("gpt-4o", "claude-3")):
        mock_application_repository.get_by_name_case_insensitive.return_value = None
        project = SimpleNamespace(name="epm-aimf")
        mock_application_repository.create.return_value = project
        mock_llm_service.get_allowed_chat_models.return_value = [SimpleNamespace(deployment_name=m) for m in models]
        return project

    def test_creates_project_without_creator_membership(
        self,
        mock_cost_center_service,
        mock_application_repository,
        mock_user_project_repository,
        mock_activity,
        mock_llm_service,
        service_account,
    ):
        session = MagicMock()
        mock_cost_center_service.ensure_exists_for_project.return_value = None
        project = self._prepare(mock_application_repository, mock_llm_service)

        result = ProjectService.create_shared_project_in_session(session, actor=service_account, name="epm-aimf")

        assert result is project
        mock_application_repository.create.assert_called_once()
        assert mock_application_repository.create.call_args.kwargs["created_by"] == service_account.id
        assert mock_application_repository.create.call_args.kwargs["project_type"] == "shared"
        mock_user_project_repository.add_project.assert_not_called()
        session.commit.assert_not_called()
        session.expunge.assert_not_called()

    def test_skips_project_limit(
        self,
        mock_cost_center_service,
        mock_application_repository,
        mock_user_project_repository,
        mock_activity,
        mock_llm_service,
        service_account,
    ):
        mock_cost_center_service.ensure_exists_for_project.return_value = None
        self._prepare(mock_application_repository, mock_llm_service)

        ProjectService.create_shared_project_in_session(MagicMock(), actor=service_account, name="epm-aimf")

        assert service_account.is_admin is False
        mock_application_repository.count_shared_projects_created_by_user.assert_not_called()

    def test_links_cost_center(
        self,
        mock_cost_center_service,
        mock_application_repository,
        mock_user_project_repository,
        mock_activity,
        mock_llm_service,
        service_account,
    ):
        session = MagicMock()
        cost_center_id = uuid4()
        mock_cost_center_service.ensure_exists_for_project.return_value = SimpleNamespace(id=cost_center_id)
        self._prepare(mock_application_repository, mock_llm_service)

        ProjectService.create_shared_project_in_session(
            session, actor=service_account, name="epm-aimf", cost_center_id=cost_center_id
        )

        mock_cost_center_service.ensure_exists_for_project.assert_called_once_with(session, cost_center_id)
        assert mock_application_repository.create.call_args.kwargs["cost_center_id"] == cost_center_id

    def test_soft_deleted_name_raises_409(
        self,
        mock_cost_center_service,
        mock_application_repository,
        mock_user_project_repository,
        mock_activity,
        mock_llm_service,
        service_account,
    ):
        mock_cost_center_service.ensure_exists_for_project.return_value = None
        mock_application_repository.get_by_name_case_insensitive.return_value = SimpleNamespace(
            name="epm-aimf", deleted_at=datetime(2026, 1, 1, tzinfo=UTC)
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.create_shared_project_in_session(MagicMock(), actor=service_account, name="epm-aimf")

        assert exc_info.value.code == 409
        assert exc_info.value.message == "Project 'epm-aimf' already exists. Please choose a different name."
        mock_application_repository.create.assert_not_called()

    def test_initializes_allowed_and_default_models(
        self,
        mock_cost_center_service,
        mock_application_repository,
        mock_user_project_repository,
        mock_activity,
        mock_llm_service,
        service_account,
    ):
        mock_cost_center_service.ensure_exists_for_project.return_value = None
        self._prepare(mock_application_repository, mock_llm_service, models=("gpt-4o", "claude-3"))

        project = ProjectService.create_shared_project_in_session(MagicMock(), actor=service_account, name="epm-aimf")

        mock_llm_service.get_allowed_chat_models.assert_called_once_with(service_account, include_all=False)
        assert project.allowed_models == ["gpt-4o", "claude-3"]
        assert project.default_model == "gpt-4o"

    def test_writes_project_created_activity_event(
        self,
        mock_cost_center_service,
        mock_application_repository,
        mock_user_project_repository,
        mock_activity,
        mock_llm_service,
        service_account,
    ):
        session = MagicMock()
        mock_cost_center_service.ensure_exists_for_project.return_value = None
        self._prepare(mock_application_repository, mock_llm_service)

        ProjectService.create_shared_project_in_session(session, actor=service_account, name="epm-aimf")

        mock_activity.insert.assert_called_once()
        event, event_session = mock_activity.insert.call_args.args
        assert event.event_type == ProjectManagementEvent.PROJECT_CREATED
        assert event.entity_id == "epm-aimf"
        assert event.actor_id == service_account.id
        assert event_session is session

    def test_invalid_name_raises_400_before_db(
        self,
        mock_cost_center_service,
        mock_application_repository,
        mock_user_project_repository,
        mock_activity,
        mock_llm_service,
        service_account,
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.create_shared_project_in_session(MagicMock(), actor=service_account, name="EPM AIMF")

        assert exc_info.value.code == 400
        assert mock_application_repository.method_calls == []
        mock_cost_center_service.ensure_exists_for_project.assert_not_called()

    def test_integrity_error_propagates(
        self,
        mock_cost_center_service,
        mock_application_repository,
        mock_user_project_repository,
        mock_activity,
        mock_llm_service,
        service_account,
    ):
        mock_cost_center_service.ensure_exists_for_project.return_value = None
        self._prepare(mock_application_repository, mock_llm_service)
        mock_application_repository.create.side_effect = IntegrityError("stmt", "params", Exception("duplicate"))

        with pytest.raises(IntegrityError):
            ProjectService.create_shared_project_in_session(MagicMock(), actor=service_account, name="epm-aimf")


class TestProjectServiceValidation:
    @pytest.mark.parametrize(
        "name", ["my project", "test.env", "project@work", "hello/world", "_private", "-draft", "MyProject"]
    )
    def test_invalid_name_pattern_returns_400(self, name):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.create_shared_project(
                user=User(id="u1", username="u1", email="u1@example.com"),
                project_name=name,
                description="desc",
            )

        assert exc_info.value.code == 400
        assert "Invalid project name" in exc_info.value.message

    def test_name_too_short_returns_400(self):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.create_shared_project(
                user=User(id="u1", username="u1", email="u1@example.com"),
                project_name="ab",
                description="desc",
            )

        assert exc_info.value.code == 400
        assert exc_info.value.message == "Project name must be at least 3 characters"

    def test_name_too_long_returns_400(self):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.create_shared_project(
                user=User(id="u1", username="u1", email="u1@example.com"),
                project_name="p" * 101,
                description="desc",
            )

        assert exc_info.value.code == 400
        assert exc_info.value.message == "Project name cannot exceed 100 characters"

    @pytest.mark.parametrize(
        "reserved_name",
        ["admin", "system", "root", "api", "null", "undefined", "default", "test", "demo"],
    )
    def test_reserved_name_returns_400(self, reserved_name):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.create_shared_project(
                user=User(id="u1", username="u1", email="u1@example.com"),
                project_name=reserved_name,
                description="desc",
            )

        assert exc_info.value.code == 400
        assert exc_info.value.message == f"Project name '{reserved_name}' is reserved and cannot be used"

    def _make_session_mocks(self):
        """Return (mock_get_session, mock_app_repo, mock_user_repo, mock_session) ready for create tests."""
        from unittest.mock import MagicMock

        mock_session = MagicMock()
        mock_app_repo = MagicMock()
        mock_user_repo = MagicMock()
        mock_app_repo.get_by_name_case_insensitive.return_value = None
        mock_app_repo.count_shared_projects_created_by_user.return_value = 0
        mock_user_repo.get_active_by_id.return_value = MagicMock(project_limit=5)
        created_app = SimpleNamespace(name="valid-name", description=None, project_type="shared", created_by="u1")
        mock_app_repo.create.return_value = created_app
        return mock_session, mock_app_repo, mock_user_repo, created_app

    @pytest.mark.parametrize("description", ["", "   "])
    @patch("codemie.service.project.project_service.activity_event_repository")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_empty_description_stored_as_null(
        self, mock_get_session, mock_app_repo, mock_user_repo, mock_cost_center, mock_upr, mock_activity, description
    ):
        """Empty or whitespace-only description normalizes to None rather than raising (EPMCDME-14336)."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_active_by_id.return_value = MagicMock(project_limit=5)
        mock_cost_center.ensure_exists_for_project.return_value = None
        mock_app_repo.get_by_name_case_insensitive.return_value = None
        mock_app_repo.count_shared_projects_created_by_user.return_value = 0
        mock_app_repo.create.return_value = SimpleNamespace(
            name="valid-name", description=None, project_type="shared", created_by="u1"
        )

        result = ProjectService.create_shared_project(
            user=User(id="u1", username="u1", email="u1@example.com"),
            project_name="valid-name",
            description=description,
        )

        assert result is not None
        mock_app_repo.create.assert_called_once()
        call_kwargs = mock_app_repo.create.call_args.kwargs
        assert call_kwargs["description"] is None

    @patch("codemie.service.project.project_service.activity_event_repository")
    @patch("codemie.service.project.project_service.user_project_repository")
    @patch("codemie.service.project.project_service.cost_center_service")
    @patch("codemie.service.project.project_service.user_repository")
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_create_project_without_description_succeeds(
        self, mock_get_session, mock_app_repo, mock_user_repo, mock_cost_center, mock_upr, mock_activity
    ):
        """Omitting description (None) succeeds and stores None (EPMCDME-14336)."""
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_repo.get_active_by_id.return_value = MagicMock(project_limit=5)
        mock_cost_center.ensure_exists_for_project.return_value = None
        mock_app_repo.get_by_name_case_insensitive.return_value = None
        mock_app_repo.count_shared_projects_created_by_user.return_value = 0
        mock_app_repo.create.return_value = SimpleNamespace(
            name="valid-name", description=None, project_type="shared", created_by="u1"
        )

        result = ProjectService.create_shared_project(
            user=User(id="u1", username="u1", email="u1@example.com"),
            project_name="valid-name",
        )

        assert result is not None
        mock_app_repo.create.assert_called_once()
        call_kwargs = mock_app_repo.create.call_args.kwargs
        assert call_kwargs["description"] is None

    def test_description_too_long_returns_400(self):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.create_shared_project(
                user=User(id="u1", username="u1", email="u1@example.com"),
                project_name="valid-name",
                description="a" * 501,
            )

        assert exc_info.value.code == 400
        assert exc_info.value.message == "Project description cannot exceed 500 characters"


class TestProjectServiceValidateDisplayName:
    """Unit tests for ProjectService._validate_display_name."""

    def test_strips_leading_and_trailing_whitespace(self):
        result = ProjectService._validate_display_name("  My Team  ")
        assert result == "My Team"

    def test_returns_value_unchanged_when_no_whitespace(self):
        result = ProjectService._validate_display_name("My Team")
        assert result == "My Team"

    def test_accepts_exactly_150_characters(self):
        name = "a" * 150
        result = ProjectService._validate_display_name(name)
        assert result == name

    def test_raises_400_when_stripped_length_exceeds_150(self):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService._validate_display_name("x" * 151)
        assert exc_info.value.code == 400
        assert exc_info.value.message == "Project display name cannot exceed 150 characters"

    def test_raises_400_for_151_chars_before_stripping(self):
        # After stripping spaces the string is still 151 chars
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService._validate_display_name(" " + "x" * 151 + " ")
        assert exc_info.value.code == 400

    def test_strips_first_then_validates_length(self):
        # 152 chars total but only 150 after stripping 1 space on each side — should pass
        result = ProjectService._validate_display_name(" " + "a" * 150 + " ")
        assert len(result) == 150


class TestValidateAtLeastOneChatModelAllowed:
    """Regression for CR-017: the validator used to build a synthetic internal User just to
    call get_allowed_chat_models, which for an internal user resolves to
    llm_service.get_all_llm_model_info() anyway -- so it now calls that directly instead of
    routing through a fake user context."""

    def _model(self, deployment_name, is_router=False, enabled=True, forbidden_for_web=False):
        model = MagicMock()
        model.deployment_name = deployment_name
        model.base_name = deployment_name
        model.switchyard = []
        model.is_declared_litellm_router.return_value = is_router
        model.enabled = enabled
        model.forbidden_for_web = forbidden_for_web
        return model

    def test_none_allowed_models_is_always_valid(self):
        ProjectService.ensure_chat_model_allowed(None)  # should not raise

    def test_empty_list_is_valid_here_handled_elsewhere(self):
        ProjectService.ensure_chat_model_allowed([])  # should not raise

    def test_raises_when_no_allowed_model_matches_the_catalog(self):
        catalog = [self._model("gpt-4"), self._model("claude-3-sonnet")]
        with patch(
            "codemie.service.llm_service.llm_service.llm_service.get_all_llm_model_info",
            return_value=catalog,
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                ProjectService.ensure_chat_model_allowed(["nonexistent-model"])
        assert exc_info.value.code == 400

    def test_passes_when_an_allowed_model_matches_the_catalog(self):
        catalog = [self._model("gpt-4"), self._model("claude-3-sonnet")]
        with patch(
            "codemie.service.llm_service.llm_service.llm_service.get_all_llm_model_info",
            return_value=catalog,
        ):
            ProjectService.ensure_chat_model_allowed(["gpt-4", "nonexistent-model"])

    def test_litellm_auto_router_base_name_counts_as_a_chat_model(self):
        catalog = [self._model("gpt-smart-router", is_router=True)]
        with patch(
            "codemie.service.llm_service.llm_service.llm_service.get_all_llm_model_info",
            return_value=catalog,
        ):
            ProjectService.ensure_chat_model_allowed(["gpt-smart-router"])  # no raise

    def test_switchyard_router_base_name_counts_as_a_chat_model(self):
        from codemie.configs.llm_config import LLMRouter, RoutingMode, SwitchyardConfig

        router = LLMRouter(
            base_name="sy-signal-claude-4-5-sonnet-test",
            label="Signal Router",
            enabled=True,
            forbidden_for_web=False,
            provider=None,
            switchyard=SwitchyardConfig(
                capable_model="claude-4-5-sonnet",
                efficient_model="claude-4-5-haiku",
                mode=RoutingMode.SIGNAL,
            ),
        )
        with (
            patch(
                "codemie.service.llm_service.llm_service.llm_service.get_all_llm_model_info",
                return_value=[],
            ),
            patch(
                "codemie.service.llm_service.llm_service.llm_service.get_llm_routers",
                return_value=[router],
            ),
        ):
            ProjectService.ensure_chat_model_allowed(["sy-signal-claude-4-5-sonnet-test"])  # no raise

    def test_disabled_litellm_auto_router_does_not_count_as_a_chat_model(self):
        catalog = [self._model("gpt-smart-router", is_router=True, enabled=False)]
        with patch(
            "codemie.service.llm_service.llm_service.llm_service.get_all_llm_model_info",
            return_value=catalog,
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                ProjectService.ensure_chat_model_allowed(["gpt-smart-router"])
        assert exc_info.value.code == 400

    def test_forbidden_for_web_litellm_auto_router_does_not_count_as_a_chat_model(self):
        catalog = [self._model("gpt-smart-router", is_router=True, forbidden_for_web=True)]
        with patch(
            "codemie.service.llm_service.llm_service.llm_service.get_all_llm_model_info",
            return_value=catalog,
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                ProjectService.ensure_chat_model_allowed(["gpt-smart-router"])
        assert exc_info.value.code == 400

    def test_disabled_switchyard_router_does_not_count_as_a_chat_model(self):
        from codemie.configs.llm_config import LLMRouter, RoutingMode, SwitchyardConfig

        router = LLMRouter(
            base_name="sy-signal-claude-4-5-sonnet-test",
            label="Signal Router",
            enabled=False,
            forbidden_for_web=False,
            provider=None,
            switchyard=SwitchyardConfig(
                capable_model="claude-4-5-sonnet",
                efficient_model="claude-4-5-haiku",
                mode=RoutingMode.SIGNAL,
            ),
        )
        with (
            patch(
                "codemie.service.llm_service.llm_service.llm_service.get_all_llm_model_info",
                return_value=[],
            ),
            patch(
                "codemie.service.llm_service.llm_service.llm_service.get_llm_routers",
                return_value=[router],
            ),
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                ProjectService.ensure_chat_model_allowed(["sy-signal-claude-4-5-sonnet-test"])
        assert exc_info.value.code == 400

    def test_forbidden_for_web_switchyard_router_does_not_count_as_a_chat_model(self):
        from codemie.configs.llm_config import LLMRouter, RoutingMode, SwitchyardConfig

        router = LLMRouter(
            base_name="sy-signal-claude-4-5-sonnet-test",
            label="Signal Router",
            enabled=True,
            forbidden_for_web=True,
            provider=None,
            switchyard=SwitchyardConfig(
                capable_model="claude-4-5-sonnet",
                efficient_model="claude-4-5-haiku",
                mode=RoutingMode.SIGNAL,
            ),
        )
        with (
            patch(
                "codemie.service.llm_service.llm_service.llm_service.get_all_llm_model_info",
                return_value=[],
            ),
            patch(
                "codemie.service.llm_service.llm_service.llm_service.get_llm_routers",
                return_value=[router],
            ),
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                ProjectService.ensure_chat_model_allowed(["sy-signal-claude-4-5-sonnet-test"])
        assert exc_info.value.code == 400


class TestCheckAllowedModelsAuthorization:
    """Regression for CR-018: the hand-rolled check `user.is_admin or (up and
    up.is_project_admin)` omitted the platform-level maintainer role its own docstring and
    ALLOWED_MODELS_UNAUTHORIZED message promise, and duplicated a separate
    user_project_repository lookup instead of reusing the shared
    Application.is_managed_by(user) authorization every other project-mutating endpoint
    uses."""

    def _user(self, is_admin=False, is_maintainer=False, admin_project_names=None):
        user = User(
            id="user-1",
            username="someone",
            name="Someone",
            email="someone@example.com",
            is_admin=is_admin,
            is_maintainer=is_maintainer,
            admin_project_names=admin_project_names or [],
        )
        # pytest.ini forces ENV=local, which the User validator unconditionally promotes
        # to is_admin=True; force the requested value back so non-admin cases are testable.
        user.is_admin = is_admin
        return user

    def test_global_admin_is_authorized(self):
        project = SimpleNamespace(name="my-project", is_managed_by=lambda user: user.is_admin)
        ProjectService._check_allowed_models_authorization(project, self._user(is_admin=True))

    def test_platform_maintainer_is_authorized(self):
        """The actual regression: a platform maintainer (not a global admin) must be
        authorized, matching the docstring/error message's promise."""
        from codemie.core.models import Application

        project = Application(name="my-project")

        ProjectService._check_allowed_models_authorization(project, self._user(is_maintainer=True))

    def test_per_project_admin_is_authorized(self):
        from codemie.core.models import Application

        project = Application(name="my-project")

        ProjectService._check_allowed_models_authorization(project, self._user(admin_project_names=["my-project"]))

    def test_regular_user_is_rejected(self):
        from codemie.core.models import Application

        project = Application(name="my-project")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService._check_allowed_models_authorization(project, self._user())

        assert exc_info.value.code == 403


class TestFindProject:
    def test_missing_project_raises_404(self):
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService._find_project(session, "ghost-project")

        assert exc_info.value.code == 404
