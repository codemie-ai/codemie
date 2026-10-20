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

"""Project API exposure and write guard for cli_analytics_enabled (EPMCDME-16025)."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.project import (
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectDetailResponse,
    ProjectListItem,
    ProjectUpdateRequest,
)
from codemie.core.models import Application
from codemie.rest_api.routers.index import router as index_router
from codemie.rest_api.routers.projects import router as projects_router
from codemie.rest_api.routers.projects.crud import create_project, get_project_detail, list_projects
from codemie.rest_api.routers.projects.management import update_project
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

KEY = "cli_analytics_enabled"

# label -> (user kwargs, may see the value of project "proj")
ROLES = {
    "admin": ({"is_admin": True}, True),
    "maintainer": ({"is_maintainer": True}, True),
    "project_admin": ({"admin_project_names": ["proj"], "project_names": ["proj"]}, True),
    "other_project_admin": ({"admin_project_names": ["other"], "project_names": ["proj"]}, False),
    "auditor": ({"is_auditor": True, "project_names": ["proj"]}, False),
    "member": ({"project_names": ["proj"]}, False),
    "outsider": ({}, False),
}
WRITERS = {"admin", "maintainer"}


def make_user(label: str) -> User:
    kwargs, _ = ROLES[label]
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id=f"user-{label}", email=f"{label}@example.com", **kwargs)


def project_row(**extra) -> SimpleNamespace:
    return SimpleNamespace(
        name="proj",
        display_name=None,
        description="d",
        project_type="shared",
        created_by="owner",
        date=datetime(2026, 1, 1, tzinfo=UTC),
        cost_center_id=None,
        chargeback_enabled=False,
        chargeback_attribution="project",
        cli_analytics_enabled=True,
        allowed_models=None,
        **extra,
    )


@pytest.fixture(autouse=True)
def _user_management_on():
    with (
        patch("codemie.rest_api.routers.projects._common.config") as cfg,
        patch("codemie.rest_api.routers.projects.crud.SettingsService") as crud_settings,
        patch("codemie.rest_api.routers.projects.management.SettingsService") as mgmt_settings,
        patch("codemie.rest_api.routers.projects.crud._resolve_cost_center_name", return_value=None),
        patch("codemie.rest_api.routers.projects.management._resolve_cost_center_name", return_value=None),
    ):
        cfg.ENABLE_USER_MANAGEMENT = True
        crud_settings.get_enforce_member_spend_limits.return_value = False
        mgmt_settings.get_enforce_member_spend_limits.return_value = False
        yield


def dumped(model) -> dict:
    return model.model_dump(mode="json")


@pytest.mark.parametrize("label", ROLES)
def test_patch_response_exposes_value_only_to_viewers(label):
    user = make_user(label)
    can_view = ROLES[label][1]
    with patch("codemie.rest_api.routers.projects.management.project_service") as service:
        service.update_project.return_value = project_row()
        # Non-writers may PATCH other fields; the response must still follow the viewer rule.
        response = update_project(payload=ProjectUpdateRequest(description="x"), project_name="proj", user=user)

    body = dumped(response)
    assert (KEY in body) is can_view
    if can_view:
        assert body[KEY] is True


@pytest.mark.parametrize("label", ROLES)
def test_create_response_exposes_value_only_to_viewers(label):
    """Mocks the service: pins "the router applies the policy to the rights the user holds".

    The creator's real project-admin grant is covered by the real-service tests below and in
    tests/codemie/service/project/test_project_service_cli_analytics.py.
    """
    user = make_user(label)
    can_view = ROLES[label][1]
    with patch("codemie.rest_api.routers.projects.crud.project_service") as service:
        service.create_shared_project.return_value = project_row()
        response = create_project(payload=ProjectCreateRequest(name="proj"), user=user)

    assert (KEY in dumped(response)) is can_view


@pytest.mark.parametrize("label", ROLES)
@pytest.mark.asyncio
async def test_list_exposes_value_only_to_viewers(label):
    user = make_user(label)
    can_view = ROLES[label][1]
    row = {
        "name": "proj",
        "description": "d",
        "project_type": "shared",
        "created_by": "owner",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "user_count": 1,
        "admin_count": 1,
        KEY: True,
    }
    with patch("codemie.rest_api.routers.projects.crud._list_projects_sync", return_value=([row], 1)):
        result = await list_projects(
            search=None,
            page=0,
            per_page=20,
            include_counters=False,
            include_spending=False,
            include_budgets=False,
            has_assigned_budgets=False,
            budget_category=None,
            sort_by=None,
            sort_order="asc",
            user=user,
        )

    assert (KEY in dumped(result)["data"][0]) is can_view


@pytest.mark.parametrize("label", ROLES)
@pytest.mark.asyncio
async def test_detail_exposes_value_only_to_viewers(label):
    user = make_user(label)
    can_view = ROLES[label][1]
    detail = {
        "name": "proj",
        "description": "d",
        "project_type": "shared",
        "created_by": "owner",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "user_count": 1,
        "admin_count": 1,
        "members": [],
        KEY: True,
    }
    with patch("codemie.rest_api.routers.projects.crud._get_project_detail_sync", return_value=detail):
        result = await get_project_detail(
            request=MagicMock(method="GET", url=SimpleNamespace(path="/v1/projects/proj")),
            project_name="proj",
            include_spending=False,
            user=user,
        )

    assert (KEY in dumped(result)) is can_view


def test_defaults_never_leak_the_field():
    """A response built without the value (Pydantic default) carries no key at all."""
    created = ProjectCreateResponse(name="p", project_type="shared", created_by="u", created_at=datetime.now(UTC))
    detail = ProjectDetailResponse(name="p", project_type="shared", user_count=0, admin_count=0, members=[])
    item = ProjectListItem(name="p", project_type="shared", user_count=0, admin_count=0)

    assert all(KEY not in dumped(m) for m in (created, detail, item))


@pytest.mark.parametrize("label", sorted(set(ROLES) - WRITERS))
@pytest.mark.parametrize("value", [True, False, None])
def test_patch_with_field_is_forbidden_without_global_write(label, value):
    user = make_user(label)
    with patch("codemie.rest_api.routers.projects.management.project_service") as service:
        with pytest.raises(ExtendedHTTPException) as exc:
            update_project(
                payload=ProjectUpdateRequest(description="changed", **{KEY: value}),
                project_name="proj",
                user=user,
            )

    assert exc.value.code == 403
    service.update_project.assert_not_called()


def test_field_only_patch_by_project_admin_is_403_not_422():
    payload = ProjectUpdateRequest(**{KEY: False})  # validate_non_empty must accept it
    with patch("codemie.rest_api.routers.projects.management.project_service") as service:
        with pytest.raises(ExtendedHTTPException) as exc:
            update_project(payload=payload, project_name="proj", user=make_user("project_admin"))

    assert exc.value.code == 403
    service.update_project.assert_not_called()


def test_project_admin_can_patch_permitted_fields_without_the_field():
    with patch("codemie.rest_api.routers.projects.management.project_service") as service:
        service.update_project.return_value = project_row()
        update_project(
            payload=ProjectUpdateRequest(description="new"), project_name="proj", user=make_user("project_admin")
        )

    assert KEY not in service.update_project.call_args.kwargs


@pytest.mark.parametrize("label", sorted(WRITERS))
def test_writer_patch_forwards_value(label):
    with patch("codemie.rest_api.routers.projects.management.project_service") as service:
        service.update_project.return_value = project_row()
        update_project(payload=ProjectUpdateRequest(**{KEY: True}), project_name="proj", user=make_user(label))

    assert service.update_project.call_args.kwargs[KEY] is True


def test_explicit_null_from_writer_means_no_change():
    with patch("codemie.rest_api.routers.projects.management.project_service") as service:
        service.update_project.return_value = project_row()
        update_project(
            payload=ProjectUpdateRequest(description="x", **{KEY: None}),
            project_name="proj",
            user=make_user("admin"),
        )

    assert KEY not in service.update_project.call_args.kwargs


@pytest.mark.parametrize("label", sorted(set(ROLES) - WRITERS))
def test_create_with_field_is_forbidden_without_global_write(label):
    with patch("codemie.rest_api.routers.projects.crud.project_service") as service:
        with pytest.raises(ExtendedHTTPException) as exc:
            create_project(payload=ProjectCreateRequest(name="proj", **{KEY: True}), user=make_user(label))

    assert exc.value.code == 403
    service.create_shared_project.assert_not_called()


def test_writer_create_forwards_value():
    with patch("codemie.rest_api.routers.projects.crud.project_service") as service:
        service.create_shared_project.return_value = project_row()
        create_project(payload=ProjectCreateRequest(name="proj", **{KEY: True}), user=make_user("admin"))

    assert service.create_shared_project.call_args.kwargs[KEY] is True


def _client(user: User, router=projects_router) -> TestClient:
    from codemie.core.exceptions import ExtendedHTTPException as Exc

    app = FastAPI()
    app.include_router(router)

    def _authenticate(request: Request):
        request.state.user = user
        return user

    app.dependency_overrides[authenticate] = _authenticate

    @app.exception_handler(Exc)
    async def _handler(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.code, content={"message": exc.message})

    return TestClient(app)


@pytest.mark.parametrize(("label", "expected"), [("admin", True), ("member", False)])
def test_wire_json_of_patch_follows_viewer_rule(label, expected):
    """Through FastAPI's response_model, the key is present only for viewers."""
    with patch("codemie.rest_api.routers.projects.management.project_service") as service:
        service.update_project.return_value = project_row()
        response = _client(make_user(label)).patch("/v1/projects/proj", json={"description": "x"})

    assert response.status_code == 200
    assert (KEY in response.json()) is expected


