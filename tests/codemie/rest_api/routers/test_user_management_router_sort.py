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

"""Router tests for EPMCDME-16070: sort_by / sort_order on GET /v1/admin/users."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codemie.rest_api.models.user_management import PaginatedUserListResponse, PaginationInfo
from codemie.rest_api.routers.user_management_router import router
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

# The main app mounts this router only when ENABLE_USER_MANAGEMENT is set at import time,
# so the tests mount it on a dedicated app.
app = FastAPI()
app.include_router(router)
client = TestClient(app)
URL = "/v1/admin/users"


@pytest.fixture
def mock_service():
    """Authenticate as an admin, enable user management and stub the service."""
    admin = User(id="admin-user", email="admin@example.com", username="admin", name="Admin", is_admin=True)
    app.dependency_overrides[authenticate] = lambda: admin
    with (
        patch("codemie.rest_api.routers.user_management_router.config.ENABLE_USER_MANAGEMENT", True),
        patch("codemie.rest_api.routers.user_management_router.user_management_service") as service,
    ):
        service.list_users_with_flow.return_value = PaginatedUserListResponse(
            data=[], pagination=PaginationInfo(total=0, page=0, per_page=20)
        )
        yield service
    app.dependency_overrides.pop(authenticate, None)


class TestListUsersSortParams:
    def test_sort_params_reach_service(self, mock_service):
        response = client.get(URL, params={"sort_by": "name", "sort_order": "asc"})

        assert response.status_code == 200
        kwargs = mock_service.list_users_with_flow.call_args.kwargs
        assert kwargs["sort_by"] == "name"
        assert kwargs["sort_order"] == "asc"

    def test_sort_order_defaults_to_asc(self, mock_service):
        response = client.get(URL, params={"sort_by": "name"})

        assert response.status_code == 200
        assert mock_service.list_users_with_flow.call_args.kwargs["sort_order"] == "asc"

    def test_invalid_sort_by_422(self, mock_service):
        response = client.get(URL, params={"sort_by": "bogus"})

        assert response.status_code == 422
        mock_service.list_users_with_flow.assert_not_called()

    def test_invalid_sort_order_422(self, mock_service):
        response = client.get(URL, params={"sort_by": "name", "sort_order": "bogus"})

        assert response.status_code == 422
        mock_service.list_users_with_flow.assert_not_called()
