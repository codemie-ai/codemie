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

"""EPMCDME-15114 (Task 6): disabled_context_tools must be carried through
version comparison, version creation, rollback, and migration — exactly like
`context`/`toolkits` already are at every one of these sites."""

from datetime import datetime, UTC
from unittest.mock import MagicMock, patch

from codemie.core.models import CreatedByUser
from codemie.rest_api.models.assistant import (
    Assistant,
    AssistantConfiguration,
    AssistantRequest,
)
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_version_compare_service import AssistantVersionCompareService
from codemie.service.assistant.assistant_version_migration_service import AssistantVersionMigrationService
from codemie.service.assistant.assistant_version_service import AssistantVersionService


class TestHasConfigurationChangesDeselectedDefaultTools:
    """Candidate dict in has_configuration_changes must include disabled_context_tools."""

    @patch('codemie.service.assistant.assistant_version_compare_service.AssistantConfiguration')
    def test_has_configuration_changes_true_when_only_deselected_tools_change(self, mock_config_class):
        current_config = MagicMock(spec=AssistantConfiguration)
        current_config.model_dump = MagicMock(
            return_value={
                'description': 'Desc',
                'system_prompt': 'Prompt',
                'llm_model_type': 'gpt-4',
                'enable_image_generation': False,
                'image_generation_model': None,
                'temperature': 0.7,
                'top_p': 0.9,
                'tools_tokens_size_limit': None,
                'context': [],
                'toolkits': [],
                'mcp_servers': [],
                'assistant_ids': [],
                'conversation_starters': [],
                'bedrock': None,
                'agent_card': None,
                'custom_metadata': None,
                'disabled_context_tools': [],
            }
        )
        mock_config_class.get_current_version.return_value = current_config

        request = AssistantRequest(
            name="Assistant",
            description="Desc",
            system_prompt="Prompt",
            llm_model_type="gpt-4",
            disabled_context_tools=["search_kb"],
        )

        assert AssistantVersionCompareService.has_configuration_changes("assistant-123", request) is True

    @patch('codemie.service.assistant.assistant_version_compare_service.AssistantConfiguration')
    def test_has_configuration_changes_false_when_deselected_tools_omitted(self, mock_config_class):
        """Omitting disabled_context_tools from a partial update must not look like a change."""
        current_config = MagicMock(spec=AssistantConfiguration)
        current_config.model_dump = MagicMock(
            return_value={
                'description': 'Desc',
                'system_prompt': 'Prompt',
                'llm_model_type': 'gpt-4',
                'enable_image_generation': False,
                'image_generation_model': None,
                'temperature': 0.7,
                'top_p': 0.9,
                'tools_tokens_size_limit': None,
                'context': [],
                'toolkits': [],
                'mcp_servers': [],
                'assistant_ids': [],
                'conversation_starters': [],
                'bedrock': None,
                'agent_card': None,
                'custom_metadata': None,
                'disabled_context_tools': ["search_kb"],
            }
        )
        mock_config_class.get_current_version.return_value = current_config

        # disabled_context_tools intentionally omitted from the request
        request = AssistantRequest(
            name="Assistant",
            description="Desc",
            system_prompt="Prompt",
            llm_model_type="gpt-4",
        )

        assert AssistantVersionCompareService.has_configuration_changes("assistant-123", request) is False


class TestCreateNewVersionDeselectedDefaultTools:
    """create_new_version must copy disabled_context_tools via the `versioned()` helper."""

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_create_new_version_carries_deselected_tools(self, mock_config_class):
        assistant = MagicMock(spec=Assistant)
        assistant.id = "assistant-123"
        assistant.disabled_context_tools = []
        mock_config_class.get_latest_version_number.return_value = 1
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        request = AssistantRequest(
            name="Assistant",
            system_prompt="Prompt",
            llm_model_type="gpt-4",
            disabled_context_tools=["search_kb"],
        )
        user = User(id="u1", username="u1", name="U1", project_names=["demo"])

        AssistantVersionService.create_new_version(assistant=assistant, request=request, user=user)

        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['disabled_context_tools'] == ["search_kb"]

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    def test_create_new_version_preserves_deselected_tools_when_omitted(self, mock_config_class):
        """A partial update omitting disabled_context_tools must preserve the assistant's
        existing value, not reset it to []."""
        assistant = MagicMock(spec=Assistant)
        assistant.id = "assistant-123"
        assistant.disabled_context_tools = ["get_repository_file_tree"]
        mock_config_class.get_latest_version_number.return_value = 1
        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        # disabled_context_tools intentionally omitted from the request
        request = AssistantRequest(name="Assistant", system_prompt="Prompt", llm_model_type="gpt-4")
        user = User(id="u1", username="u1", name="U1", project_names=["demo"])

        AssistantVersionService.create_new_version(assistant=assistant, request=request, user=user)

        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['disabled_context_tools'] == ["get_repository_file_tree"]


