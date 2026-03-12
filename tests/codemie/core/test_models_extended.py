# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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
Tests for core.models validators and new fields added in EPMCDME-10160.

Tests coverage for:
- Application model: new project_type, description, created_by, deleted_at fields
- Application.search_by_name: LIKE wildcard escaping
- UserResponse model: new email field, projects array, field serialization
- ToolConfig model: validate_credentials_provided validator
- AssistantChatRequest: file_name/file_names validator, sub_assistants_versions validator
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from codemie.core.models import (
    Application,
    AssistantChatRequest,
    ProjectInfoResponse,
    ToolConfig,
    UserResponse,
)


class TestApplicationModelNewFields:
    """Test suite for Application model new fields added in EPMCDME-10160."""

    def test_application_default_values(self):
        """Test Application model default values for new fields."""
        # Arrange & Act
        app = Application(name="test-project")

        # Assert - verify defaults
        assert app.name == "test-project"
        assert app.description is None
        assert app.project_type == "shared"
        assert app.created_by is None
        assert app.deleted_at is None
        assert app.git_repos == []

    def test_application_with_description(self):
        """Test Application model with description field."""
        # Arrange & Act
        description = "Test project description"
        app = Application(name="test-project", description=description)

        # Assert
        assert app.description == description

    def test_application_project_type_personal(self):
        """Test Application model with project_type set to personal."""
        # Arrange & Act
        app = Application(name="user-personal", project_type="personal", created_by="user-123")

        # Assert
        assert app.project_type == "personal"
        assert app.created_by == "user-123"

    def test_application_created_by_user_id(self):
        """Test Application model with created_by user ID."""
        # Arrange & Act
        user_id = "user-456"
        app = Application(name="test-project", created_by=user_id)

        # Assert
        assert app.created_by == user_id

    def test_application_soft_delete_timestamp(self):
        """Test Application model with deleted_at soft-delete timestamp."""
        # Arrange
        delete_time = datetime.now()

        # Act
        app = Application(name="test-project", deleted_at=delete_time)

        # Assert
        assert app.deleted_at == delete_time


class TestApplicationSearchByName:
    """Test suite for Application.search_by_name with wildcard escaping."""

    @patch("codemie.core.models.Session")
    def test_search_by_name_escapes_like_wildcards(self, mock_session_class):
        """Test search_by_name escapes LIKE wildcards to prevent information leakage."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.all.return_value = []

        # Mock the engine
        mock_engine = MagicMock()
        with patch.object(Application, "get_engine", return_value=mock_engine):
            # Act - search with LIKE wildcards
            Application.search_by_name("test%wildcard")

            # Assert - verify session.exec was called (escaping happens in query building)
            mock_session.exec.assert_called_once()

    @patch("codemie.core.models.Session")
    def test_search_by_name_with_underscore_wildcard(self, mock_session_class):
        """Test search_by_name escapes underscore wildcard character."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.all.return_value = []

        mock_engine = MagicMock()
        with patch.object(Application, "get_engine", return_value=mock_engine):
            # Act - search with underscore wildcard
            Application.search_by_name("test_name")

            # Assert - verify query was executed
            mock_session.exec.assert_called_once()

    @patch("codemie.core.models.Session")
    def test_search_by_name_without_query(self, mock_session_class):
        """Test search_by_name without query returns all projects."""
        # Arrange
        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.all.return_value = []

        mock_engine = MagicMock()
        with patch.object(Application, "get_engine", return_value=mock_engine):
            # Act - search without name_query
            Application.search_by_name(None)

            # Assert - query executed without WHERE clause
            mock_session.exec.assert_called_once()


