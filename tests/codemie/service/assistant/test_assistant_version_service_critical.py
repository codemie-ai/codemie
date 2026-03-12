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

"""Critical tests for AssistantVersionService with custom_metadata"""

import pytest
from unittest.mock import MagicMock, patch

from codemie.rest_api.models.assistant import Assistant, AssistantConfiguration, AssistantRequest
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_version_service import AssistantVersionService


@pytest.fixture
def mock_user():
    """Mock user for testing"""
    return User(id="test-user", username="testuser", name="Test User", project_names=["demo"], auth_token=None)


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
    assistant.custom_metadata = None
    return assistant


class TestCreateNewVersionCritical:
    """CRITICAL: Test version creation when only metadata changes - TC-VS-2.1"""

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_create_new_version_when_only_metadata_changes(self, mock_config_class, mock_assistant, mock_user):
        """
        CRITICAL: Verify that a new version is created when only custom_metadata changes.
        This ensures versioning works for metadata-only changes.
        """
        # Arrange
        mock_assistant.version_count = 1
        mock_assistant.custom_metadata = {'status': 'draft'}

        # Mock current version
        current_config = MagicMock(spec=AssistantConfiguration)
        current_config.custom_metadata = {'status': 'draft'}
        current_config.description = "Test Description"
        current_config.system_prompt = "Test Prompt"
        current_config.llm_model_type = "gpt-4"
        current_config.temperature = 0.7
        current_config.top_p = 0.9
        current_config.context = []
        current_config.toolkits = []
        current_config.mcp_servers = []
        current_config.assistant_ids = []
        current_config.conversation_starters = []
        current_config.bedrock = None
        current_config.agent_card = None

        # Request with only metadata changed
        request = AssistantRequest(
            name="Test Assistant",
            description="Test Description",  # Same
            system_prompt="Test Prompt",  # Same
            llm_model_type="gpt-4",  # Same
            temperature=0.7,  # Same
            top_p=0.9,  # Same
            custom_metadata={'status': 'published'},  # CHANGED
        )

        # Mock configuration instance
        new_config = MagicMock()
        mock_config_class.return_value = new_config
        mock_config_class.get_latest_version_number.return_value = 1
        mock_config_class.get_current_version.return_value = current_config

        # Act
        result = AssistantVersionService.create_new_version(
            assistant=mock_assistant, request=request, user=mock_user, change_notes="Metadata update"
        )

        # Assert
        assert result == new_config
        mock_config_class.assert_called_once()
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['version_number'] == 2  # Incremented
        assert call_kwargs['custom_metadata'] == {'status': 'published'}  # New metadata
        assert call_kwargs['change_notes'] == "Metadata update"
        new_config.save.assert_called_once()


