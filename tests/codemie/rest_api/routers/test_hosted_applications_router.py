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

"""The real hosted router inside the real application: status codes, error shapes and body bounds."""

import asyncio
import re
import struct
import sys
import types
import zlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.identity import VerifiedIdentityContext
from codemie.hosted_apps_gateway import management as hosted_management
from codemie.hosted_apps_gateway.management import HostedManagementBodyBoundary
from codemie.rest_api import main
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.hosted_applications import hosted_identity
from codemie.rest_api.security.user import User

UI = "https://codemie.example.com"
APP = getattr(main.app, "management", main.app)  # the FastAPI application behind optional host dispatch


def identity():
    now = datetime.now(timezone.utc)
    return VerifiedIdentityContext(
        "user-1", "local_jwt", now - timedelta(seconds=5), now + timedelta(hours=1), b"a" * 32
    )


def runtime(enabled=True, **services):
    settings = SimpleNamespace(enabled=enabled, ui_origins=(UI,), secret=b"s" * 48)
    return SimpleNamespace(settings=settings, **services)


@pytest.fixture
def client():
    overrides = dict(APP.dependency_overrides)
    previous = getattr(APP.state, "hosted_applications", None)
    yield TestClient(main.app, raise_server_exceptions=False)
    APP.dependency_overrides.clear()
    APP.dependency_overrides.update(overrides)
    APP.state.hosted_applications = previous


@pytest.fixture
def signed_in():
    APP.dependency_overrides[authenticate] = lambda: User(id="user-1", username="user-1", name="User")
    APP.dependency_overrides[hosted_identity] = identity


def hosted(response):
    return response.json()["error"]["details"]


def test_unauthenticated_requests_are_401_not_503(client):
    APP.state.hosted_applications = runtime()
    for method, path in [("GET", "/v1/hosted-applications"), ("GET", "/v1/hosted-applications/capabilities")]:
        response = client.request(method, path)
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "Authentication failed"


def test_platform_errors_from_dependencies_keep_their_status(client):
    APP.state.hosted_applications = runtime()

    def forbidden():
        raise ExtendedHTTPException(code=403, message="Access denied")

    APP.dependency_overrides[authenticate] = forbidden
    response = client.get("/v1/hosted-applications/capabilities")
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Access denied"


