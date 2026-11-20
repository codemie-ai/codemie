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

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User
from codemie.rest_api.models.activity_event import (
    ActivityEventListItem,
    ActivityEventProjectMetadata,
    ProjectActivityEventFilters,
)
from codemie.rest_api.routers import project_activity_events_router as router_module
from codemie.rest_api.routers.project_activity_events_router import (
    get_project_activity_events_metadata,
    list_project_activity_events,
    router,
)

PROJECT_ID = UUID("4d35d8a6-99eb-4a39-8dc5-49ad1c9ee37c")
OTHER_PROJECT_ID = UUID("e89e1d73-3cf9-46d1-97d4-754f71ad6a2a")
UNKNOWN_PROJECT_ID = UUID("8dbf97e0-3775-4e3c-9536-b0a1b3da4df0")
PROJECT_ID_PATH = str(PROJECT_ID)
OTHER_PROJECT_ID_PATH = str(OTHER_PROJECT_ID)
UNKNOWN_PROJECT_ID_PATH = str(UNKNOWN_PROJECT_ID)


def _item(**kwargs) -> ActivityEventListItem:
    defaults = {
        "id": "evt-1",
        "domain": "user_management",
        "event_type": "user.created",
        "label": "User Created",
        "category": "Account",
        "created_at": datetime(2026, 7, 15, tzinfo=timezone.utc),
    }
    defaults.update(kwargs)
    return ActivityEventListItem(**defaults)


def _filters(**kwargs) -> ProjectActivityEventFilters:
    return ProjectActivityEventFilters(**kwargs)


class TestListProjectActivityEvents:
    @patch("codemie.rest_api.routers.project_activity_events_router.activity_event_service")
    def test_returns_paginated_response(self, mock_svc):
        mock_svc.list_project_events.return_value = ([_item()], 1, True)

        response = list_project_activity_events(project_instance_id=PROJECT_ID, filters=_filters(), _=None)

        assert response.pagination.total == 1
        assert response.data[0].id == "evt-1"
        assert response.data[0].label == "User Created"
        assert response.data[0].category == "Account"
        assert response.recording_enabled is True

    @patch("codemie.rest_api.routers.project_activity_events_router.activity_event_service")
    def test_returns_recording_disabled_from_service(self, mock_svc):
        mock_svc.list_project_events.return_value = ([], 0, False)

        response = list_project_activity_events(project_instance_id=PROJECT_ID, filters=_filters(), _=None)

        assert response.recording_enabled is False
        assert response.data == []
        assert response.pagination.total == 0

    @patch("codemie.rest_api.routers.project_activity_events_router.activity_event_service")
    def test_passes_project_instance_id_and_filters_to_service(self, mock_svc):
        mock_svc.list_project_events.return_value = ([], 0, True)
        filters = _filters(
            domain=["budget_management"],
            event_type=["budget.project_budget.reset"],
            actor_id="a-1",
            sort_dir="asc",
            limit=25,
            offset=100,
        )

        list_project_activity_events(project_instance_id=PROJECT_ID, filters=filters, _=None)

        mock_svc.list_project_events.assert_called_once_with(PROJECT_ID, filters)

    @patch("codemie.rest_api.routers.project_activity_events_router.logger")
    @patch("codemie.rest_api.routers.project_activity_events_router.activity_event_service")
    def test_wraps_unexpected_exceptions_without_leaking_details(self, mock_svc, mock_logger):
        mock_svc.list_project_events.side_effect = RuntimeError("secret database details")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            list_project_activity_events(project_instance_id=PROJECT_ID, filters=_filters(), _=None)

        assert exc_info.value.code == 500
        assert exc_info.value.details == "Please try again later."
        assert "secret database details" not in exc_info.value.details
        mock_logger.exception.assert_called_once()

    @patch("codemie.rest_api.routers.project_activity_events_router.activity_event_service")
    def test_propagates_extended_http_exceptions(self, mock_svc):
        original = ExtendedHTTPException(code=403, message="forbidden")
        mock_svc.list_project_events.side_effect = original

        with pytest.raises(ExtendedHTTPException) as exc_info:
            list_project_activity_events(project_instance_id=PROJECT_ID, filters=_filters(), _=None)

        assert exc_info.value is original

    @patch("codemie.rest_api.routers.project_activity_events_router.activity_event_service")
    def test_pagination_pages_calculated_correctly(self, mock_svc):
        items = [_item() for _ in range(10)]
        mock_svc.list_project_events.return_value = (items, 47, True)

        response = list_project_activity_events(
            project_instance_id=PROJECT_ID, filters=_filters(limit=10, offset=20), _=None
        )

        assert response.pagination.total == 47
        assert response.pagination.pages == 5  # ceil(47/10)
        assert response.pagination.page == 2  # offset(20) // limit(10)


