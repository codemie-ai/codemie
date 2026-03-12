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

"""Unit tests for AssistantVersionService"""

from datetime import datetime, UTC
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.assistant import (
    Assistant,
    AssistantConfiguration,
    AssistantRequest,
    AssistantVersionHistoryResponse,
    Context,
    ContextType,
    ToolKitDetails,
    MCPServerDetails,
)
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_version_service import AssistantVersionService


@pytest.fixture
def mock_user():
    """Mock user for testing"""
    return User(id="test-user", username="testuser", name="Test User", project_names=["demo"])


@pytest.fixture
def mock_assistant():
    """Mock assistant for testing"""
    assistant = MagicMock(spec=Assistant)
    assistant.id = "assistant-123"
    assistant.name = "Test Assistant"
    assistant.description = "Test Description"
    assistant.system_prompt = "Test Prompt"
    assistant.version_count = 1
    assistant.llm_model_type = "gpt-4"
    assistant.temperature = 0.7
    assistant.top_p = 0.9
    assistant.context = []
    assistant.toolkits = []
    assistant.mcp_servers = []
    assistant.assistant_ids = []
    assistant.conversation_starters = []
    assistant.bedrock = None
    assistant.agent_card = None
    assistant.created_date = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    assistant.updated_date = None
    # Mock model_dump to return the assistant data
    assistant.model_dump.return_value = {
        "id": assistant.id,
        "name": assistant.name,
        "description": assistant.description,
        "system_prompt": assistant.system_prompt,
        "version_count": assistant.version_count,
        "llm_model_type": assistant.llm_model_type,
        "temperature": assistant.temperature,
        "top_p": assistant.top_p,
        "context": assistant.context,
        "toolkits": assistant.toolkits,
        "mcp_servers": assistant.mcp_servers,
        "assistant_ids": assistant.assistant_ids,
        "conversation_starters": assistant.conversation_starters,
        "bedrock": assistant.bedrock,
        "agent_card": assistant.agent_card,
        "created_date": assistant.created_date,
        "updated_date": assistant.updated_date,
    }
    return assistant


@pytest.fixture
def mock_assistant_request():
    """Mock assistant request for testing"""
    return AssistantRequest(
        name="Test Assistant",
        description="Updated Description",
        system_prompt="Updated Prompt",
        llm_model_type="gpt-4",
        temperature=0.8,
        top_p=0.95,
        context=[],
        toolkits=[],
        mcp_servers=[],
        assistant_ids=[],
        conversation_starters=["Hello!", "How are you?"],
    )


@pytest.fixture
def mock_config():
    """Mock configuration for testing"""
    config = MagicMock(spec=AssistantConfiguration)
    config.id = "config-123"
    config.assistant_id = "assistant-123"
    config.version_number = 1
    config.description = "Test Description"
    config.system_prompt = "Test Prompt"
    config.llm_model_type = "gpt-4"
    config.temperature = 0.7
    config.top_p = 0.9
    config.context = []
    config.toolkits = []
    config.mcp_servers = []
    config.assistant_ids = []
    config.conversation_starters = []
    config.bedrock = None
    config.agent_card = None
    config.created_date = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    config.created_by = CreatedByUser(id="test-user", username="testuser", name="Test User")
    config.change_notes = "Initial version"
    return config