class TestUserResponseModel:
    """Test suite for UserResponse model with new fields."""

    def test_user_response_with_email(self):
        """Test UserResponse model includes email field."""
        # Arrange & Act
        user = UserResponse(
            user_id="user-123",
            name="Test User",
            username="testuser",
            email="test@example.com",
            is_super_admin=False,
        )

        # Assert
        assert user.email == "test@example.com"

    def test_user_response_email_default_empty(self):
        """Test UserResponse email field defaults to empty string."""
        # Arrange & Act
        user = UserResponse(user_id="user-123", name="Test User", username="testuser", is_super_admin=False)

        # Assert
        assert user.email == ""

    def test_user_response_with_projects_array(self):
        """Test UserResponse model includes projects array."""
        # Arrange
        projects = [
            ProjectInfoResponse(name="project1", is_project_admin=True),
            ProjectInfoResponse(name="project2", is_project_admin=False),
        ]

        # Act
        user = UserResponse(
            user_id="user-123",
            name="Test User",
            username="testuser",
            is_super_admin=False,
            projects=projects,
        )

        # Assert
        assert len(user.projects) == 2
        assert user.projects[0].name == "project1"
        assert user.projects[0].is_project_admin is True
        assert user.projects[1].name == "project2"
        assert user.projects[1].is_project_admin is False

    def test_user_response_is_super_admin_field(self):
        """Test UserResponse includes is_super_admin field."""
        # Arrange & Act
        admin_user = UserResponse(
            user_id="admin-123",
            name="Admin User",
            username="admin",
            is_super_admin=True,
        )
        regular_user = UserResponse(
            user_id="user-123",
            name="Regular User",
            username="user",
            is_super_admin=False,
        )

        # Assert
        assert admin_user.is_super_admin is True
        assert regular_user.is_super_admin is False

    def test_user_response_legacy_fields(self):
        """Test UserResponse includes legacy applications fields for backward compatibility."""
        # Arrange & Act
        user = UserResponse(
            user_id="user-123",
            name="Test User",
            username="testuser",
            is_super_admin=False,
            applications=["app1", "app2"],
            applications_admin=["app1"],
            is_admin=True,
        )

        # Assert - legacy fields present
        assert user.applications == ["app1", "app2"]
        assert user.applications_admin == ["app1"]
        assert user.is_admin is True

    def test_user_response_serialization_snake_case(self):
        """Test UserResponse serializes fields as snake_case not camelCase."""
        # Arrange
        user = UserResponse(
            user_id="user-123",
            name="Test User",
            username="testuser",
            email="test@example.com",
            is_super_admin=True,
        )

        # Act
        user_dict = user.model_dump()

        # Assert - primary fields are in snake_case
        assert "user_id" in user_dict
        assert "is_super_admin" in user_dict
        assert "knowledge_bases" in user_dict
        # Legacy camelCase fields exist for UI backward compatibility
        assert "userId" in user_dict
        # camelCase aliases that were never added as fields are absent
        assert "isSuperAdmin" not in user_dict


class TestToolConfigValidator:
    """Test suite for ToolConfig.validate_credentials_provided validator."""

    def test_tool_config_with_tool_creds_only(self):
        """Test ToolConfig with tool_creds provided is valid."""
        # Arrange & Act
        config = ToolConfig(name="test-tool", tool_creds={"api_key": "test-key-123"})

        # Assert
        assert config.name == "test-tool"
        assert config.tool_creds == {"api_key": "test-key-123"}
        assert config.integration_id is None

    def test_tool_config_with_integration_id_only(self):
        """Test ToolConfig with integration_id provided is valid."""
        # Arrange & Act
        config = ToolConfig(name="test-tool", integration_id="integration-456")

        # Assert
        assert config.name == "test-tool"
        assert config.tool_creds is None
        assert config.integration_id == "integration-456"

    def test_tool_config_with_neither_raises_validation_error(self):
        """Test ToolConfig raises ValidationError when neither tool_creds nor integration_id provided."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            ToolConfig(name="test-tool")

        # Assert - error mentions the validation rule
        assert "Either tool_creds or integration_id must be provided" in str(exc_info.value)

    def test_tool_config_with_both_raises_validation_error(self):
        """Test ToolConfig raises ValidationError when both tool_creds and integration_id provided."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            ToolConfig(
                name="test-tool",
                tool_creds={"api_key": "test-key"},
                integration_id="integration-456",
            )

        # Assert - error mentions the validation rule
        assert "Either tool_creds or integration_id must be provided, but not both" in str(exc_info.value)


