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

"""Unit tests for AssistantVersionMigrationService"""

from datetime import datetime, UTC
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.models import CreatedByUser
from codemie.rest_api.models.assistant import (
    Assistant,
    SystemPromptHistory,
)
from codemie.service.assistant.assistant_version_migration_service import AssistantVersionMigrationService


@pytest.fixture
def mock_assistant_no_history():
    """Mock assistant with no system_prompt_history"""
    assistant = MagicMock(spec=Assistant)
    assistant.id = "assistant-no-history"
    assistant.name = "Test Assistant"
    assistant.description = "Test Description"
    assistant.system_prompt = "Current Prompt"
    assistant.system_prompt_history = []
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
    assistant.updated_date = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    assistant.created_by = CreatedByUser(id="user-1", username="user1", name="User One")
    return assistant


@pytest.fixture
def mock_assistant_with_history():
    """Mock assistant with system_prompt_history"""
    assistant = MagicMock(spec=Assistant)
    assistant.id = "assistant-with-history"
    assistant.name = "Test Assistant"
    assistant.description = "Test Description"
    assistant.system_prompt = "Current Prompt (v3)"
    assistant.system_prompt_history = [
        SystemPromptHistory(
            system_prompt="Old Prompt 1",
            date=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            created_by=CreatedByUser(id="user-1", username="user1", name="User One"),
        ),
        SystemPromptHistory(
            system_prompt="Old Prompt 2",
            date=datetime(2024, 3, 1, 12, 0, 0, tzinfo=UTC),
            created_by=CreatedByUser(id="user-2", username="user2", name="User Two"),
        ),
    ]
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
    assistant.updated_date = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    assistant.created_by = CreatedByUser(id="user-1", username="user1", name="User One")
    return assistant


class TestMigrateAssistantToVersions:
    """Tests for migrate_assistant_to_versions method"""

    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_migrate_assistant_no_history(self, mock_config_class, mock_assistant_no_history):
        """Test migration of assistant with no history creates single version"""
        # Setup
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Execute
        result = AssistantVersionMigrationService.migrate_assistant_to_versions(mock_assistant_no_history)

        # Verify
        assert result == 1
        mock_config_class.assert_called_once()
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['assistant_id'] == mock_assistant_no_history.id
        assert call_kwargs['version_number'] == 1
        assert call_kwargs['system_prompt'] == mock_assistant_no_history.system_prompt
        assert call_kwargs['change_notes'] == "Initial version (migrated)"
        mock_config_instance.save.assert_called_once()
        mock_assistant_no_history.update.assert_called_once()
        assert mock_assistant_no_history.version_count == 1

    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_migrate_assistant_with_history(self, mock_config_class, mock_assistant_with_history):
        """Test migration of assistant with history creates multiple versions"""
        # Setup
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Execute
        result = AssistantVersionMigrationService.migrate_assistant_to_versions(mock_assistant_with_history)

        # Verify
        assert result == 3  # 2 history entries + 1 current
        assert mock_config_class.call_count == 3
        mock_assistant_with_history.update.assert_called_once()
        assert mock_assistant_with_history.version_count == 3

    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_migrate_creates_versions_in_correct_order(self, mock_config_class, mock_assistant_with_history):
        """Test that versions are created in chronological order (oldest first)"""
        # Setup
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Execute
        AssistantVersionMigrationService.migrate_assistant_to_versions(mock_assistant_with_history)

        # Verify version numbers are sequential
        calls = mock_config_class.call_args_list
        assert calls[0][1]['version_number'] == 1
        assert calls[0][1]['system_prompt'] == "Old Prompt 2"  # Reversed, so oldest first
        assert calls[1][1]['version_number'] == 2
        assert calls[1][1]['system_prompt'] == "Old Prompt 1"
        assert calls[2][1]['version_number'] == 3
        assert calls[2][1]['system_prompt'] == "Current Prompt (v3)"

    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_migrate_preserves_history_metadata(self, mock_config_class, mock_assistant_with_history):
        """Test that migration preserves created_by and date from history"""
        # Setup
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Execute
        AssistantVersionMigrationService.migrate_assistant_to_versions(mock_assistant_with_history)

        # Verify first history entry metadata preserved
        calls = mock_config_class.call_args_list
        first_call_kwargs = calls[0][1]
        assert first_call_kwargs['created_by'].id == "user-2"
        assert first_call_kwargs['created_date'] == datetime(2024, 3, 1, 12, 0, 0, tzinfo=UTC)
        assert first_call_kwargs['change_notes'] == "Migrated from system_prompt_history"