class TestApplyVersionToAssistant:
    """CRITICAL: Test metadata application to assistant - TC-VS-3.1"""

    def test_apply_version_metadata_to_assistant(self):
        """
        CRITICAL: Verify that custom_metadata from configuration is applied to assistant.
        This is essential for rollback functionality.
        """
        # Arrange
        assistant = Assistant(
            name="Test",
            description="Old description",
            system_prompt="Old prompt",
            project="demo",
            custom_metadata={'old': 'data'},
        )

        config = MagicMock(spec=AssistantConfiguration)
        config.description = "New description"
        config.system_prompt = "New prompt"
        config.llm_model_type = "gpt-4"
        config.temperature = 0.8
        config.top_p = 0.95
        config.context = []
        config.toolkits = []
        config.mcp_servers = []
        config.assistant_ids = []
        config.conversation_starters = []
        config.bedrock = None
        config.agent_card = None
        config.custom_metadata = {'new': 'data'}  # New metadata

        # Act
        AssistantVersionService.update_assistant_config_fields(assistant, config)

        # Assert
        assert assistant.custom_metadata == {'new': 'data'}
        assert assistant.description == "New description"
        assert assistant.system_prompt == "New prompt"

    def test_apply_version_with_none_metadata(self):
        """CRITICAL: Verify that None metadata is correctly applied, clearing assistant metadata"""
        # Arrange
        assistant = Assistant(
            name="Test",
            description="Test",
            system_prompt="Test",
            project="demo",
            custom_metadata={'existing': 'data'},
        )

        config = MagicMock(spec=AssistantConfiguration)
        config.description = "Test"
        config.system_prompt = "Test"
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
        config.custom_metadata = None  # Clearing metadata

        # Act
        AssistantVersionService.update_assistant_config_fields(assistant, config)

        # Assert
        assert assistant.custom_metadata is None

    def test_apply_version_verify_all_fields_including_metadata(self):
        """Verify that all fields (including metadata) are applied together correctly"""
        # Arrange
        assistant = Assistant(
            name="Test",
            description="Old",
            system_prompt="Old prompt",
            project="demo",
            temperature=0.7,
            custom_metadata={'old': 'data'},
        )

        config = MagicMock(spec=AssistantConfiguration)
        config.description = "New"
        config.system_prompt = "New prompt"
        config.llm_model_type = "gpt-4"
        config.temperature = 0.9
        config.top_p = 0.95
        config.context = []
        config.toolkits = []
        config.mcp_servers = []
        config.assistant_ids = []
        config.conversation_starters = ["Hello"]
        config.bedrock = None
        config.agent_card = None
        config.custom_metadata = {'new': 'data'}

        # Act
        AssistantVersionService.update_assistant_config_fields(assistant, config)

        # Assert - All fields updated
        assert assistant.description == "New"
        assert assistant.system_prompt == "New prompt"
        assert assistant.temperature == 0.9
        assert assistant.custom_metadata == {'new': 'data'}
        assert assistant.conversation_starters == ["Hello"]

    def test_apply_version_metadata_overwrite_not_merge(self):
        """Verify that applying version completely overwrites existing metadata (not merge)"""
        # Arrange
        assistant = Assistant(
            name="Test",
            description="Test",
            system_prompt="Test",
            project="demo",
            custom_metadata={'key1': 'value1', 'key2': 'value2'},
        )

        config = MagicMock(spec=AssistantConfiguration)
        config.description = "Test"
        config.system_prompt = "Test"
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
        config.custom_metadata = {'key3': 'value3'}  # Different keys

        # Act
        AssistantVersionService.update_assistant_config_fields(assistant, config)

        # Assert - Complete replacement, not merge
        assert assistant.custom_metadata == {'key3': 'value3'}
        assert 'key1' not in assistant.custom_metadata
        assert 'key2' not in assistant.custom_metadata


class TestCreateInitialVersion:
    """Tests for create_initial_version with metadata"""

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_create_initial_version_with_metadata(self, mock_config_class, mock_assistant, mock_user):
        """Verify that custom_metadata is saved in the initial version"""
        # Arrange
        request = AssistantRequest(
            name="Test Assistant",
            description="Test",
            system_prompt="Test",
            llm_model_type="gpt-4",
            custom_metadata={'workflow': 'draft', 'priority': 5},
        )

        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Act
        result = AssistantVersionService.create_initial_version(
            assistant=mock_assistant, request=request, user=mock_user
        )

        # Assert
        assert result == mock_config_instance
        mock_config_class.assert_called_once()
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['version_number'] == 1
        assert call_kwargs['custom_metadata'] == {'workflow': 'draft', 'priority': 5}
        assert call_kwargs['change_notes'] == "Initial version"
        mock_config_instance.save.assert_called_once()

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_create_initial_version_with_none_metadata(self, mock_config_class, mock_assistant, mock_user):
        """Verify that None metadata is handled correctly in initial version"""
        # Arrange
        request = AssistantRequest(
            name="Test Assistant",
            description="Test",
            system_prompt="Test",
            llm_model_type="gpt-4",
            custom_metadata=None,
        )

        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Act
        result = AssistantVersionService.create_initial_version(
            assistant=mock_assistant, request=request, user=mock_user
        )

        # Assert
        assert result == mock_config_instance
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['custom_metadata'] is None

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_create_initial_version_with_complex_nested_metadata(self, mock_config_class, mock_assistant, mock_user):
        """Verify that complex nested metadata structures are correctly saved"""
        # Arrange
        complex_metadata = {
            'workflow': {
                'stage': 'development',
                'approvals': ['user1', 'user2'],
                'history': [{'date': '2024-01-01', 'action': 'created'}, {'date': '2024-01-02', 'action': 'reviewed'}],
            },
            'tags': ['urgent', 'customer-facing'],
            'metrics': {'usage_count': 0, 'last_used': None},
        }
        request = AssistantRequest(
            name="Test Assistant",
            description="Test",
            system_prompt="Test",
            llm_model_type="gpt-4",
            custom_metadata=complex_metadata,
        )

        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Act
        AssistantVersionService.create_initial_version(assistant=mock_assistant, request=request, user=mock_user)

        # Assert
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['custom_metadata'] == complex_metadata
        assert call_kwargs['custom_metadata']['workflow']['stage'] == 'development'
        assert len(call_kwargs['custom_metadata']['workflow']['approvals']) == 2