class TestCreateInitialVersion:
    """Tests for create_initial_version method"""

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_create_initial_version_success(self, mock_config_class, mock_assistant, mock_assistant_request, mock_user):
        """Test successful creation of initial version"""
        # Setup
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Execute
        result = AssistantVersionService.create_initial_version(
            assistant=mock_assistant, request=mock_assistant_request, user=mock_user
        )

        # Verify return value is the created config
        assert result == mock_config_instance

        # Verify configuration creation
        mock_config_class.assert_called_once()
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['assistant_id'] == mock_assistant.id
        assert call_kwargs['version_number'] == 1
        assert call_kwargs['description'] == mock_assistant_request.description
        assert call_kwargs['system_prompt'] == mock_assistant_request.system_prompt
        assert call_kwargs['change_notes'] == "Initial version"
        mock_config_instance.save.assert_called_once()

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_create_initial_version_with_none_description(self, mock_config_class, mock_assistant, mock_user):
        """Test creation with None description in request"""
        # Setup request with None description
        request = AssistantRequest(
            name="Test Assistant",
            description=None,
            system_prompt="Test Prompt",
            llm_model_type="gpt-4",
        )
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Execute
        result = AssistantVersionService.create_initial_version(
            assistant=mock_assistant, request=request, user=mock_user
        )

        # Verify return value
        assert result == mock_config_instance

        # Verify empty string used for None description
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['description'] == ""
        assert call_kwargs['system_prompt'] == "Test Prompt"

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_create_initial_version_with_complex_fields(self, mock_config_class, mock_assistant, mock_user):
        """Test creation with complex fields like toolkits and context"""
        # Setup request with complex fields
        request = AssistantRequest(
            name="Test Assistant",
            description="Test",
            system_prompt="Test Prompt",
            llm_model_type="gpt-4",
            context=[Context(name="test-repo", context_type=ContextType.CODE)],
            toolkits=[ToolKitDetails(toolkit="General", tools=[], label="General Tools")],
            mcp_servers=[MCPServerDetails(name="test-mcp", enabled=True)],
            assistant_ids=["sub-assistant-1"],
            conversation_starters=["Hello!", "Hi!"],
        )
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Execute
        result = AssistantVersionService.create_initial_version(
            assistant=mock_assistant, request=request, user=mock_user
        )

        # Verify return value
        assert result == mock_config_instance

        # Verify complex fields are passed correctly
        call_kwargs = mock_config_class.call_args[1]
        assert len(call_kwargs['context']) == 1
        assert len(call_kwargs['toolkits']) == 1
        assert len(call_kwargs['mcp_servers']) == 1
        assert len(call_kwargs['assistant_ids']) == 1
        assert len(call_kwargs['conversation_starters']) == 2


class TestCreateNewVersion:
    """Tests for create_new_version method"""

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_create_new_version_success(self, mock_config_class, mock_assistant, mock_assistant_request, mock_user):
        """Test successful creation of new version"""
        # Setup
        mock_assistant.version_count = 2
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance
        mock_config_class.get_latest_version_number.return_value = 2

        # Execute
        result = AssistantVersionService.create_new_version(
            assistant=mock_assistant, request=mock_assistant_request, user=mock_user, change_notes="Test changes"
        )

        # Verify return value is the created config
        assert result == mock_config_instance

        mock_config_class.get_latest_version_number.assert_called_once_with(mock_assistant.id)

        # Verify configuration creation
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['version_number'] == 3  # latest_version + 1
        assert call_kwargs['change_notes'] == "Test changes"
        mock_config_instance.save.assert_called_once()
        mock_assistant.update.assert_called_once()
        assert mock_assistant.version_count == 3

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_create_new_version_default_change_notes(
        self, mock_config_class, mock_assistant, mock_assistant_request, mock_user
    ):
        """Test creation with default change notes"""
        # Setup
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance
        # Mock get_latest_version_number
        mock_config_class.get_latest_version_number.return_value = 1

        # Execute
        result = AssistantVersionService.create_new_version(
            assistant=mock_assistant, request=mock_assistant_request, user=mock_user
        )

        # Verify return value
        assert result == mock_config_instance

        # Verify default change notes
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['change_notes'] == "Configuration updated"

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    @patch('codemie.service.assistant.assistant_version_service.datetime')
    def test_create_new_version_updates_timestamp(
        self, mock_datetime, mock_config_class, mock_assistant, mock_assistant_request, mock_user
    ):
        """Test that updated_date is set correctly"""
        # Setup
        fixed_time = datetime(2024, 6, 15, 10, 30, 0, tzinfo=UTC)
        mock_datetime.now.return_value = fixed_time
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance
        # Mock get_latest_version_number
        mock_config_class.get_latest_version_number.return_value = 1

        # Execute
        result = AssistantVersionService.create_new_version(
            assistant=mock_assistant, request=mock_assistant_request, user=mock_user
        )

        # Verify return value
        assert result == mock_config_instance

        # Verify timestamp was updated
        mock_datetime.now.assert_called_once_with(UTC)
        assert mock_assistant.updated_date == fixed_time


class TestGetVersion:
    """Tests for get_version method"""

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_get_version_success(self, mock_config_class, mock_config):
        """Test successful retrieval of version"""
        # Setup
        mock_config_class.get_by_assistant_and_version.return_value = mock_config

        # Execute
        result = AssistantVersionService.get_version("assistant-123", 1)

        # Verify
        mock_config_class.get_by_assistant_and_version.assert_called_once_with("assistant-123", 1)
        assert result == mock_config

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_get_version_not_found(self, mock_config_class):
        """Test version not found raises exception"""
        # Setup
        mock_config_class.get_by_assistant_and_version.return_value = None

        # Execute & Verify
        with pytest.raises(ExtendedHTTPException) as exc_info:
            AssistantVersionService.get_version("assistant-123", 5)

        assert exc_info.value.code == 404
        assert "Version not found" in exc_info.value.message
        assert "Version 5" in exc_info.value.details


