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

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from codemie.rest_api.main import app
from codemie.rest_api.security.user import User

client = TestClient(app)


@pytest.fixture
def mock_headers():
    return {"Authorization": "Bearer testtoken"}


@pytest.fixture
def mock_admin_user():
    return User(id="user123", username="testuser", roles=["admin"])


@pytest.fixture
def mock_non_admin_user():
    return User(id="user123", username="testuser")


@pytest.fixture
def mock_create_payload():
    return {
        "name": "string",
        "service_location_url": "http://provider-mock.com",
        "configuration": {"auth_type": "Bearer"},
        "provided_toolkits": [
            {
                "name": "Math",
                "description": "math operations",
                "toolkit_config": {
                    "name": {
                        "description": "",
                        "type": "String",
                        "required": False,
                    }
                },
                "provided_tools": [
                    {
                        "name": "string",
                        "description": "",
                        "tool_result_type": "String",
                        "args_schema": {
                            "arg1": {
                                "type": "String",
                                "required": False,
                                "description": "",
                            },
                            "arg2": {
                                "type": "Number",
                                "required": True,
                                "description": "",
                            },
                        },
                        "tool_metadata": {},
                        "sync_invocation_supported": True,
                        "async_invocation_supported": True,
                    }
                ],
            }
        ],
    }


PROVIDER_PATH = "/v1/providers/1234"


@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
@patch("codemie.service.provider.ProviderService.index")
def test_list_providers(mock_index, mock_authenticate, mock_admin_user, mock_headers):
    mock_authenticate.return_value = mock_admin_user
    mock_index.return_value = [{"name": "Test Provider 1"}]

    response = client.get("/v1/providers", headers=mock_headers)

    assert response.status_code == 200
    assert response.json() == [{"name": "Test Provider 1"}]
    mock_index.assert_called_once()


@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
@patch("codemie.service.provider.ProviderService.get")
def test_get_provider(mock_get, mock_authenticate, mock_admin_user, mock_headers):
    mock_authenticate.return_value = mock_admin_user
    mock_get.return_value = {"name": "Test Provider 3"}

    response = client.get(PROVIDER_PATH, headers=mock_headers)

    assert response.status_code == 200
    assert response.json() == {"name": "Test Provider 3"}
    mock_get.assert_called_once()


@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
@patch("codemie.service.provider.ProviderService.create")
def test_create_provider(mock_create, mock_authenticate, mock_admin_user, mock_headers, mock_create_payload):
    mock_authenticate.return_value = mock_admin_user
    mock_create.return_value = "test_response"

    response = client.post("/v1/providers/", headers=mock_headers, json=mock_create_payload)
    assert response.status_code == 200
    assert response.json() == "test_response"
    mock_create.assert_called_once()


@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
@patch("codemie.service.provider.ProviderService.update")
def test_update_provider(mock_update, mock_authenticate, mock_admin_user, mock_headers, mock_create_payload):
    mock_authenticate.return_value = mock_admin_user
    mock_update.return_value = "test_response"

    response = client.put(PROVIDER_PATH, headers=mock_headers, json={"name": "test"})

    assert response.status_code == 200
    assert response.json() == "test_response"
    mock_update.assert_called_once()


@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
@patch("codemie.service.provider.ProviderService.delete")
def test_delete_provider(mock_delete, mock_authenticate, mock_admin_user, mock_headers, mock_create_payload):
    mock_authenticate.return_value = mock_admin_user
    mock_delete.return_value = True

    response = client.delete(PROVIDER_PATH, headers=mock_headers)

    assert response.status_code == 204
    mock_delete.assert_called_once()