def test_wire_patch_with_field_by_member_is_403():
    with patch("codemie.rest_api.routers.projects.management.project_service") as service:
        response = _client(make_user("member")).patch("/v1/projects/proj", json={KEY: False})

    assert response.status_code == 403
    service.update_project.assert_not_called()


def _get_legacy_application(label: str):
    code_repositories = "codemie.rest_api.routers.index.code_repositories"
    with (
        patch(
            f"{code_repositories}.Application.get_by_id",
            return_value=Application(name="proj", cli_analytics_enabled=True),
        ),
        patch(f"{code_repositories}.CodeIndexInfo.get_all", return_value=[]),
        patch(f"{code_repositories}.CodeIndexInfo.filter_by_names_or_user", return_value=[]),
        patch(f"{code_repositories}.GitRepo.get_by_app_id", return_value=[]),
    ):
        return _client(make_user(label), router=index_router).get("/v1/application/proj")


@pytest.mark.parametrize("label", ROLES)
def test_legacy_application_get_exposes_value_only_to_viewers(label):
    response = _get_legacy_application(label)

    if label == "outsider":
        assert response.status_code == 403
        return
    assert response.status_code == 200
    body = response.json()
    assert (KEY in body) is ROLES[label][1]
    if ROLES[label][1]:
        assert body[KEY] is True