def test_accounts_that_cannot_use_the_feature_see_it_unavailable_instead_of_a_sign_in_loop(client):
    APP.state.hosted_applications = runtime()
    APP.dependency_overrides[authenticate] = lambda: User(
        id="service-1", username="service-1", name="Service", user_type="external"
    )
    capabilities = client.get("/v1/hosted-applications/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["view"] is False and capabilities.json()["disabled_reason"]
    listing = client.get("/v1/hosted-applications")
    assert listing.status_code == 403 and hosted(listing)["code"] == "ACCOUNT_NOT_SUPPORTED"


@pytest.mark.parametrize("state", [None, runtime(enabled=False)])
def test_disabled_feature_reports_capabilities_and_denies_routes(client, signed_in, state):
    APP.state.hosted_applications = state
    capabilities = client.get("/v1/hosted-applications/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["create"] is False and capabilities.json()["disabled_reason"]
    for method, path in [
        ("GET", "/v1/hosted-applications"),
        ("GET", f"/v1/hosted-applications/{uuid4()}"),
    ]:
        response = client.request(method, path)
        assert response.status_code == 403
        assert hosted(response)["code"] == "FEATURE_DISABLED"
    created = client.post(
        "/v1/hosted-applications",
        json={"name": "App", "description": "Text"},
        headers={"Authorization": "Bearer token", "Idempotency-Key": str(uuid4())},
    )
    assert created.status_code == 403 and hosted(created)["code"] == "FEATURE_DISABLED"


@pytest.mark.parametrize(
    ("headers", "status", "code"),
    [
        ({"Content-Type": "text/plain", "Authorization": "Bearer token"}, 415, "UNSUPPORTED_MEDIA_TYPE"),
        ({"Content-Type": "application/json", "Cookie": "codemie_access_token=t"}, 403, "CSRF_REJECTED"),
        (
            {
                "Content-Type": "application/json",
                "Cookie": "codemie_access_token=t",
                "Origin": "https://attacker.example",
                "Sec-Fetch-Site": "cross-site",
                "Sec-Fetch-Mode": "cors",
            },
            403,
            "CSRF_REJECTED",
        ),
    ],
)
def test_mutations_require_json_and_browser_provenance(client, signed_in, headers, status, code):
    create_app = AsyncMock(return_value={"id": "never"})
    APP.state.hosted_applications = runtime(catalog=SimpleNamespace(create_app=create_app))
    response = client.post(
        "/v1/hosted-applications",
        content=b'{"name": "App", "description": "Text"}',
        headers={**headers, "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == status
    assert hosted(response)["code"] == code
    create_app.assert_not_awaited()


def test_usage_reports_the_quota_row_read_quota_returns(client, signed_in):
    from contextlib import asynccontextmanager

    state = SimpleNamespace(byte_limit=1000, stored_bytes=300, reserved_bytes=900)
    read_quota = AsyncMock(return_value=state)
    opened = []

    @asynccontextmanager
    async def transaction(user, recorded=None, **options):
        opened.append((user.id, options))
        yield SimpleNamespace(read_quota=read_quota)

    APP.state.hosted_applications = runtime(
        directory=SimpleNamespace(require=AsyncMock()), repository=SimpleNamespace(transaction=transaction)
    )
    response = client.get("/v1/hosted-applications/usage", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    assert response.json() == {"byte_limit": 1000, "available_bytes": 0, "stored_bytes": 300, "reserved_bytes": 900}
    read_quota.assert_awaited_once_with("user-1")
    assert opened == [("user-1", {})], "A read transaction: usage never takes the write path"


def test_validation_errors_are_field_level_and_never_echo_input(client, signed_in):
    APP.state.hosted_applications = runtime(catalog=SimpleNamespace(get_app=AsyncMock()))
    response = client.get("/v1/hosted-applications/not-a-uuid-SECRET-VALUE")
    assert response.status_code == 422
    details = hosted(response)
    assert details["code"] == "INVALID_REQUEST"
    assert details["fields"] == [
        {"location": ["path", "app_id"], "message": "Input should be a valid UUID", "type": "uuid_parsing"}
    ]
    assert "SECRET" not in response.text

    created = client.post(
        "/v1/hosted-applications",
        json={"name": "App", "description": "Text", "owner": "SECRET-OWNER"},
        headers={"Authorization": "Bearer token", "Idempotency-Key": str(uuid4())},
    )
    assert created.status_code == 422
    fields = hosted(created)["fields"]
    assert {"location": ["body", "owner"], "message": "Extra inputs are not permitted", "type": "extra_forbidden"} in (
        fields
    )
    assert "SECRET-OWNER" not in created.text


@pytest.mark.parametrize("body, shared", [({}, False), ({"shared": False}, False), ({"shared": True}, True)])
def test_create_accepts_shared_and_passes_it_to_the_catalog(client, signed_in, body, shared):
    create_app = AsyncMock(return_value={"id": "app-1"})
    APP.state.hosted_applications = runtime(catalog=SimpleNamespace(create_app=create_app))
    response = client.post(
        "/v1/hosted-applications",
        json={"name": "App", "description": "Text", "project": "team-a", **body},
        headers={"Authorization": "Bearer token", "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 201, response.text
    assert response.json() == {"id": "app-1"}
    request = create_app.await_args.args[1]
    assert request.shared is shared


def test_catalog_folders_are_not_an_api_surface(client, signed_in):
    paths = {route.path for route in APP.routes}
    assert not {path for path in paths if "application-folders" in path}
    APP.state.hosted_applications = runtime(catalog=SimpleNamespace(list_apps=AsyncMock(return_value={"items": []})))
    assert client.get("/v1/application-folders").status_code == 404
    assert client.post("/v1/application-folders", json={"name": "x"}).status_code in (404, 405)


def test_the_list_takes_no_folder_filter(client, signed_in):
    list_apps = AsyncMock(return_value={"items": []})
    APP.state.hosted_applications = runtime(catalog=SimpleNamespace(list_apps=list_apps))
    response = client.get("/v1/hosted-applications?folder_id=x&include_subfolders=true")
    assert response.status_code == 200
    assert {"folder_id", "include_subfolders"}.isdisjoint(list_apps.await_args.kwargs)


def test_the_capabilities_carry_no_folder_depth_or_sharing_audience(client, signed_in):
    transaction = MagicMock()
    transaction.return_value.__aenter__ = AsyncMock()
    transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    APP.state.hosted_applications = runtime(
        directory=SimpleNamespace(require=AsyncMock()), repository=SimpleNamespace(transaction=transaction)
    )
    APP.state.hosted_applications.settings.launch_policy = lambda: {}
    with patch("codemie.rest_api.routers.hosted_applications.verified_context", return_value=identity()):
        response = client.get("/v1/hosted-applications/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["create"] is True and "folder_depth" not in body["limits"]
    # The audience options and the recipient limit belonged to the removed sharing model.
    assert {"selected_share", "org_share"}.isdisjoint(body) and "recipients" not in body["limits"]


def test_validator_messages_that_quote_input_are_replaced():
    from fastapi.exceptions import RequestValidationError

    error = RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("body", "name"),
                "msg": "Value error, bad SECRET-NAME",
                "input": "SECRET-NAME",
            },
            {"type": "value_error", "loc": ("body", "name"), "msg": "Value error, Text is required", "input": "  "},
            {
                "type": "union_tag_invalid",
                "loc": ("body", "x" * 200),
                "msg": "Input tag 'SECRET' is wrong",
                "input": {},
            },
        ]
    )
    body = hosted_management.invalid_request(error).body.decode()
    assert "SECRET" not in body
    assert "Text is required" in body
    assert "x" * 65 not in body


def test_unexpected_failures_are_logged_with_an_error_id(client, signed_in):
    failing = AsyncMock(side_effect=RuntimeError("database said SECRET-ROW"))
    APP.state.hosted_applications = runtime(catalog=SimpleNamespace(get_app=failing))
    with patch.object(hosted_management, "logger") as logger:
        response = client.get(f"/v1/hosted-applications/{uuid4()}")
    assert response.status_code == 503
    details = hosted(response)
    assert details["code"] == "APPLICATION_UNAVAILABLE"
    assert re.fullmatch("[0-9a-f]{32}", details["error_id"])
    assert "SECRET" not in response.text
    logger.error.assert_called_once()
    message = logger.error.call_args.args[0]
    assert details["error_id"] in message and "/v1/hosted-applications/{app_id}" in message
    assert isinstance(logger.error.call_args.kwargs["exc_info"], RuntimeError)


def test_hosted_server_errors_are_logged_by_the_global_handler(client, signed_in):
    failing = AsyncMock(side_effect=HostedApplicationError("STORAGE_UNAVAILABLE", "Application storage is down", 503))
    APP.state.hosted_applications = runtime(catalog=SimpleNamespace(get_app=failing))
    with patch.object(hosted_management, "logger") as logger:
        response = client.get(f"/v1/hosted-applications/{uuid4()}")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert hosted(response)["code"] == "STORAGE_UNAVAILABLE"
    logger.error.assert_called_once()
    message = logger.error.call_args.args[0]
    assert "STORAGE_UNAVAILABLE" in message and "/v1/hosted-applications/{app_id}" in message
    assert logger.error.call_args.kwargs["exc_info"]


def test_hosted_client_errors_are_not_logged_as_server_errors(client, signed_in):
    failing = AsyncMock(side_effect=HostedApplicationError("NOT_FOUND", "Application not found", 404))
    APP.state.hosted_applications = runtime(catalog=SimpleNamespace(get_app=failing))
    with patch.object(hosted_management, "logger") as logger:
        response = client.get(f"/v1/hosted-applications/{uuid4()}")
    assert response.status_code == 404 and hosted(response)["code"] == "NOT_FOUND"
    logger.error.assert_not_called()


def test_http_exceptions_keep_their_status(client, signed_in):
    failing = AsyncMock(side_effect=HTTPException(status_code=409, detail="Conflict"))
    APP.state.hosted_applications = runtime(catalog=SimpleNamespace(get_app=failing))
    assert client.get(f"/v1/hosted-applications/{uuid4()}").status_code == 409


# Management body boundary


class Recorder:
    """Inner application that records the body it was handed."""

    def __init__(self):
        self.bodies = []

    async def __call__(self, scope, receive, send):
        self.bodies.append((await receive())["body"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


def drive(app, *, path, root_path="", method="POST", body=b""):
    """uvicorn --root-path prefixes both path and raw_path with the root path."""

    async def run():
        scope = {
            "type": "http",
            "method": method,
            "path": root_path + path,
            "raw_path": (root_path + path).encode(),
            "root_path": root_path,
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"",
        }
        chunks = [{"type": "http.request", "body": body, "more_body": False}]
        sent = []

        async def receive():
            return chunks.pop(0) if chunks else {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        await app(scope, receive, send)
        return sent[0]["status"]

    return asyncio.run(run())


@pytest.mark.parametrize("root_path", ["", "/code-assistant-api"])
@pytest.mark.parametrize(
    "path",
    [
        "/v1/hosted-applications",
        "/v1/hosted-applications/6f1c1a52-3a43-4d55-9d43-0d6f1d7b3a10/launch",
    ],
)
def test_json_body_limit_applies_under_a_root_path(root_path, path):
    inner = Recorder()
    status = drive(HostedManagementBodyBoundary(inner, maximum=32), path=path, root_path=root_path, body=b"x" * 33)
    assert (status, inner.bodies) == (413, [])


def test_small_bodies_and_streamed_artifacts_pass_through_under_a_root_path():
    artifact = f"/v1/hosted-applications/{uuid4()}/versions/{uuid4()}/artifact"
    for path, method, body in [
        ("/v1/hosted-applications", "POST", b"{}"),
        (artifact, "PUT", b"x" * 64),
        ("/v1/other", "POST", b"x" * 64),
    ]:
        inner = Recorder()
        boundary = HostedManagementBodyBoundary(inner, maximum=32)
        status = drive(boundary, path=path, root_path="/code-assistant-api", method=method, body=body)
        assert (status, inner.bodies) == (204, [body])


@pytest.mark.parametrize("root_path", ["", "/code-assistant-api"])
def test_cover_upload_has_its_own_bound_under_a_root_path(root_path):
    cover = f"/v1/hosted-applications/{uuid4()}/cover"
    for method, path, size, status in [
        ("PUT", cover, 64, 204),
        ("PUT", cover, 65, 413),
        # Only the cover upload is widened: its DELETE, nested paths and sibling PUTs keep the JSON bound.
        ("DELETE", cover, 33, 413),
        ("PUT", cover + "/x", 33, 413),
        ("PUT", f"/v1/hosted-applications/{uuid4()}/sharing", 33, 413),
    ]:
        inner = Recorder()
        boundary = HostedManagementBodyBoundary(inner, maximum=32, cover_maximum=64)
        assert drive(boundary, path=path, root_path=root_path, method=method, body=b"x" * size) == status, path
        assert inner.bodies == ([b"x" * size] if status == 204 else [])


def test_body_limit_runs_inside_cors_so_browsers_can_read_the_refusal(client):
    order = [entry.cls for entry in APP.user_middleware]
    assert order.index(CORSMiddleware) < order.index(HostedManagementBodyBoundary)
    origin = main.WEBAPP_CORS[0]
    for root_path in ("", "/code-assistant-api"):
        browser = TestClient(main.app, raise_server_exceptions=False, root_path=root_path)
        response = browser.post(
            root_path + "/v1/hosted-applications",
            content=b"x" * 40000,
            headers={"Origin": origin, "Content-Type": "application/json"},
        )
        assert response.status_code == 413
        assert hosted(response)["code"] == "REQUEST_TOO_LARGE"
        assert response.headers["access-control-allow-origin"] == origin


# Cover uploads


def tiny_png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(4))) + chunk(b"IEND", b"")


def cover_runtime(**catalog):
    return runtime(catalog=SimpleNamespace(**catalog))


def cover_headers(media_type="image/png"):
    return {
        "Content-Type": media_type,
        "Authorization": "Bearer token",
        "If-Match": '"app:cover:1"',
        "Idempotency-Key": str(uuid4()),
    }


def test_cover_upload_streams_a_raw_png_body_to_the_service(client, signed_in):
    received = []

    async def put_cover(identity, app_id, media_type, match, key, stream):
        received.append((app_id, media_type, match, key, b"".join([chunk async for chunk in stream])))
        return {"id": str(app_id), "has_cover": True, "etag": '"app:cover:2"'}

    APP.state.hosted_applications = cover_runtime(put_cover=put_cover)
    app_id, headers = uuid4(), cover_headers()
    response = client.put(f"/v1/hosted-applications/{app_id}/cover", content=tiny_png(), headers=headers)
    assert response.status_code == 200 and response.json()["has_cover"] is True
    assert response.headers["etag"] == '"app:cover:2"' and response.headers["cache-control"] == "no-store"
    assert received == [(app_id, "image/png", '"app:cover:1"', headers["Idempotency-Key"], tiny_png())]


@pytest.mark.parametrize("media_type", ["image/jpeg", "image/webp", "IMAGE/PNG"])
def test_cover_upload_accepts_exactly_the_three_raster_types(client, signed_in, media_type):
    put_cover = AsyncMock(return_value={"id": "app", "has_cover": True})
    APP.state.hosted_applications = cover_runtime(put_cover=put_cover)
    response = client.put(f"/v1/hosted-applications/{uuid4()}/cover", content=b"\0", headers=cover_headers(media_type))
    assert response.status_code == 200
    assert put_cover.await_args.args[2] == media_type.lower()


@pytest.mark.parametrize(
    "media_type",
    [
        "application/json",
        "application/octet-stream",
        "image/gif",
        "image/svg+xml",
        "text/html",
        "image/png; charset=utf-8",
        "multipart/form-data; boundary=cover",
    ],
)
def test_cover_upload_refuses_every_other_content_type_before_the_service(client, signed_in, media_type):
    put_cover = AsyncMock()
    APP.state.hosted_applications = cover_runtime(put_cover=put_cover)
    response = client.put(
        f"/v1/hosted-applications/{uuid4()}/cover", content=tiny_png(), headers=cover_headers(media_type)
    )
    assert response.status_code == 415 and hosted(response)["code"] == "UNSUPPORTED_MEDIA_TYPE"
    put_cover.assert_not_awaited()


def test_cover_removal_is_a_json_mutation(client, signed_in):
    delete_cover = AsyncMock(return_value={"id": "app", "has_cover": False, "etag": '"app:cover:3"'})
    APP.state.hosted_applications = cover_runtime(delete_cover=delete_cover)
    app_id, headers = uuid4(), cover_headers("application/json")
    response = client.delete(f"/v1/hosted-applications/{app_id}/cover", headers=headers)
    assert response.status_code == 200 and response.json()["has_cover"] is False
    assert response.headers["etag"] == '"app:cover:3"'
    assert delete_cover.await_args.args[1:] == (app_id, '"app:cover:1"', headers["Idempotency-Key"])
    refused = client.delete(f"/v1/hosted-applications/{app_id}/cover", headers=cover_headers("image/png"))
    assert refused.status_code == 415 and hosted(refused)["code"] == "UNSUPPORTED_MEDIA_TYPE"
    delete_cover.assert_awaited_once()


def test_delete_is_one_bodyless_mutation_and_trash_restore_permanent_are_gone(client, signed_in):
    delete = AsyncMock(return_value={"state": "purging", "job_id": "job", "application": {"id": "app"}})
    APP.state.hosted_applications = runtime(catalog=SimpleNamespace(delete=delete))
    app_id, headers = uuid4(), cover_headers("application/json")
    response = client.delete(f"/v1/hosted-applications/{app_id}", headers=headers)
    assert response.status_code == 202 and response.json()["state"] == "purging"
    assert delete.await_args.args[1:] == (app_id, '"app:cover:1"', headers["Idempotency-Key"])
    paths = {route.path for route in APP.routes}
    assert not {path for path in paths if path.endswith(("/trash", "/restore", "/permanent"))}
    for action in ("trash", "restore"):
        assert client.post(f"/v1/hosted-applications/{app_id}/{action}", headers=headers, json={}).status_code in (
            404,
            405,
        )
    assert client.delete(f"/v1/hosted-applications/{app_id}/permanent", headers=headers).status_code in (404, 405)


def test_body_limit_allows_one_mebibyte_on_the_cover_upload_only(client, signed_in):
    sizes = []

    async def put_cover(identity, app_id, media_type, match, key, stream):
        sizes.append(len(b"".join([chunk async for chunk in stream])))
        return {"id": str(app_id), "has_cover": True}

    share, delete_cover = AsyncMock(), AsyncMock()
    APP.state.hosted_applications = cover_runtime(put_cover=put_cover, share=share, delete_cover=delete_cover)
    app_id = uuid4()
    cover = f"/v1/hosted-applications/{app_id}/cover"
    assert client.put(cover, content=bytes(1024 * 1024), headers=cover_headers()).status_code == 200
    too_large = client.put(cover, content=bytes(1024 * 1024 + 1), headers=cover_headers())
    assert too_large.status_code == 413 and hosted(too_large)["code"] == "COVER_TOO_LARGE"
    assert sizes == [1024 * 1024]
    for method, path in [
        ("PUT", f"/v1/hosted-applications/{app_id}/sharing"),
        ("DELETE", cover),
        ("POST", "/v1/hosted-applications"),
    ]:
        response = client.request(method, path, content=bytes(40000), headers=cover_headers("application/json"))
        assert response.status_code == 413 and hosted(response)["code"] == "REQUEST_TOO_LARGE", path
    share.assert_not_awaited()
    delete_cover.assert_not_awaited()


# Startup


def runtime_module(factory):
    """Stand-in runtime module so startup handling is tested without the worker import graph."""
    module = types.ModuleType("codemie.service.hosted_applications.runtime")
    module.HostedApplicationsRuntime = factory
    return patch.dict(sys.modules, {"codemie.service.hosted_applications.runtime": module})


def test_runtime_failures_leave_the_platform_running_with_the_feature_closed():
    state = SimpleNamespace(state=SimpleNamespace(hosted_applications="stale"))
    failing = MagicMock(side_effect=HostedApplicationError("FEATURE_DISABLED", "Unsupported directory", 503))
    with runtime_module(failing), patch.object(main, "logger") as logger:
        asyncio.run(main._start_hosted_applications(state))
    assert state.state.hosted_applications is None
    logger.error.assert_called_once()
    assert "HostedApplicationError" in logger.error.call_args.args[0]


def test_misconfigured_feature_reports_its_reason_at_startup():
    settings = SimpleNamespace(enabled=False, disabled_reason="A protected validation service is required")
    started = SimpleNamespace(settings=settings, start=AsyncMock())
    state = SimpleNamespace(state=SimpleNamespace())
    with (
        runtime_module(MagicMock(return_value=started)),
        patch("codemie.core.hosted_applications.settings.hosted_feature_enabled", return_value=True),
        patch("codemie.configs.logger.logger") as logger,
    ):
        asyncio.run(main._start_hosted_applications(state))
    assert state.state.hosted_applications is started
    started.start.assert_awaited_once()
    logger.warning.assert_called_once()
    assert "A protected validation service is required" in logger.warning.call_args.args[0]


@pytest.mark.parametrize("refused", [False, True])
def test_suspend_route_answers_with_the_services_administrator_check(client, refused):
    # The service checks the ``users`` row; the request User's ``is_admin`` (forced on by ENV=local) is not read.
    error = HostedApplicationError("ACCESS_DENIED", "Administrator permission is required", 403)
    security = AsyncMock(side_effect=error) if refused else AsyncMock(return_value={"suspended": True})
    APP.state.hosted_applications = runtime(administration=SimpleNamespace(security=security))
    user = User(id="user-1", username="user-1", name="User")
    user.is_admin = False
    APP.dependency_overrides[authenticate] = lambda: user
    APP.dependency_overrides[hosted_identity] = identity
    response = client.post(
        f"/v1/admin/hosted-applications/{uuid4()}/security/suspend",
        json={"reason": "Phishing report"},
        headers={"Authorization": "Bearer token", "Idempotency-Key": str(uuid4()), "If-Match": '"app:1:1"'},
    )
    security.assert_awaited_once()
    if refused:
        assert response.status_code == 403 and hosted(response)["code"] == "ACCESS_DENIED"
    else:
        assert response.status_code == 200 and response.json()["suspended"] is True


NOT_OWNER = "Only the owner can share or revoke access"


@pytest.fixture
def share_catalog(client, signed_in, monkeypatch):
    from codemie.configs import config

    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)
    calls = {
        name: AsyncMock(return_value=value)
        for name, value in (
            ("share", {"shared_with": [{"id": "u-2", "username": "two"}]}),
            ("unshare", {"shared_with": []}),
            ("shared_with", {"shared_with": [{"id": "u-2", "username": "two"}]}),
            ("sharable_users", {"users": [{"id": "u-3"}]}),
        )
    }
    APP.state.hosted_applications = runtime(catalog=SimpleNamespace(**calls))
    return calls


def share_headers():
    return {"Authorization": "Bearer token", "Idempotency-Key": str(uuid4())}


@pytest.mark.parametrize("method", ["post", "delete"])
@pytest.mark.parametrize(
    "body",
    [
        {"user_ids": []},
        {"user_ids": [f"u-{index}" for index in range(101)]},
        {"user_ids": ["u-2"], "permission_level": "WRITE"},
        {},
    ],
)
def test_share_and_unshare_bodies_are_bounded_and_closed(client, share_catalog, method, body):
    response = client.request(method, f"/v1/hosted-applications/{uuid4()}/share", json=body, headers=share_headers())
    assert response.status_code == 422
    assert all(call.await_count == 0 for call in share_catalog.values())


def test_share_and_unshare_pass_the_ids_to_the_catalog_and_return_the_grantees(client, share_catalog):
    app_id = uuid4()
    posted = client.post(f"/v1/hosted-applications/{app_id}/share", json={"user_ids": ["u-2"]}, headers=share_headers())
    assert posted.status_code == 200 and posted.json() == {"shared_with": [{"id": "u-2", "username": "two"}]}
    assert share_catalog["share"].await_args.args[1:] == (app_id, ["u-2"])
    deleted = client.request(
        "DELETE", f"/v1/hosted-applications/{app_id}/share", json={"user_ids": ["u-2"]}, headers=share_headers()
    )
    assert deleted.status_code == 200 and deleted.json() == {"shared_with": []}
    assert share_catalog["unshare"].await_args.args[1:] == (app_id, ["u-2"])


def test_shared_with_and_sharable_users_answer_the_owner(client, share_catalog):
    app_id = uuid4()
    listed = client.get(f"/v1/hosted-applications/{app_id}/shared-with")
    assert listed.status_code == 200 and listed.json()["shared_with"][0]["id"] == "u-2"
    found = client.get(f"/v1/hosted-applications/{app_id}/sharable-users?q=ab")
    assert found.status_code == 200 and found.json() == {"users": [{"id": "u-3"}]}
    assert share_catalog["sharable_users"].await_args.args[1:] == (app_id, "ab")


@pytest.mark.parametrize("query", ["", "?q=", "?q=a"])
def test_sharable_users_needs_at_least_two_characters(client, share_catalog, query):
    response = client.get(f"/v1/hosted-applications/{uuid4()}/sharable-users{query}")
    assert response.status_code == 422
    assert share_catalog["sharable_users"].await_count == 0


@pytest.mark.parametrize("name", ["share", "unshare", "shared_with", "sharable_users"])
@pytest.mark.parametrize(("error", "status"), [(NOT_OWNER, 403), ("Application not found", 404)])
def test_owner_only_refusals_keep_their_status(client, share_catalog, name, error, status):
    code = "ACCESS_DENIED" if status == 403 else "NOT_FOUND"
    share_catalog[name].side_effect = HostedApplicationError(code, error, status)
    app = f"/v1/hosted-applications/{uuid4()}"
    request = {
        "share": lambda: client.post(f"{app}/share", json={"user_ids": ["u-2"]}, headers=share_headers()),
        "unshare": lambda: client.request(
            "DELETE", f"{app}/share", json={"user_ids": ["u-2"]}, headers=share_headers()
        ),
        "shared_with": lambda: client.get(f"{app}/shared-with"),
        "sharable_users": lambda: client.get(f"{app}/sharable-users?q=ab"),
    }[name]
    response = request()
    assert response.status_code == status and error in response.text


def test_the_share_routes_need_user_management(client, share_catalog, monkeypatch):
    from codemie.configs import config

    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", False)
    app = f"/v1/hosted-applications/{uuid4()}"
    for response in (
        client.get(f"{app}/shared-with"),
        client.get(f"{app}/sharable-users?q=ab"),
        client.post(f"{app}/share", json={"user_ids": ["u-2"]}, headers=share_headers()),
        client.request("DELETE", f"{app}/share", json={"user_ids": ["u-2"]}, headers=share_headers()),
    ):
        assert response.status_code == 400
    assert all(call.await_count == 0 for call in share_catalog.values())


def test_the_list_accepts_the_shared_scope_and_app_responses_carry_no_grantees(client, signed_in):
    item = {"id": "a", "name": "App", "shared": True}
    list_apps = AsyncMock(return_value={"items": [item]})
    get_app = AsyncMock(return_value=item)
    APP.state.hosted_applications = runtime(catalog=SimpleNamespace(list_apps=list_apps, get_app=get_app))
    listed = client.get("/v1/hosted-applications?scope=shared")
    assert listed.status_code == 200 and list_apps.await_args.kwargs["scope"] == "shared"
    opened = client.get(f"/v1/hosted-applications/{uuid4()}")
    assert opened.status_code == 200
    assert "shared_with" not in opened.json() and "shared_with" not in listed.json()["items"][0]


def test_the_list_passes_project_creator_sharing_and_status_to_the_catalog(client, signed_in):
    list_apps = AsyncMock(return_value={"items": [], "next_cursor": None})
    APP.state.hosted_applications = runtime(catalog=SimpleNamespace(list_apps=list_apps))
    response = client.get(
        "/v1/hosted-applications?project=a&project=b&created_by=Jane%20Doe&shared=people&status=draft"
    )
    assert response.status_code == 200
    kwargs = list_apps.await_args.kwargs
    assert kwargs["project"] == ["a", "b"] and kwargs["created_by"] == "Jane Doe"
    assert kwargs["shared"] == "people" and kwargs["status"] == "draft"
    client.get("/v1/hosted-applications")
    kwargs = list_apps.await_args.kwargs
    assert kwargs["project"] is None and kwargs["created_by"] is None
    assert kwargs["shared"] is None and kwargs["status"] is None


def test_the_list_no_longer_takes_a_security_state_filter(client, signed_in):
    """S-28: no moderation list; a stray ``security_state`` is not passed on (the catalog refuses the scope)."""
    list_apps = AsyncMock(return_value={"items": [], "next_cursor": None})
    APP.state.hosted_applications = runtime(catalog=SimpleNamespace(list_apps=list_apps))
    assert client.get("/v1/hosted-applications?scope=project&security_state=suspended").status_code == 200
    kwargs = list_apps.await_args.kwargs
    assert kwargs["scope"] == "project" and "security_state" not in kwargs


def test_the_list_refuses_an_overlong_creator_before_the_catalog(client, signed_in):
    list_apps = AsyncMock(return_value={"items": []})
    APP.state.hosted_applications = runtime(catalog=SimpleNamespace(list_apps=list_apps))
    assert client.get("/v1/hosted-applications?created_by=" + "x" * 201).status_code == 422
    assert list_apps.await_count == 0


def test_creators_route_answers_with_the_catalog_list_and_passes_the_scope(client, signed_in):
    creators = [{"id": "u-2", "username": "jane", "name": "Jane Doe"}]
    list_creators = AsyncMock(return_value=creators)
    APP.state.hosted_applications = runtime(catalog=SimpleNamespace(list_creators=list_creators))
    response = client.get("/v1/hosted-applications/users")
    assert response.status_code == 200 and response.json() == creators
    assert response.headers["Cache-Control"] == "no-store"
    assert list_creators.await_args.kwargs == {"scope": None}
    client.get("/v1/hosted-applications/users?scope=marketplace")
    assert list_creators.await_args.kwargs == {"scope": "marketplace"}


def test_creators_route_needs_the_feature_and_a_signed_in_user(client, signed_in):
    APP.state.hosted_applications = runtime(enabled=False, catalog=SimpleNamespace(list_creators=AsyncMock()))
    response = client.get("/v1/hosted-applications/users")
    assert response.status_code == 403 and hosted(response)["code"] == "FEATURE_DISABLED"
    APP.dependency_overrides.clear()
    assert client.get("/v1/hosted-applications/users").status_code == 401


LINK = {"link": {"id": "l-1", "token": "tok-abc", "created_at": "2026-11-06T00:00:00+00:00"}}


@pytest.fixture
def link_catalog(client, signed_in, monkeypatch):
    from codemie.configs import config

    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)
    calls = {
        "get_share_link": AsyncMock(return_value=LINK),
        "create_share_link": AsyncMock(return_value=LINK),
        "revoke_share_link": AsyncMock(return_value={"link": None}),
        "open_share_link": AsyncMock(return_value={"app_id": "app-1"}),
    }
    APP.state.hosted_applications = runtime(catalog=SimpleNamespace(**calls))
    return calls


def test_share_link_routes_reach_the_catalog(client, link_catalog):
    app_id = uuid4()
    url, headers = f"/v1/hosted-applications/{app_id}/share-link", cover_headers("application/json")
    assert client.get(url).json() == LINK
    assert client.post(url, headers=headers).json() == LINK
    assert client.delete(url, headers=headers).json() == {"link": None}
    for name in ("get_share_link", "create_share_link", "revoke_share_link"):
        assert link_catalog[name].await_args.args[1:] == (app_id,)


def test_share_link_open_passes_the_token_and_returns_only_the_app(client, link_catalog):
    response = client.post(
        "/v1/hosted-applications/share-links/open", json={"token": "tok-abc"}, headers=share_headers()
    )
    assert response.status_code == 200 and response.json() == {"app_id": "app-1"}
    assert "tok-abc" not in response.text
    assert link_catalog["open_share_link"].await_args.args[1:] == ("tok-abc",)


@pytest.mark.parametrize("body", [{}, {"token": ""}, {"token": 5}, {"token": "SECRET-TOK", "extra": "SECRET-X"}])
def test_share_link_open_rejects_bad_bodies_without_echoing_values(client, link_catalog, body):
    response = client.post("/v1/hosted-applications/share-links/open", json=body, headers=share_headers())
    assert response.status_code == 422 and "SECRET" not in response.text
    assert link_catalog["open_share_link"].await_count == 0


def test_share_link_writes_and_open_need_the_mutation_guard(client, link_catalog):
    app = f"/v1/hosted-applications/{uuid4()}/share-link"
    bare = {"Authorization": "Bearer token"}
    for response in (
        client.post(app, headers=bare),
        client.delete(app, headers=bare),
        client.post("/v1/hosted-applications/share-links/open", content=b'{"token": "t"}', headers=bare),
    ):
        assert response.status_code == 415
    assert all(call.await_count == 0 for call in link_catalog.values())


def test_share_link_routes_need_user_management(client, link_catalog, monkeypatch):
    from codemie.configs import config

    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", False)
    app = f"/v1/hosted-applications/{uuid4()}/share-link"
    for response in (
        client.get(app),
        client.post(app, headers=cover_headers("application/json")),
        client.delete(app, headers=cover_headers("application/json")),
        client.post("/v1/hosted-applications/share-links/open", json={"token": "t"}, headers=share_headers()),
    ):
        assert response.status_code == 400
    assert all(call.await_count == 0 for call in link_catalog.values())


@pytest.mark.parametrize(("code", "status"), [("ACCESS_DENIED", 403), ("SHARE_LINK_NOT_AVAILABLE", 404)])
def test_share_link_open_keeps_catalog_refusal_statuses(client, link_catalog, code, status):
    link_catalog["open_share_link"].side_effect = HostedApplicationError(code, "Refused", status)
    response = client.post("/v1/hosted-applications/share-links/open", json={"token": "t"}, headers=share_headers())
    assert response.status_code == status and hosted(response)["code"] == code


def test_share_link_endpoints_and_payload_are_fully_typed():
    import inspect
    import typing

    from codemie.rest_api.routers import hosted_applications as routes
    from codemie.service.hosted_applications.catalog import HostedApplicationCatalog

    functions = (
        routes.open_share_link,
        routes.get_share_link,
        routes.create_share_link,
        routes.revoke_share_link,
        HostedApplicationCatalog._link_payload,
    )
    for function in functions:
        hints = typing.get_type_hints(function)
        assert "return" in hints, function.__name__
        assert set(inspect.signature(function).parameters) <= set(hints), function.__name__


def test_launch_takes_no_static_preview_mode():
    from pydantic import ValidationError

    from codemie.rest_api.routers.hosted_applications import LaunchRequest, launch as launch_endpoint

    with pytest.raises(ValidationError):
        LaunchRequest(purpose="owner_preview", preview="static")
    value = SimpleNamespace(launch=SimpleNamespace(issue=AsyncMock(return_value={})))
    app_id = uuid4()
    asyncio.run(launch_endpoint(app_id, LaunchRequest(purpose="published_view"), MagicMock(), value, MagicMock()))
    assert value.launch.issue.call_args.kwargs == {"purpose": "published_view", "version_id": None}
