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

from codemie.rest_api.main import app
from codemie.rest_api.routers.tool import router
from codemie.rest_api.security.user import User
from codemie.service.llm_service.llm_service import LLMService

app.include_router(router)
client = TestClient(app)


@pytest.fixture
def mock_user():
    return User(id="test_user", email="test@example.com")


@pytest.fixture
def mock_auth_header():
    return {"Authorization": "Bearer test_token"}


@pytest.fixture
def mock_authenticate():
    with patch("codemie.rest_api.routers.tool.authenticate") as mock:
        mock.return_value = User(id="test_user", email="test@example.com")
        yield mock


# New test for get_tools endpoint
def test_get_tools_success(mock_auth_header, mock_authenticate):
    mock_toolkits = [
        {
            "toolkit": "Test Toolkit",
            "tools": [{"name": "tool1", "label": "Tool 1"}, {"name": "tool2", "label": "Tool 2"}],
        }
    ]

    with patch("codemie.rest_api.routers.tool.ToolsInfoService") as mock_service:
        mock_service.get_tools_info.return_value = mock_toolkits

        response = client.get("/v1/tools", headers=mock_auth_header)

        assert response.status_code == 200
        assert response.json() == ["tool1", "tool2"]
        mock_service.get_tools_info.assert_called_once()


def test_get_tools_error(mock_auth_header, mock_authenticate):
    with patch("codemie.rest_api.routers.tool.ToolsInfoService") as mock_service:
        mock_service.get_tools_info.side_effect = Exception("Failed to get tools")

        response = client.get("/v1/tools", headers=mock_auth_header)

        assert response.status_code == 500
        assert "Failed to retrieve tools information" in str(response.json())


def test_get_tools_configs_success(mock_auth_header, mock_authenticate):
    """Test successful retrieval of tool configurations."""
    mock_configs = [
        {
            "gitlabconfig": {
                "class": "codemie_tools.git.models.GitLabConfig",
                "gitlab_url": {
                    "description": "GitLab instance URL",
                    "type": "string",
                    "placeholder": "https://gitlab.example.com",
                },
                "access_token": {
                    "description": "GitLab personal access token",
                    "type": "string",
                    "sensitive": True,
                },
            }
        },
        {
            "jiraconfig": {
                "class": "codemie_tools.core.project_management.jira.models.JiraConfig",
                "jira_url": {
                    "description": "Jira instance URL",
                    "type": "string",
                    "placeholder": "https://jira.example.com",
                },
                "api_token": {"description": "Jira API token", "type": "string", "sensitive": True},
            }
        },
    ]

    with patch("codemie.rest_api.routers.tool.toolkit_provider") as mock_provider:
        mock_provider.get_available_tools_configs_info.return_value = mock_configs

        response = client.get("/v1/tools/configs", headers=mock_auth_header)

        assert response.status_code == 200
        assert response.json() == mock_configs
        mock_provider.get_available_tools_configs_info.assert_called_once()


def test_get_tools_configs_empty_list(mock_auth_header, mock_authenticate):
    """Test when no tool configurations are available."""
    with patch("codemie.rest_api.routers.tool.toolkit_provider") as mock_provider:
        mock_provider.get_available_tools_configs_info.return_value = []

        response = client.get("/v1/tools/configs", headers=mock_auth_header)

        assert response.status_code == 200
        assert response.json() == []
        mock_provider.get_available_tools_configs_info.assert_called_once()


def test_get_tools_configs_error(mock_auth_header, mock_authenticate):
    """Test error handling when toolkit_provider fails."""
    with patch("codemie.rest_api.routers.tool.toolkit_provider") as mock_provider:
        mock_provider.get_available_tools_configs_info.side_effect = Exception("Failed to load configurations")

        response = client.get("/v1/tools/configs", headers=mock_auth_header)

        assert response.status_code == 500
        response_data = response.json()
        assert "Failed to retrieve tool configurations" in str(response_data)
        assert "Failed to load configurations" in str(response_data)


def test_invoke_tool_success(mock_auth_header, mock_authenticate):
    with patch("codemie.rest_api.routers.tool.ToolExecutionService") as mock_service:
        mock_service.invoke.return_value = "Tool execution successful"
        request_data = {
            "tool_args": {"arg1": "value1"},
            "tool_attributes": {},
            "project": "test_project",
            "llm_model": LLMService.BASE_NAME_GPT_41_MINI,
        }

        response = client.post("/v1/tools/test_tool/invoke", headers=mock_auth_header, json=request_data)

        assert response.status_code == 200
        response_data = response.json()
        assert response_data["output"] == "Tool execution successful"
        assert response_data["error"] is None