class TestCreateSingleVersionFromCurrent:
    """Tests for _create_single_version_from_current method"""

    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_create_single_version_success(self, mock_config_class, mock_assistant_no_history):
        """Test successful creation of single version from current state"""
        # Setup
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Execute
        result = AssistantVersionMigrationService._create_single_version_from_current(mock_assistant_no_history)

        # Verify
        assert result == 1
        mock_config_class.assert_called_once()
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['version_number'] == 1
        assert call_kwargs['description'] == mock_assistant_no_history.description
        assert call_kwargs['system_prompt'] == mock_assistant_no_history.system_prompt
        assert call_kwargs['llm_model_type'] == mock_assistant_no_history.llm_model_type
        mock_config_instance.save.assert_called_once()
        mock_assistant_no_history.update.assert_called_once()

    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_create_single_version_copies_all_fields(self, mock_config_class, mock_assistant_no_history):
        """Test that all configuration fields are copied"""
        # Setup
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Execute
        result = AssistantVersionMigrationService._create_single_version_from_current(mock_assistant_no_history)

        # Verify return value
        assert result == 1

        # Verify all fields copied
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['description'] == mock_assistant_no_history.description
        assert call_kwargs['system_prompt'] == mock_assistant_no_history.system_prompt
        assert call_kwargs['llm_model_type'] == mock_assistant_no_history.llm_model_type
        assert call_kwargs['temperature'] == mock_assistant_no_history.temperature
        assert call_kwargs['top_p'] == mock_assistant_no_history.top_p
        assert call_kwargs['context'] == mock_assistant_no_history.context
        assert call_kwargs['toolkits'] == mock_assistant_no_history.toolkits
        assert call_kwargs['mcp_servers'] == mock_assistant_no_history.mcp_servers
        assert call_kwargs['assistant_ids'] == mock_assistant_no_history.assistant_ids
        assert call_kwargs['conversation_starters'] == mock_assistant_no_history.conversation_starters

        # Verify version_count was updated
        assert mock_assistant_no_history.version_count == 1


class TestCreateVersionFromHistory:
    """Tests for _create_version_from_history method"""

    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_create_version_from_history_success(self, mock_config_class, mock_assistant_with_history):
        """Test successful creation of version from history entry"""
        # Setup
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance
        history_entry = mock_assistant_with_history.system_prompt_history[0]

        # Execute
        result = AssistantVersionMigrationService._create_version_from_history(
            assistant=mock_assistant_with_history, version_number=1, history_entry=history_entry
        )

        # Verify return value is the created config
        assert result == mock_config_instance

        # Verify configuration creation
        mock_config_class.assert_called_once()
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['version_number'] == 1
        assert call_kwargs['system_prompt'] == history_entry.system_prompt
        assert call_kwargs['created_date'] == history_entry.date
        assert call_kwargs['created_by'] == history_entry.created_by
        assert call_kwargs['change_notes'] == "Migrated from system_prompt_history"
        mock_config_instance.save.assert_called_once()

    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_create_version_from_history_uses_current_config_fields(
        self, mock_config_class, mock_assistant_with_history
    ):
        """Test that non-prompt fields come from current assistant state"""
        # Setup
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance
        history_entry = mock_assistant_with_history.system_prompt_history[0]

        # Execute
        result = AssistantVersionMigrationService._create_version_from_history(
            assistant=mock_assistant_with_history, version_number=1, history_entry=history_entry
        )

        # Verify return value
        assert result == mock_config_instance

        # Verify non-prompt fields come from current assistant
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['description'] == mock_assistant_with_history.description
        assert call_kwargs['llm_model_type'] == mock_assistant_with_history.llm_model_type
        assert call_kwargs['temperature'] == mock_assistant_with_history.temperature
        assert call_kwargs['toolkits'] == mock_assistant_with_history.toolkits

        # But system_prompt comes from history
        assert call_kwargs['system_prompt'] == history_entry.system_prompt


