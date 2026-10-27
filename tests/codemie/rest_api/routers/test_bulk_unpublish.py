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

"""Tests for POST /entities/{kind}/bulk-unpublish endpoint."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers import marketplace_management as mm
from codemie.rest_api.security.user import User

_MOD = "codemie.rest_api.routers.marketplace_management"


@pytest.fixture(scope="module")
def app():
    test_app = FastAPI()
    test_app.include_router(mm.router)
    admin = User(id="admin-1", username="admin", name="Admin User")
    test_app.dependency_overrides[mm.authenticate] = lambda: admin
    test_app.dependency_overrides[mm.admin_access_only] = lambda: None
    return test_app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


class TestBulkUnpublish:
    def test_bulk_unpublish_empty_entity_ids_422(self, client: TestClient):
        """POST with empty entity_ids list should return 422 (min_length=1)."""
        response = client.post(
            "/v1/marketplace-management/entities/assistants/bulk-unpublish",
            json={"entity_ids": []},
        )
        assert response.status_code == 422

    def test_bulk_unpublish_too_many_entity_ids_422(self, client: TestClient):
        """POST with 501 entity_ids should return 422 (max_length=500)."""
        ids = [f"id-{i}" for i in range(501)]
        response = client.post(
            "/v1/marketplace-management/entities/assistants/bulk-unpublish",
            json={"entity_ids": ids},
        )
        assert response.status_code == 422

    @patch(f"{_MOD}._queue_service")
    def test_bulk_unpublish_success_200(self, mock_qs, client: TestClient):
        """POST with valid ids should return 200 with unpublished count."""
        from codemie.service.marketplace_management.queue_service import DecisionOutcome

        mock_qs.withdraw.return_value = DecisionOutcome(
            result={"ok": True, "entity_kind": "ASSISTANT", "entity_id": "x", "status": "WITHDRAWN"},
            replayed=False,
        )
        ids = ["asst-1", "asst-2", "asst-3"]

        response = client.post(
            "/v1/marketplace-management/entities/assistants/bulk-unpublish",
            json={"entity_ids": ids},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["unpublished"] == len(ids)
        assert mock_qs.withdraw.call_count == len(ids)

    @patch(f"{_MOD}._queue_service")
    def test_bulk_unpublish_entity_not_found_returns_failure(self, mock_qs, client: TestClient):
        """POST where one entity raises ValueError should return 200 with a per-entity failure."""
        mock_qs.withdraw.side_effect = ValueError("not found")

        response = client.post(
            "/v1/marketplace-management/entities/assistants/bulk-unpublish",
            json={"entity_ids": ["missing-id"]},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["unpublished"] == 0
        assert len(body["failed"]) == 1
        assert body["failed"][0]["entity_id"] == "missing-id"
        assert "not found" in body["failed"][0]["reason"]

    def test_bulk_unpublish_non_assistant_kind_501(self, client: TestClient):
        """POST to /entities/workflows/bulk-unpublish should return 501."""
        response = client.post(
            "/v1/marketplace-management/entities/workflows/bulk-unpublish",
            json={"entity_ids": ["wf-1"]},
        )
        assert response.status_code == 501

    def test_bulk_unpublish_requires_admin(self):
        """Non-admin user must receive 403 — admin_access_only is NOT overridden."""
        non_admin = User(id="user-1", username="regular", name="Regular User")
        # ENV=local sets is_admin=True for all users; force it off so the gate fires.
        non_admin.is_admin = False
        non_admin.is_maintainer = False
        non_admin_app = FastAPI()
        non_admin_app.include_router(mm.router)

        async def _fake_authenticate(request: Request) -> User:
            request.state.user = non_admin
            return non_admin

        @non_admin_app.exception_handler(ExtendedHTTPException)
        async def _ext_handler(request: Request, exc: ExtendedHTTPException):
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=exc.code, content={"message": exc.message})

        non_admin_app.dependency_overrides[mm.authenticate] = _fake_authenticate

        with TestClient(non_admin_app) as c:
            response = c.post(
                "/v1/marketplace-management/entities/assistants/bulk-unpublish",
                json={"entity_ids": ["asst-1"]},
            )

        assert response.status_code == 403
