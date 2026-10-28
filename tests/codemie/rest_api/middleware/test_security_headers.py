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

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from codemie.rest_api.middleware.security_headers import SecurityHeadersMiddleware


def _client() -> TestClient:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.post("/json")
    def json_endpoint():
        return {"ok": True}

    @app.get("/stream")
    def stream_endpoint():
        return StreamingResponse(iter([b"a", b"b"]), media_type="text/plain")

    return TestClient(app)


def test_nosniff_on_json_response():
    response = _client().post("/json")
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_nosniff_on_streaming_response():
    response = _client().get("/stream")
    assert response.text == "ab"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_nosniff_on_error_response():
    response = _client().get("/missing")
    assert response.status_code == 404
    assert response.headers["X-Content-Type-Options"] == "nosniff"
