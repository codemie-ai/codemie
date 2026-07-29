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

"""Regression tests for EPMCDME-13770.

`redis` is an optional dependency (``poetry.lock``: ``optional = true``,
``markers = extra == "enterprise"``), which is why
:func:`codemie.clients.redis.create_redis_client` imports it lazily inside the
function body. Importing an OAuth router must therefore never construct a Redis
client, otherwise backend startup dies with ``ModuleNotFoundError: No module
named 'redis'`` in deployments that do not install the extra.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import codemie.service.sharepoint_pkce_service as sharepoint_pkce_service
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User
from codemie.service.sharepoint_pkce_service import SharePointPKCEService

_TEST_USER = User(
    id="test_user_id",
    username="test_user",
    name="Test User",
    project_names=[],
    admin_project_names=[],
    knowledge_bases=[],
    auth_token=None,
)


def _build_client(router_module) -> TestClient:
    app = FastAPI()

    @app.exception_handler(ExtendedHTTPException)
    async def _exc_handler(request, exc: ExtendedHTTPException):
        return JSONResponse(status_code=exc.code, content={"error": {"message": exc.message}})

    app.include_router(router_module.router)
    app.dependency_overrides[router_module.authenticate] = lambda: _TEST_USER
    return TestClient(app)


# NOTE: `codemie.rest_api.routers.google_oauth` is imported inside the tests that
# need it, not at module scope. While the eager module-level service is still
# present, importing it raises ModuleNotFoundError in an environment without the
# optional `redis` package, which would turn these tests into a collection error.

_REPO_ROOT = Path(__file__).resolve().parents[4]

# Blocks the `redis` package the way a deployment without the `enterprise` extra
# does, then builds the whole ASGI app exactly as `uvicorn` does at startup.
_IMPORT_PROBE = """
import importlib.abc
import sys


class _BlockRedis(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "redis" or fullname.startswith("redis."):
            raise ModuleNotFoundError(f"No module named '{fullname}'", name=fullname)
        return None


sys.meta_path.insert(0, _BlockRedis())

import codemie.rest_api.main as main  # noqa: E402

paths = {getattr(route, "path", "") for route in main.app.routes}
assert "/v1/sharepoint/oauth/device/initiate" in paths, "device-code route missing"
assert "/v1/sharepoint/oauth/initiate" in paths, "pkce route missing"
print("APP_IMPORT_OK")
"""


def test_app_starts_without_redis_package_when_pkce_disabled():
    """Backend startup must survive a missing `redis` package when PKCE is off.

    This is the ticket's reproduction: `uvicorn` imports `rest_api.main`, which used
    to build the OAuth services eagerly and die on `import redis`.
    """
    env = {
        "SHAREPOINT_PKCE_ENABLED": "false",
        "GOOGLE_OAUTH_CLIENT_ID": "",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": "/tmp",
    }
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )

    assert "ModuleNotFoundError: No module named 'redis'" not in result.stderr, (
        "Building the app created a Redis client at import time:\n" + result.stderr[-2000:]
    )
    assert "APP_IMPORT_OK" in result.stdout, f"probe failed (exit {result.returncode}):\n{result.stderr[-3000:]}"


# ---------------------------------------------------------------------------
# SharePointPKCEService — SHAREPOINT_PKCE_ENABLED gating
# ---------------------------------------------------------------------------


def test_sharepoint_pkce_service_skips_redis_when_pkce_disabled(monkeypatch):
    factory = MagicMock()
    monkeypatch.setattr(sharepoint_pkce_service, "create_redis_client", factory)
    monkeypatch.setattr(sharepoint_pkce_service.config, "SHAREPOINT_PKCE_ENABLED", False)

    service = SharePointPKCEService()

    factory.assert_not_called()
    assert service._redis is None


def test_sharepoint_pkce_service_creates_redis_when_pkce_enabled(monkeypatch):
    factory = MagicMock()
    monkeypatch.setattr(sharepoint_pkce_service, "create_redis_client", factory)
    monkeypatch.setattr(sharepoint_pkce_service.config, "SHAREPOINT_PKCE_ENABLED", True)

    service = SharePointPKCEService()

    factory.assert_called_once()
    assert service._redis is factory.return_value


def test_sharepoint_pkce_service_prefers_injected_redis_client_when_disabled(monkeypatch):
    """Injection must keep working so existing tests can drive a disabled-by-default config."""
    factory = MagicMock()
    injected = MagicMock()
    monkeypatch.setattr(sharepoint_pkce_service, "create_redis_client", factory)
    monkeypatch.setattr(sharepoint_pkce_service.config, "SHAREPOINT_PKCE_ENABLED", False)

    service = SharePointPKCEService(redis_client=injected)

    factory.assert_not_called()
    assert service._redis is injected


# ---------------------------------------------------------------------------
# Device Code flow stays usable while PKCE is disabled (no Redis involved)
# ---------------------------------------------------------------------------


def test_device_code_flow_works_while_pkce_disabled(monkeypatch, httpx_mock):
    """The non-PKCE SharePoint OAuth flow must not be collateral damage of the fix."""
    import codemie.rest_api.routers.sharepoint_oauth as sharepoint_oauth

    monkeypatch.setattr(sharepoint_oauth.config, "SHAREPOINT_PKCE_ENABLED", False)
    assert sharepoint_oauth._pkce_service._redis is None, "PKCE service should hold no Redis client"

    client = _build_client(sharepoint_oauth)

    httpx_mock.add_response(
        method="POST",
        url="https://login.microsoftonline.com/common/oauth2/v2.0/devicecode",
        json={
            "user_code": "ABC123",
            "verification_uri": "https://microsoft.com/devicelogin",
            "device_code": "dev-code",
            "expires_in": 900,
            "interval": 5,
            "message": "Enter ABC123",
        },
        status_code=200,
    )

    response = client.post("/v1/sharepoint/oauth/device/initiate", json={})

    assert response.status_code == 200
    assert response.json()["user_code"] == "ABC123"


def test_pkce_endpoints_still_return_503_while_disabled(monkeypatch):
    """The disabled PKCE flow must degrade to 503, never AttributeError on a None client."""
    import codemie.rest_api.routers.sharepoint_oauth as sharepoint_oauth

    monkeypatch.setattr(sharepoint_oauth.config, "SHAREPOINT_PKCE_ENABLED", False)

    client = _build_client(sharepoint_oauth)

    assert client.post("/v1/sharepoint/oauth/initiate", json={}).status_code == 503
    assert client.get("/v1/sharepoint/oauth/status/any-state").status_code == 503
    assert client.get("/v1/sharepoint/oauth/callback?code=x&state=y").status_code == 503


# ---------------------------------------------------------------------------
# Google OAuth router — lazy service construction
# ---------------------------------------------------------------------------


def test_google_oauth_router_has_no_eager_module_level_service():
    import codemie.rest_api.routers.google_oauth as google_oauth

    assert not hasattr(
        google_oauth, "oauth_service"
    ), "google_oauth.oauth_service is built at import time and reaches Redis; use a lazy accessor"


def test_google_oauth_service_accessor_is_lazy_and_cached(monkeypatch):
    import codemie.rest_api.routers.google_oauth as google_oauth

    built = MagicMock()
    monkeypatch.setattr(google_oauth, "_oauth_service", None)
    monkeypatch.setattr(google_oauth, "GoogleOAuthFlowService", built)

    first = google_oauth._get_oauth_service()
    second = google_oauth._get_oauth_service()

    built.assert_called_once()
    assert first is second is built.return_value
