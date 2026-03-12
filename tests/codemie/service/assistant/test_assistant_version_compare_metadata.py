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

"""Tests for metadata field in AssistantVersionCompareService"""

from unittest.mock import Mock, patch

from codemie.rest_api.models.assistant import AssistantConfiguration, AssistantRequest
from codemie.service.assistant.assistant_version_compare_service import AssistantVersionCompareService


class TestAssistantVersionCompareMetadata:
    """Test suite for metadata handling in version comparison."""

    def test_has_configuration_changes_with_metadata_change(self):
        """Test that metadata changes are detected as configuration changes."""
        # Mock current configuration
        current_config = Mock(spec=AssistantConfiguration)
        current_config.model_dump.return_value = {
            'id': 'test-id',
            'assistant_id': 'assistant-1',
            'version_number': 1,
            'description': 'Test',
            'system_prompt': 'Prompt',
            'llm_model_type': 'gpt-4',
            'temperature': None,
            'top_p': None,
            'context': [],
            'toolkits': [],
            'mcp_servers': [],
            'assistant_ids': [],
            'conversation_starters': [],
            'bedrock': None,
            'agent_card': None,
            'custom_metadata': {'key': 'old_value'},
            'created_date': None,
            'created_by': None,
            'change_notes': None,
        }

        # Mock request with different metadata
        request = AssistantRequest(
            name='Test',
            description='Test',
            system_prompt='Prompt',
            llm_model_type='gpt-4',
            custom_metadata={'key': 'new_value'},  # Changed metadata
        )

        with patch.object(AssistantConfiguration, 'get_current_version', return_value=current_config):
            has_changes = AssistantVersionCompareService.has_configuration_changes('assistant-1', request)

        assert has_changes is True

    def test_has_configuration_changes_metadata_added(self):
        """Test that adding metadata triggers version change."""
        # Mock current configuration without metadata
        current_config = Mock(spec=AssistantConfiguration)
        current_config.model_dump.return_value = {
            'id': 'test-id',
            'assistant_id': 'assistant-1',
            'version_number': 1,
            'description': 'Test',
            'system_prompt': 'Prompt',
            'llm_model_type': 'gpt-4',
            'temperature': None,
            'top_p': None,
            'context': [],
            'toolkits': [],
            'mcp_servers': [],
            'assistant_ids': [],
            'conversation_starters': [],
            'bedrock': None,
            'agent_card': None,
            'custom_metadata': None,  # No metadata
            'created_date': None,
            'created_by': None,
            'change_notes': None,
        }

        # Mock request with new metadata
        request = AssistantRequest(
            name='Test',
            description='Test',
            system_prompt='Prompt',
            llm_model_type='gpt-4',
            custom_metadata={'new_key': 'value'},  # Adding metadata
        )

        with patch.object(AssistantConfiguration, 'get_current_version', return_value=current_config):
            has_changes = AssistantVersionCompareService.has_configuration_changes('assistant-1', request)

        assert has_changes is True

    def test_has_configuration_changes_metadata_removed(self):
        """Test that removing metadata triggers version change."""
        # Mock current configuration with metadata
        current_config = Mock(spec=AssistantConfiguration)
        current_config.model_dump.return_value = {
            'id': 'test-id',
            'assistant_id': 'assistant-1',
            'version_number': 1,
            'description': 'Test',
            'system_prompt': 'Prompt',
            'llm_model_type': 'gpt-4',
            'temperature': None,
            'top_p': None,
            'context': [],
            'toolkits': [],
            'mcp_servers': [],
            'assistant_ids': [],
            'conversation_starters': [],
            'bedrock': None,
            'agent_card': None,
            'custom_metadata': {'key': 'value'},  # Has metadata
            'created_date': None,
            'created_by': None,
            'change_notes': None,
        }

        # Mock request without metadata
        request = AssistantRequest(
            name='Test',
            description='Test',
            system_prompt='Prompt',
            llm_model_type='gpt-4',
            custom_metadata=None,  # Removing metadata
        )

        with patch.object(AssistantConfiguration, 'get_current_version', return_value=current_config):
            has_changes = AssistantVersionCompareService.has_configuration_changes('assistant-1', request)

        assert has_changes is True

    def test_has_configuration_changes_metadata_unchanged(self):
        """Test that identical metadata does not trigger version change."""
        # Mock current configuration
        current_config = Mock(spec=AssistantConfiguration)
        current_config.model_dump.return_value = {
            'id': 'test-id',
            'assistant_id': 'assistant-1',
            'version_number': 1,
            'description': 'Test',
            'system_prompt': 'Prompt',
            'llm_model_type': 'gpt-4',
            'temperature': None,
            'top_p': None,
            'context': [],
            'toolkits': [],
            'mcp_servers': [],
            'assistant_ids': [],
            'conversation_starters': [],
            'bedrock': None,
            'agent_card': None,
            'custom_metadata': {'key': 'value'},
            'created_date': None,
            'created_by': None,
            'change_notes': None,
        }

        # Mock request with same metadata
        request = AssistantRequest(
            name='Test',
            description='Test',
            system_prompt='Prompt',
            llm_model_type='gpt-4',
            custom_metadata={'key': 'value'},  # Same metadata
        )

        with patch.object(AssistantConfiguration, 'get_current_version', return_value=current_config):
            has_changes = AssistantVersionCompareService.has_configuration_changes('assistant-1', request)

        assert has_changes is False

    def test_has_configuration_changes_nested_metadata_change(self):
        """Test that nested metadata changes are detected."""
        # Mock current configuration with nested metadata
        current_config = Mock(spec=AssistantConfiguration)
        current_config.model_dump.return_value = {
            'id': 'test-id',
            'assistant_id': 'assistant-1',
            'version_number': 1,
            'description': 'Test',
            'system_prompt': 'Prompt',
            'llm_model_type': 'gpt-4',
            'temperature': None,
            'top_p': None,
            'context': [],
            'toolkits': [],
            'mcp_servers': [],
            'assistant_ids': [],
            'conversation_starters': [],
            'bedrock': None,
            'agent_card': None,
            'custom_metadata': {'workflow': {'stage': 'development', 'priority': 1}},
            'created_date': None,
            'created_by': None,
            'change_notes': None,
        }

        # Mock request with changed nested value
        request = AssistantRequest(
            name='Test',
            description='Test',
            system_prompt='Prompt',
            llm_model_type='gpt-4',
            custom_metadata={
                'workflow': {
                    'stage': 'production',  # Changed
                    'priority': 1,
                }
            },
        )

        with patch.object(AssistantConfiguration, 'get_current_version', return_value=current_config):
            has_changes = AssistantVersionCompareService.has_configuration_changes('assistant-1', request)

        assert has_changes is True

    def test_prepare_for_comparison_includes_metadata(self):
        """Test that _prepare_for_comparison includes custom_metadata field."""
        config = AssistantConfiguration(
            id='test-id',
            assistant_id='assistant-1',
            version_number=1,
            description='Test',
            system_prompt='Prompt',
            custom_metadata={'key': 'value'},
        )

        prepared = AssistantVersionCompareService._prepare_for_comparison(config)

        assert 'custom_metadata' in prepared
        assert prepared['custom_metadata'] == {'key': 'value'}

    def test_prepare_for_comparison_excludes_metadata_fields(self):
        """Test that _prepare_for_comparison excludes non-comparable fields but includes custom_metadata."""
        config = AssistantConfiguration(
            id='test-id',
            assistant_id='assistant-1',
            version_number=1,
            description='Test',
            system_prompt='Prompt',
            custom_metadata={'key': 'value'},
            change_notes='Some notes',
        )

        prepared = AssistantVersionCompareService._prepare_for_comparison(config)

        # Should exclude these fields
        assert 'id' not in prepared
        assert 'assistant_id' not in prepared
        assert 'version_number' not in prepared
        assert 'created_date' not in prepared
        assert 'created_by' not in prepared
        assert 'change_notes' not in prepared

        # Should include custom_metadata
        assert 'custom_metadata' in prepared
        assert prepared['custom_metadata'] == {'key': 'value'}

    def test_compare_versions_with_metadata_difference(self):
        """Test comparing two versions with different metadata."""
        version1 = AssistantConfiguration(
            id='test-id-1',
            assistant_id='assistant-1',
            version_number=1,
            description='Test',
            system_prompt='Prompt',
            custom_metadata={'key': 'old_value'},
        )

        version2 = AssistantConfiguration(
            id='test-id-2',
            assistant_id='assistant-1',
            version_number=2,
            description='Test',
            system_prompt='Prompt',
            custom_metadata={'key': 'new_value'},
        )

        result = AssistantVersionCompareService.compare_versions('assistant-1', version1, version2)

        assert result.assistant_id == 'assistant-1'
        assert result.version1 == version1
        assert result.version2 == version2
        assert len(result.differences) > 0

    def test_compare_versions_with_empty_metadata(self):
        """Test comparing versions where one has empty metadata."""
        version1 = AssistantConfiguration(
            id='test-id-1',
            assistant_id='assistant-1',
            version_number=1,
            description='Test',
            system_prompt='Prompt',
            custom_metadata={},
        )

        version2 = AssistantConfiguration(
            id='test-id-2',
            assistant_id='assistant-1',
            version_number=2,
            description='Test',
            system_prompt='Prompt',
            custom_metadata=None,
        )

        result = AssistantVersionCompareService.compare_versions('assistant-1', version1, version2)

        # Should detect difference between {} and None
        assert len(result.differences) > 0