class TestListProjectActivityEventsQueryParsing:
    @staticmethod
    def _client() -> TestClient:
        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[router_module.authenticate] = lambda: None
        test_app.dependency_overrides[router_module.project_activity_instance_access] = lambda: None
        return TestClient(test_app, raise_server_exceptions=False)

    def test_query_params_are_parsed_into_filters_model(self):
        with patch.object(router_module, "activity_event_service") as mock_svc:
            mock_svc.list_project_events.return_value = ([], 0, True)
            response = self._client().get(
                f"/v1/projects/{PROJECT_ID_PATH}/activity-events",
                params={
                    "category": ["Budget", "Members"],
                    "from": "2026-07-01T00:00:00Z",
                    "to": "2026-07-31T00:00:00Z",
                    "sort_dir": "asc",
                    "limit": 10,
                    "offset": 20,
                },
            )

        assert response.status_code == 200
        project_instance_id, filters = mock_svc.list_project_events.call_args.args
        assert project_instance_id == PROJECT_ID
        assert filters.category == ["Budget", "Members"]
        assert filters.from_dt == datetime(2026, 7, 1, tzinfo=timezone.utc)
        assert filters.to_dt == datetime(2026, 7, 31, tzinfo=timezone.utc)
        assert (filters.sort_dir, filters.limit, filters.offset) == ("asc", 10, 20)

    @pytest.mark.parametrize(
        "params",
        [
            {"domain": "no_such_domain"},
            {"category": "NoSuchCategory"},
            {"event_type": "no.such.event"},
            {"sort_dir": "sideways"},
            {"limit": 0},
            {"offset": -1},
        ],
    )
    def test_invalid_filters_are_rejected_with_422(self, params):
        with patch.object(router_module, "activity_event_service") as mock_svc:
            response = self._client().get(f"/v1/projects/{PROJECT_ID_PATH}/activity-events", params=params)

        assert response.status_code == 422
        mock_svc.list_project_events.assert_not_called()


class TestListProjectActivityEventsAccessControl:
    def test_non_project_admin_gets_403_and_no_event_data(self):
        def _deny():
            raise ExtendedHTTPException(
                code=404,
                message="Project activity history not found",
            )

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[router_module.authenticate] = lambda: None
        test_app.dependency_overrides[router_module.project_activity_instance_access] = _deny

        @test_app.exception_handler(ExtendedHTTPException)
        async def _handle(request: Request, exc: ExtendedHTTPException):
            return JSONResponse(status_code=exc.code, content={"error": exc.message})

        with patch.object(router_module, "activity_event_service") as mock_svc:
            client = TestClient(test_app, raise_server_exceptions=False)
            response = client.get(f"/v1/projects/{PROJECT_ID_PATH}/activity-events")

        assert response.status_code == 404
        body = response.json()
        assert "data" not in body
        assert "pagination" not in body
        mock_svc.list_project_events.assert_not_called()


class TestGetProjectActivityEventsMetadata:
    @patch("codemie.rest_api.routers.project_activity_events_router.activity_event_service")
    def test_returns_metadata_from_service(self, mock_svc):
        mock_svc.get_project_event_metadata.return_value = ActivityEventProjectMetadata(
            domains=["budget_management"],
            event_types=[{"event_type": "budget.created", "label": "Budget Created", "category": "Budget"}],
            categories=["Budget", "Members", "Settings", "Account"],
            recording_enabled=True,
        )

        response = get_project_activity_events_metadata(project_instance_id=PROJECT_ID, _=None)

        assert response.recording_enabled is True
        assert response.domains == ["budget_management"]
        assert response.event_types[0].event_type == "budget.created"
        assert response.categories == ["Budget", "Members", "Settings", "Account"]
        mock_svc.get_project_event_metadata.assert_called_once_with(PROJECT_ID)

    @patch("codemie.rest_api.routers.project_activity_events_router.logger")
    @patch("codemie.rest_api.routers.project_activity_events_router.activity_event_service")
    def test_hides_unexpected_exception_details(self, mock_svc, mock_logger):
        mock_svc.get_project_event_metadata.side_effect = RuntimeError("secret database details")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_project_activity_events_metadata(project_instance_id=PROJECT_ID, _=None)

        assert exc_info.value.code == 500
        assert exc_info.value.details == "Please try again later."
        assert "secret database details" not in exc_info.value.details
        mock_logger.exception.assert_called_once()

    @patch("codemie.rest_api.routers.project_activity_events_router.activity_event_service")
    def test_propagates_extended_http_exceptions(self, mock_svc):
        original = ExtendedHTTPException(code=403, message="forbidden")
        mock_svc.get_project_event_metadata.side_effect = original

        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_project_activity_events_metadata(project_instance_id=PROJECT_ID, _=None)

        assert exc_info.value is original


