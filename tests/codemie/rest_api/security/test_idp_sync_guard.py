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

"""Identity rule of the IdP-sync API: AC1-AC4 (EPMCDME-16895).

The rule runs inside the real ``authenticate`` (next to ``enforce_provisioning_scope``); only the user
provider is replaced, so a route that calls ``authenticate`` in its handler is covered too.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.routing import Route

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security import authentication, idp_sync_access, idp_sync_guard
from codemie.rest_api.security.authentication import authenticate as real_authenticate
from codemie.rest_api.security.user import User

SYNC_ID = "idp-sync-bot"


def _user(user_id: str, user_type: str = "regular", is_admin: bool = False, username: str | None = None) -> User:
    return User(
        id=user_id,
        username=username or user_id,
        name=user_id,
        email=f"{user_id}@example.com",
        user_type=user_type,
        is_admin=is_admin,
        project_names=[],
        admin_project_names=[],
        knowledge_bases=[],
    )


CALLERS = {
    # the account is matched by user name only: any id and any stored user type work
    "sync": _user("kc-sub-1111", username=SYNC_ID),
    "sync-sa": _user("kc-sub-3333", "service_account", username=SYNC_ID),
    "admin": _user("admin-1", is_admin=True),
    "other-sa": _user("other-bot", "service_account"),
    "teams": _user("codemie-teams-bot"),
    # same id as the setting, different user name: the id grants nothing
    "id-only": _user(SYNC_ID, username="someone-else"),
}


class _Provider:
    async def authenticate_and_load_user(self, request: Request, _idp) -> User:
        return CALLERS[request.headers.get("X-Caller", "admin")]


def _build_app(root_path: str = "") -> FastAPI:
    app = FastAPI(root_path=root_path)

    @app.exception_handler(ExtendedHTTPException)
    async def _handler(_request: Request, exc: ExtendedHTTPException):
        return JSONResponse(status_code=exc.code, content={"error": exc.message})

    @app.middleware("http")
    async def _request_uuid(request: Request, call_next):
        request.state.uuid = "test-request"  # authenticate reads it for logging
        return await call_next(request)

    secured = APIRouter(dependencies=[Depends(real_authenticate)])

    @secured.get("/v1/assistants")
    def assistants():
        return {"ok": True}

    sync = APIRouter(prefix="/v1/idp-sync", dependencies=[Depends(idp_sync_guard.require_idp_sync_account)])

    @sync.get("/users")
    def sync_users():
        return {"ok": True}

    @app.get("/v1/health")
    def health():
        return {"ok": True}

    @app.get("/v1/in-handler")
    async def in_handler(request: Request):
        # like the a2a agent card route: authenticate is called inside the handler
        await real_authenticate(request, None, None)
        return {"ok": True}

    app.include_router(secured)
    app.include_router(sync)
    return app


@pytest.fixture(autouse=True)
def _real_authenticate_with_stub_provider():
    with (
        patch.object(authentication, "get_user_provider", return_value=_Provider()),
        patch.object(authentication.IdpFactory, "create", return_value=MagicMock()),
        patch.object(authentication, "enforce_provisioning_scope"),
    ):
        yield


@pytest.fixture
def configured():
    with patch.object(config, "IDP_SYNC_SERVICE_ACCOUNT_USERNAME", SYNC_ID):
        yield


def _get(app: FastAPI, path: str, caller: str) -> int:
    return TestClient(app).get(path, headers={"X-Caller": caller}).status_code


@pytest.mark.parametrize("caller", ["sync", "sync-sa"])
def test_sync_account_refused_outside_its_api(configured, caller):
    assert _get(_build_app(), "/v1/assistants", caller) == 403


def test_sync_account_refused_on_a_route_that_authenticates_in_the_handler(configured):
    assert _get(_build_app(), "/v1/in-handler", "sync") == 403


@pytest.mark.parametrize("caller", ["sync", "sync-sa"])
def test_sync_account_allowed_on_its_api_whatever_the_stored_user_type(configured, caller):
    assert _get(_build_app(), "/v1/idp-sync/users", caller) == 200


@pytest.mark.parametrize("caller", ["admin", "other-sa", "teams", "id-only"])
def test_others_refused_on_management_routes(configured, caller):
    assert _get(_build_app(), "/v1/idp-sync/users", caller) == 403


@pytest.mark.parametrize("caller", ["admin", "other-sa", "teams", "id-only"])
def test_others_unaffected_elsewhere(configured, caller):
    assert _get(_build_app(), "/v1/assistants", caller) == 200
    assert _get(_build_app(), "/v1/in-handler", caller) == 200


@pytest.mark.parametrize("caller", ["sync", "sync-sa", "admin", "other-sa", "teams", "id-only"])
def test_empty_setting_closes_management_api_for_all(caller):
    with patch.object(config, "IDP_SYNC_SERVICE_ACCOUNT_USERNAME", ""):
        assert _get(_build_app(), "/v1/idp-sync/users", caller) == 403


def test_empty_setting_does_not_restrict_anyone_elsewhere():
    with patch.object(config, "IDP_SYNC_SERVICE_ACCOUNT_USERNAME", ""):
        assert _get(_build_app(), "/v1/assistants", "sync") == 200


def test_public_route_stays_public_without_credentials(configured):
    with patch.object(authentication, "get_user_provider", side_effect=AssertionError("must not authenticate")):
        assert TestClient(_build_app()).get("/v1/health").status_code == 200


def test_root_path_neither_locks_out_nor_admits(configured):
    client = TestClient(_build_app("/api"))
    assert client.get("/v1/idp-sync/users", headers={"X-Caller": "sync"}).status_code == 200
    assert client.get("/v1/assistants", headers={"X-Caller": "sync"}).status_code == 403
    assert client.get("/v1/idp-sync/users", headers={"X-Caller": "admin"}).status_code == 403


def _request(route) -> SimpleNamespace:
    return SimpleNamespace(scope={"route": route}, method="GET")


async def _plain(_request):  # pragma: no cover - never called
    return None


def test_unknown_route_fails_closed_for_the_sync_account(configured):
    with pytest.raises(ExtendedHTTPException) as exc:
        idp_sync_access.enforce_idp_sync_scope(CALLERS["sync"], _request(None))
    assert exc.value.code == 403


def test_mounted_route_outside_the_api_fails_closed_for_the_sync_account(configured):
    with pytest.raises(ExtendedHTTPException):
        idp_sync_access.enforce_idp_sync_scope(CALLERS["sync"], _request(Route("/mounted/thing", _plain)))


def test_scope_rule_lets_other_callers_through(configured):
    idp_sync_access.enforce_idp_sync_scope(CALLERS["admin"], _request(Route("/mounted/thing", _plain)))


def test_internal_signed_calls_are_not_subject_to_the_rule(configured):
    # the bind-key branch of authenticate never reaches the rule: it is the loopback of trigger actors
    request = MagicMock()
    request.headers = {"X-Bind-Nonce": "n", "X-Bind-Timestamp": "1"}
    with (
        patch.object(authentication, "_verify_internal_request"),
        patch.object(authentication, "LocalIdp") as local_idp,
        patch.object(authentication, "enforce_idp_sync_scope") as rule,
    ):
        local_idp.return_value.authenticate = MagicMock(side_effect=lambda *a, **k: _async(CALLERS["sync"]))
        import asyncio

        asyncio.run(real_authenticate(request, "kc-sub-1111", "key"))
    rule.assert_not_called()


async def _async(value):
    return value
