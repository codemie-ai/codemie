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

"""Repository registration routes (EPMCDME-16440)."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.projects import router as projects_router
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

MOD = "codemie.rest_api.routers.projects.repositories"
URL = "/v1/projects/proj/repositories"
SECRET = "s3cret"


def make_user(**kwargs) -> User:
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id="u1", email="u@example.com", **kwargs)


ALLOWED = {
    "admin": {"is_admin": True},
    "maintainer": {"is_maintainer": True},
    "project_admin": {"admin_project_names": ["proj"], "project_names": ["proj"]},
}
DENIED = {
    "other_project_admin": {"admin_project_names": ["other"], "project_names": ["proj"]},
    "auditor": {"is_auditor": True, "project_names": ["proj"]},
    "member": {"project_names": ["proj"]},
    "outsider": {},
}


def client(user: User) -> TestClient:
    app = FastAPI()
    app.include_router(projects_router)

    def _authenticate(request: Request):
        request.state.user = user
        return user

    app.dependency_overrides[authenticate] = _authenticate

    @app.exception_handler(ExtendedHTTPException)
    async def _handler(request, exc):
        return JSONResponse(status_code=exc.code, content={"message": exc.message})

    return TestClient(app)


@pytest.fixture
def deps():
    @contextmanager
    def fake_session():
        yield MagicMock()

    project = SimpleNamespace(name="proj", project_type="shared", deleted_at=None)
    with (
        patch(f"{MOD}.get_session", fake_session),
        patch(f"{MOD}.application_repository") as apps,
        patch(f"{MOD}.RepositoryRegistrationRepository") as regs,
        patch("codemie.rest_api.routers.projects._common.config") as cfg,
    ):
        cfg.ENABLE_USER_MANAGEMENT = True
        apps.get_by_name.return_value = project
        regs.register.return_value = SimpleNamespace(normalized_remote="github.com/Org/Repo", project_name="proj")
        regs.unregister.return_value = "github.com/Org/Repo"
        regs.list_for_project.return_value = [
            SimpleNamespace(normalized_remote="github.com/Org/Repo", project_name="proj")
        ]
        yield SimpleNamespace(apps=apps, regs=regs, project=project)


@pytest.mark.parametrize("label", ALLOWED)
def test_allowed_roles_put_get_delete(label, deps):
    c = client(make_user(**ALLOWED[label]))
    body = {"remote": "git@github.com:Org/Repo.git"}
    assert c.put(URL, json=body).status_code == 200
    got = c.get(URL)
    assert got.status_code == 200
    assert got.json() == [{"key": "github.com/Org/Repo", "project_name": "proj"}]
    assert c.request("DELETE", URL, json=body).status_code == 200


@pytest.mark.parametrize("label", DENIED)
def test_denied_roles_get_404_and_nothing_written(label, deps):
    c = client(make_user(**DENIED[label]))
    body = {"remote": "git@github.com:Org/Repo.git"}
    assert c.put(URL, json=body).status_code == 404
    assert c.get(URL).status_code == 404
    assert c.request("DELETE", URL, json=body).status_code == 404
    deps.regs.register.assert_not_called()
    deps.regs.unregister.assert_not_called()


def test_missing_or_deleted_project_is_404(deps):
    deps.apps.get_by_name.return_value = None
    assert client(make_user(is_admin=True)).put(URL, json={"remote": "a/b"}).status_code == 404


def test_personal_project_rejected_400(deps):
    deps.project.project_type = "personal"
    c = client(make_user(is_admin=True))
    assert c.put(URL, json={"remote": "git@github.com:Org/Repo"}).status_code == 400
    deps.regs.register.assert_not_called()


def test_conflict_propagates_409(deps):
    deps.regs.register.side_effect = ExtendedHTTPException(code=409, message="conflict")
    assert client(make_user(is_admin=True)).put(URL, json={"remote": "a:b/c"}).status_code == 409


def test_delete_unknown_is_404(deps):
    deps.regs.unregister.return_value = None
    assert client(make_user(is_admin=True)).request("DELETE", URL, json={"remote": "a:b/c"}).status_code == 404


def test_credentials_never_logged_or_returned(deps, caplog):
    deps.regs.register.return_value = SimpleNamespace(normalized_remote="host/a/b", project_name="proj")
    with caplog.at_level("DEBUG"):
        r = client(make_user(is_admin=True)).put(URL, json={"remote": f"https://user:{SECRET}@host/a/b.git"})
    assert r.status_code == 200
    assert SECRET not in r.text
    assert SECRET not in caplog.text


A_RAW, A_KEY = "https://host.example:8443/Org/Repo", "host.example:8443/Org/Repo"
B_RAW, B_KEY = "git@host.example:8443/Org/Repo", "host.example/8443/Org/Repo"


@pytest.fixture
def real_db():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session

    from tests.codemie.service.project import registration_db

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    saved = registration_db.create(engine)

    @contextmanager
    def real_session():
        with Session(engine) as s:
            yield s

    with Session(engine) as s:
        registration_db.add_project(s, "proj")
    project = SimpleNamespace(name="proj", project_type="shared", deleted_at=None)
    try:
        with (
            patch(f"{MOD}.get_session", real_session),
            patch(f"{MOD}.application_repository") as apps,
            patch("codemie.rest_api.routers.projects._common.config") as cfg,
        ):
            cfg.ENABLE_USER_MANAGEMENT = True
            apps.get_by_name.return_value = project
            yield client(make_user(is_admin=True))
    finally:
        registration_db.restore(saved)


@pytest.mark.parametrize(
    "remote, key",
    [
        ("https://host.example/Org/Repo.git", "host.example/Org/Repo"),
        ("https://host.example:8443/Org/Repo.git", A_KEY),
        ("https://[2001:db8::1]:8443/Org/Repo", "[2001:db8::1]:8443/Org/Repo"),
        (B_RAW, B_KEY),
    ],
)
def test_put_get_delete_round_trip_uses_the_get_item_as_the_delete_body(real_db, remote, key):
    c = real_db
    put = c.put(URL, json={"remote": remote})
    assert put.status_code == 200 and put.json() == {"key": key, "project_name": "proj"}
    (item,) = c.get(URL).json()
    assert item == put.json()
    assert c.put(URL, json=item).json() == item  # PUT of the item again: the same row
    deleted = c.request("DELETE", URL, json=item)  # the GET item itself is the body
    assert deleted.status_code == 200 and deleted.json() == item
    assert c.get(URL).json() == []
    assert c.request("DELETE", URL, json=item).status_code == 404


@pytest.mark.parametrize("target", [A_KEY, B_KEY])
def test_two_registrations_in_one_project_are_deleted_one_at_a_time(real_db, target):
    c = real_db
    for raw in (A_RAW, B_RAW):
        assert c.put(URL, json={"remote": raw}).status_code == 200
    keys = {i["key"] for i in c.get(URL).json()}
    assert keys == {A_KEY, B_KEY}
    assert c.request("DELETE", URL, json={"key": target}).json()["key"] == target
    assert {i["key"] for i in c.get(URL).json()} == keys - {target}


@pytest.mark.parametrize(
    "body",
    [{}, {"remote": "a:b/c", "key": "a/b"}, {"key": "https://host.example/a/b"}, {"remote": "nonsense"}, {"key": ""}],
)
def test_a_body_naming_no_single_valid_repository_is_400(real_db, body):
    assert real_db.request("DELETE", URL, json=body).status_code == 400
    assert real_db.put(URL, json=body).status_code == 400