class TestGetProjectActivityEventsMetadataAccessControl:
    def test_non_project_admin_gets_403_and_no_event_data(self):
        def _deny():
            raise ExtendedHTTPException(
                code=404,
                message="Project activity history not found",
            )

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[router_module.authenticate] = lambda: None
        test_app.dependency_overrides[router_module.project_activity_instance_access] = _deny

        @test_app.exception_handler(ExtendedHTTPException)
        async def _handle(request: Request, exc: ExtendedHTTPException):
            return JSONResponse(status_code=exc.code, content={"error": exc.message})

        with patch.object(router_module, "activity_event_service") as mock_svc:
            client = TestClient(test_app, raise_server_exceptions=False)
            response = client.get(f"/v1/projects/{PROJECT_ID_PATH}/activity-events/metadata")

        assert response.status_code == 404
        body = response.json()
        assert "event_types" not in body
        assert "data" not in body
        mock_svc.get_project_event_metadata.assert_not_called()


class TestProjectActivityEventsRealAccessDependency:
    """Routes wired to the real access dependency (only authenticate and storage are stubbed)."""

    @staticmethod
    def _client(
        user: User,
        *,
        projects: dict[UUID, SimpleNamespace] | None = None,
        event_ids: set[UUID] = frozenset(),
        admin_of: set[str] = frozenset(),
    ) -> TestClient:
        def _auth(request: Request):
            request.state.user = user

        projects = projects or {}
        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[router_module.authenticate] = _auth

        @test_app.exception_handler(ExtendedHTTPException)
        async def _handle(request: Request, exc: ExtendedHTTPException):
            return JSONResponse(status_code=exc.code, content={"error": exc.message, "details": exc.details})

        patches = [
            patch("codemie.clients.postgres.get_session"),
            patch(
                "codemie.repository.user_project_repository.user_project_repository.is_admin",
                side_effect=lambda _session, _user_id, project: project in admin_of,
            ),
            patch(
                "codemie.repository.application_repository.application_repository.get_by_project_instance_id",
                side_effect=lambda _session, project_instance_id: projects.get(project_instance_id),
            ),
            patch(
                "codemie.service.activity.activity_repository.activity_event_repository.has_project_instance_events",
                side_effect=lambda project_instance_id, _session: project_instance_id in event_ids,
            ),
        ]
        for p in patches:
            p.start()
        test_app.state.patches = patches
        return TestClient(test_app, raise_server_exceptions=False)

    @staticmethod
    def _stop(client: TestClient) -> None:
        for p in client.app.state.patches:
            p.stop()

    @pytest.fixture
    def regular_user(self) -> User:
        return User(id="pa-1", username="pa", roles=[])

    @pytest.fixture(autouse=True)
    def _user_management_on(self):
        with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
            yield

    @pytest.mark.parametrize("suffix", ["", "/metadata"])
    def test_project_admin_of_other_project_gets_not_found_and_service_not_called(self, regular_user, suffix):
        client = self._client(
            regular_user,
            projects={OTHER_PROJECT_ID: SimpleNamespace(name="project-b", project_type="shared", deleted_at=None)},
            event_ids={OTHER_PROJECT_ID},
            admin_of={"project-a"},
        )
        try:
            with patch.object(router_module, "activity_event_service") as mock_svc:
                response = client.get(f"/v1/projects/{OTHER_PROJECT_ID_PATH}/activity-events{suffix}")
        finally:
            self._stop(client)

        assert response.status_code == 404
        mock_svc.list_project_events.assert_not_called()
        mock_svc.get_project_event_metadata.assert_not_called()

    @pytest.mark.parametrize("suffix", ["", "/metadata"])
    def test_invalid_project_instance_id_returns_422(self, regular_user, suffix):
        client = self._client(regular_user)
        try:
            with patch.object(router_module, "activity_event_service") as mock_svc:
                response = client.get(f"/v1/projects/not-a-uuid/activity-events{suffix}")
        finally:
            self._stop(client)

        assert response.status_code == 422
        mock_svc.list_project_events.assert_not_called()
        mock_svc.get_project_event_metadata.assert_not_called()

    def test_unauthorized_and_unknown_generations_are_indistinguishable(self, regular_user):
        client = self._client(
            regular_user,
            projects={PROJECT_ID: SimpleNamespace(name="existing-project", project_type="shared", deleted_at=None)},
        )
        try:
            with patch.object(router_module, "activity_event_service"):
                existing = client.get(f"/v1/projects/{PROJECT_ID_PATH}/activity-events")
                unknown = client.get(f"/v1/projects/{UNKNOWN_PROJECT_ID_PATH}/activity-events")
        finally:
            self._stop(client)

        assert existing.status_code == unknown.status_code == 404
        assert existing.json() == unknown.json()

    def test_project_admin_of_the_project_is_served(self, regular_user):
        client = self._client(
            regular_user,
            projects={PROJECT_ID: SimpleNamespace(name="acme", project_type="shared", deleted_at=None)},
            admin_of={"acme"},
        )
        try:
            with patch.object(router_module, "activity_event_service") as mock_svc:
                mock_svc.list_project_events.return_value = ([], 0, True)
                response = client.get(f"/v1/projects/{PROJECT_ID_PATH}/activity-events")
        finally:
            self._stop(client)

        assert response.status_code == 200
        mock_svc.list_project_events.assert_called_once_with(PROJECT_ID, _filters())

    def test_platform_admin_can_read_deleted_generation(self):
        admin = User(id="adm-1", username="adm", is_admin=True, roles=[])
        client = self._client(admin, event_ids={PROJECT_ID})
        try:
            with patch.object(router_module, "activity_event_service") as mock_svc:
                mock_svc.list_project_events.return_value = ([], 0, True)
                response = client.get(f"/v1/projects/{PROJECT_ID_PATH}/activity-events")
        finally:
            self._stop(client)

        assert response.status_code == 200
        mock_svc.list_project_events.assert_called_once()

    def test_former_project_admin_cannot_read_deleted_generation(self, regular_user):
        client = self._client(regular_user, event_ids={PROJECT_ID}, admin_of={"acme"})
        try:
            with patch.object(router_module, "activity_event_service") as mock_svc:
                response = client.get(f"/v1/projects/{PROJECT_ID_PATH}/activity-events")
        finally:
            self._stop(client)

        assert response.status_code == 404
        mock_svc.list_project_events.assert_not_called()

    @pytest.mark.parametrize("suffix", ["", "/metadata"])
    def test_personal_project_trail_is_not_served_even_to_platform_admin(self, suffix):
        admin = User(id="adm-1", username="adm", is_admin=True, roles=[])
        client = self._client(
            admin,
            projects={PROJECT_ID: SimpleNamespace(name="alice@example.com", project_type="personal", deleted_at=None)},
        )
        try:
            with patch.object(router_module, "activity_event_service") as mock_svc:
                response = client.get(f"/v1/projects/{PROJECT_ID_PATH}/activity-events{suffix}")
        finally:
            self._stop(client)

        assert response.status_code == 404
        mock_svc.list_project_events.assert_not_called()
        mock_svc.get_project_event_metadata.assert_not_called()


