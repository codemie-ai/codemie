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

import base64
import json
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers import auth as auth_router
from codemie.rest_api.security.user import AUTHORIZATION_HEADER


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(auth_router.router)
    return TestClient(app)


def _decode_token_from_redirect(location: str) -> dict:
    query = parse_qs(urlparse(location).query)
    return json.loads(base64.b64decode(query["token"][0]).decode("ascii"))


def test_login_local_provider_forwards_valid_auth_cookie(monkeypatch):
    monkeypatch.setattr(auth_router.config, "IDP_PROVIDER", "local")
    monkeypatch.setattr(auth_router.config, "ENABLE_USER_MANAGEMENT", True)
    monkeypatch.setattr(auth_router.config, "AUTH_COOKIE_NAME", "codemie_access_token")

    validator = MagicMock()
    monkeypatch.setattr(auth_router.jwt_local, "validate_local_jwt", validator)

    client = _build_client()

    response = client.get(
        "/v1/auth/login/8123",
        cookies={"codemie_access_token": "cookie-token"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"].startswith("http://localhost:8123/auth?token=")
    assert _decode_token_from_redirect(response.headers["location"]) == {
        "provider": "local",
        "cookies": {"codemie_access_token": "cookie-token"},
    }
    validator.assert_called_once_with("cookie-token")


def test_login_local_provider_redirects_to_sign_in_when_cookie_invalid(monkeypatch):
    monkeypatch.setattr(auth_router.config, "IDP_PROVIDER", "local")
    monkeypatch.setattr(auth_router.config, "ENABLE_USER_MANAGEMENT", True)
    monkeypatch.setattr(auth_router.config, "AUTH_COOKIE_NAME", "codemie_access_token")
    monkeypatch.setattr(auth_router.config, "FRONTEND_URL", "http://frontend.local")
    monkeypatch.setattr(auth_router.config, "API_ROOT_PATH", "")

    validator = MagicMock(side_effect=ExtendedHTTPException(code=401, message="invalid token"))
    monkeypatch.setattr(auth_router.jwt_local, "validate_local_jwt", validator)

    client = _build_client()

    response = client.get(
        "/v1/auth/login/8123",
        cookies={"codemie_access_token": "bad-cookie"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "http://frontend.local/auth/sign-in?next=/v1/auth/login/8123"
    validator.assert_called_once_with("bad-cookie")


def test_login_local_provider_accepts_bearer_header_when_cookie_missing(monkeypatch):
    monkeypatch.setattr(auth_router.config, "IDP_PROVIDER", "local")
    monkeypatch.setattr(auth_router.config, "ENABLE_USER_MANAGEMENT", True)
    monkeypatch.setattr(auth_router.config, "AUTH_COOKIE_NAME", "codemie_access_token")

    validator = MagicMock()
    monkeypatch.setattr(auth_router.jwt_local, "validate_local_jwt", validator)

    client = _build_client()

    response = client.get(
        "/v1/auth/login/8123",
        headers={AUTHORIZATION_HEADER: "Bearer header-token"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert _decode_token_from_redirect(response.headers["location"]) == {
        "provider": "local",
        "cookies": {"codemie_access_token": "header-token"},
    }
    validator.assert_called_once_with("header-token")