class TestCreateNewVersion:
    """Tests for create_new_version with metadata"""

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_create_new_version_with_metadata_and_other_changes(self, mock_config_class, mock_assistant, mock_user):
        """Verify new version correctly saves metadata along with other field changes"""
        # Arrange
        mock_assistant.version_count = 1

        current_config = MagicMock(spec=AssistantConfiguration)
        current_config.custom_metadata = {'key': 'old_value'}
        current_config.description = "Old description"
        current_config.system_prompt = "Old prompt"
        current_config.llm_model_type = "gpt-4"
        current_config.temperature = 0.7
        current_config.top_p = 0.9
        current_config.context = []
        current_config.toolkits = []
        current_config.mcp_servers = []
        current_config.assistant_ids = []
        current_config.conversation_starters = []
        current_config.bedrock = None
        current_config.agent_card = None

        request = AssistantRequest(
            name="Test Assistant",
            description="New description",
            system_prompt="Updated prompt",
            llm_model_type="gpt-4",
            custom_metadata={'key': 'new_value', 'new_key': 'data'},
        )

        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance
        mock_config_class.get_latest_version_number.return_value = 1
        mock_config_class.get_current_version.return_value = current_config

        # Act
        AssistantVersionService.create_new_version(
            assistant=mock_assistant, request=request, user=mock_user, change_notes="Major update"
        )

        # Assert
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['version_number'] == 2
        assert call_kwargs['description'] == "New description"
        assert call_kwargs['system_prompt'] == "Updated prompt"
        assert call_kwargs['custom_metadata'] == {'key': 'new_value', 'new_key': 'data'}
        assert call_kwargs['change_notes'] == "Major update"

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_create_new_version_clearing_metadata(self, mock_config_class, mock_assistant, mock_user):
        """Verify that metadata can be cleared by setting to None in new version"""
        # Arrange
        mock_assistant.version_count = 1
        mock_assistant.custom_metadata = {'key': 'value'}

        current_config = MagicMock(spec=AssistantConfiguration)
        current_config.custom_metadata = {'key': 'value'}
        current_config.description = "Test"
        current_config.system_prompt = "Test"
        current_config.llm_model_type = "gpt-4"
        current_config.temperature = 0.7
        current_config.top_p = 0.9
        current_config.context = []
        current_config.toolkits = []
        current_config.mcp_servers = []
        current_config.assistant_ids = []
        current_config.conversation_starters = []
        current_config.bedrock = None
        current_config.agent_card = None

        request = AssistantRequest(
            name="Test Assistant",
            description="Test",
            system_prompt="Test",
            llm_model_type="gpt-4",
            custom_metadata=None,
        )

        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        with patch.object(AssistantConfiguration, 'get_current_version', return_value=current_config):
            # Act
            AssistantVersionService.create_new_version(assistant=mock_assistant, request=request, user=mock_user)

        # Assert
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['custom_metadata'] is None