class TestCreateVersionFromCurrent:
    """Tests for _create_version_from_current method"""

    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_create_version_from_current_success(self, mock_config_class, mock_assistant_with_history):
        """Test successful creation of version from current state"""
        # Setup
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Execute
        result = AssistantVersionMigrationService._create_version_from_current(
            assistant=mock_assistant_with_history, version_number=3
        )

        # Verify return value is the created config
        assert result == mock_config_instance

        # Verify configuration creation
        mock_config_class.assert_called_once()
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['version_number'] == 3
        assert call_kwargs['system_prompt'] == mock_assistant_with_history.system_prompt
        assert call_kwargs['change_notes'] == "Current version (migrated)"
        mock_config_instance.save.assert_called_once()

    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_create_version_from_current_uses_updated_date(self, mock_config_class, mock_assistant_with_history):
        """Test that updated_date is used if available"""
        # Setup
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Execute
        result = AssistantVersionMigrationService._create_version_from_current(
            assistant=mock_assistant_with_history, version_number=1
        )

        # Verify return value
        assert result == mock_config_instance

        # Verify updated_date used (not created_date)
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['created_date'] == mock_assistant_with_history.updated_date
        assert call_kwargs['created_date'] != mock_assistant_with_history.created_date

    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_create_version_from_current_falls_back_to_created_date(self, mock_config_class, mock_assistant_no_history):
        """Test that created_date is used if updated_date is None"""
        # Setup
        mock_assistant_no_history.updated_date = None
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Execute
        result = AssistantVersionMigrationService._create_version_from_current(
            assistant=mock_assistant_no_history, version_number=1
        )

        # Verify return value
        assert result == mock_config_instance

        # Verify created_date used as fallback when updated_date is None
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['created_date'] == mock_assistant_no_history.created_date

        # Also verify the assistant was set up with None updated_date
        assert mock_assistant_no_history.updated_date is None


