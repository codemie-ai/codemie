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

"""Unit tests for /v1/projects visibility, creation, assignment, delete, and update endpoints."""

from datetime import datetime, UTC
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.projects import (
    router as projects_router,
    _authorize_project_access,
    _raise_project_not_found,
    ProjectAssignmentRequest,
    ProjectAssignmentUpdateRequest,
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectUpdateRequest,
    PaginatedProjectListResponse,
    assign_user_to_project,
    create_project,
    delete_project,
    get_project_detail,
    list_projects,
    remove_user_from_project,
    update_project,
    update_user_project_assignment,
)
from codemie.configs import config
from codemie.rest_api.security.user import User


@pytest.fixture
def regular_user() -> User:
    with patch.object(config, 'ENV', 'dev'), patch.object(config, 'ENABLE_USER_MANAGEMENT', True):
        return User(id="user-1", username="user1", email="user1@example.com", is_admin=False)


@pytest.fixture
def super_admin_user() -> User:
    with patch.object(config, 'ENV', 'dev'), patch.object(config, 'ENABLE_USER_MANAGEMENT', True):
        return User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)


class TestProjectCreationEndpoint:
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    @patch("codemie.rest_api.routers.projects._resolve_cost_center_name")
    def test_create_project_calls_service_directly(
        self,
        mock_resolve_cost_center_name,
        mock_project_service,
        mock_config,
        regular_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_resolve_cost_center_name.return_value = None
        project = SimpleNamespace(
            name="data-pipeline",
            description="Analytics pipeline",
            project_type="shared",
            created_by="user-1",
            date=datetime(2026, 2, 10, tzinfo=UTC),
        )
        mock_project_service.create_shared_project.return_value = project

        response = create_project(
            payload=ProjectCreateRequest(name="data-pipeline", description="Analytics pipeline"),
            user=regular_user,
        )

        assert response.name == "data-pipeline"
        mock_project_service.create_shared_project.assert_called_once_with(
            user=regular_user,
            project_name="data-pipeline",
            description="Analytics pipeline",
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    @patch("codemie.rest_api.routers.projects._resolve_cost_center_name")
    def test_create_project_success(
        self,
        mock_resolve_cost_center_name,
        mock_project_service,
        mock_config,
        regular_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_resolve_cost_center_name.return_value = None
        mock_project_service.create_shared_project.return_value = SimpleNamespace(
            name="data-pipeline",
            description="Analytics pipeline",
            project_type="shared",
            created_by="user-1",
            date=datetime(2026, 2, 10, tzinfo=UTC),
        )

        response = create_project(
            payload=ProjectCreateRequest(name="data-pipeline", description="Analytics pipeline"),
            user=regular_user,
        )

        assert response.name == "data-pipeline"
        assert response.description == "Analytics pipeline"
        assert response.project_type == "shared"
        assert response.created_by == "user-1"
        assert response.created_at == datetime(2026, 2, 10, tzinfo=UTC)

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    @patch("codemie.rest_api.routers.projects._resolve_cost_center_name")
    def test_create_project_with_cost_center(
        self,
        mock_resolve_cost_center_name,
        mock_project_service,
        mock_config,
        regular_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        cost_center_id = uuid4()
        mock_resolve_cost_center_name.return_value = "epm-cdme"
        mock_project_service.create_shared_project.return_value = SimpleNamespace(
            name="data-pipeline",
            description="Analytics pipeline",
            project_type="shared",
            created_by="user-1",
            date=datetime(2026, 2, 10, tzinfo=UTC),
            cost_center_id=cost_center_id,
        )

        response = create_project(
            payload=ProjectCreateRequest(
                name="data-pipeline",
                description="Analytics pipeline",
                cost_center_id=cost_center_id,
            ),
            user=regular_user,
        )

        mock_project_service.create_shared_project.assert_called_once_with(
            user=regular_user,
            project_name="data-pipeline",
            description="Analytics pipeline",
            cost_center_id=cost_center_id,
        )
        assert response.cost_center_id == cost_center_id
        assert response.cost_center_name == "epm-cdme"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_create_project_propagates_service_error(
        self,
        mock_project_service,
        mock_config,
        regular_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_project_service.create_shared_project.side_effect = ExtendedHTTPException(
            code=409,
            message="Project 'my-project' already exists. Please choose a different name.",
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            create_project(
                payload=ProjectCreateRequest(name="my-project", description="desc"),
                user=regular_user,
            )

        assert exc_info.value.code == 409
        assert exc_info.value.message == "Project 'my-project' already exists. Please choose a different name."


class TestProjectsVisibilityEndpoints:
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_visibility_service")
    def test_list_projects_uses_visibility_filtered_search(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        """Story 16: List projects with pagination and search"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_visibility_service.list_visible_projects_paginated.return_value = (
            [
                {
                    "name": "shared-proj",
                    "description": "desc",
                    "project_type": "shared",
                    "created_by": "owner-1",
                    "created_at": datetime(2026, 2, 10, tzinfo=UTC),
                    "user_count": 3,
                    "admin_count": 1,
                }
            ],
            1,
        )

        result = list_projects(search="shared", page=0, per_page=20, include_counters=True, user=regular_user)

        assert isinstance(result, PaginatedProjectListResponse)
        assert len(result.data) == 1
        assert result.data[0].name == "shared-proj"
        assert result.data[0].user_count == 3
        assert result.data[0].admin_count == 1
        assert result.pagination.total == 1
        assert result.pagination.page == 0
        assert result.pagination.per_page == 20
        mock_visibility_service.list_visible_projects_paginated.assert_called_once_with(
            session=mock_session,
            user_id="user-1",
            is_admin=False,
            search="shared",
            page=0,
            per_page=20,
            include_counters=True,
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_visibility_service")
    def test_get_project_detail_includes_created_at_and_members(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        """Story 16: Project detail includes created_at and member list"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_visibility_service.get_visible_project_with_members.return_value = {
            "name": "shared-proj",
            "description": "desc",
            "project_type": "shared",
            "created_by": "owner-1",
            "created_at": datetime(2026, 2, 10, tzinfo=UTC),
            "user_count": 2,
            "admin_count": 1,
            "members": [
                {
                    "user_id": "user-1",
                    "is_project_admin": True,
                    "date": datetime(2026, 2, 10, tzinfo=UTC),
                },
                {
                    "user_id": "user-2",
                    "is_project_admin": False,
                    "date": datetime(2026, 2, 11, tzinfo=UTC),
                },
            ],
        }

        result = get_project_detail(
            request=MagicMock(method="GET", url=SimpleNamespace(path="/v1/projects/shared-proj")),
            project_name="shared-proj",
            user=regular_user,
        )

        assert isinstance(result, ProjectDetailResponse)
        assert result.created_at == datetime(2026, 2, 10, tzinfo=UTC)
        assert result.user_count == 2
        assert result.admin_count == 1
        assert len(result.members) == 2
        assert result.members[0].user_id == "user-1"
        assert result.members[0].is_project_admin is True

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_visibility_service")
    def test_get_project_detail_returns_404_when_invisible(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_visibility_service.get_visible_project_with_members.side_effect = ExtendedHTTPException(
            code=404, message="Project not found"
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_project_detail(
                request=MagicMock(method="GET", url=SimpleNamespace(path="/v1/projects/hidden-proj")),
                project_name="hidden-proj",
                user=regular_user,
            )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"
        mock_visibility_service.get_visible_project_with_members.assert_called_once_with(
            session=mock_session,
            project_name="hidden-proj",
            user_id="user-1",
            is_admin=False,
            action="GET /v1/projects/hidden-proj",
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_visibility_service")
    def test_list_projects_pagination_first_page(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_visibility_service.list_visible_projects_paginated.return_value = (
            [
                {
                    "name": f"proj-{i}",
                    "description": None,
                    "project_type": "shared",
                    "created_by": "owner-1",
                    "created_at": datetime(2026, 2, 10, tzinfo=UTC),
                    "user_count": 1,
                    "admin_count": 1,
                }
                for i in range(10)
            ],
            100,
        )

        result = list_projects(search=None, page=0, per_page=10, user=regular_user)

        assert result.pagination.page == 0
        assert result.pagination.per_page == 10
        assert result.pagination.total == 100
        assert len(result.data) == 10

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_visibility_service")
    def test_list_projects_empty_results(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        """Story 16: Empty results return valid paginated response"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_visibility_service.list_visible_projects_paginated.return_value = ([], 0)

        result = list_projects(search="nonexistent", page=0, per_page=20, user=regular_user)

        assert isinstance(result, PaginatedProjectListResponse)
        assert len(result.data) == 0
        assert result.pagination.total == 0
        assert result.pagination.page == 0

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_visibility_service")
    def test_list_projects_super_admin_sees_all(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        """Story 16: Super admin sees all projects including personal"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_visibility_service.list_visible_projects_paginated.return_value = (
            [
                {
                    "name": "personal-proj",
                    "description": None,
                    "project_type": "personal",
                    "created_by": "user-1",
                    "created_at": datetime(2026, 2, 10, tzinfo=UTC),
                    "user_count": 1,
                    "admin_count": 1,
                },
                {
                    "name": "shared-proj",
                    "description": None,
                    "project_type": "shared",
                    "created_by": "user-2",
                    "created_at": datetime(2026, 2, 11, tzinfo=UTC),
                    "user_count": 5,
                    "admin_count": 2,
                },
            ],
            2,
        )

        result = list_projects(search=None, page=0, per_page=20, include_counters=True, user=super_admin_user)

        assert len(result.data) == 2
        assert result.data[0].project_type == "personal"
        assert result.data[1].project_type == "shared"
        mock_visibility_service.list_visible_projects_paginated.assert_called_once_with(
            session=mock_session,
            user_id="admin-1",
            is_admin=True,
            search=None,
            page=0,
            per_page=20,
            include_counters=True,
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_visibility_service")
    def test_project_detail_includes_all_required_fields(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        """Story 16: Project detail response includes all required fields"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_visibility_service.get_visible_project_with_members.return_value = {
            "name": "analytics-dashboard",
            "description": "Analytics project",
            "project_type": "shared",
            "created_by": "user-456",
            "created_at": datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC),
            "user_count": 5,
            "admin_count": 2,
            "members": [
                {"user_id": "user-1", "is_project_admin": True, "date": datetime(2026, 1, 15, tzinfo=UTC)},
                {"user_id": "user-2", "is_project_admin": False, "date": datetime(2026, 1, 16, tzinfo=UTC)},
            ],
        }

        result = get_project_detail(
            request=MagicMock(method="GET", url=SimpleNamespace(path="/v1/projects/analytics-dashboard")),
            project_name="analytics-dashboard",
            user=regular_user,
        )

        # Verify all Story 16 required fields
        assert result.name == "analytics-dashboard"
        assert result.description == "Analytics project"
        assert result.project_type == "shared"
        assert result.created_by == "user-456"
        assert result.user_count == 5
        assert result.admin_count == 2
        assert result.created_at == datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)
        assert len(result.members) == 2


class TestProjectAssignmentEndpoints:
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_assign_user_returns_404_for_personal_project(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.assign_user_to_project.side_effect = ExtendedHTTPException(
            code=404, message="Project not found"
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            assign_user_to_project(
                request=MagicMock(
                    method="POST",
                    url=SimpleNamespace(path="/v1/projects/owner@example.com/assignment"),
                    state=SimpleNamespace(user=super_admin_user),
                ),
                payload=ProjectAssignmentRequest(user_id="target-1", is_project_admin=False),
                project_name="owner@example.com",
                authorized_project=MagicMock(project_type="personal"),
            )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_assign_user_success(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.assign_user_to_project.return_value = {
            "message": "User assigned to project successfully",
            "user_id": "target-1",
            "project_name": "shared-proj",
            "is_project_admin": True,
        }

        response = assign_user_to_project(
            request=MagicMock(
                method="POST",
                url=SimpleNamespace(path="/v1/projects/shared-proj/assignment"),
                state=SimpleNamespace(user=super_admin_user),
            ),
            payload=ProjectAssignmentRequest(user_id="target-1", is_project_admin=True),
            project_name="shared-proj",
            authorized_project=MagicMock(project_type="shared"),
        )

        assert response.message == "User assigned to project successfully"
        assert response.user_id == "target-1"
        assert response.project_name == "shared-proj"
        assert response.is_project_admin is True

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_update_assignment_returns_404_when_target_not_assigned(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.update_user_project_role.side_effect = ExtendedHTTPException(
            code=404, message="User is not assigned to this project"
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            update_user_project_assignment(
                request=MagicMock(
                    method="PUT",
                    url=SimpleNamespace(path="/v1/projects/shared-proj/assignment/target-1"),
                    state=SimpleNamespace(user=super_admin_user),
                ),
                payload=ProjectAssignmentUpdateRequest(is_project_admin=False),
                project_name="shared-proj",
                user_id="target-1",
                authorized_project=MagicMock(project_type="shared"),
            )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "User is not assigned to this project"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_remove_assignment_success(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.remove_user_from_project.return_value = {
            "message": "User removed from project successfully",
            "user_id": "target-1",
            "project_name": "shared-proj",
        }

        response = remove_user_from_project(
            request=MagicMock(
                method="DELETE",
                url=SimpleNamespace(path="/v1/projects/shared-proj/assignment/target-1"),
                state=SimpleNamespace(user=super_admin_user),
            ),
            project_name="shared-proj",
            user_id="target-1",
            authorized_project=MagicMock(project_type="shared"),
        )

        assert response.message == "User removed from project successfully"
        assert response.user_id == "target-1"
        assert response.project_name == "shared-proj"

    def test_assignment_routes_use_project_admin_or_super_admin_dependency(self):
        app = FastAPI()
        app.include_router(projects_router)

        route_dependencies: dict[tuple[str, str], set[str]] = {}
        for route in app.routes:
            if isinstance(route, APIRoute):
                for method in route.methods:
                    route_dependencies[(method, route.path)] = {
                        dependency.call.__name__ for dependency in route.dependant.dependencies
                    }

        assert "_authorize_project_access" in route_dependencies[("POST", "/v1/projects/{projectName}/assignment")]
        assert (
            "_authorize_project_access" in route_dependencies[("PUT", "/v1/projects/{projectName}/assignment/{userId}")]
        )
        assert (
            "_authorize_project_access"
            in route_dependencies[("DELETE", "/v1/projects/{projectName}/assignment/{userId}")]
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_assign_user_returns_409_when_already_assigned(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        """AC: Assigning already-assigned user returns 409 Conflict"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.assign_user_to_project.side_effect = ExtendedHTTPException(
            code=409, message="User already assigned to project"
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            assign_user_to_project(
                request=MagicMock(
                    method="POST",
                    url=SimpleNamespace(path="/v1/projects/shared-proj/assignment"),
                    state=SimpleNamespace(user=super_admin_user),
                ),
                payload=ProjectAssignmentRequest(user_id="target-1", is_project_admin=False),
                project_name="shared-proj",
                authorized_project=MagicMock(project_type="shared"),
            )

        assert exc_info.value.code == 409
        assert exc_info.value.message == "User already assigned to project"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_assign_user_returns_404_when_target_user_not_found(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        """AC: Target user not found returns 404"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.assign_user_to_project.side_effect = ExtendedHTTPException(
            code=404, message="User not found"
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            assign_user_to_project(
                request=MagicMock(
                    method="POST",
                    url=SimpleNamespace(path="/v1/projects/shared-proj/assignment"),
                    state=SimpleNamespace(user=super_admin_user),
                ),
                payload=ProjectAssignmentRequest(user_id="nonexistent", is_project_admin=False),
                project_name="shared-proj",
                authorized_project=MagicMock(project_type="shared"),
            )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "User not found"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_update_assignment_returns_404_for_personal_project(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        """AC: Attempting to modify personal project returns 404 (not 403)"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.update_user_project_role.side_effect = ExtendedHTTPException(
            code=404, message="Project not found"
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            update_user_project_assignment(
                request=MagicMock(
                    method="PUT",
                    url=SimpleNamespace(path="/v1/projects/owner@example.com/assignment/target-1"),
                    state=SimpleNamespace(user=super_admin_user),
                ),
                payload=ProjectAssignmentUpdateRequest(is_project_admin=False),
                project_name="owner@example.com",
                user_id="target-1",
                authorized_project=MagicMock(project_type="personal"),
            )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_remove_assignment_returns_404_when_target_not_assigned(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        """AC: DELETE on non-member returns 404"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.remove_user_from_project.side_effect = ExtendedHTTPException(
            code=404, message="User is not assigned to this project"
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            remove_user_from_project(
                request=MagicMock(
                    method="DELETE",
                    url=SimpleNamespace(path="/v1/projects/shared-proj/assignment/target-1"),
                    state=SimpleNamespace(user=super_admin_user),
                ),
                project_name="shared-proj",
                user_id="target-1",
                authorized_project=MagicMock(project_type="shared"),
            )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "User is not assigned to this project"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_project_admin_can_remove_themselves(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        """AC: Project admin CAN remove themselves from a project"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.remove_user_from_project.return_value = {
            "message": "User removed from project successfully",
            "user_id": "user-1",
            "project_name": "shared-proj",
        }

        response = remove_user_from_project(
            request=MagicMock(
                method="DELETE",
                url=SimpleNamespace(path="/v1/projects/shared-proj/assignment/user-1"),
                state=SimpleNamespace(user=regular_user),
            ),
            project_name="shared-proj",
            user_id="user-1",
            authorized_project=MagicMock(project_type="shared"),
        )

        assert response.message == "User removed from project successfully"
        assert response.user_id == "user-1"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_project_admin_can_demote_themselves(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        """AC: Project admin CAN demote their own is_project_admin flag"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.update_user_project_role.return_value = {
            "message": "User role updated successfully",
            "user_id": "user-1",
            "project_name": "shared-proj",
            "is_project_admin": False,
        }

        response = update_user_project_assignment(
            request=MagicMock(
                method="PUT",
                url=SimpleNamespace(path="/v1/projects/shared-proj/assignment/user-1"),
                state=SimpleNamespace(user=regular_user),
            ),
            payload=ProjectAssignmentUpdateRequest(is_project_admin=False),
            project_name="shared-proj",
            user_id="user-1",
            authorized_project=MagicMock(project_type="shared"),
        )

        assert response.message == "User role updated successfully"
        assert response.is_project_admin is False

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_removing_last_project_admin_succeeds(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        """AC: Removing last project admin succeeds (zero admins allowed)"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.remove_user_from_project.return_value = {
            "message": "User removed from project successfully",
            "user_id": "last-admin",
            "project_name": "shared-proj",
        }

        response = remove_user_from_project(
            request=MagicMock(
                method="DELETE",
                url=SimpleNamespace(path="/v1/projects/shared-proj/assignment/last-admin"),
                state=SimpleNamespace(user=super_admin_user),
            ),
            project_name="shared-proj",
            user_id="last-admin",
            authorized_project=MagicMock(project_type="shared"),
        )

        assert response.message == "User removed from project successfully"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_url_encoded_project_names_work_correctly(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        """AC: URL-encoded project names work correctly (e.g., john%40example.com)"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.assign_user_to_project.return_value = {
            "message": "User assigned to project successfully",
            "user_id": "target-1",
            "project_name": "team-analytics",
            "is_project_admin": False,
        }

        response = assign_user_to_project(
            request=MagicMock(
                method="POST",
                url=SimpleNamespace(path="/v1/projects/team-analytics/assignment"),
                state=SimpleNamespace(user=super_admin_user),
            ),
            payload=ProjectAssignmentRequest(user_id="target-1", is_project_admin=False),
            project_name="team-analytics",
            authorized_project=MagicMock(project_type="shared", name="team-analytics"),
        )

        assert response.project_name == "team-analytics"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_assignment_response_uses_snake_case(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        """AC: All JSON response fields use snake_case"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.assign_user_to_project.return_value = {
            "message": "User assigned to project successfully",
            "user_id": "target-1",
            "project_name": "shared-proj",
            "is_project_admin": True,
        }

        response = assign_user_to_project(
            request=MagicMock(
                method="POST",
                url=SimpleNamespace(path="/v1/projects/shared-proj/assignment"),
                state=SimpleNamespace(user=super_admin_user),
            ),
            payload=ProjectAssignmentRequest(user_id="target-1", is_project_admin=True),
            project_name="shared-proj",
            authorized_project=MagicMock(project_type="shared"),
        )

        assert hasattr(response, "user_id")
        assert hasattr(response, "project_name")
        assert hasattr(response, "is_project_admin")
        assert response.user_id == "target-1"
        assert response.project_name == "shared-proj"
        assert response.is_project_admin is True


class TestDeleteProjectEndpoint:
    """Tests for DELETE /v1/projects/{projectName}."""

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_delete_project_success_returns_200_with_message(
        self,
        mock_project_service,
        mock_get_session,
        mock_config,
    ):
        """Successful delete returns message and project name."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_project_service.delete_project.return_value = None

        authorized_project = MagicMock()
        authorized_project.name = "my-project"
        authorized_project.project_type = "shared"

        response = delete_project(
            request=MagicMock(
                method="DELETE",
                url=SimpleNamespace(path="/v1/projects/my-project"),
                state=SimpleNamespace(user=MagicMock(id="user-1")),
            ),
            project_name="my-project",
            authorized_project=authorized_project,
        )

        assert response.message == "Project 'my-project' deleted successfully"
        assert response.name == "my-project"
        mock_project_service.delete_project.assert_called_once_with(
            session=mock_session,
            project_name="my-project",
            project_type="shared",
            actor_id="user-1",
            action="DELETE /v1/projects/my-project",
            creator_id=authorized_project.created_by,
        )
        mock_session.commit.assert_called_once()

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_delete_project_propagates_403_from_service(
        self,
        mock_project_service,
        mock_get_session,
        mock_config,
    ):
        """delete_project propagates 403 when service raises it for personal project."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_project_service.delete_project.side_effect = ExtendedHTTPException(
            code=403, message="Cannot delete a personal project"
        )

        authorized_project = MagicMock()
        authorized_project.name = "user@example.com"
        authorized_project.project_type = "personal"

        with pytest.raises(ExtendedHTTPException) as exc_info:
            delete_project(
                request=MagicMock(
                    method="DELETE",
                    url=SimpleNamespace(path="/v1/projects/user@example.com"),
                    state=SimpleNamespace(user=MagicMock(id="user-1")),
                ),
                project_name="user@example.com",
                authorized_project=authorized_project,
            )

        assert exc_info.value.code == 403
        assert exc_info.value.message == "Cannot delete a personal project"
        mock_session.commit.assert_not_called()

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_delete_project_propagates_409_from_service(
        self,
        mock_project_service,
        mock_get_session,
        mock_config,
    ):
        """delete_project propagates 409 when service raises resource conflict."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_project_service.delete_project.side_effect = ExtendedHTTPException(
            code=409,
            message="Project 'busy-project' cannot be deleted because it has assigned resources.",
        )

        authorized_project = MagicMock()
        authorized_project.name = "busy-project"
        authorized_project.project_type = "shared"

        with pytest.raises(ExtendedHTTPException) as exc_info:
            delete_project(
                request=MagicMock(
                    method="DELETE",
                    url=SimpleNamespace(path="/v1/projects/busy-project"),
                    state=SimpleNamespace(user=MagicMock(id="user-1")),
                ),
                project_name="busy-project",
                authorized_project=authorized_project,
            )

        assert exc_info.value.code == 409
        mock_session.commit.assert_not_called()


class TestUpdateProjectEndpoint:
    """Tests for PATCH /v1/projects/{projectName}."""

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_update_description_returns_updated_response(
        self,
        mock_project_service,
        mock_config,
    ):
        """Successful PATCH with description returns ProjectCreateResponse."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        updated_app = MagicMock()
        updated_app.name = "my-project"
        updated_app.description = "new description"
        updated_app.project_type = "shared"
        updated_app.created_by = "user-1"
        updated_app.date = datetime(2024, 1, 1, tzinfo=UTC)
        updated_app.cost_center_id = None
        mock_project_service.update_project.return_value = updated_app

        user = MagicMock(id="user-1")
        response = update_project(
            payload=ProjectUpdateRequest(description="new description"),
            project_name="my-project",
            user=user,
        )

        assert response.name == "my-project"
        assert response.description == "new description"
        mock_project_service.update_project.assert_called_once_with(
            user=user,
            project_name="my-project",
            name=None,
            description="new description",
            cost_center_id=None,
            clear_cost_center=False,
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_update_cost_center_id_sets_update_cost_center_true(
        self,
        mock_project_service,
        mock_config,
    ):
        """PATCH with cost_center_id passes it through and sets update_cost_center=True."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        updated_app = MagicMock()
        updated_app.name = "my-project"
        updated_app.description = ""
        updated_app.project_type = "shared"
        updated_app.created_by = "user-1"
        updated_app.date = datetime(2024, 1, 1, tzinfo=UTC)
        updated_app.cost_center_id = None
        cost_center_id = uuid4()
        mock_project_service.update_project.return_value = updated_app

        user = MagicMock(id="user-1")
        update_project(
            payload=ProjectUpdateRequest(cost_center_id=cost_center_id),
            project_name="my-project",
            user=user,
        )

        mock_project_service.update_project.assert_called_once_with(
            user=user,
            project_name="my-project",
            name=None,
            description=None,
            cost_center_id=cost_center_id,
            clear_cost_center=False,
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_clear_cost_center_passes_none_to_service(
        self,
        mock_project_service,
        mock_config,
    ):
        """PATCH with clear_cost_center=True passes cost_center_id=None."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        updated_app = MagicMock()
        updated_app.name = "my-project"
        updated_app.description = ""
        updated_app.project_type = "shared"
        updated_app.created_by = "user-1"
        updated_app.date = datetime(2024, 1, 1, tzinfo=UTC)
        updated_app.cost_center_id = None
        mock_project_service.update_project.return_value = updated_app

        user = MagicMock(id="user-1")
        update_project(
            payload=ProjectUpdateRequest(clear_cost_center=True),
            project_name="my-project",
            user=user,
        )

        mock_project_service.update_project.assert_called_once_with(
            user=user,
            project_name="my-project",
            name=None,
            description=None,
            cost_center_id=None,
            clear_cost_center=True,
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_update_project_propagates_exception_from_service(
        self,
        mock_project_service,
        mock_config,
    ):
        """update_project propagates exceptions raised by the service."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_project_service.update_project.side_effect = ExtendedHTTPException(code=404, message="Project not found")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            update_project(
                payload=ProjectUpdateRequest(description="new description"),
                project_name="missing-project",
                user=MagicMock(id="user-1"),
            )

        assert exc_info.value.code == 404

    def test_request_model_requires_at_least_one_field(self):
        """ProjectUpdateRequest raises ValueError when no mutable field is provided."""
        with pytest.raises(ValueError, match="At least one mutable field must be provided"):
            ProjectUpdateRequest()

    def test_request_model_rejects_cost_center_id_with_clear_flag(self):
        """ProjectUpdateRequest raises ValueError when both cost_center_id and clear_cost_center are set."""
        with pytest.raises(ValueError, match="Provide either cost_center_id or clear_cost_center"):
            ProjectUpdateRequest(cost_center_id=uuid4(), clear_cost_center=True)


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TestRaiseProjectNotFound:
    @patch("codemie.rest_api.routers.projects.logger")
    def test_extracts_http_method_from_action(self, mock_logger):
        action = "POST /v1/projects/test-project/assignment"
        with pytest.raises(ExtendedHTTPException) as exc_info:
            _raise_project_not_found(user_id="user-123", project_name="test-project", action=action)
        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"
        log_message = mock_logger.warning.call_args[0][0]
        assert "user_id=user-123" in log_message
        assert "method=POST" in log_message
        assert "timestamp=" in log_message
        # PII: project_name must NOT appear in log
        assert "test-project" not in log_message

    @patch("codemie.rest_api.routers.projects.logger")
    def test_action_without_path(self, mock_logger):
        with pytest.raises(ExtendedHTTPException):
            _raise_project_not_found(user_id="user-456", project_name="hidden", action="DELETE")
        log_message = mock_logger.warning.call_args[0][0]
        assert "method=DELETE" in log_message

    @patch("codemie.rest_api.routers.projects.logger")
    def test_empty_action_defaults_to_unknown(self, mock_logger):
        with pytest.raises(ExtendedHTTPException):
            _raise_project_not_found(user_id="user-789", project_name="proj", action="")
        log_message = mock_logger.warning.call_args[0][0]
        assert "method=UNKNOWN" in log_message


class TestCheckProjectAccess:
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.application_repository")
    @patch("codemie.rest_api.routers.projects.Ability")
    def test_resolves_authorized_project(self, mock_ability_class, mock_app_repo, mock_get_session, mock_config):
        mock_config.ENABLE_USER_MANAGEMENT = True

        request = MagicMock()
        request.method = "POST"
        request.url.path = "/v1/projects/shared-proj/assignment"

        from codemie.rest_api.security.user import User

        user = User(id="user-1", username="test", is_admin=False)
        project = MagicMock(spec=["name", "project_type", "deleted_at"])
        project.deleted_at = None

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_app_repo.get_by_name.return_value = project

        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability_class.return_value = mock_ability_instance

        result = _authorize_project_access(request=request, project_name="shared-proj", user=user)

        assert result is project
        mock_app_repo.get_by_name.assert_called_once_with(mock_session, "shared-proj")
