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

"""Router of the IdP-sync API: access rule and one-transaction behaviour (EPMCDME-16895)."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.idp_sync import IdpUserPage, IdpUserResponse
from codemie.rest_api.routers import idp_sync as router_module
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

SYNC_ID = "idp-sync-bot"
USER_ID = "8f14e45f-ceea-467a-9575-1c5e1f2b3d4a"
_BODY = {"id": USER_ID, "email": "a@example.com", "username": "a", "name": "A", "is_admin": False}
_RESPONSE = IdpUserResponse(
    id=USER_ID, is_active=True, email="a@example.com", username="a", name="A", is_admin=False, memberships=[]
)


def _user(user_id: str, user_type: str = "service_account") -> User:
    return User(
        id=user_id,
        username=user_id,
        name=user_id,
        email=f"{user_id}@example.com",
        user_type=user_type,
        project_names=[],
        admin_project_names=[],
        knowledge_bases=[],
    )


CALLERS = {"sync": _user(SYNC_ID), "admin": _user("admin-1", "regular"), "other": _user("other-bot")}


@pytest.fixture()
def session():
    return MagicMock(name="session")


@pytest.fixture()
def service(session):
    svc = MagicMock(name="idp_sync_service")
    svc.upsert_user.return_value = (_RESPONSE, False)
    svc.get_user.return_value = _RESPONSE
    svc.assign.return_value = (_RESPONSE, None)
    svc.unassign.return_value = None
    svc.list_users.return_value = IdpUserPage(items=[_RESPONSE], total=1, page=0, per_page=100)

    @contextmanager
    def _get_session():
        yield session

    with (
        patch.object(router_module, "idp_sync_service", svc),
        patch.object(router_module, "get_session", _get_session),
        patch.object(router_module, "invalidate_user_from_cache") as invalidate,
        patch.object(router_module, "budget_service") as budgets,
        patch.object(router_module, "project_assignment_service") as shares,
    ):
        svc.invalidate = invalidate
        svc.budgets = budgets
        svc.shares = shares
        yield svc


@pytest.fixture()
def client(service):
    app = FastAPI()

    @app.exception_handler(ExtendedHTTPException)
    async def _handler(_request: Request, exc: ExtendedHTTPException):
        return JSONResponse(status_code=exc.code, content={"error": exc.message})

    async def _fake_authenticate(request: Request) -> User:
        return CALLERS[request.headers.get("X-Caller", "sync")]

    app.dependency_overrides[authenticate] = _fake_authenticate
    app.include_router(router_module.router)
    with patch.object(config, "IDP_SYNC_SERVICE_ACCOUNT_USERNAME", SYNC_ID):
        yield TestClient(app)


_PROJECT = f"/v1/idp-sync/users/{USER_ID}/projects/team-a"
_ROUTES = [
    ("PUT", f"/v1/idp-sync/users/{USER_ID}", {"json": _BODY}),
    ("GET", f"/v1/idp-sync/users/{USER_ID}", {}),
    ("GET", "/v1/idp-sync/users?page=0&per_page=100", {}),
    ("PUT", _PROJECT, {}),
    ("DELETE", _PROJECT, {}),
    ("POST", f"/v1/idp-sync/users/{USER_ID}/deactivate", {}),
]


@pytest.mark.parametrize("idp_managed", [True, False])
@pytest.mark.parametrize(("method", "path", "kwargs"), _ROUTES)
def test_configured_account_succeeds_with_switch_on_and_off(client, idp_managed, method, path, kwargs):
    with patch.object(config, "IDP_MANAGED_ENABLED", idp_managed):
        response = client.request(method, path, headers={"X-Caller": "sync"}, **kwargs)

    assert response.status_code in (200, 204)


@pytest.mark.parametrize("idp_managed", [True, False])
@pytest.mark.parametrize("caller", ["admin", "other"])
@pytest.mark.parametrize(("method", "path", "kwargs"), _ROUTES)
def test_other_callers_get_403(client, service, idp_managed, caller, method, path, kwargs):
    with patch.object(config, "IDP_MANAGED_ENABLED", idp_managed):
        response = client.request(method, path, headers={"X-Caller": caller}, **kwargs)

    assert response.status_code == 403
    assert service.mock_calls == []


@pytest.mark.parametrize(("method", "path", "kwargs"), _ROUTES)
def test_empty_setting_closes_every_route(client, service, method, path, kwargs):
    with patch.object(config, "IDP_SYNC_SERVICE_ACCOUNT_USERNAME", ""):
        response = client.request(method, path, headers={"X-Caller": "sync"}, **kwargs)

    assert response.status_code == 403


def test_upsert_commits_once_and_invalidates_cache(client, service, session):
    client.put(f"/v1/idp-sync/users/{USER_ID}", json=_BODY)

    session.commit.assert_called_once()
    session.rollback.assert_not_called()
    service.invalidate.assert_called_once_with(USER_ID)


def test_error_rolls_back_without_commit(client, service, session):
    service.upsert_user.side_effect = ExtendedHTTPException(code=409, message="conflict")

    response = client.put(f"/v1/idp-sync/users/{USER_ID}", json=_BODY)

    assert response.status_code == 409
    session.commit.assert_not_called()
    session.rollback.assert_called_once()
    service.invalidate.assert_not_called()


def test_upsert_with_mismatching_path_and_body_id_is_400(client, service):
    response = client.put("/v1/idp-sync/users/someone-else", json=_BODY)

    assert response.status_code == 400
    service.upsert_user.assert_not_called()


def test_assign_passes_project_and_flag(client, service):
    client.put(f"{_PROJECT}?is_project_admin=true")

    request = service.assign.call_args.args[3]
    assert (request.project_name, request.is_project_admin) == ("team-a", True)
    assert service.assign.call_args.args[1].id == SYNC_ID


@pytest.mark.parametrize("per_page", [0, 101])
def test_per_page_is_limited_to_100(client, per_page):
    assert client.get(f"/v1/idp-sync/users?per_page={per_page}").status_code == 422


@pytest.mark.parametrize("project", ["%20%20", "x" * 256, "%20team-a", "team-a%20", "%20team-a%20"])
def test_bad_project_name_is_422_not_500(client, service, project):
    response = client.put(f"/v1/idp-sync/users/{USER_ID}/projects/{project}", headers={"X-Caller": "sync"})

    assert response.status_code == 422
    service.assign.assert_not_called()


def test_over_long_user_id_path_is_422(client, service):
    response = client.get(f"/v1/idp-sync/users/{'x' * 256}", headers={"X-Caller": "sync"})

    assert response.status_code == 422


def test_deactivate_commits_then_runs_post_commit_effects(client, service, session):
    service.deactivate.return_value = True

    response = client.post(f"/v1/idp-sync/users/{USER_ID}/deactivate", headers={"X-Caller": "sync"})

    assert response.status_code == 204
    session.commit.assert_called_once()
    service.after_deactivation_commit.assert_called_once_with(USER_ID, SYNC_ID)


def test_deactivate_noop_has_no_post_commit_effects(client, service, session):
    service.deactivate.return_value = False

    client.post(f"/v1/idp-sync/users/{USER_ID}/deactivate", headers={"X-Caller": "sync"})

    service.after_deactivation_commit.assert_not_called()


def test_response_body_carries_picture_and_is_maintainer(client, service):
    service.get_user.return_value = _RESPONSE.model_copy(
        update={"picture": "https://e.com/a.png", "is_maintainer": True}
    )

    body = client.get(f"/v1/idp-sync/users/{USER_ID}").json()

    assert body["picture"] == "https://e.com/a.png"
    assert body["is_maintainer"] is True


def test_upsert_with_maintainer_but_not_admin_is_422_and_never_reaches_the_service(client, service):
    response = client.put(f"/v1/idp-sync/users/{USER_ID}", json={**_BODY, "is_admin": False, "is_maintainer": True})

    assert response.status_code == 422
    service.upsert_user.assert_not_called()


def test_upsert_passes_the_role_flags_to_the_service(client, service):
    response = client.put(
        f"/v1/idp-sync/users/{USER_ID}", json={**_BODY, "is_admin": True, "is_maintainer": True, "is_auditor": True}
    )

    assert response.status_code == 200
    request = service.upsert_user.call_args.args[2]
    assert (request.is_admin, request.is_maintainer, request.is_auditor) == (True, True, True)


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
@pytest.mark.parametrize("project", ["%20team-a", "team-a%20"])
def test_project_name_with_surrounding_spaces_is_422_on_assign_and_unassign(client, service, method, project):
    response = client.request(method, f"/v1/idp-sync/users/{USER_ID}/projects/{project}")

    assert response.status_code == 422
    service.assign.assert_not_called()
    service.unassign.assert_not_called()


def test_a_project_name_with_inner_spaces_is_accepted(client, service):
    assert client.put(f"/v1/idp-sync/users/{USER_ID}/projects/team%20a").status_code == 200


def test_upsert_schedules_the_default_budget_only_for_a_created_user(client, service):
    service.upsert_user.return_value = (_RESPONSE, True)
    client.put(f"/v1/idp-sync/users/{USER_ID}", json=_BODY)
    service.budgets.schedule_default_budget_provisioning.assert_called_once_with(USER_ID, "a")

    service.budgets.reset_mock()
    service.upsert_user.return_value = (_RESPONSE, False)
    client.put(f"/v1/idp-sync/users/{USER_ID}", json=_BODY)
    service.budgets.schedule_default_budget_provisioning.assert_not_called()


def test_upsert_passes_and_returns_the_user_type(client, service):
    service.upsert_user.return_value = (_RESPONSE.model_copy(update={"user_type": "external"}), False)

    response = client.put(f"/v1/idp-sync/users/{USER_ID}", json={**_BODY, "user_type": "external"})

    assert response.json()["user_type"] == "external"
    assert service.upsert_user.call_args.args[2].user_type == "external"


def test_upsert_with_service_account_type_is_422(client, service):
    response = client.put(f"/v1/idp-sync/users/{USER_ID}", json={**_BODY, "user_type": "service_account"})

    assert response.status_code == 422
    service.upsert_user.assert_not_called()


def test_assign_refreshes_shares_after_commit_only_when_the_member_set_changed(client, service, session):
    service.assign.return_value = (_RESPONSE, "Team-A")
    client.put(_PROJECT)
    service.shares.refresh_member_shares.assert_called_once_with("Team-A")
    session.commit.assert_called_once()

    service.shares.reset_mock()
    service.assign.return_value = (_RESPONSE, None)
    client.put(_PROJECT)
    service.shares.refresh_member_shares.assert_not_called()


def test_unassign_refreshes_shares_only_when_the_user_was_removed(client, service):
    service.unassign.return_value = "team-a"
    client.delete(_PROJECT)
    service.shares.refresh_member_shares.assert_called_once_with("team-a")

    service.shares.reset_mock()
    service.unassign.return_value = None
    client.delete(_PROJECT)
    service.shares.refresh_member_shares.assert_not_called()