class TestGetCurrentVersion:
    """Tests for get_current_version method"""

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_get_current_version_success(self, mock_config_class, mock_config):
        """Test successful retrieval of current version"""
        # Setup
        mock_config_class.get_current_version.return_value = mock_config

        # Execute
        result = AssistantVersionService.get_current_version("assistant-123")

        # Verify
        mock_config_class.get_current_version.assert_called_once_with("assistant-123")
        assert result == mock_config

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_get_current_version_not_found(self, mock_config_class):
        """Test no versions found raises exception"""
        # Setup
        mock_config_class.get_current_version.return_value = None

        # Execute & Verify
        with pytest.raises(ExtendedHTTPException) as exc_info:
            AssistantVersionService.get_current_version("assistant-123")

        assert exc_info.value.code == 404
        assert "No versions found" in exc_info.value.message


class TestGetVersionHistory:
    """Tests for get_version_history method"""

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_get_version_history_success(self, mock_config_class, mock_assistant, mock_config):
        """Test successful retrieval of version history"""
        # Setup
        mock_assistant.version_count = 3
        mock_config_class.get_version_history.return_value = [mock_config]

        # Execute
        result = AssistantVersionService.get_version_history(assistant=mock_assistant, page=0, per_page=20)

        # Verify
        mock_config_class.get_version_history.assert_called_once_with(mock_assistant.id, 0, 20)
        assert isinstance(result, AssistantVersionHistoryResponse)
        assert result.assistant_id == mock_assistant.id
        assert result.assistant_name == mock_assistant.name
        assert result.total_versions == 3
        assert len(result.versions) == 1

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_get_version_history_pagination(self, mock_config_class, mock_assistant):
        """Test pagination parameters are passed correctly"""
        # Setup
        mock_config_class.get_version_history.return_value = []

        # Execute
        result = AssistantVersionService.get_version_history(assistant=mock_assistant, page=2, per_page=10)

        # Verify return value
        assert isinstance(result, AssistantVersionHistoryResponse)
        assert result.assistant_id == mock_assistant.id
        assert result.assistant_name == mock_assistant.name
        assert len(result.versions) == 0

        # Verify pagination params
        mock_config_class.get_version_history.assert_called_once_with(mock_assistant.id, 2, 10)


