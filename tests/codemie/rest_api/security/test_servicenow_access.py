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

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import servicenow_integration_access_only
from codemie.rest_api.security.servicenow_access import (
    enforce_servicenow_scope,
    is_servicenow_service_account,
)
from codemie.rest_api.security.user import User

CONFIG_PATH = "codemie.rest_api.security.servicenow_access.config"
ROUTES_PATH = "codemie.rest_api.security.servicenow_access._allowed_routes"
SERVICE_ACCOUNT_ID = "sn-sa-1"
ALLOWED = frozenset(
    {
        ("GET", "/v1/integrations/servicenow/projects"),
        ("POST", "/v1/integrations/servicenow/budgets"),
    }
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def servicenow_config():
    with patch(CONFIG_PATH) as mock_config:
        mock_config.SERVICENOW_INTEGRATION_ENABLED = True
        mock_config.SERVICENOW_SERVICE_ACCOUNT_ID = SERVICE_ACCOUNT_ID
        yield mock_config


@pytest.fixture
def allowed_routes():
    with patch(ROUTES_PATH, return_value=ALLOWED) as mock_routes:
        yield mock_routes


def _sa(user_id=SERVICE_ACCOUNT_ID, user_type="regular", **extra):
    return User(id=user_id, email="sn@svc.example.com", user_type=user_type, project_names=[], **extra)


def _request(method, path):
    request = MagicMock()
    request.method = method
    request.scope = {"route": SimpleNamespace(path=path)} if path else {}
    return request


class TestIsServiceNowServiceAccount:
    @pytest.mark.parametrize("user_type", ["regular", "service_account", None])
    def test_enabled_matching_id_ignores_user_type(self, servicenow_config, user_type):
        assert is_servicenow_service_account(_sa(user_type=user_type)) is True

    def test_disabled_integration(self, servicenow_config):
        servicenow_config.SERVICENOW_INTEGRATION_ENABLED = False

        assert is_servicenow_service_account(_sa()) is False

    def test_id_mismatch(self, servicenow_config):
        assert is_servicenow_service_account(_sa(user_id="someone-else")) is False

    def test_service_account_type_with_other_id(self, servicenow_config):
        assert is_servicenow_service_account(_sa(user_id="other-sa", user_type="service_account")) is False

    def test_empty_configured_id_never_matches_empty_user_id(self, servicenow_config):
        servicenow_config.SERVICENOW_SERVICE_ACCOUNT_ID = ""

        assert is_servicenow_service_account(_sa(user_id="")) is False


class TestEnforceServiceNowScope:
    def test_allowed_route_passes(self, servicenow_config, allowed_routes):
        enforce_servicenow_scope(_sa(), _request("POST", "/v1/integrations/servicenow/budgets"))

    def test_same_path_other_method_is_403(self, servicenow_config, allowed_routes):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            enforce_servicenow_scope(_sa(), _request("DELETE", "/v1/integrations/servicenow/budgets"))

        assert exc_info.value.code == 403

    def test_other_route_is_403(self, servicenow_config, allowed_routes):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            enforce_servicenow_scope(_sa(), _request("GET", "/v1/admin/project-budget-groups"))

        assert exc_info.value.code == 403

    def test_missing_route_is_403(self, servicenow_config, allowed_routes):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            enforce_servicenow_scope(_sa(), _request("GET", None))

        assert exc_info.value.code == 403

    def test_other_users_are_not_checked(self, servicenow_config, allowed_routes):
        regular = User(id="u1", username="regular", project_names=[])

        enforce_servicenow_scope(regular, _request("GET", "/v1/admin/project-budget-groups"))

        allowed_routes.assert_not_called()

    def test_disabled_integration_is_not_checked(self, servicenow_config, allowed_routes):
        servicenow_config.SERVICENOW_INTEGRATION_ENABLED = False

        enforce_servicenow_scope(_sa(), _request("GET", "/v1/admin/project-budget-groups"))

        allowed_routes.assert_not_called()


class TestServiceNowIntegrationAccessOnly:
    @staticmethod
    def _guard_request(user):
        request = MagicMock()
        request.state.user = user
        return request

    @pytest.mark.anyio
    @pytest.mark.parametrize("user_type", ["regular", "service_account"])
    async def test_service_account_passes(self, servicenow_config, user_type):
        assert await servicenow_integration_access_only(self._guard_request(_sa(user_type=user_type))) is None

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "user",
        [
            User(id="u1", username="regular", project_names=[]),
            User(id="admin-1", username="admin", is_admin=True, project_names=[]),
            User(id="other-sa", user_type="service_account", project_names=[]),
        ],
        ids=["regular", "admin", "other_service_account"],
    )
    async def test_other_callers_are_403(self, servicenow_config, user):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await servicenow_integration_access_only(self._guard_request(user))

        assert exc_info.value.code == 403
