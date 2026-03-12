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
Tests for the assistant prompt variable mapping repository
"""

import uuid
from unittest.mock import patch, MagicMock

import pytest

from codemie.repository.assistants.assistant_prompt_variable_mapping_repository import (
    SQLAssistantPromptVariableMappingRepository,
)
from codemie.rest_api.models.usage.assistant_prompt_variable_mapping import (
    AssistantPromptVariableMappingSQL,
    PromptVariableConfig,
)


class TestAssistantPromptVariableMappingRepository:
    """Tests for the assistant prompt variable mapping repository"""

    @pytest.fixture
    def repository(self):
        return SQLAssistantPromptVariableMappingRepository()

    @pytest.fixture
    def mock_session(self):
        with patch("codemie.repository.assistants.assistant_prompt_variable_mapping_repository.Session") as mock:
            # Mock session context manager
            mock_session = MagicMock()
            mock.return_value.__enter__.return_value = mock_session
            yield mock_session

    @pytest.fixture
    def assistant_id(self):
        return str(uuid.uuid4())

    @pytest.fixture
    def user_id(self):
        return str(uuid.uuid4())

    @pytest.fixture
    def variables_config(self):
        return [
            PromptVariableConfig(variable_key="project_name", variable_value="My Project"),
            PromptVariableConfig(variable_key="team_name", variable_value="My Team"),
        ]

    def test_create_mapping(self, repository, mock_session, assistant_id, user_id, variables_config):
        """Test creating a new mapping"""
        # Set up mock to return None for get_mapping to simulate a new mapping
        with patch.object(repository, 'get_mapping', return_value=None):
            mock_session.exec.return_value.first.return_value = None

            repository.create_or_update_mapping(assistant_id, user_id, variables_config)

            # Check that session.add was called with the correct object
            assert mock_session.add.called
            mapping = mock_session.add.call_args[0][0]
            assert mapping.assistant_id == assistant_id
            assert mapping.user_id == user_id
            assert len(mapping.variables_config) == 2
            assert mapping.variables_config[0].variable_key == "project_name"
            assert mapping.variables_config[0].variable_value == "My Project"

    def test_update_mapping(self, repository, mock_session, assistant_id, user_id, variables_config):
        """Test updating an existing mapping"""
        # Set up mock to return an existing mapping
        existing_mapping = AssistantPromptVariableMappingSQL(
            id=str(uuid.uuid4()),
            assistant_id=assistant_id,
            user_id=user_id,
            variables_config=[PromptVariableConfig(variable_key="old_key", variable_value="old_value")],
        )
        with patch.object(repository, 'get_mapping', return_value=existing_mapping):
            repository.create_or_update_mapping(assistant_id, user_id, variables_config)

            # Check that session.add was called with the updated object
            assert mock_session.add.called
            updated_mapping = mock_session.add.call_args[0][0]
            assert updated_mapping.assistant_id == assistant_id
            assert updated_mapping.user_id == user_id
            assert len(updated_mapping.variables_config) == 2
            assert updated_mapping.variables_config[0].variable_key == "project_name"

    def test_get_mapping(self, repository, mock_session, assistant_id, user_id):
        """Test retrieving a mapping"""
        expected_mapping = AssistantPromptVariableMappingSQL(
            id=str(uuid.uuid4()),
            assistant_id=assistant_id,
            user_id=user_id,
            variables_config=[PromptVariableConfig(variable_key="project_name", variable_value="My Project")],
        )
        mock_session.exec.return_value.first.return_value = expected_mapping

        result = repository.get_mapping(assistant_id, user_id)

        assert result == expected_mapping
        assert mock_session.exec.called

    def test_get_mappings_by_assistant(self, repository, mock_session, assistant_id):
        """Test retrieving mappings by assistant ID"""
        expected_mappings = [
            AssistantPromptVariableMappingSQL(
                id=str(uuid.uuid4()),
                assistant_id=assistant_id,
                user_id=str(uuid.uuid4()),
                variables_config=[PromptVariableConfig(variable_key="project_name", variable_value="Project 1")],
            ),
            AssistantPromptVariableMappingSQL(
                id=str(uuid.uuid4()),
                assistant_id=assistant_id,
                user_id=str(uuid.uuid4()),
                variables_config=[PromptVariableConfig(variable_key="project_name", variable_value="Project 2")],
            ),
        ]
        mock_session.exec.return_value.all.return_value = expected_mappings

        result = repository.get_mappings_by_assistant(assistant_id)

        assert result == expected_mappings
        assert mock_session.exec.called

    def test_get_mappings_by_user(self, repository, mock_session, user_id):
        """Test retrieving mappings by user ID"""
        expected_mappings = [
            AssistantPromptVariableMappingSQL(
                id=str(uuid.uuid4()),
                assistant_id=str(uuid.uuid4()),
                user_id=user_id,
                variables_config=[PromptVariableConfig(variable_key="project_name", variable_value="Project A")],
            ),
            AssistantPromptVariableMappingSQL(
                id=str(uuid.uuid4()),
                assistant_id=str(uuid.uuid4()),
                user_id=user_id,
                variables_config=[PromptVariableConfig(variable_key="project_name", variable_value="Project B")],
            ),
        ]
        mock_session.exec.return_value.all.return_value = expected_mappings

        result = repository.get_mappings_by_user(user_id)

        assert result == expected_mappings
        assert mock_session.exec.called