def test_legacy_application_get_body_differs_only_by_the_key():
    viewer = _get_legacy_application("admin").json()
    non_viewer = _get_legacy_application("member").json()

    assert {k: v for k, v in viewer.items() if k != KEY} == non_viewer


@pytest.fixture
def real_create_service():
    with (
        patch("codemie.service.project.project_service.get_session") as get_session,
        patch("codemie.service.project.project_service.application_repository") as app_repo,
        patch("codemie.service.project.project_service.user_project_repository"),
        patch("codemie.service.project.project_service.cost_center_service"),
        patch("codemie.service.project.project_service.activity_event_repository"),
        patch("codemie.service.project.project_service.llm_service") as llm,
        patch("codemie.service.project.project_service.ProjectService._enforce_project_limit"),
        patch("codemie.service.project.project_service.ProjectService.ensure_chat_model_allowed"),
    ):
        get_session.return_value.__enter__.return_value = MagicMock()
        llm.get_allowed_chat_models.return_value = [MagicMock(deployment_name="gpt-x")]
        app_repo.get_by_name_case_insensitive.return_value = None
        app_repo.create.return_value = Application(
            id="proj", name="proj", project_type="shared", cli_analytics_enabled=True, date=datetime.now(UTC)
        )
        yield app_repo


def _ordinary_user() -> User:
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id="user-creator", email="creator@example.com", project_names=["demo"])


def test_create_by_ordinary_user_shows_value_to_the_new_project_admin(real_create_service):
    response = create_project(payload=ProjectCreateRequest(name="proj"), user=_ordinary_user())

    assert dumped(response)[KEY] is True


def test_create_with_field_by_ordinary_user_is_403_before_any_mutation(real_create_service):
    user = _ordinary_user()
    with pytest.raises(ExtendedHTTPException) as exc:
        create_project(payload=ProjectCreateRequest(name="proj", **{KEY: True}), user=user)

    assert exc.value.code == 403
    real_create_service.create.assert_not_called()
    assert user.admin_project_names == []
