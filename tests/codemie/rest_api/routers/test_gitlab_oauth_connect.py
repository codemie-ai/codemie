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

"""Per-user connect / connection-status / disconnect endpoints for GitLab OAuth.

Drives the real endpoints (built by the shared oauth_router_factory) through a FastAPI TestClient,
patching the router's seams: caller authorization (``load_authorized_setting``), the token vault
(``ToolOAuthTokenPort``), and the flow service (``_get_oauth_service``).
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import codemie.rest_api.routers.gitlab_oauth as gitlab_oauth
import codemie.rest_api.routers.oauth_router_factory as factory
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import authenticate


def _client(user_id="u1") -> TestClient:
    app = FastAPI()

    @app.exception_handler(ExtendedHTTPException)
    async def _handler(request, exc: ExtendedHTTPException):
        return JSONResponse(status_code=exc.code, content={"error": {"message": exc.message}})

    app.include_router(gitlab_oauth.router)
    app.dependency_overrides[authenticate] = lambda: SimpleNamespace(id=user_id, username=user_id)
    return TestClient(app, raise_server_exceptions=False)


def _setting(**creds):
    return SimpleNamespace(credential_values=[SimpleNamespace(key=k, value=v) for k, v in creds.items()])


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(gitlab_oauth.config, "GITLAB_OAUTH_ENABLED", True, raising=False)


@pytest.fixture
def _authorized(monkeypatch):
    """Treat the caller as authorized so a test can focus on the endpoint's own behaviour."""
    monkeypatch.setattr(factory, "load_authorized_setting", lambda *a, **k: _setting(), raising=True)


def test_connection_status_not_connected(monkeypatch, _authorized):
    monkeypatch.setattr(
        factory.ToolOAuthTokenPort,
        "get_oauth2_token_or_none",
        staticmethod(lambda *, user_id, integration_id: None),
        raising=True,
    )
    resp = _client().get("/v1/gitlab-oauth/connection", params={"setting_id": "s1"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "not_connected", "username": ""}


def test_connection_status_connected_shows_username(monkeypatch, _authorized):
    token_data = SimpleNamespace(access_token="x", provider_username="groot")
    monkeypatch.setattr(
        factory.ToolOAuthTokenPort,
        "get_oauth2_token_or_none",
        staticmethod(lambda *, user_id, integration_id: token_data),
        raising=True,
    )
    resp = _client().get("/v1/gitlab-oauth/connection", params={"setting_id": "s1"})
    assert resp.json() == {"status": "connected", "username": "groot"}


def test_connection_status_scoped_to_caller(monkeypatch, _authorized):
    captured = {}

    def fake_get(*, user_id, integration_id):
        captured["args"] = (integration_id, user_id)
        return None

    monkeypatch.setattr(factory.ToolOAuthTokenPort, "get_oauth2_token_or_none", staticmethod(fake_get), raising=True)
    _client("alice").get("/v1/gitlab-oauth/connection", params={"setting_id": "s1"})
    assert captured["args"] == ("s1", "alice")


def test_disconnect_revokes_and_deletes_caller_record(monkeypatch, _authorized):
    revoked = {}

    class _Service:
        def revoke_connection(self, user_id, integration_id):
            revoked["args"] = (integration_id, user_id)

    monkeypatch.setattr(gitlab_oauth, "_get_oauth_service", lambda: _Service(), raising=True)
    resp = _client("bob").delete("/v1/gitlab-oauth/connection", params={"setting_id": "s1"})
    assert resp.json() == {"status": "disconnected"}
    assert revoked["args"] == ("s1", "bob")


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/v1/gitlab-oauth/connection", None),
        ("delete", "/v1/gitlab-oauth/connection", None),
        ("post", "/v1/gitlab-oauth/connect", {"setting_id": "s1"}),
    ],
    ids=["connection_status", "disconnect", "connect"],
)
def test_endpoints_reject_caller_without_access(monkeypatch, method, path, body):
    """A caller who is not a member of the owning project must not reach token state."""

    def _deny(*a, **k):
        raise ExtendedHTTPException(403, "Access denied")

    monkeypatch.setattr(factory, "load_authorized_setting", _deny, raising=True)
    monkeypatch.setattr(
        factory.ToolOAuthTokenPort,
        "get_oauth2_token_or_none",
        staticmethod(lambda *, user_id, integration_id: pytest.fail("token state must not be read")),
        raising=True,
    )
    client = _client("mallory")
    kwargs = {"params": {"setting_id": "s1"}} if body is None else {"json": body}
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code == 403


class _RecordingService:
    """Captures the kwargs the router passes to the flow service's initiate_flow."""

    def __init__(self):
        self.calls = []

    def initiate_flow(self, user_id, **kwargs):
        self.calls.append({"user_id": user_id, **kwargs})
        return {"auth_url": "https://auth", "state": "st"}


@pytest.fixture
def _decrypt_ok(monkeypatch):
    """App-credential decryption returns the stored secret unchanged (strips an 'enc-' marker)."""
    enc = SimpleNamespace(decrypt=lambda v: v.replace("enc-", ""))
    monkeypatch.setattr(
        factory, "EncryptionFactory", lambda: SimpleNamespace(get_current_encryption_service=lambda: enc)
    )


def _gitlab_app_setting():
    return _setting(
        client_id="c",
        client_secret="enc-secret",
        instance_url="https://gitlab.com",
    )


def test_connect_defaults_to_persisting(monkeypatch, _decrypt_ok):
    """The Save/Connect action (test omitted) must persist the token under the integration."""
    monkeypatch.setattr(factory, "load_authorized_setting", lambda *a, **k: _gitlab_app_setting(), raising=True)
    svc = _RecordingService()
    monkeypatch.setattr(gitlab_oauth, "_get_oauth_service", lambda: svc, raising=True)

    _client().post("/v1/gitlab-oauth/connect", json={"setting_id": "s1"})

    assert svc.calls[0]["persist_token"] is True
    assert svc.calls[0]["integration_id"] == "s1"
    # EPMCDME-14587: the callback base URL is derived server-side, never forwarded from the setting.
    assert "callback_base_url" not in svc.calls[0]


def test_connect_with_test_flag_does_not_persist(monkeypatch, _decrypt_ok):
    """A Test from the edit form runs the flow without saving the token."""
    monkeypatch.setattr(factory, "load_authorized_setting", lambda *a, **k: _gitlab_app_setting(), raising=True)
    svc = _RecordingService()
    monkeypatch.setattr(gitlab_oauth, "_get_oauth_service", lambda: svc, raising=True)

    _client().post("/v1/gitlab-oauth/connect", json={"setting_id": "s1", "test": True})

    assert svc.calls[0]["persist_token"] is False


def test_initiate_is_always_test_mode(monkeypatch):
    """/initiate has no setting to persist under, so it must never persist a token."""
    svc = _RecordingService()
    monkeypatch.setattr(gitlab_oauth, "_get_oauth_service", lambda: svc, raising=True)

    _client().post("/v1/gitlab-oauth/initiate", json={"client_id": "c", "client_secret": "s"})

    assert svc.calls[0]["persist_token"] is False
    assert svc.calls[0].get("integration_id") is None
