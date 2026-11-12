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

"""Classification-policy advertisement of publication contracts (EPMCDME-16849)."""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import codemie.rest_api.routers.cli_analytics as router_module
from codemie.rest_api.security.authentication import authenticate

URL = "/v1/analytics/cli-analytics/classification-policy"


def _get() -> dict:
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[authenticate] = lambda: object()
    with (
        patch.object(router_module, "_ensure_enabled", return_value=None),
        patch.object(router_module, "corporate_organizations", return_value=["Acme"]),
    ):
        return TestClient(app).get(URL).json()


def test_contracts_always_advertised():
    body = _get()
    assert body["publication_contracts"] == ["1"]
    assert body["organizations"] == ["Acme"]
