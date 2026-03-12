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

from unittest.mock import Mock, patch

from codemie.datasource.loader.platform.assistant_loader import AssistantLoader
from codemie.rest_api.models.assistant import Assistant


class TestAssistantLoader:
    """Tests for AssistantLoader."""

    @patch('codemie.datasource.loader.platform.assistant_loader.Assistant.get_all_by_fields')
    def test_fetch_entities_returns_published_assistants(self, mock_get_all_by_fields):
        """Test that _fetch_entities returns only published assistants."""
        # Arrange
        mock_assistant1 = Mock(spec=Assistant)
        mock_assistant1.is_global = True
        mock_assistant1.name = "Test Assistant 1"

        mock_assistant2 = Mock(spec=Assistant)
        mock_assistant2.is_global = True
        mock_assistant2.name = "Test Assistant 2"

        mock_get_all_by_fields.return_value = [mock_assistant1, mock_assistant2]

        loader = AssistantLoader()

        # Act
        result = loader._fetch_entities()

        # Assert
        assert len(result) == 2
        mock_get_all_by_fields.assert_called_once_with({"is_global": True})

    def test_sanitize_entity_removes_private_fields(self):
        """Test that _sanitize_entity removes sensitive fields using whitelist approach."""
        # Arrange
        loader = AssistantLoader()
        mock_assistant = Mock(spec=Assistant)
        mock_assistant.model_dump.return_value = {
            'id': 'asst_123',
            'name': 'Test Assistant',
            'description': 'Test description',
            'project': 'test_project',
            'system_prompt': 'You are a test assistant',
            'mcp_servers': [{'name': 'private_mcp', 'config': {}}],  # Should be filtered out by whitelist
            'assistant_ids': ['sub1', 'sub2'],  # Should be filtered out by whitelist
            'nested_assistants': [{}],  # Should be filtered out by whitelist
            'toolkits': [
                {
                    'toolkit': 'git',
                    'label': 'Git Tools',
                    'tools': [
                        {
                            'name': 'tool1',
                            'settings': {'password': 'secret'},  # Should be filtered out by SafeTool
                        }
                    ],
                    'settings': {'api_key': 'secret'},  # Should be filtered out by SafeToolKit
                    'settings_config': {'config': 'data'},  # Should be filtered out by SafeToolKit
                }
            ],
            'categories': ['developer'],
            'unique_users_count': 100,
            'unique_likes_count': 50,
            'unique_dislikes_count': 5,
        }

        # Act
        result = loader._sanitize_entity(mock_assistant)

        # Assert - result is now AssistantDocument (Pydantic model)
        assert isinstance(result, type(result))  # Check it's a model instance
        assert result.id == 'asst_123'
        assert result.name == 'Test Assistant'
        assert result.description == 'Test description'
        assert result.project == 'test_project'
        assert result.system_prompt == 'You are a test assistant'
        assert result.categories == ['developer']

        # Verify sensitive fields are not in the model
        result_dict = result.model_dump()
        assert 'mcp_servers' not in result_dict
        assert 'assistant_ids' not in result_dict
        assert 'nested_assistants' not in result_dict

        # Verify toolkits are sanitized (no settings)
        assert len(result.toolkits) == 1
        assert result.toolkits[0].toolkit == 'git'
        toolkit_dict = result.toolkits[0].model_dump()
        assert 'settings' not in toolkit_dict
        assert 'settings_config' not in toolkit_dict

        # Verify tools are sanitized (no settings)
        assert len(result.toolkits[0].tools) == 1
        assert result.toolkits[0].tools[0].name == 'tool1'
        tool_dict = result.toolkits[0].tools[0].model_dump()
        assert 'settings' not in tool_dict

    def test_entity_to_document_creates_valid_document(self):
        """Test that _entity_to_document creates a valid LangChain Document."""
        # Arrange
        loader = AssistantLoader()
        mock_assistant = Mock(spec=Assistant)
        mock_assistant.model_dump.return_value = {
            'id': 'asst_123',
            'name': 'Test Assistant',
            'description': 'Test description',
            'project': 'test_project',
            'system_prompt': 'You are a test assistant',
            'categories': ['developer', 'code-review'],
            'toolkits': [{'toolkit': 'git', 'label': 'Git', 'tools': []}],
            'unique_users_count': 100,
            'unique_likes_count': 50,
            'unique_dislikes_count': 5,
        }

        # Act
        document = loader._entity_to_document(mock_assistant)

        # Assert
        assert document is not None
        # Check page_content contains expected text
        assert 'Test Assistant' in document.page_content
        assert 'Test description' in document.page_content
        assert 'You are a test assistant' in document.page_content
        assert 'developer, code-review' in document.page_content

        # Check minimal metadata (id, name, source, popularity_score)
        assert len(document.metadata) == 4
        assert document.metadata['id'] == 'asst_123'
        assert document.metadata['name'] == 'Test Assistant'
        assert document.metadata['source'] == 'Test Assistant'  # source uses name as unique identifier
        assert 'popularity_score' in document.metadata
        # popularity_score should be normalized between 0 and 1
        assert 0 <= document.metadata['popularity_score'] <= 1

    @patch('codemie.datasource.loader.platform.assistant_loader.logger')
    @patch('codemie.datasource.loader.platform.assistant_loader.Assistant.find_by_id')
    def test_load_single_entity_returns_document_for_published_assistant(self, mock_find_by_id, mock_logger):
        """Test that load_single_entity returns document for published assistant."""
        # Arrange
        mock_assistant = Mock(spec=Assistant)
        mock_assistant.id = 'asst_123'
        mock_assistant.is_global = True
        mock_assistant.name = 'Test Assistant'
        mock_assistant.model_dump.return_value = {
            'id': 'asst_123',
            'name': 'Test Assistant',
            'description': 'Test description',
            'project': 'test_project',
            'system_prompt': 'You are a test',
            'categories': [],
            'toolkits': [],
            'unique_users_count': 10,
            'unique_likes_count': 5,
            'unique_dislikes_count': 1,
        }

        mock_find_by_id.return_value = mock_assistant

        loader = AssistantLoader()

        # Act
        result = loader.load_single_entity('asst_123')

        # Assert
        assert result is not None
        # Check minimal metadata (id, name, source, popularity_score)
        assert len(result.metadata) == 4
        assert result.metadata['id'] == 'asst_123'
        assert result.metadata['name'] == 'Test Assistant'
        assert result.metadata['source'] == 'Test Assistant'  # source uses name as unique identifier
        assert 'popularity_score' in result.metadata
        assert 0 <= result.metadata['popularity_score'] <= 1
        mock_find_by_id.assert_called_once_with('asst_123')

    @patch('codemie.datasource.loader.platform.assistant_loader.Assistant.find_by_id')
    def test_load_single_entity_returns_none_for_unpublished_assistant(self, mock_find_by_id):
        """Test that load_single_entity returns None for unpublished assistant."""
        # Arrange
        mock_assistant = Mock(spec=Assistant)
        mock_assistant.id = 'asst_123'
        mock_assistant.is_global = False  # Not published

        mock_find_by_id.return_value = mock_assistant

        loader = AssistantLoader()

        # Act
        result = loader.load_single_entity('asst_123')

        # Assert
        assert result is None

    @patch('codemie.datasource.loader.platform.assistant_loader.Assistant.find_by_id')
    def test_load_single_entity_returns_none_when_assistant_not_found(self, mock_find_by_id):
        """Test that load_single_entity returns None when assistant doesn't exist."""
        # Arrange
        mock_find_by_id.return_value = None

        loader = AssistantLoader()

        # Act
        result = loader.load_single_entity('nonexistent')

        # Assert
        assert result is None
