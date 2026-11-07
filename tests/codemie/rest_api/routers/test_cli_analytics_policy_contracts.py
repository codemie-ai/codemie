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

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codemie.rest_api.routers.cli_analytics as router_module
from codemie.rest_api.security.authentication import authenticate

URL = "/v1/analytics/cli-analytics/classification-policy"


def _get(enabled: bool) -> dict:
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[authenticate] = lambda: object()
    cfg = MagicMock(CLI_ANALYTICS_PUBLICATION_CONTRACTS_ENABLED=enabled)
    with (
        patch.object(router_module, "_ensure_enabled", return_value=None),
        patch.object(router_module, "corporate_organizations", return_value=["Acme"]),
        patch.object(router_module, "config", cfg),
    ):
        return TestClient(app).get(URL).json()


def test_contracts_advertised_only_when_enabled():
    assert _get(True)["publication_contracts"] == ["1"]


def test_contracts_key_absent_when_disabled():
    body = _get(False)
    assert "publication_contracts" not in body
    assert body["organizations"] == ["Acme"]


@pytest.mark.parametrize("enabled", [True, False])
def test_version_unchanged_by_setting(enabled):
    assert _get(enabled)["version"] == _get(not enabled)["version"]
