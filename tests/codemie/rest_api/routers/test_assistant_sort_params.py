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

from unittest.mock import patch

import pytest
from fastapi import status
from httpx import AsyncClient, ASGITransport

from codemie.rest_api.main import app
from codemie.rest_api.security.user import User


@pytest.fixture(autouse=True)
def override_auth():
    from codemie.rest_api.security.authentication import authenticate

    user = User(id="123", username="testuser", name="Test User")
    app.dependency_overrides[authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_invalid_sort_by_returns_422():
    """Invalid sort_by value should return HTTP 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/assistants?scope=marketplace&sort_by=invalid_field")
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_valid_sort_by_is_forwarded_to_repository():
    """Valid sort_by should be passed to repository.query()."""
    mock_result = {"data": [], "pagination": {"page": 0, "per_page": 12, "total": 0, "pages": 0}}

    with patch("codemie.rest_api.routers.assistant.catalog.AssistantRepository") as mock_repo:
        instance = mock_repo.return_value
        instance.query.return_value = mock_result

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/assistants?scope=marketplace&sort_by=likes&sort_order=asc")

        assert response.status_code == status.HTTP_200_OK
        call_kwargs = instance.query.call_args.kwargs
        from codemie.rest_api.models.assistant import AssistantSortBy
        from codemie.rest_api.models.index import SortOrder

        assert call_kwargs["sort_by"] == AssistantSortBy.LIKES
        assert call_kwargs["sort_order"] == SortOrder.ASC


@pytest.mark.asyncio
async def test_group_by_is_global_default_is_true():
    """group_by_is_global should default to True."""
    mock_result = {"data": [], "pagination": {"page": 0, "per_page": 12, "total": 0, "pages": 0}}

    with patch("codemie.rest_api.routers.assistant.catalog.AssistantRepository") as mock_repo:
        instance = mock_repo.return_value
        instance.query.return_value = mock_result

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/assistants?scope=marketplace")

        assert response.status_code == status.HTTP_200_OK
        call_kwargs = instance.query.call_args.kwargs
        assert call_kwargs["group_by_is_global"] is True
