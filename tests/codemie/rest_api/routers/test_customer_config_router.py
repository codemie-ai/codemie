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
from unittest.mock import AsyncMock, patch

from codemie.configs.customer_config import Component, ComponentSetting
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import app
from codemie.rest_api.routers.customer_config import router

app.include_router(router)

client = TestClient(app)


@pytest.fixture
def mock_resolved_components():
    """Stand in for the resolver the router reads through.

    These cases assert the response contract, not the source the response is built from.
    """
    with patch(
        "codemie.rest_api.routers.customer_config.resolve_components",
        new_callable=AsyncMock,
    ) as mock:
        yield mock


def test_get_config_success(mock_resolved_components):
    enabled_components = [
        Component(
            id="component1", settings=ComponentSetting(enabled=True, name="Test Component 1", url="http://test1.com")
        ),
        Component(
            id="component2", settings=ComponentSetting(enabled=True, name="Test Component 2", url="http://test2.com")
        ),
    ]

    mock_resolved_components.return_value = enabled_components

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


def test_get_config_no_enabled_components(mock_resolved_components):
    mock_resolved_components.return_value = []

    response = client.get("/v1/config")

    assert response.status_code == 200
    assert response.json() == []


def test_get_config_components_with_minimal_settings(mock_resolved_components):
    enabled_components = [Component(id="minimal-component", settings=ComponentSetting(enabled=True))]

    mock_resolved_components.return_value = enabled_components

    response = client.get("/v1/config")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0] == {"id": "minimal-component", "settings": {"enabled": True, "availableForExternal": True}}


def test_get_config_additional_settings_fields(mock_resolved_components):
    enabled_components = [
        Component(
            id="extended-component",
            settings=ComponentSetting(
                enabled=True, name="Extended Component", url="http://test.com", custom_field="custom_value"
            ),
        )
    ]

    mock_resolved_components.return_value = enabled_components

    response = client.get("/v1/config")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1

    assert data[0]["id"] == "extended-component"
    assert data[0]["settings"]["enabled"] is True
    assert data[0]["settings"]["name"] == "Extended Component"
    assert data[0]["settings"]["url"] == "http://test.com"
    assert data[0]["settings"]["custom_field"] == "custom_value"


def test_get_applications(mock_resolved_components):
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

    mock_resolved_components.return_value = enabled_components

    response = client.get("/v1/applications")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1

    assert data[0]["slug"] == "app-component"
    assert data[0]["type"] == "module"


def test_get_config_with_idp_provider(mock_resolved_components):
    """Test that IDP provider feature component is returned correctly"""
    enabled_components = [
        Component(id="component1", settings=ComponentSetting(enabled=True, name="Test Component 1")),
        Component(
            id="idpProvider",
            settings=ComponentSetting(enabled=True, value="keycloak"),
        ),
    ]

    mock_resolved_components.return_value = enabled_components

    response = client.get("/v1/config")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2

    # Find the IDP provider component
    idp_component = next((c for c in data if c["id"] == "idpProvider"), None)
    assert idp_component is not None
    assert idp_component["settings"]["enabled"] is True
    assert idp_component["settings"]["value"] == "keycloak"


def test_get_config_with_mcp_auth_origin(mock_resolved_components):
    enabled_components = [
        Component(id="component1", settings=ComponentSetting(enabled=True, name="Test Component 1")),
        Component(
            id="mcpAuthOrigin",
            settings=ComponentSetting(enabled=True, value="https://codemie.example.com"),
        ),
    ]

    mock_resolved_components.return_value = enabled_components

    response = client.get("/v1/config")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2

    mcp_component = next((c for c in data if c["id"] == "mcpAuthOrigin"), None)
    assert mcp_component is not None
    assert mcp_component["settings"]["enabled"] is True
    assert mcp_component["settings"]["value"] == "https://codemie.example.com"


def test_get_config_with_allowed_image_domains(mock_resolved_components):
    """Configured image allow-list is exposed verbatim to every user, external included."""
    enabled_components = [
        Component(id="component1", settings=ComponentSetting(enabled=True, name="Test Component 1")),
        Component(
            id="allowedImageDomains",
            settings=ComponentSetting(enabled=True, value="raw.githubusercontent.com,.example.com,cdn.customer.io"),
        ),
    ]

    mock_resolved_components.return_value = enabled_components

    response = client.get("/v1/config")

    assert response.status_code == 200
    data = response.json()

    domains_component = next((c for c in data if c["id"] == "allowedImageDomains"), None)
    assert domains_component is not None
    assert domains_component["settings"]["enabled"] is True
    assert domains_component["settings"]["value"] == "raw.githubusercontent.com,.example.com,cdn.customer.io"
    # Not filtered out for external users
    assert domains_component["settings"]["availableForExternal"] is True


