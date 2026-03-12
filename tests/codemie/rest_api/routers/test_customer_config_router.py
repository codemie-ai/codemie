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
from fastapi.testclient import TestClient
from unittest.mock import patch

from codemie.configs.customer_config import Component, ComponentSetting
from codemie.rest_api.main import app
from codemie.rest_api.routers.customer_config import router

app.include_router(router)

client = TestClient(app)


@pytest.fixture
def mock_customer_config():
    with patch("codemie.rest_api.routers.customer_config.customer_config") as mock:
        yield mock


def test_get_config_success(mock_customer_config):
    enabled_components = [
        Component(
            id="component1", settings=ComponentSetting(enabled=True, name="Test Component 1", url="http://test1.com")
        ),
        Component(
            id="component2", settings=ComponentSetting(enabled=True, name="Test Component 2", url="http://test2.com")
        ),
    ]

    mock_customer_config.get_enabled_components.return_value = enabled_components

    response = client.get("/v1/config")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2

    assert data[0] == {
        "id": "component1",
        "settings": {
            "enabled": True,
            "availableForExternal": True,
            "name": "Test Component 1",
            "url": "http://test1.com",
        },
    }

    assert data[1] == {
        "id": "component2",
        "settings": {
            "enabled": True,
            "availableForExternal": True,
            "name": "Test Component 2",
            "url": "http://test2.com",
        },
    }


def test_get_config_no_enabled_components(mock_customer_config):
    mock_customer_config.get_enabled_components.return_value = []

    response = client.get("/v1/config")

    assert response.status_code == 200
    assert response.json() == []


def test_get_config_components_with_minimal_settings(mock_customer_config):
    enabled_components = [Component(id="minimal-component", settings=ComponentSetting(enabled=True))]

    mock_customer_config.get_enabled_components.return_value = enabled_components

    response = client.get("/v1/config")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0] == {"id": "minimal-component", "settings": {"enabled": True, "availableForExternal": True}}


def test_get_config_additional_settings_fields(mock_customer_config):
    enabled_components = [
        Component(
            id="extended-component",
            settings=ComponentSetting(
                enabled=True, name="Extended Component", url="http://test.com", custom_field="custom_value"
            ),
        )
    ]

    mock_customer_config.get_enabled_components.return_value = enabled_components

    response = client.get("/v1/config")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1

    assert data[0]["id"] == "extended-component"
    assert data[0]["settings"]["enabled"] is True
    assert data[0]["settings"]["name"] == "Extended Component"
    assert data[0]["settings"]["url"] == "http://test.com"
    assert data[0]["settings"]["custom_field"] == "custom_value"


def test_get_applications(mock_customer_config):
    enabled_components = [
        Component(
            id="applications:app-component",
            settings=ComponentSetting(
                enabled=True,
                name="App Component",
                url="http://test.com",
                type="module",
                description="",
            ),
        ),
        Component(
            id="not-app:app-component",
            settings=ComponentSetting(enabled=True, name="App Component", url="http://test.com", type="module"),
        ),
    ]

    mock_customer_config.get_enabled_components.return_value = enabled_components

    response = client.get("/v1/applications")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1

    assert data[0]["slug"] == "app-component"
    assert data[0]["type"] == "module"
