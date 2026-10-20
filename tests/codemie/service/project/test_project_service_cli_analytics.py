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

"""ProjectService handling of cli_analytics_enabled (EPMCDME-16025)."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.rest_api.security.user import User
from codemie.service.project import cli_analytics_policy
from codemie.service.project.project_service import ProjectService


def _app(project_type: str = "shared") -> Application:
    return Application(
        id="proj",
        name="proj",
        project_type=project_type,
        date=datetime.now(),
        update_date=datetime.now(),
    )


def _user(*, global_write: bool) -> MagicMock:
    user = MagicMock()
    user.id = "u-1"
    user.is_admin_or_maintainer = global_write
    return user


def _ordinary_user() -> User:
    """A real user with no global or project-admin rights (ENV=local would make every User admin)."""
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id="u-1", email="u-1@example.com", project_names=["demo"])


@pytest.fixture
def repo():
    with (
        patch("codemie.service.project.project_service.get_session") as get_session,
        patch("codemie.service.project.project_service.application_repository") as app_repo,
        patch("codemie.service.project.project_service.user_project_repository") as user_repo,
        patch("codemie.service.project.project_service.cost_center_service"),
        patch("codemie.service.project.project_service.activity_event_repository"),
    ):
        get_session.return_value.__enter__.return_value = MagicMock()
        user_repo.is_admin.return_value = True  # a project admin: allowed to edit other fields
        yield app_repo


def test_update_forwards_value_to_repository(repo):
    project = _app()
    repo.get_by_name.return_value = project
    repo.update_project.return_value = project

    ProjectService.update_project(_user(global_write=True), "proj", cli_analytics_enabled=True)

    assert repo.update_project.call_args.kwargs["cli_analytics_enabled"] is True


def test_update_without_value_passes_none(repo):
    project = _app()
    repo.get_by_name.return_value = project
    repo.update_project.return_value = project

    ProjectService.update_project(_user(global_write=False), "proj", description="d")

    assert repo.update_project.call_args.kwargs["cli_analytics_enabled"] is None


@pytest.mark.parametrize("value", [True, False])
def test_update_rejects_value_without_global_write(repo, value):
    repo.get_by_name.return_value = _app()

    with pytest.raises(ExtendedHTTPException) as exc:
        ProjectService.update_project(_user(global_write=False), "proj", cli_analytics_enabled=value)

    assert exc.value.code == 403
    repo.update_project.assert_not_called()


def test_update_rejects_enabling_personal_project(repo):
    repo.get_by_name.return_value = _app("personal")

    with pytest.raises(ExtendedHTTPException) as exc:
        ProjectService.update_project(_user(global_write=True), "proj", cli_analytics_enabled=True)

    assert exc.value.code == 400
    repo.update_project.assert_not_called()


def test_update_allows_disabling_personal_project(repo):
    project = _app("personal")
    repo.get_by_name.return_value = project
    repo.update_project.return_value = project

    ProjectService.update_project(_user(global_write=True), "proj", cli_analytics_enabled=False)

    assert repo.update_project.call_args.kwargs["cli_analytics_enabled"] is False


def test_create_forwards_value_to_repository(repo):
    repo.get_by_name_case_insensitive.return_value = None
    repo.create.return_value = _app()
    with patch.object(ProjectService, "_enforce_project_limit"):
        ProjectService.create_shared_project(_user(global_write=True), "proj-name", cli_analytics_enabled=True)

    assert repo.create.call_args.kwargs["cli_analytics_enabled"] is True


def test_create_rejects_value_without_global_write(repo):
    user = _ordinary_user()
    with patch.object(ProjectService, "_enforce_project_limit"):
        with pytest.raises(ExtendedHTTPException) as exc:
            ProjectService.create_shared_project(user, "proj-name", cli_analytics_enabled=False)

    assert exc.value.code == 403
    repo.create.assert_not_called()
    assert user.admin_project_names == []
    assert user.project_names == ["demo"]


def test_create_grants_creator_project_admin_on_the_request_user_without_global_write(repo):
    user = _ordinary_user()
    repo.get_by_name_case_insensitive.return_value = None
    repo.create.return_value = _app()
    with (
        patch.object(ProjectService, "_enforce_project_limit"),
        patch.object(ProjectService, "ensure_chat_model_allowed"),
        patch("codemie.service.project.project_service.llm_service") as llm,
    ):
        llm.get_allowed_chat_models.return_value = [MagicMock(deployment_name="gpt-x")]
        ProjectService.create_shared_project(user, "proj-name")

    assert "proj-name" in user.admin_project_names
    assert "proj-name" in user.project_names
    assert user.is_admin_or_maintainer is False
    with pytest.raises(ExtendedHTTPException) as exc:
        cli_analytics_policy.require_write(user, {cli_analytics_policy.FIELD})
    assert exc.value.code == 403
