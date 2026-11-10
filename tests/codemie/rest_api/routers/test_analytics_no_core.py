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

"""Baseline (EPMCDME-16884): Analytics, metrics and cli_analytics endpoints never 5xx in Standalone
with no provider settings and Elasticsearch unconfigured."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers import analytics, cli_analytics, metrics
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

PROVIDER_SETTINGS = ("AWS_BEDROCK_REGION", "AZURE_OPENAI_URL", "AZURE_OPENAI_API_KEY", "GOOGLE_VERTEXAI_REGION")


@pytest.fixture
def client():
    app = FastAPI()
    for module in (analytics, metrics, cli_analytics):
        app.include_router(module.router)
    # same mapping as the handler in main.py, which is too heavy to import here
    app.add_exception_handler(ExtendedHTTPException, lambda _r, exc: JSONResponse({}, status_code=exc.code))
    app.dependency_overrides[authenticate] = lambda: User(id="u-1", email="a@example.com", username="a", is_admin=True)
    with patch.object(config, "STANDALONE", True), patch.object(config, "ELASTIC_URL", "", create=True):
        for name in PROVIDER_SETTINGS:
            patch.object(config, name, "", create=True).start()
        try:
            yield TestClient(app, raise_server_exceptions=False)
        finally:
            patch.stopall()


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/v1/analytics/summaries", None),
        ("GET", "/v1/analytics/cli-summary", None),
        ("GET", "/v1/analytics/users-activity", None),
        ("GET", "/v1/analytics/cli-analytics/classification-policy", None),
        ("POST", "/v1/metrics", {"name": "x", "attributes": {}}),
    ],
)
def test_no_5xx_in_standalone_without_core(client, method, path, body):
    response = client.request(method, path, json=body)
    assert response.status_code < 500, response.text
