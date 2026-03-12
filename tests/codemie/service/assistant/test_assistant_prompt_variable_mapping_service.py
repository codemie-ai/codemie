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
Tests for the assistant prompt variable mapping service
"""

import uuid
from unittest.mock import MagicMock

import pytest

from codemie.rest_api.models.usage.assistant_prompt_variable_mapping import (
    AssistantPromptVariableMappingSQL,
    PromptVariableConfig,
)
from codemie.service.assistant.assistant_prompt_variable_mapping_service import AssistantPromptVariableMappingService


class TestAssistantPromptVariableMappingService:
    """Tests for the assistant prompt variable mapping service"""

    @pytest.fixture
    def mock_repository(self):
        return MagicMock()

    @pytest.fixture
    def service(self, mock_repository):
        return AssistantPromptVariableMappingService(repository=mock_repository)

    @pytest.fixture
    def assistant_id(self):
        return str(uuid.uuid4())

    @pytest.fixture
    def user_id(self):
        return str(uuid.uuid4())

    @pytest.fixture
    def variables_config(self):
        return [
            PromptVariableConfig(variable_key="project_name", variable_value="Custom Project", is_sensitive=False),
            PromptVariableConfig(variable_key="team_name", variable_value="Custom Team", is_sensitive=False),
        ]

    def test_create_or_update_mapping(self, service, mock_repository, assistant_id, user_id, variables_config):
        """Test creating or updating a mapping"""
        service.create_or_update_mapping(assistant_id, user_id, variables_config)

        # Verify repository method was called with correct parameters
        mock_repository.create_or_update_mapping.assert_called_once()
        args, _ = mock_repository.create_or_update_mapping.call_args
        assert args[0] == assistant_id
        assert args[1] == user_id
        assert len(args[2]) == 2
        assert args[2][0].variable_key == "project_name"
        assert args[2][0].variable_value == "Custom Project"

    def test_get_mapping(self, service, mock_repository, assistant_id, user_id):
        """Test retrieving a mapping"""
        mock_mapping = AssistantPromptVariableMappingSQL(
            id=str(uuid.uuid4()),
            assistant_id=assistant_id,
            user_id=user_id,
            variables_config=[PromptVariableConfig(variable_key="project_name", variable_value="Custom Project")],
        )
        mock_repository.get_mapping.return_value = mock_mapping

        result = service.get_mapping(assistant_id, user_id)

        assert result == mock_mapping
        mock_repository.get_mapping.assert_called_once_with(assistant_id, user_id)

    def test_get_mappings_by_assistant(self, service, mock_repository, assistant_id):
        """Test retrieving mappings by assistant ID"""
        mock_mappings = [
            AssistantPromptVariableMappingSQL(
                id=str(uuid.uuid4()),
                assistant_id=assistant_id,
                user_id=str(uuid.uuid4()),
                variables_config=[PromptVariableConfig(variable_key="project_name", variable_value="Project A")],
            )
        ]
        mock_repository.get_mappings_by_assistant.return_value = mock_mappings

        result = service.get_mappings_by_assistant(assistant_id)

        assert result == mock_mappings
        mock_repository.get_mappings_by_assistant.assert_called_once_with(assistant_id)

    def test_get_mappings_by_user(self, service, mock_repository, user_id):
        """Test retrieving mappings by user ID"""
        mock_mappings = [
            AssistantPromptVariableMappingSQL(
                id=str(uuid.uuid4()),
                assistant_id=str(uuid.uuid4()),
                user_id=user_id,
                variables_config=[PromptVariableConfig(variable_key="project_name", variable_value="Project 1")],
            )
        ]
        mock_repository.get_mappings_by_user.return_value = mock_mappings

        result = service.get_mappings_by_user(user_id)

        assert result == mock_mappings
        mock_repository.get_mappings_by_user.assert_called_once_with(user_id)

    def test_get_user_variable_values(self, service, mock_repository, assistant_id, user_id):
        """Test retrieving user variable values as a dictionary"""
        mock_mapping = AssistantPromptVariableMappingSQL(
            id=str(uuid.uuid4()),
            assistant_id=assistant_id,
            user_id=user_id,
            variables_config=[
                PromptVariableConfig(variable_key="project_name", variable_value="Custom Project"),
                PromptVariableConfig(variable_key="team_name", variable_value="Custom Team"),
            ],
        )
        mock_repository.get_mapping.return_value = mock_mapping

        result = service.get_user_variable_values(assistant_id, user_id)

        assert result == {"project_name": "Custom Project", "team_name": "Custom Team"}
        mock_repository.get_mapping.assert_called_once_with(assistant_id, user_id)

    def test_get_user_variable_values_no_mapping(self, service, mock_repository, assistant_id, user_id):
        """Test retrieving user variable values when no mapping exists"""
        mock_repository.get_mapping.return_value = None

        result = service.get_user_variable_values(assistant_id, user_id)

        assert result == {}
        mock_repository.get_mapping.assert_called_once_with(assistant_id, user_id)
