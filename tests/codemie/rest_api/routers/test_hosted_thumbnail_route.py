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

"""Thumbnail route bounds."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from codemie.rest_api.routers import hosted_applications as routes


def application():
    from codemie.core.hosted_applications.errors import HostedApplicationError
    from codemie.hosted_apps_gateway.management import hosted_error_response

    app = FastAPI()
    app.include_router(routes.router)
    app.add_exception_handler(HostedApplicationError, hosted_error_response)
    app.dependency_overrides[routes.hosted_actor] = actor
    return app


def actor():
    return SimpleNamespace(principal_id="user-1")


@pytest.mark.asyncio
async def test_thumbnail_route_reads_if_none_match_after_authorization(monkeypatch):
    from codemie.core.hosted_applications.errors import HostedApplicationError
    from codemie.service.hosted_applications import thumbnails

    tag = '"' + "a" * 64 + '"'
    calls = []

    class Covers:
        def __init__(self, catalog):
            pass

        async def get(self, identity, app_id):
            calls.append(identity.principal_id)
            if identity.principal_id != "user-1":
                raise HostedApplicationError("NOT_FOUND", "Application not found", 404)
            return thumbnails.Cover(b"\x89PNG", "image/png", tag)

    monkeypatch.setattr(thumbnails, "HostedThumbnailService", Covers)
    value = SimpleNamespace(settings=SimpleNamespace(enabled=True), catalog=None)
    app = application()
    app.state.hosted_applications = value
    path = "/v1/hosted-applications/6f1c1a52-3a43-4d55-9d43-0d6f1d7b3a10/thumbnail"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test") as client:
        first = await client.get(path)
        cached = await client.get(path, headers={"If-None-Match": tag})
        app.dependency_overrides[routes.hosted_actor] = lambda: SimpleNamespace(principal_id="revoked")
        revoked = await client.get(path, headers={"If-None-Match": tag})
    assert first.status_code == 200 and first.content == b"\x89PNG" and first.headers["etag"] == tag
    assert first.headers["cache-control"] == "private, no-cache"
    assert cached.status_code == 304 and cached.content == b"" and cached.headers["etag"] == tag
    assert revoked.status_code == 404 and calls == ["user-1", "user-1", "revoked"]