class TestRollbackToVersion:
    """Tests for rollback_to_version method"""

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    @patch.object(AssistantVersionService, 'get_version')
    def test_rollback_to_version_success(
        self, mock_get_version, mock_config_class, mock_assistant, mock_config, mock_user
    ):
        """Test successful rollback to previous version"""
        # Setup
        mock_assistant.version_count = 3
        mock_get_version.return_value = mock_config
        mock_new_config = MagicMock()
        mock_config_class.return_value = mock_new_config

        # Execute
        result = AssistantVersionService.rollback_to_version(
            assistant=mock_assistant, target_version_number=1, user=mock_user
        )

        # Verify return value is the created config
        assert result == mock_new_config

        # Verify rollback behavior
        mock_get_version.assert_called_once_with(mock_assistant.id, 1)
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['version_number'] == 4  # version_count + 1
        assert "Rolled back to version 1" in call_kwargs['change_notes']
        mock_new_config.save.assert_called_once()
        mock_assistant.update.assert_called_once()
        assert mock_assistant.version_count == 4

    @patch.object(AssistantVersionService, 'get_version')
    def test_rollback_to_current_version_fails(self, mock_get_version, mock_assistant, mock_user):
        """Test rollback to current version raises exception"""
        # Setup
        mock_assistant.version_count = 3

        # Execute & Verify
        with pytest.raises(ExtendedHTTPException) as exc_info:
            AssistantVersionService.rollback_to_version(
                assistant=mock_assistant, target_version_number=3, user=mock_user
            )

        assert exc_info.value.code == 400
        assert "Cannot rollback to current version" in exc_info.value.message

    @patch.object(AssistantVersionService, 'get_version')
    def test_rollback_to_invalid_version_fails(self, mock_get_version, mock_assistant, mock_user):
        """Test rollback to invalid version number raises exception"""
        # Setup
        mock_assistant.version_count = 3

        # Execute & Verify - version too high
        with pytest.raises(ExtendedHTTPException) as exc_info:
            AssistantVersionService.rollback_to_version(
                assistant=mock_assistant, target_version_number=5, user=mock_user
            )

        assert exc_info.value.code == 400
        assert "Invalid version number" in exc_info.value.message

        # Execute & Verify - version too low
        with pytest.raises(ExtendedHTTPException) as exc_info:
            AssistantVersionService.rollback_to_version(
                assistant=mock_assistant, target_version_number=0, user=mock_user
            )

        assert exc_info.value.code == 400
        assert "Invalid version number" in exc_info.value.message

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    @patch.object(AssistantVersionService, 'get_version')
    def test_rollback_with_custom_change_notes(
        self, mock_get_version, mock_config_class, mock_assistant, mock_config, mock_user
    ):
        """Test rollback with custom change notes"""
        # Setup
        mock_assistant.version_count = 3
        mock_get_version.return_value = mock_config
        mock_new_config = MagicMock()
        mock_config_class.return_value = mock_new_config

        # Execute
        result = AssistantVersionService.rollback_to_version(
            assistant=mock_assistant, target_version_number=1, user=mock_user, change_notes="Custom rollback reason"
        )

        # Verify return value is the created config
        assert result == mock_new_config

        # Verify custom change notes
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['change_notes'] == "Custom rollback reason"

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    @patch.object(AssistantVersionService, 'get_version')
    def test_rollback_copies_all_fields(self, mock_get_version, mock_config_class, mock_assistant, mock_user):
        """Test rollback copies all configuration fields from target version"""
        # Setup target config with specific values
        target_config = MagicMock(spec=AssistantConfiguration)
        target_config.description = "Old Description"
        target_config.system_prompt = "Old Prompt"
        target_config.llm_model_type = "gpt-3.5-turbo"
        target_config.temperature = 0.5
        target_config.top_p = 0.8
        target_config.context = [Context(name="old-repo", context_type=ContextType.CODE)]
        target_config.toolkits = []
        target_config.mcp_servers = []
        target_config.assistant_ids = ["old-sub"]
        target_config.conversation_starters = ["Old starter"]
        target_config.bedrock = None
        target_config.agent_card = None

        mock_assistant.version_count = 2
        mock_get_version.return_value = target_config
        mock_new_config = MagicMock()
        mock_config_class.return_value = mock_new_config

        # Execute
        result = AssistantVersionService.rollback_to_version(
            assistant=mock_assistant, target_version_number=1, user=mock_user
        )

        # Verify return value is the created config
        assert result == mock_new_config

        # Verify all fields copied
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['description'] == "Old Description"
        assert call_kwargs['system_prompt'] == "Old Prompt"
        assert call_kwargs['llm_model_type'] == "gpt-3.5-turbo"
        assert call_kwargs['temperature'] == 0.5
        assert call_kwargs['top_p'] == 0.8
        assert len(call_kwargs['context']) == 1
        assert call_kwargs['assistant_ids'] == ["old-sub"]


class TestApplyVersionToAssistant:
    """Tests for apply_version_to_assistant method"""

    @patch.object(AssistantVersionService, 'get_version')
    def test_apply_version_to_assistant_success(self, mock_get_version, mock_assistant, mock_config):
        """Test successful application of version to assistant"""
        # Setup
        mock_get_version.return_value = mock_config

        # Execute
        result = AssistantVersionService.apply_version_to_assistant(assistant=mock_assistant, version_number=1)

        # Verify
        mock_get_version.assert_called_once_with(mock_assistant.id, 1)
        # Verify the returned instance has the version configuration applied
        assert result.description == mock_config.description
        assert result.system_prompt == mock_config.system_prompt
        assert result.llm_model_type == mock_config.llm_model_type
        assert result.temperature == mock_config.temperature
        assert result.top_p == mock_config.top_p
        assert result.context == mock_config.context
        assert result.toolkits == mock_config.toolkits
        assert result.mcp_servers == mock_config.mcp_servers
        assert result.assistant_ids == mock_config.assistant_ids
        assert result.conversation_starters == mock_config.conversation_starters
        assert result.bedrock == mock_config.bedrock
        assert result.agent_card == mock_config.agent_card
        assert result.version == mock_config.version_number
        # Verify original assistant was not modified (new instance was created)
        assert result is not mock_assistant

    @patch.object(AssistantVersionService, 'get_version')
    def test_apply_version_not_found_raises_exception(self, mock_get_version, mock_assistant):
        """Test applying non-existent version raises exception"""
        # Setup
        mock_get_version.side_effect = ExtendedHTTPException(
            code=404, message="Version not found", details="Test error"
        )

        # Execute & Verify
        with pytest.raises(ExtendedHTTPException):
            AssistantVersionService.apply_version_to_assistant(assistant=mock_assistant, version_number=99)