def test_invoke_tool_error(mock_auth_header, mock_authenticate):
    with patch("codemie.rest_api.routers.tool.ToolExecutionService") as mock_service:
        mock_service.invoke.side_effect = Exception("Tool execution failed")
        request_data = {
            "tool_args": {"arg1": "value1"},
            "tool_attributes": {},
            "project": "test_project",
            "llm_model": LLMService.BASE_NAME_GPT_41_MINI,
        }

        response = client.post("/v1/tools/test_tool/invoke", headers=mock_auth_header, json=request_data)

        assert response.status_code == 200
        response_data = response.json()
        assert response_data["output"] is None
        assert response_data["error"] == "Tool execution failed"


def test_invoke_tool_with_empty_request(mock_auth_header, mock_authenticate):
    response = client.post("/v1/tools/test_tool/invoke", headers=mock_auth_header, json={})
    assert response.status_code == 422


def test_invoke_tool_with_invalid_request(mock_auth_header, mock_authenticate):
    request_data = {"invalid_field": "value"}
    response = client.post("/v1/tools/test_tool/invoke", headers=mock_auth_header, json=request_data)
    assert response.status_code == 422


def test_get_tool_schema_success(mock_auth_header, mock_authenticate):
    from codemie.service.tools.discovery import FormattedToolSchema

    formatted_schema = FormattedToolSchema(
        tool_name="test_tool",
        creds_schema={"api_key": {"type": "str", "required": True}, "base_url": {"type": "str", "required": False}},
        args_schema={"method": {"type": "str", "required": True}},
    )

    with patch("codemie.rest_api.routers.tool.ToolDiscoveryService") as mock_finder:
        mock_finder.get_formatted_tool_schema.return_value = formatted_schema

        response = client.get("/v1/tools/test_tool/schema", headers=mock_auth_header)

        assert response.status_code == 200
        response_data = response.json()
        assert response_data["tool_name"] == "test_tool"
        assert "creds_schema" in response_data
        assert "args_schema" in response_data
        assert response_data["creds_schema"]["api_key"]["type"] == "str"
        assert response_data["creds_schema"]["api_key"]["required"] is True
        assert response_data["creds_schema"]["base_url"]["type"] == "str"
        assert response_data["creds_schema"]["base_url"]["required"] is False
        assert response_data["args_schema"]["method"]["type"] == "str"
        assert response_data["args_schema"]["method"]["required"] is True


def test_get_tool_schema_tool_not_found(mock_auth_header, mock_authenticate):
    with patch("codemie.rest_api.routers.tool.ToolDiscoveryService") as mock_finder:
        mock_finder.get_formatted_tool_schema.return_value = None

        response = client.get("/v1/tools/nonexistent_tool/schema", headers=mock_auth_header)

        assert response.status_code == 404
        response_data = response.json()
        assert "Tool 'nonexistent_tool' not found" in str(response_data)


def test_get_tool_schema_no_schema_available(mock_auth_header, mock_authenticate):
    from codemie.service.tools.discovery import FormattedToolSchema

    formatted_schema = FormattedToolSchema(tool_name="test_tool", creds_schema={}, args_schema={})

    with patch("codemie.rest_api.routers.tool.ToolDiscoveryService") as mock_finder:
        mock_finder.get_formatted_tool_schema.return_value = formatted_schema

        response = client.get("/v1/tools/test_tool/schema", headers=mock_auth_header)

        assert response.status_code == 200
        response_data = response.json()
        assert response_data == {"tool_name": "test_tool", "creds_schema": {}, "args_schema": {}}


def test_get_tool_schema_with_complex_type(mock_auth_header, mock_authenticate):
    from codemie.service.tools.discovery import FormattedToolSchema

    formatted_schema = FormattedToolSchema(
        tool_name="test_tool", creds_schema={"complex_field": {"type": "ComplexType", "required": True}}, args_schema={}
    )

    with patch("codemie.rest_api.routers.tool.ToolDiscoveryService") as mock_finder:
        mock_finder.get_formatted_tool_schema.return_value = formatted_schema

        response = client.get("/v1/tools/test_tool/schema", headers=mock_auth_header)

        assert response.status_code == 200
        response_data = response.json()
        assert response_data["creds_schema"]["complex_field"]["type"] == "ComplexType"
