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

"""
Tests for assistant prompt variable mappings API.
"""

import uuid
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from codemie.rest_api.main import app
from codemie.rest_api.models.usage.assistant_prompt_variable_mapping import (
    AssistantPromptVariableMappingSQL,
    PromptVariableConfig,
)
from codemie.rest_api.models.assistant import Assistant, PromptVariable


@pytest.fixture
def test_client():
    return TestClient(app)


@pytest.fixture
def mock_assistant():
    assistant = MagicMock(spec=Assistant)
    assistant.id = str(uuid.uuid4())
    assistant.name = "Test Assistant"
    assistant.system_prompt = "Working on project {{project_name}} with team {{team_name}}"
    assistant.prompt_variables = [
        PromptVariable(key="project_name", description="Project name", default_value="Default Project"),
        PromptVariable(key="team_name", description="Team name", default_value="Default Team"),
    ]
    return assistant


@pytest.fixture
def mock_mapping_service():
    with patch(
        "codemie.rest_api.routers.assistant_prompt_variable_mapping.assistant_prompt_variable_mapping_service"
    ) as mock:
        mock.get_mapping.return_value = AssistantPromptVariableMappingSQL(
            id=str(uuid.uuid4()),
            assistant_id="test-assistant-id",
            user_id="test-user-id",
            variables_config=[
                PromptVariableConfig(variable_key="project_name", variable_value="Custom Project"),
                PromptVariableConfig(variable_key="team_name", variable_value="Custom Team"),
            ],
        )
        yield mock


@pytest.fixture
def mock_authenticate():
    with patch("codemie.rest_api.security.authentication.authenticate") as mock:
        mock.return_value.id = "test-user-id"
        mock.return_value.username = "test-user"
        mock.return_value.name = "Test User"
        mock.return_value.project_names = ["demo"]
        mock.return_value.admin_project_names = ["demo"]
        yield mock


@pytest.fixture(autouse=True)
def override_dependency(mock_authenticate):
    from codemie.rest_api.routers import assistant_prompt_variable_mapping

    app.dependency_overrides[assistant_prompt_variable_mapping.authenticate] = lambda: mock_authenticate.return_value
    yield
    app.dependency_overrides = {}


