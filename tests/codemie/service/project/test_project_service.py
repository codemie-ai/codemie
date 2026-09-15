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

import pytest
from sqlalchemy.exc import IntegrityError

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User
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
        assert "project_created" in mock_logger.info.call_args[0][0]

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