class TestProjectActivityEventsRecordingDisabled:
    """ACTIVITY_EVENTS_ENABLED=false: real service answers 200 with an empty result, no storage access."""

    @staticmethod
    def _client() -> TestClient:
        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[router_module.authenticate] = lambda: None
        test_app.dependency_overrides[router_module.project_activity_instance_access] = lambda: None
        return TestClient(test_app, raise_server_exceptions=False)

    def test_trail_returns_empty_result_with_recording_disabled(self):
        with (
            patch.object(config, "ACTIVITY_EVENTS_ENABLED", False),
            patch("codemie.service.activity.activity_event_service.PostgresClient") as postgres,
        ):
            response = self._client().get(f"/v1/projects/{PROJECT_ID_PATH}/activity-events")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0
        assert body["recording_enabled"] is False
        postgres.get_engine.assert_not_called()

    def test_metadata_returns_empty_result_with_recording_disabled(self):
        with (
            patch.object(config, "ACTIVITY_EVENTS_ENABLED", False),
            patch("codemie.service.activity.activity_event_service.PostgresClient") as postgres,
        ):
            response = self._client().get(f"/v1/projects/{PROJECT_ID_PATH}/activity-events/metadata")

        assert response.status_code == 200
        body = response.json()
        assert body["domains"] == []
        assert body["event_types"] == []
        assert body["recording_enabled"] is False
        postgres.get_engine.assert_not_called()