class TestRollbackRestoresDeselectedTools:
    """rollback_to_version must restore disabled_context_tools onto the new version AND
    the live Assistant record (via update_assistant_config_fields)."""

    @patch('codemie.service.assistant.assistant_version_service.AssistantConfiguration')
    @patch.object(AssistantVersionService, 'get_version')
    def test_rollback_restores_deselected_tools_onto_assistant(self, mock_get_version, mock_config_class):
        assistant = MagicMock(spec=Assistant)
        assistant.id = "assistant-123"
        assistant.version_count = 2
        assistant.disabled_context_tools = []

        target_config = MagicMock(spec=AssistantConfiguration)
        target_config.disabled_context_tools = ["get_repository_file_tree"]
        target_config.mcp_servers = []
        # rollback_to_version restores MCP links via target_config.model_copy(update={"mcp_servers": ...});
        # a MagicMock's model_copy would otherwise drop the attributes set above.
        target_config.model_copy.return_value = target_config
        mock_get_version.return_value = target_config

        mock_new_config = MagicMock()
        mock_config_class.return_value = mock_new_config

        user = User(id="u1", username="u1", name="U1", project_names=["demo"])

        AssistantVersionService.rollback_to_version(assistant=assistant, target_version_number=1, user=user)

        # new version row carries it
        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['disabled_context_tools'] == ["get_repository_file_tree"]

        # live assistant record is restored too
        assert assistant.disabled_context_tools == ["get_repository_file_tree"]

    def test_update_assistant_config_fields_copies_deselected_tools(self):
        assistant = MagicMock(spec=Assistant)
        config = MagicMock(spec=AssistantConfiguration)
        config.disabled_context_tools = ["search_code_repo"]

        AssistantVersionService.update_assistant_config_fields(assistant, config)

        assert assistant.disabled_context_tools == ["search_code_repo"]


class TestMigrationCarriesDeselectedTools:
    """All three migration _create_* methods must copy disabled_context_tools from assistant."""

    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_migration_carries_deselected_tools_from_assistant(self, mock_config_class):
        assistant = MagicMock(spec=Assistant)
        assistant.id = "assistant-123"
        assistant.system_prompt_history = []
        assistant.disabled_context_tools = ["search_code_repo"]
        assistant.created_date = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        assistant.updated_date = None
        assistant.created_by = CreatedByUser(id="user-1", username="user1", name="User One")

        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        AssistantVersionMigrationService._create_single_version_from_current(assistant)

        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['disabled_context_tools'] == ["search_code_repo"]

    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_create_version_from_history_carries_deselected_tools(self, mock_config_class):
        from codemie.rest_api.models.assistant import SystemPromptHistory

        assistant = MagicMock(spec=Assistant)
        assistant.id = "assistant-123"
        assistant.disabled_context_tools = ["search_code_repo"]
        history_entry = SystemPromptHistory(
            system_prompt="Old Prompt",
            date=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            created_by=CreatedByUser(id="user-1", username="user1", name="User One"),
        )

        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        AssistantVersionMigrationService._create_version_from_history(
            assistant=assistant, version_number=1, history_entry=history_entry
        )

        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['disabled_context_tools'] == ["search_code_repo"]

    @patch('codemie.service.assistant.assistant_version_migration_service.AssistantConfiguration')
    def test_create_version_from_current_carries_deselected_tools(self, mock_config_class):
        assistant = MagicMock(spec=Assistant)
        assistant.id = "assistant-123"
        assistant.disabled_context_tools = ["search_code_repo"]
        assistant.updated_date = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        assistant.created_date = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

        mock_config_instance = MagicMock()
        mock_config_class.return_value = mock_config_instance

        AssistantVersionMigrationService._create_version_from_current(assistant=assistant, version_number=2)

        call_kwargs = mock_config_class.call_args[1]
        assert call_kwargs['disabled_context_tools'] == ["search_code_repo"]