def test_get_config_with_unset_allowed_image_domains(mock_resolved_components):
    """Unconfigured allow-list returns an empty value instead of a default domain list."""
    enabled_components = [
        Component(id="allowedImageDomains", settings=ComponentSetting(enabled=True, value="")),
    ]

    mock_resolved_components.return_value = enabled_components

    response = client.get("/v1/config")

    assert response.status_code == 200
    data = response.json()

    domains_component = next((c for c in data if c["id"] == "allowedImageDomains"), None)
    assert domains_component is not None
    assert domains_component["settings"]["value"] == ""


# --- Dynamic customer configuration ---


from codemie.rest_api.security.authentication import (  # noqa: E402
    authenticate,
    require_customer_config_write,
)
from codemie.rest_api.security.user import User  # noqa: E402
from codemie.rest_api.routers import customer_config as customer_config_router  # noqa: E402


@pytest.fixture
def admin_user():
    return User(
        id="admin-123",
        username="admin",
        name="Admin User",
        email="admin@example.com",
        project_names=["project1"],
        admin_project_names=["project1"],
        knowledge_bases=["kb1"],
        user_type="admin",
        is_admin=True,
    )


@pytest.fixture
def as_admin(admin_user):
    app.dependency_overrides[authenticate] = lambda: admin_user
    app.dependency_overrides[require_customer_config_write] = lambda: None
    yield
    app.dependency_overrides = {}


@pytest.fixture
def declared_settings():
    with patch.object(customer_config_router, "list_settings", new=AsyncMock()) as mock:
        yield mock


def test_declarations_endpoint_returns_fields_value_and_marker(as_admin, declared_settings):
    declared_settings.return_value = [
        {
            "component_id": "chatDisclaimer",
            "label": "Chat disclaimer",
            "description": None,
            "overridden": True,
            "value": {"enabled": True, "text": "Mind the gap"},
            "fields": [
                {
                    "name": "enabled",
                    "type": "switch",
                    "label": "Show disclaimer",
                    "description": None,
                    "required": False,
                    "max_length": None,
                    "markup": "plain",
                }
            ],
        }
    ]

    response = client.get("/v1/config/declarations")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["component_id"] == "chatDisclaimer"
    assert body[0]["overridden"] is True
    assert body[0]["value"] == {"enabled": True, "text": "Mind the gap"}
    assert body[0]["fields"][0]["type"] == "switch"


def test_declarations_endpoint_returns_an_empty_list_for_an_empty_registry(as_admin, declared_settings):
    declared_settings.return_value = []

    response = client.get("/v1/config/declarations")

    assert response.status_code == 200
    assert response.json() == []


def test_declarations_endpoint_is_gated_by_the_same_guard_as_writes():
    """Read and write share one dependency so a later RBAC repoint cannot split them."""

    async def deny():
        raise ExtendedHTTPException(code=403, message="Forbidden", details="nope")

    app.dependency_overrides[authenticate] = lambda: None
    app.dependency_overrides[require_customer_config_write] = deny
    try:
        response = client.get("/v1/config/declarations")
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 403


def test_put_saves_the_setting(as_admin):
    saver = AsyncMock(return_value={"enabled": True, "text": "Mind the gap"})

    with patch.object(customer_config_router, "save_setting", new=saver):
        response = client.put(
            "/v1/config/declarations/chatDisclaimer",
            json={"settings": {"enabled": True, "text": "Mind the gap"}},
        )

    assert response.status_code == 200
    assert response.json() == {"component_id": "chatDisclaimer", "settings": {"enabled": True, "text": "Mind the gap"}}
    assert saver.await_args.args[0] == "chatDisclaimer"


def test_put_rejects_an_undeclared_component(as_admin):
    failing = AsyncMock(
        side_effect=ExtendedHTTPException(code=404, message="Setting not found", details="not declared")
    )

    with patch.object(customer_config_router, "save_setting", new=failing):
        response = client.put("/v1/config/declarations/features:webSearch", json={"settings": {"enabled": True}})

    assert response.status_code == 404


def test_put_requires_write_permission(admin_user):
    async def deny():
        raise ExtendedHTTPException(code=403, message="Forbidden", details="nope")

    app.dependency_overrides[authenticate] = lambda: admin_user
    app.dependency_overrides[require_customer_config_write] = deny
    try:
        response = client.put("/v1/config/declarations/chatDisclaimer", json={"settings": {"enabled": True}})
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 403


def test_delete_resets_the_setting(as_admin):
    resetter = AsyncMock()

    with patch.object(customer_config_router, "reset_setting", new=resetter):
        response = client.delete("/v1/config/declarations/chatDisclaimer")

    assert response.status_code == 204
    assert resetter.await_args.args[0] == "chatDisclaimer"


def test_get_config_uses_the_resolver(as_admin):
    resolver = AsyncMock(
        return_value=[Component(id="chatDisclaimer", settings=ComponentSetting(enabled=True, text="hello"))]
    )

    with patch.object(customer_config_router, "resolve_components", new=resolver):
        response = client.get("/v1/config")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "chatDisclaimer"
    resolver.assert_awaited_once()
