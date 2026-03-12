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

from codemie.datasource.platform.assistant_datasource_processor import AssistantDatasourceProcessor
from codemie.service.constants import FullDatasourceTypes


class TestAssistantDatasourceProcessor:
    """Tests for AssistantDatasourceProcessor."""

    def test_index_type_is_set_correctly(self):
        """Test that INDEX_TYPE is set to platform assistant type."""
        assert FullDatasourceTypes.PLATFORM_ASSISTANT.value == AssistantDatasourceProcessor.INDEX_TYPE

    @patch('codemie.datasource.platform.assistant_datasource_processor.AssistantLoader')
    @patch('codemie.datasource.platform.base_platform_processor.IndexInfo')
    def test_init_loader_returns_assistant_loader(self, mock_index_info, mock_assistant_loader_class):
        """Test that _init_loader returns AssistantLoader instance."""
        # Arrange
        mock_loader = Mock()
        mock_assistant_loader_class.return_value = mock_loader

        # Mock IndexInfo.filter_by_project_and_repo to return empty list
        mock_index_info.filter_by_project_and_repo.return_value = []

        processor = AssistantDatasourceProcessor(datasource_name='test_datasource')

        # Act
        result = processor._init_loader()

        # Assert
        assert result == mock_loader
        mock_assistant_loader_class.assert_called_once()

    @patch('codemie.datasource.platform.base_platform_processor.logger')
    @patch('codemie.datasource.platform.base_platform_processor.ElasticSearchClient.get_client')
    @patch('codemie.datasource.platform.base_platform_processor.IndexInfo')
    def test_remove_single_entity_removes_from_index(self, mock_index_info, mock_es_client, mock_logger):
        """Test that remove_single_entity removes assistant from ES index and updates metadata."""
        # Arrange
        mock_index = Mock()
        mock_index.id = 'idx_123'
        mock_index.current_state = 5
        mock_index.complete_state = 5
        mock_index_info.filter_by_project_and_repo.return_value = [mock_index]

        mock_client = Mock()
        mock_es_client.return_value = mock_client

        processor = AssistantDatasourceProcessor(datasource_name='test_datasource')

        # Act
        processor.remove_single_entity('asst_123', 'Test Assistant')

        # Assert
        # Verify ES delete was called
        mock_client.delete.assert_called_once()
        call_kwargs = mock_client.delete.call_args[1]
        assert call_kwargs['id'] == 'asst_123'

        # Verify decrease_progress was called with correct parameters
        mock_index.decrease_progress.assert_called_once_with(chunks_count=1, processed_file='Test Assistant')

    @patch('codemie.datasource.platform.base_platform_processor.logger')
    @patch('codemie.datasource.platform.base_platform_processor.ElasticSearchClient.get_client')
    @patch('codemie.datasource.platform.base_platform_processor.IndexInfo')
    def test_remove_single_entity_handles_es_error(self, mock_index_info, mock_es_client, mock_logger):
        """Test that remove_single_entity handles Elasticsearch errors correctly."""
        # Arrange
        mock_index = Mock()
        mock_index.id = 'idx_123'
        mock_index_info.filter_by_project_and_repo.return_value = [mock_index]

        mock_client = Mock()
        mock_client.delete.side_effect = Exception("ES error")
        mock_es_client.return_value = mock_client

        processor = AssistantDatasourceProcessor(datasource_name='test_datasource')

        # Act & Assert
        try:
            processor.remove_single_entity('asst_123', 'Test Assistant')
            raise AssertionError("Expected exception to be raised")
        except Exception as e:
            assert str(e) == "ES error"

        # Verify decrease_progress was NOT called due to error
        mock_index.decrease_progress.assert_not_called()
