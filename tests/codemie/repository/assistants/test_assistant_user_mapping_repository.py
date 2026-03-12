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
Tests for the assistant user mapping repository.
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, UTC

from codemie.rest_api.models.usage.assistant_user_mapping import AssistantUserMappingSQL, ToolConfig
from codemie.repository.assistants.assistant_user_mapping_repository import SQLAssistantUserMappingRepository


@pytest.fixture
def sample_tools_config():
    return [
        ToolConfig(name="Git", integration_id="git-integration-id"),
        ToolConfig(name="JIRA", integration_id="jira-integration-id"),
    ]


@pytest.fixture
def sample_mapping(sample_tools_config):
    return AssistantUserMappingSQL(
        id="test-id",
        assistant_id="test-assistant-id",
        user_id="test-user-id",
        tools_config=sample_tools_config,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def repository():
    return SQLAssistantUserMappingRepository()


def test_create_or_update_mapping_new_record(repository, sample_tools_config):
    # Arrange
    assistant_id = "test-assistant-id"
    user_id = "test-user-id"

    # Create mock objects
    mock_session = MagicMock()
    mock_session_instance = mock_session.return_value.__enter__.return_value

    # Mock the get_mapping method to return None (no existing record)
    with patch.object(repository, "get_mapping", return_value=None):
        # Mock Session
        with patch("codemie.repository.assistants.assistant_user_mapping_repository.Session", mock_session):
            # Mock the engine for SQLModel
            with patch.object(AssistantUserMappingSQL, "get_engine", return_value="mock_engine"):
                # Act
                repository.create_or_update_mapping(assistant_id, user_id, sample_tools_config)

                # Assert
                mock_session.assert_called_once_with("mock_engine")
                mock_session_instance.add.assert_called_once()
                mock_session_instance.commit.assert_called_once()
                mock_session_instance.refresh.assert_called_once()

                # Verify the created object
                created_object = mock_session_instance.add.call_args[0][0]
                assert isinstance(created_object, AssistantUserMappingSQL)
                assert created_object.assistant_id == assistant_id
                assert created_object.user_id == user_id
                assert created_object.tools_config == sample_tools_config


def test_create_or_update_mapping_existing_record(repository, sample_mapping):
    # Arrange
    assistant_id = "test-assistant-id"
    user_id = "test-user-id"
    updated_tools_config = [ToolConfig(name="Updated", integration_id="updated-id")]

    # Create mock objects
    mock_session = MagicMock()
    mock_session_instance = mock_session.return_value.__enter__.return_value

    # Mock the get_mapping method to return an existing mapping
    with patch.object(repository, "get_mapping", return_value=sample_mapping):
        # Mock Session
        with patch("codemie.repository.assistants.assistant_user_mapping_repository.Session", mock_session):
            # Mock the engine for SQLModel
            with patch.object(AssistantUserMappingSQL, "get_engine", return_value="mock_engine"):
                # Act
                repository.create_or_update_mapping(assistant_id, user_id, updated_tools_config)

                # Assert
                mock_session.assert_called_once_with("mock_engine")
                mock_session_instance.add.assert_called_once_with(sample_mapping)
                mock_session_instance.commit.assert_called_once()
                mock_session_instance.refresh.assert_called_once_with(sample_mapping)

                # Verify the updated object
                assert sample_mapping.tools_config == updated_tools_config
                assert isinstance(sample_mapping.updated_at, datetime)


def test_get_mapping(repository):
    # Arrange
    assistant_id = "test-assistant-id"
    user_id = "test-user-id"
    expected_mapping = MagicMock()

    # Mock Session and select function
    mock_session = MagicMock()
    mock_session_instance = mock_session.return_value.__enter__.return_value
    mock_session_instance.exec.return_value.first.return_value = expected_mapping

    # Create a proper mock for SQLModel's select function
    mock_query = MagicMock()
    mock_where_result = MagicMock()
    mock_select = MagicMock(return_value=mock_query)
    mock_query.where.return_value = mock_where_result

    with (
        patch("codemie.repository.assistants.assistant_user_mapping_repository.Session", mock_session),
        patch("codemie.repository.assistants.assistant_user_mapping_repository.select", mock_select),
        patch.object(AssistantUserMappingSQL, "get_engine", return_value="mock_engine"),
    ):
        # Act
        result = repository.get_mapping(assistant_id, user_id)

        # Assert
        mock_session.assert_called_once_with("mock_engine")
        mock_select.assert_called_once_with(AssistantUserMappingSQL)
        mock_query.where.assert_called_once()
        mock_session_instance.exec.assert_called_once_with(mock_where_result)
        assert result == expected_mapping


def test_get_mappings_by_assistant(repository):
    # Arrange
    assistant_id = "test-assistant-id"
    expected_mappings = [MagicMock(), MagicMock()]

    # Mock Session and select function
    mock_session = MagicMock()
    mock_session_instance = mock_session.return_value.__enter__.return_value
    mock_session_instance.exec.return_value.all.return_value = expected_mappings

    # Create a proper mock for SQLModel's select function
    mock_query = MagicMock()
    mock_where_result = MagicMock()
    mock_select = MagicMock(return_value=mock_query)
    mock_query.where.return_value = mock_where_result

    with (
        patch("codemie.repository.assistants.assistant_user_mapping_repository.Session", mock_session),
        patch("codemie.repository.assistants.assistant_user_mapping_repository.select", mock_select),
        patch.object(AssistantUserMappingSQL, "get_engine", return_value="mock_engine"),
    ):
        # Act
        result = repository.get_mappings_by_assistant(assistant_id)

        # Assert
        mock_session.assert_called_once_with("mock_engine")
        mock_select.assert_called_once_with(AssistantUserMappingSQL)
        mock_query.where.assert_called_once()
        mock_session_instance.exec.assert_called_once_with(mock_where_result)
        mock_session_instance.exec.return_value.all.assert_called_once()
        assert result == expected_mappings


def test_get_mappings_by_user(repository):
    # Arrange
    user_id = "test-user-id"
    expected_mappings = [MagicMock(), MagicMock()]

    # Mock Session and select function
    mock_session = MagicMock()
    mock_session_instance = mock_session.return_value.__enter__.return_value
    mock_session_instance.exec.return_value.all.return_value = expected_mappings

    # Create a proper mock for SQLModel's select function
    mock_query = MagicMock()
    mock_where_result = MagicMock()
    mock_select = MagicMock(return_value=mock_query)
    mock_query.where.return_value = mock_where_result

    with (
        patch("codemie.repository.assistants.assistant_user_mapping_repository.Session", mock_session),
        patch("codemie.repository.assistants.assistant_user_mapping_repository.select", mock_select),
        patch.object(AssistantUserMappingSQL, "get_engine", return_value="mock_engine"),
    ):
        # Act
        result = repository.get_mappings_by_user(user_id)

        # Assert
        mock_session.assert_called_once_with("mock_engine")
        mock_select.assert_called_once_with(AssistantUserMappingSQL)
        mock_query.where.assert_called_once()
        mock_session_instance.exec.assert_called_once_with(mock_where_result)
        mock_session_instance.exec.return_value.all.assert_called_once()
        assert result == expected_mappings
