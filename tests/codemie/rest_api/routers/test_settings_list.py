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

"""Tests for GET /v1/settings (the combined user and project integrations list)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import SettingType
from codemie.rest_api.routers.settings import router
from codemie.rest_api.security.user import User

PER_PAGE_LIMIT = 100
# The largest page whose row offset (page * per_page) stays within PostgreSQL's bigint OFFSET at any allowed per_page.
PAGE_LIMIT = (2**63 - 1) // PER_PAGE_LIMIT

app = FastAPI()
app.include_router(router)

ALL = frozenset({SettingType.USER, SettingType.PROJECT})
LIST_RESULT = {"data": [], "pagination": {"page": 0, "per_page": 10, "total": 0, "pages": 0}}


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def user():
    return User(id="user-1", username="user@example.com", project_names=["A"], admin_project_names=["A"])


@pytest.fixture
def mock_query_postgres():
    """Mocks the query at ``_query_postgres`` so ``run``'s own filters parsing runs for real."""
    with patch(
        "codemie.service.settings.settings_index_service.SettingsIndexService._query_postgres",
        return_value=([], 0),
    ) as mock:
        yield mock


@pytest.fixture
def authenticated(user):
    with patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate", return_value=user) as mock:
        yield mock


async def _get(params: dict):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        return await ac.get("/v1/settings", headers={"user-id": "user-1"}, params=params)


@pytest.mark.anyio
async def test_no_scope_lists_both_scopes(authenticated, mock_query_postgres, user):
    response = await _get({})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == LIST_RESULT
    mock_query_postgres.assert_called_once_with(user=user, scopes=ALL, page=0, per_page=10, filters={})


@pytest.mark.anyio
@pytest.mark.parametrize(
    "scope, expected",
    [
        ("user", frozenset({SettingType.USER})),
        ("project", frozenset({SettingType.PROJECT})),
        ("all", ALL),
    ],
)
async def test_scope_value_selects_scopes(authenticated, mock_query_postgres, scope, expected):
    response = await _get({"scope": scope, "page": 2, "per_page": 5})

    assert response.status_code == status.HTTP_200_OK
    assert mock_query_postgres.call_args.kwargs["scopes"] == expected
    assert mock_query_postgres.call_args.kwargs["page"] == 2
    assert mock_query_postgres.call_args.kwargs["per_page"] == 5


@pytest.mark.anyio
async def test_scope_inside_filters_is_ignored(authenticated, mock_query_postgres):
    response = await _get({"filters": '{"scope": "user", "search": "jira"}'})

    assert response.status_code == status.HTTP_200_OK
    assert mock_query_postgres.call_args.kwargs["scopes"] == ALL
    assert mock_query_postgres.call_args.kwargs["filters"] == {"scope": "user", "search": "jira"}


@pytest.mark.anyio
async def test_unknown_scope_returns_422(authenticated, mock_query_postgres):
    response = await _get({"scope": "bogus"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert all(f"'{value}'" in response.text for value in ("all", "user", "project"))
    mock_query_postgres.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "params",
    [
        {"page": -1},
        {"per_page": 0},
        {"per_page": PER_PAGE_LIMIT + 1},
        {"per_page": 1_000_000},
        {"page": PAGE_LIMIT + 1},
        {"page": 10**18},
    ],
)
async def test_bad_paging_returns_422(authenticated, mock_query_postgres, params):
    response = await _get(params)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    mock_query_postgres.assert_not_called()


@pytest.mark.anyio
async def test_paging_upper_bounds_are_accepted(authenticated, mock_query_postgres):
    response = await _get({"page": PAGE_LIMIT, "per_page": PER_PAGE_LIMIT})

    assert response.status_code == status.HTTP_200_OK
    assert mock_query_postgres.call_args.kwargs["page"] == PAGE_LIMIT
    assert mock_query_postgres.call_args.kwargs["per_page"] == PER_PAGE_LIMIT


# ExtendedHTTPException is rendered by the handler registered in main.py, which this bare app does not have, so
# these assert on the raised exception (as test_settings_transfer.py does) rather than on the rendered response.
@pytest.mark.anyio
# 20,000 levels make json.loads raise RecursionError on Python 3.12 while the URL-encoded query stays under httpx's
# 64 KiB limit.
@pytest.mark.parametrize("raw_filters", ["{bad", "[1]", "[" * 20_000], ids=["malformed", "not-object", "deep-nesting"])
async def test_bad_filters_return_422(authenticated, mock_query_postgres, raw_filters):
    with pytest.raises(ExtendedHTTPException) as excinfo:
        await _get({"filters": raw_filters})

    assert excinfo.value.code == status.HTTP_422_UNPROCESSABLE_CONTENT
    mock_query_postgres.assert_not_called()
