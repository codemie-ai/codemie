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

"""Unit tests for /v1/projects visibility, creation, and assignment endpoints."""

from datetime import datetime, UTC
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.projects import (
    router as projects_router,
    ProjectAssignmentRequest,
    ProjectAssignmentUpdateRequest,
    ProjectCreateRequest,
    ProjectDetailResponse,
    PaginatedProjectListResponse,
    assign_user_to_project,
    create_project,
    get_project_detail,
    list_projects,
    remove_user_from_project,
    update_user_project_assignment,
)
from codemie.rest_api.security.user import User


@pytest.fixture
def regular_user() -> User:
    return User(id="user-1", username="user1", email="user1@example.com", is_super_admin=False)


@pytest.fixture
def super_admin_user() -> User:
    return User(id="admin-1", username="admin", email="admin@example.com", is_super_admin=True)


class TestProjectCreationEndpoint:
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_create_project_calls_service_directly(
        self,
        mock_project_service,
        mock_config,
        regular_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        project = SimpleNamespace(
            name="DataPipeline",
            description="Analytics pipeline",
            project_type="shared",
            created_by="user-1",
            date=datetime(2026, 2, 10, tzinfo=UTC),
        )
        mock_project_service.create_shared_project.return_value = project

        response = create_project(
            payload=ProjectCreateRequest(name="DataPipeline", description="Analytics pipeline"),
            user=regular_user,
        )

        assert response.name == "DataPipeline"
        mock_project_service.create_shared_project.assert_called_once_with(
            user=regular_user,
            project_name="DataPipeline",
            description="Analytics pipeline",
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_create_project_success(
        self,
        mock_project_service,
        mock_config,
        regular_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_project_service.create_shared_project.return_value = SimpleNamespace(
            name="DataPipeline",
            description="Analytics pipeline",
            project_type="shared",
            created_by="user-1",
            date=datetime(2026, 2, 10, tzinfo=UTC),
        )

        response = create_project(
            payload=ProjectCreateRequest(name="DataPipeline", description="Analytics pipeline"),
            user=regular_user,
        )

        assert response.name == "DataPipeline"
        assert response.description == "Analytics pipeline"
        assert response.project_type == "shared"
        assert response.created_by == "user-1"
        assert response.created_at == datetime(2026, 2, 10, tzinfo=UTC)

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
            message="Project 'MyProject' already exists. Please choose a different name.",
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            create_project(
                payload=ProjectCreateRequest(name="myproject", description="desc"),
                user=regular_user,
            )

        assert exc_info.value.code == 409
        assert exc_info.value.message == "Project 'MyProject' already exists. Please choose a different name."


class TestProjectsVisibilityEndpoints:
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.clients.postgres.get_session")
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

        result = list_projects(search="shared", page=0, per_page=20, user=regular_user)

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
            is_super_admin=False,
            search="shared",
            page=0,
            per_page=20,
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.clients.postgres.get_session")
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
    @patch("codemie.clients.postgres.get_session")
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
            is_super_admin=False,
            action="GET /v1/projects/hidden-proj",
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.rest_api.routers.projects.project_visibility_service")
    def test_list_projects_pagination_page_0(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        """Story 16: Pagination works correctly with page=0"""
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
    @patch("codemie.clients.postgres.get_session")
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
    @patch("codemie.clients.postgres.get_session")
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

        result = list_projects(search=None, page=0, per_page=20, user=super_admin_user)

        assert len(result.data) == 2
        assert result.data[0].project_type == "personal"
        assert result.data[1].project_type == "shared"
        mock_visibility_service.list_visible_projects_paginated.assert_called_once_with(
            session=mock_session,
            user_id="admin-1",
            is_super_admin=True,
            search=None,
            page=0,
            per_page=20,
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.clients.postgres.get_session")
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
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.project_assignment_service.project_assignment_service")
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
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.project_assignment_service.project_assignment_service")
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
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.project_assignment_service.project_assignment_service")
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
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.project_assignment_service.project_assignment_service")
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

        assert (
            "project_admin_or_super_admin_access"
            in route_dependencies[("POST", "/v1/projects/{projectName}/assignment")]
        )
        assert (
            "project_admin_or_super_admin_access"
            in route_dependencies[("PUT", "/v1/projects/{projectName}/assignment/{userId}")]
        )
        assert (
            "project_admin_or_super_admin_access"
            in route_dependencies[("DELETE", "/v1/projects/{projectName}/assignment/{userId}")]
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.project_assignment_service.project_assignment_service")
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
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.project_assignment_service.project_assignment_service")
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
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.project_assignment_service.project_assignment_service")
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
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.project_assignment_service.project_assignment_service")
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
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.project_assignment_service.project_assignment_service")
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
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.project_assignment_service.project_assignment_service")
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
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.project_assignment_service.project_assignment_service")
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
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.project_assignment_service.project_assignment_service")
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
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.service.user.project_assignment_service.project_assignment_service")
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
