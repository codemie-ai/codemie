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

"""The guarded app must contain every conditional router and be built without any outbound contact."""

from __future__ import annotations

from typing import Any

import pytest

from tests.codemie.rest_api.router_guards.guarded_app import load_guarded_app_dump


@pytest.fixture(scope="module")
def dump() -> dict[str, Any]:
    return load_guarded_app_dump()


@pytest.mark.parametrize(
    ("method", "path", "enabled_by"),
    [
        pytest.param("get", "/v1/index/{index_id}", "index router (Elasticsearch configured)", id="index"),
        pytest.param("get", "/v1/admin/budgets/{budgetId}", "budget_router (LiteLLM + budget check)", id="budget"),
        pytest.param("get", "/v1/admin/users/{user_id}", "user_management_router (ENABLE_USER_MANAGEMENT)", id="users"),
        pytest.param("post", "/v1/local-auth/login", "local_auth_router (IDP_PROVIDER=local)", id="local-auth"),
        pytest.param("post", "/v1/workflows/generate", "WORKFLOW_GENERATION_ENABLED", id="workflow-generation"),
    ],
)
def test_conditional_route_is_in_the_guarded_openapi(dump, method, path, enabled_by):
    assert method in dump["openapi"]["paths"].get(path, {}), f"{method.upper()} {path} missing ({enabled_by})"


def test_conditional_routes_are_registered_on_the_app(dump):
    registered = {(method, route["path"]) for route in dump["routes"] for method in route["methods"]}

    assert ("GET", "/v1/index/{index_id}") in registered
    assert ("POST", "/v1/local-auth/login") in registered


def test_building_the_app_attempts_no_network_connection(dump):
    assert dump["contacts_attempted"] == []
