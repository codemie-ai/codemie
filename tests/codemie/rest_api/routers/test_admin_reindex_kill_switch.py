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

"""`POST /v1/admin/marketplace/reindex` predates marketplace management (the
moderation/review feature) and is not one of its routes: it is the UI
data-sources page's reindex action for the marketplace_assistants platform
datasource.

A prior change gated it on `MARKETPLACE_MANAGEMENT_ENABLED` so the moderation
feature's kill switch would also cover it, but that flag defaults off, which
404s reindexing for every environment that has not opted into moderation --
the regression MR !4070 review comment #14 flagged. This route must keep
working regardless of that flag; the moderation feature's own routes carry
their own kill switch independently.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request, status
from fastapi.testclient import TestClient

from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User


@pytest.fixture(scope="module")
def app() -> FastAPI:
    """The production ASGI app, with the production router registration."""
    from codemie.rest_api import main

    return main.app


@pytest.fixture
def admin_client(app: FastAPI):
    """A TestClient authenticated as an admin.

    `main.app` is a process-wide singleton shared with every other module in
    the session, so the override is restored rather than cleared.
    """

    def _admin() -> User:
        user = User(
            id="admin-killswitch",
            username="admin",
            name="Admin",
            email="admin@codemie.ai",
            project_names=["demo"],
        )
        # User.resolve_is_admin force-promotes everyone when ENV=local, which
        # pytest.ini sets; pin the flag so the fixture means what it says.
        user.is_admin = True
        user.is_maintainer = False
        return user

    async def _fake_authenticate(request: Request) -> User:
        # admin_access_only reads request.state.user, so the override has to set
        # it — otherwise the test exercises an AttributeError, not the guard.
        user = _admin()
        request.state.user = user
        return user

    saved = dict(app.dependency_overrides)
    app.dependency_overrides[authenticate] = _fake_authenticate
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


def test_reindex_works_when_marketplace_management_is_disabled(admin_client) -> None:
    """The deployment-level default (flag off) must not break this route."""
    with (
        patch(
            "codemie.configs.marketplace_management.is_marketplace_management_enabled",
            return_value=False,
        ),
        patch("codemie.service.marketplace_management.marketplace_index_sync.measure_index_divergence"),
    ):
        response = admin_client.post("/v1/admin/marketplace/reindex")

    assert response.status_code == status.HTTP_202_ACCEPTED, response.text


def test_reindex_still_works_when_the_feature_is_enabled(admin_client) -> None:
    """The flag must not matter in the other direction either."""
    with (
        patch(
            "codemie.configs.marketplace_management.is_marketplace_management_enabled",
            return_value=True,
        ),
        patch("codemie.service.marketplace_management.marketplace_index_sync.measure_index_divergence"),
    ):
        response = admin_client.post("/v1/admin/marketplace/reindex")

    assert response.status_code == status.HTTP_202_ACCEPTED, response.text