class TestAssistantPromptVariableMapping:
    """Tests for the assistant prompt variable mappings functionality"""

    def test_create_or_update_mappings(self, test_client, mock_authenticate, mock_mapping_service):
        """Test creating or updating prompt variable mappings"""
        with (
            patch(
                "codemie.rest_api.routers.assistant_prompt_variable_mapping._get_assistant_by_id_or_raise"
            ) as mock_get_assistant,
            patch(
                "codemie.rest_api.routers.assistant_prompt_variable_mapping._check_user_can_access_assistant"
            ) as mock_check_access,
        ):
            mock_get_assistant.return_value = MagicMock()

            response = test_client.post(
                "/v1/assistants/test-assistant-id/users/prompt-variables",
                json={
                    "variables_config": [
                        {"variable_key": "project_name", "variable_value": "Custom Project"},
                        {"variable_key": "team_name", "variable_value": "Custom Team"},
                    ]
                },
            )

            assert response.status_code == 200
            assert response.json()["message"] == "Prompt variable mappings created or updated successfully"
            mock_mapping_service.create_or_update_mapping.assert_called_once()
            mock_check_access.assert_called_once()

    def test_get_mappings(self, test_client, mock_authenticate, mock_mapping_service, mock_assistant):
        """Test retrieving prompt variable mappings"""
        with (
            patch(
                "codemie.rest_api.routers.assistant_prompt_variable_mapping._get_assistant_by_id_or_raise"
            ) as mock_get_assistant,
            patch(
                "codemie.rest_api.routers.assistant_prompt_variable_mapping._check_user_can_access_assistant"
            ) as mock_check_access,
        ):
            # Setup mock assistant with prompt_variables
            mock_assistant.prompt_variables = [
                PromptVariable(
                    key="project_name", description="Project name", default_value="Default Project", is_sensitive=False
                ),
                PromptVariable(
                    key="team_name", description="Team name", default_value="Default Team", is_sensitive=False
                ),
            ]
            mock_get_assistant.return_value = mock_assistant

            # Mock get_mapping_with_masked_values to return proper mapping
            mock_mapping_service.get_mapping_with_masked_values.return_value = AssistantPromptVariableMappingSQL(
                id=str(uuid.uuid4()),
                assistant_id="test-assistant-id",
                user_id="test-user-id",
                variables_config=[
                    PromptVariableConfig(
                        variable_key="project_name", variable_value="Custom Project", is_sensitive=False
                    ),
                    PromptVariableConfig(variable_key="team_name", variable_value="Custom Team", is_sensitive=False),
                ],
            )

            response = test_client.get("/v1/assistants/test-assistant-id/users/prompt-variables")

            assert response.status_code == 200
            assert response.json()["assistant_id"] == "test-assistant-id"
            assert response.json()["user_id"] == "test-user-id"
            assert len(response.json()["variables_config"]) == 2
            assert response.json()["variables_config"][0]["variable_key"] == "project_name"
            assert response.json()["variables_config"][0]["variable_value"] == "Custom Project"
            mock_check_access.assert_called_once()

    def test_get_mappings_with_sensitive_variables_masked(
        self, test_client, mock_authenticate, mock_mapping_service, mock_assistant
    ):
        """Test retrieving prompt variable mappings with sensitive values masked"""
        with (
            patch(
                "codemie.rest_api.routers.assistant_prompt_variable_mapping._get_assistant_by_id_or_raise"
            ) as mock_get_assistant,
            patch(
                "codemie.rest_api.routers.assistant_prompt_variable_mapping._check_user_can_access_assistant"
            ) as mock_check_access,
        ):
            # Setup mock assistant with sensitive prompt_variable
            mock_assistant.prompt_variables = [
                PromptVariable(key="api_key", description="API Key", default_value="secret123", is_sensitive=True),
                PromptVariable(
                    key="project_name", description="Project name", default_value="Default Project", is_sensitive=False
                ),
            ]
            mock_get_assistant.return_value = mock_assistant

            # Mock get_mapping_with_masked_values to return masked sensitive values
            mock_mapping_service.get_mapping_with_masked_values.return_value = AssistantPromptVariableMappingSQL(
                id=str(uuid.uuid4()),
                assistant_id="test-assistant-id",
                user_id="test-user-id",
                variables_config=[
                    PromptVariableConfig(variable_key="api_key", variable_value="**********", is_sensitive=True),
                    PromptVariableConfig(
                        variable_key="project_name", variable_value="Custom Project", is_sensitive=False
                    ),
                ],
            )

            response = test_client.get("/v1/assistants/test-assistant-id/users/prompt-variables")

            assert response.status_code == 200
            # Verify sensitive value is masked
            assert response.json()["variables_config"][0]["variable_key"] == "api_key"
            assert response.json()["variables_config"][0]["variable_value"] == "**********"
            assert response.json()["variables_config"][0]["is_sensitive"] is True
            # Verify non-sensitive value is not masked
            assert response.json()["variables_config"][1]["variable_key"] == "project_name"
            assert response.json()["variables_config"][1]["variable_value"] == "Custom Project"
            assert response.json()["variables_config"][1]["is_sensitive"] is False
            mock_check_access.assert_called_once()

    def test_get_system_prompt_with_user_variables(self, mock_assistant):
        """Test that get_system_prompt uses user-specific variable values"""
        with patch(
            "codemie.service.assistant.assistant_prompt_variable_mapping_service.assistant_prompt_variable_mapping_service.get_user_variable_values"
        ) as mock_get_vars:
            mock_get_vars.return_value = {"project_name": "User Project", "team_name": "User Team"}

            # Import the patched method from assistant_service
            from codemie.service.assistant_service import AssistantService
            from codemie.rest_api.models.assistant import AssistantBase

            # Call the method with a user_id
            assistant_base = AssistantBase(
                id="test-id",
                name="Test Assistant",
                description="Test description",
                system_prompt="Working on project {{project_name}} with team {{team_name}}",
                prompt_variables=[
                    PromptVariable(key="project_name", description="Project name", default_value="Default Project"),
                    PromptVariable(key="team_name", description="Team name", default_value="Default Team"),
                ],
            )

            # Test with user_id - should use user variables
            with patch.object(assistant_base, 'id', "test-id"):
                result = AssistantService.get_system_prompt(assistant_base, user_id="test-user-id")
                assert "User Project" in result
                assert "User Team" in result
                mock_get_vars.assert_called_once_with("test-id", "test-user-id")

            # Test with an empty user_id - should use defaults
            mock_get_vars.reset_mock()
            mock_get_vars.return_value = {}  # Empty dict for when user_id is None
            result = AssistantService.get_system_prompt(assistant_base, user_id=None)
            assert "Default Project" in result
            assert "Default Team" in result