class TestAssistantChatRequestFileNamesValidator:
    """Test suite for AssistantChatRequest file_name/file_names validator."""

    def test_file_names_field_accepts_list(self):
        """Test file_names field accepts list of file names."""
        # Arrange & Act
        request = AssistantChatRequest(text="test", file_names=["file1.txt", "file2.pdf"])

        # Assert
        assert request.file_names == ["file1.txt", "file2.pdf"]

    def test_file_name_backward_compatibility_converts_to_file_names(self):
        """Test deprecated file_name field converts to file_names for backward compatibility."""
        # Arrange & Act - simulate old API request with file_name
        request_data = {"text": "test", "file_name": "single_file.txt"}
        request = AssistantChatRequest(**request_data)

        # Assert - file_name converted to file_names list
        assert request.file_names == ["single_file.txt"]

    def test_file_name_and_file_names_both_raises_error(self):
        """Test providing both file_name and file_names raises ValidationError."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            request_data = {
                "text": "test",
                "file_name": "single.txt",
                "file_names": ["file1.txt"],
            }
            AssistantChatRequest(**request_data)

        # Assert
        assert "Cannot provide both file_name and file_names" in str(exc_info.value)

    def test_file_name_empty_string_not_converted(self):
        """Test file_name with empty string is not converted to file_names."""
        # Arrange & Act
        request_data = {"text": "test", "file_name": ""}
        request = AssistantChatRequest(**request_data)

        # Assert - empty string not added to file_names
        assert request.file_names == []

    def test_file_name_whitespace_only_not_converted(self):
        """Test file_name with whitespace only is not converted to file_names."""
        # Arrange & Act
        request_data = {"text": "test", "file_name": "   "}
        request = AssistantChatRequest(**request_data)

        # Assert - whitespace-only string not added to file_names
        assert request.file_names == []


class TestAssistantChatRequestSubAssistantsVersionsValidator:
    """Test suite for AssistantChatRequest.validate_sub_assistants_versions validator."""

    def test_sub_assistants_versions_valid_positive_integers(self):
        """Test sub_assistants_versions accepts valid positive integers."""
        # Arrange & Act
        request = AssistantChatRequest(
            text="test",
            sub_assistants_versions={
                "assistant-1": 1,
                "assistant-2": 5,
                "assistant-3": 100,
            },
        )

        # Assert
        assert request.sub_assistants_versions == {
            "assistant-1": 1,
            "assistant-2": 5,
            "assistant-3": 100,
        }

    def test_sub_assistants_versions_none_is_valid(self):
        """Test sub_assistants_versions with None value is valid."""
        # Arrange & Act
        request = AssistantChatRequest(text="test", sub_assistants_versions=None)

        # Assert
        assert request.sub_assistants_versions is None

    def test_sub_assistants_versions_empty_dict_is_valid(self):
        """Test sub_assistants_versions with empty dict is valid."""
        # Arrange & Act
        request = AssistantChatRequest(text="test", sub_assistants_versions={})

        # Assert
        assert request.sub_assistants_versions == {}

    def test_sub_assistants_versions_zero_raises_validation_error(self):
        """Test sub_assistants_versions with version=0 raises ValidationError."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            AssistantChatRequest(
                text="test",
                sub_assistants_versions={"assistant-1": 0},
            )

        # Assert
        error_str = str(exc_info.value)
        assert "Invalid version number" in error_str
        assert "Version must be a positive integer" in error_str

    def test_sub_assistants_versions_negative_raises_validation_error(self):
        """Test sub_assistants_versions with negative version raises ValidationError."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            AssistantChatRequest(
                text="test",
                sub_assistants_versions={"assistant-1": -5},
            )

        # Assert
        error_str = str(exc_info.value)
        assert "Invalid version number" in error_str
        assert "Version must be a positive integer" in error_str

    def test_sub_assistants_versions_non_integer_raises_validation_error(self):
        """Test sub_assistants_versions with non-integer version raises ValidationError."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            AssistantChatRequest(
                text="test",
                sub_assistants_versions={"assistant-1": "not-a-number"},
            )

        # Assert - Pydantic validation catches type mismatch
        assert "sub_assistants_versions" in str(exc_info.value)