class TestMigrateAllAssistants:
    """Tests for migrate_all_assistants method"""

    @patch('codemie.service.assistant.assistant_version_migration_service.Assistant')
    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_migrate_all_assistants_success(self, mock_config_class, mock_assistant_class, mock_assistant_no_history):
        """Test successful migration of all assistants"""
        # Setup
        mock_assistant_class.get_all.return_value = [mock_assistant_no_history]
        mock_config_class.count_versions.return_value = 0
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Execute
        result = AssistantVersionMigrationService.migrate_all_assistants()

        # Verify
        assert result['total_assistants'] == 1
        assert result['migrated'] == 1
        assert result['skipped'] == 0
        assert result['errors'] == 0
        assert result['total_versions_created'] == 1

    @patch('codemie.service.assistant.assistant_version_migration_service.Assistant')
    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_migrate_all_assistants_skips_already_migrated(
        self, mock_config_class, mock_assistant_class, mock_assistant_no_history
    ):
        """Test that already migrated assistants are skipped"""
        # Setup
        mock_assistant_class.get_all.return_value = [mock_assistant_no_history]
        mock_config_class.count_versions.return_value = 3  # Already has versions

        # Execute
        result = AssistantVersionMigrationService.migrate_all_assistants()

        # Verify
        assert result['total_assistants'] == 1
        assert result['migrated'] == 0
        assert result['skipped'] == 1
        assert result['errors'] == 0

    @patch('codemie.service.assistant.assistant_version_migration_service.Assistant')
    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_migrate_all_assistants_handles_errors(
        self, mock_config_class, mock_assistant_class, mock_assistant_no_history
    ):
        """Test that errors during migration are handled gracefully"""
        # Setup
        mock_assistant_class.get_all.return_value = [mock_assistant_no_history]
        mock_config_class.count_versions.return_value = 0
        mock_config_class.side_effect = Exception("Test error")

        # Execute
        result = AssistantVersionMigrationService.migrate_all_assistants()

        # Verify
        assert result['total_assistants'] == 1
        assert result['migrated'] == 0
        assert result['errors'] == 1

    @patch('codemie.service.assistant.assistant_version_migration_service.Assistant')
    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_migrate_all_assistants_multiple_assistants(self, mock_config_class, mock_assistant_class):
        """Test migration of multiple assistants"""
        # Setup
        assistant1 = MagicMock(spec=Assistant)
        assistant1.id = "assistant-1"
        assistant1.system_prompt_history = []
        assistant1.description = "Test"
        assistant1.system_prompt = "Test"
        assistant1.llm_model_type = "gpt-4"
        assistant1.temperature = 0.7
        assistant1.top_p = 0.9
        assistant1.context = []
        assistant1.toolkits = []
        assistant1.mcp_servers = []
        assistant1.assistant_ids = []
        assistant1.conversation_starters = []
        assistant1.bedrock = None
        assistant1.agent_card = None
        assistant1.created_date = datetime.utcnow()
        assistant1.updated_date = None
        assistant1.created_by = None

        assistant2 = MagicMock(spec=Assistant)
        assistant2.id = "assistant-2"
        assistant2.system_prompt_history = [
            SystemPromptHistory(
                system_prompt="Old", date=datetime.utcnow(), created_by=CreatedByUser(id="u", username="u", name="U")
            )
        ]
        assistant2.description = "Test"
        assistant2.system_prompt = "Test"
        assistant2.llm_model_type = "gpt-4"
        assistant2.temperature = 0.7
        assistant2.top_p = 0.9
        assistant2.context = []
        assistant2.toolkits = []
        assistant2.mcp_servers = []
        assistant2.assistant_ids = []
        assistant2.conversation_starters = []
        assistant2.bedrock = None
        assistant2.agent_card = None
        assistant2.created_date = datetime.utcnow()
        assistant2.updated_date = None
        assistant2.created_by = None

        mock_assistant_class.get_all.return_value = [assistant1, assistant2]
        mock_config_class.count_versions.return_value = 0
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Execute
        result = AssistantVersionMigrationService.migrate_all_assistants()

        # Verify
        assert result['total_assistants'] == 2
        assert result['migrated'] == 2
        assert result['total_versions_created'] == 3  # 1 for assistant1, 2 for assistant2


class TestIntegration:
    """Integration tests for migration workflow"""

    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_full_migration_no_history(self, mock_config_class, mock_assistant_no_history):
        """Test complete migration flow for assistant without history"""
        # Setup
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Execute
        result = AssistantVersionMigrationService.migrate_assistant_to_versions(mock_assistant_no_history)

        # Verify complete flow
        assert result == 1
        assert mock_config_class.call_count == 1
        assert mock_config_instance.save.call_count == 1
        assert mock_assistant_no_history.update.call_count == 1
        assert mock_assistant_no_history.version_count == 1

    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_full_migration_with_history(self, mock_config_class, mock_assistant_with_history):
        """Test complete migration flow for assistant with history"""
        # Setup
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # Execute
        result = AssistantVersionMigrationService.migrate_assistant_to_versions(mock_assistant_with_history)

        # Verify complete flow
        assert result == 3
        assert mock_config_class.call_count == 3
        assert mock_config_instance.save.call_count == 3
        assert mock_assistant_with_history.update.call_count == 1
        assert mock_assistant_with_history.version_count == 3

        # Verify version ordering
        calls = mock_config_class.call_args_list
        assert calls[0][1]['version_number'] == 1
        assert calls[1][1]['version_number'] == 2
        assert calls[2][1]['version_number'] == 3
