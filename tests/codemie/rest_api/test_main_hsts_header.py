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

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from codemie.rest_api.main import HSTS_HEADER_VALUE, add_hsts_header


@pytest.fixture
def client():
    app = FastAPI()
    app.middleware("http")(add_hsts_header)

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.post("/ok")
    async def ok_post():
        return {"ok": True}

    @app.get("/missing")
    async def missing():
        raise HTTPException(status_code=404)

    return TestClient(app)


def test_hsts_value_matches_policy():
    assert HSTS_HEADER_VALUE == "max-age=31536000; includeSubDomains"


@pytest.mark.parametrize("method,path,status", [("get", "/ok", 200), ("post", "/ok", 200), ("get", "/missing", 404)])
def test_hsts_header_present(client, method, path, status):
    response = getattr(client, method)(path)
    assert response.status_code == status
    assert response.headers["Strict-Transport-Security"] == HSTS_HEADER_VALUE
