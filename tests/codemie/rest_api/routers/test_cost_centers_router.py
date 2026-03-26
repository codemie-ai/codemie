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

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from codemie.rest_api.routers.cost_centers import (
    CostCenterCreateRequest,
    create_cost_center,
    get_cost_center_detail,
    list_cost_centers,
)
from codemie.rest_api.security.user import User


def _super_admin() -> User:
    return User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)


class TestCostCenterRouter:
    @patch("codemie.rest_api.routers.cost_centers.config")
    @patch("codemie.rest_api.routers.cost_centers.get_session")
    @patch("codemie.rest_api.routers.cost_centers.application_repository")
    @patch("codemie.rest_api.routers.cost_centers.cost_center_service")
    def test_create_cost_center(
        self,
        mock_cost_center_service,
        mock_application_repository,
        mock_get_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_application_repository.count_active_projects_by_cost_center_id.return_value = 0
        cost_center = SimpleNamespace(
            id=uuid4(),
            name="epm-cdme",
            description="Core platform",
            created_by="admin-1",
            date=datetime(2026, 3, 19, tzinfo=UTC),
        )
        mock_cost_center_service.create.return_value = cost_center

        response = create_cost_center(
            payload=CostCenterCreateRequest(name="epm-cdme", description="Core platform"),
            user=_super_admin(),
            _=None,
        )

        mock_cost_center_service.create.assert_called_once()
        assert response.name == "epm-cdme"
        assert response.project_count == 0

    @patch("codemie.rest_api.routers.cost_centers.config")
    @patch("codemie.rest_api.routers.cost_centers.get_session")
    @patch("codemie.rest_api.routers.cost_centers.application_repository")
    @patch("codemie.rest_api.routers.cost_centers.cost_center_service")
    def test_list_cost_centers(
        self,
        mock_cost_center_service,
        mock_application_repository,
        mock_get_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_application_repository.count_active_projects_by_cost_center_id.return_value = 0
        cost_center = SimpleNamespace(
            id=uuid4(),
            name="epm-cdme",
            description="Core platform",
            created_by="admin-1",
            date=datetime(2026, 3, 19, tzinfo=UTC),
        )
        mock_cost_center_service.list_paginated.return_value = ([cost_center], 1)

        response = list_cost_centers(search="epm", page=0, per_page=20, user=_super_admin(), _=None)

        assert response.pagination.total == 1
        assert response.data[0].name == "epm-cdme"

    @patch("codemie.rest_api.routers.cost_centers.config")
    @patch("codemie.rest_api.routers.cost_centers.get_session")
    @patch("codemie.rest_api.routers.cost_centers.application_repository")
    @patch("codemie.rest_api.routers.cost_centers.cost_center_service")
    def test_get_cost_center_detail(
        self,
        mock_cost_center_service,
        mock_application_repository,
        mock_get_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        cost_center_id = uuid4()
        cost_center = SimpleNamespace(
            id=cost_center_id,
            name="epm-cdme",
            description="Core platform",
            created_by="admin-1",
            date=datetime(2026, 3, 19, tzinfo=UTC),
        )
        project = SimpleNamespace(
            name="data-pipeline",
            description="Analytics pipeline",
            project_type="shared",
            created_by="user-1",
            date=datetime(2026, 3, 19, tzinfo=UTC),
            cost_center_id=cost_center_id,
        )
        mock_cost_center_service.get_or_404.return_value = cost_center
        mock_application_repository.list_projects_by_cost_center_id.return_value = [project]
        mock_application_repository.get_project_member_counts_bulk.return_value = {"data-pipeline": (3, 1)}
        mock_application_repository.count_active_projects_by_cost_center_id.return_value = 1

        response = get_cost_center_detail(cost_center_id=cost_center_id, user=_super_admin(), _=None)

        assert response.id == cost_center_id
        assert response.project_count == 1
        assert response.projects[0].cost_center_name == "epm-cdme"
