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

"""Tests for the /assistants/plugin_tools endpoint."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from codemie.enterprise.plugin import PluginToolkitUI
from codemie.rest_api.main import app
from codemie.rest_api.routers.assistant import router
from codemie.rest_api.security.user import User
from codemie_tools.base.models import ToolSet

app.include_router(router)
client = TestClient(app)


@pytest.fixture
def mock_user():
    """Create a mock user for testing."""
    return User(id="test-user-id", email="test@example.com", project_names=["test-project"])


@pytest.fixture
def mock_auth_header():
    """Mock authorization header."""
    return {"Authorization": "Bearer test_token"}


@pytest.fixture(autouse=True)
def mock_authenticate(mock_user):
    """Mock authentication dependency."""
    with patch("codemie.rest_api.routers.assistant.authenticate", return_value=mock_user) as mock:
        yield mock


@pytest.fixture
def mock_plugin_toolkit_ui():
    """Mock PluginToolkitUI with sample tools."""
    return PluginToolkitUI(
        toolkit=ToolSet.PLUGIN,
        settings_config=True,
        tools=[
            {"name": "_test_tool", "description": "Test tool description", "label": "Test Tool"},
            {"name": "_another_tool", "description": "Another tool description", "label": "Another Tool"},
        ],
    )


class TestGetPluginTools:
    """Test suite for GET /assistants/plugin_tools endpoint."""

    def test_get_plugin_tools_success(self, mock_auth_header, mock_authenticate, mock_plugin_toolkit_ui):
        """Test successful retrieval of plugin tools."""
        with patch(
            "codemie.service.tools.plugin_tools_info_service.PluginToolsInfoService.get_plugin_toolkit_info"
        ) as mock_get_info:
            mock_get_info.return_value = mock_plugin_toolkit_ui

            response = client.get("/v1/assistants/plugin_tools", headers=mock_auth_header)

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["toolkit"] == "Plugin"
            assert data[0]["settings_config"] is True
            assert len(data[0]["tools"]) == 2
            assert data[0]["tools"][0]["name"] == "_test_tool"
            assert data[0]["tools"][1]["name"] == "_another_tool"

            call_args = mock_get_info.call_args
            assert call_args[1]["plugin_setting_id"] is None
            assert call_args[1]["project_name"] is None
            assert call_args[1]["user"] is not None

    def test_get_plugin_tools_with_setting_id(self, mock_auth_header, mock_authenticate, mock_plugin_toolkit_ui):
        """Test retrieval of plugin tools with specific setting ID."""
        setting_id = "test-setting-123"

        with patch(
            "codemie.service.tools.plugin_tools_info_service.PluginToolsInfoService.get_plugin_toolkit_info"
        ) as mock_get_info:
            mock_get_info.return_value = mock_plugin_toolkit_ui

            response = client.get(
                f"/v1/assistants/plugin_tools?plugin_setting_id={setting_id}", headers=mock_auth_header
            )

            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["toolkit"] == "Plugin"

            call_args = mock_get_info.call_args
            assert call_args[1]["plugin_setting_id"] == setting_id
            assert call_args[1]["project_name"] is None
            assert call_args[1]["user"] is not None

    def test_get_plugin_tools_error_handling(self, mock_auth_header, mock_authenticate):
        """Test endpoint handles service errors properly."""
        from codemie.service.tools.plugin_tools_info_service import PluginToolsInfoServiceError

        with patch(
            "codemie.service.tools.plugin_tools_info_service.PluginToolsInfoService.get_plugin_toolkit_info"
        ) as mock_get_info:
            mock_get_info.side_effect = PluginToolsInfoServiceError("No plugin credentials found")

            response = client.get("/v1/assistants/plugin_tools", headers=mock_auth_header)

            assert response.status_code == 422
            data = response.json()
            assert "No plugin credentials found" in data["error"]["message"]

    def test_get_plugin_tools_requires_authentication(self):
        """Test endpoint requires authentication."""
        response = client.get("/v1/assistants/plugin_tools")

        assert response.status_code in [401, 403]

    def test_get_plugin_tools_with_empty_tools_list(self, mock_auth_header, mock_authenticate):
        """Test endpoint with toolkit containing empty tools list."""
        empty_toolkit = PluginToolkitUI(toolkit=ToolSet.PLUGIN, settings_config=True, tools=[])

        with patch(
            "codemie.service.tools.plugin_tools_info_service.PluginToolsInfoService.get_plugin_toolkit_info"
        ) as mock_get_info:
            mock_get_info.return_value = empty_toolkit

            response = client.get("/v1/assistants/plugin_tools", headers=mock_auth_header)

            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["toolkit"] == "Plugin"
            assert data[0]["tools"] == []

    def test_get_plugin_tools_response_model_structure(
        self, mock_auth_header, mock_authenticate, mock_plugin_toolkit_ui
    ):
        """Test response structure matches ToolKit model."""
        with patch(
            "codemie.service.tools.plugin_tools_info_service.PluginToolsInfoService.get_plugin_toolkit_info"
        ) as mock_get_info:
            mock_get_info.return_value = mock_plugin_toolkit_ui

            response = client.get("/v1/assistants/plugin_tools", headers=mock_auth_header)

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)

            toolkit = data[0]
            assert "toolkit" in toolkit
            assert "tools" in toolkit
            assert isinstance(toolkit["tools"], list)

            if toolkit["tools"]:
                tool = toolkit["tools"][0]
                assert "name" in tool
                assert "description" in tool
                assert "label" in tool

    def test_get_plugin_tools_with_multiple_query_params(
        self, mock_auth_header, mock_authenticate, mock_plugin_toolkit_ui
    ):
        """Test endpoint handles query parameters correctly."""
        setting_id = "setting-456"

        with patch(
            "codemie.service.tools.plugin_tools_info_service.PluginToolsInfoService.get_plugin_toolkit_info"
        ) as mock_get_info:
            mock_get_info.return_value = mock_plugin_toolkit_ui

            response = client.get(
                f"/v1/assistants/plugin_tools?plugin_setting_id={setting_id}&extra_param=ignored",
                headers=mock_auth_header,
            )

            assert response.status_code == 200
            call_args = mock_get_info.call_args
            assert call_args[1]["plugin_setting_id"] == setting_id
            assert call_args[1]["project_name"] is None
            assert call_args[1]["user"] is not None

    def test_get_plugin_tools_validates_response_by_alias(self, mock_auth_header, mock_authenticate):
        """Test response is serialized using field aliases."""
        toolkit_with_aliases = PluginToolkitUI(
            toolkit=ToolSet.PLUGIN,
            settings_config=True,
            tools=[{"name": "_tool", "description": "Tool desc", "label": "Tool Label"}],
        )

        with patch(
            "codemie.service.tools.plugin_tools_info_service.PluginToolsInfoService.get_plugin_toolkit_info"
        ) as mock_get_info:
            mock_get_info.return_value = toolkit_with_aliases

            response = client.get("/v1/assistants/plugin_tools", headers=mock_auth_header)

            assert response.status_code == 200
            data = response.json()
            assert data[0]["toolkit"] == "Plugin"
