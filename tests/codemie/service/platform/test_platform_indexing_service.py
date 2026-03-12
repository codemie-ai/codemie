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

from codemie.service.platform.platform_indexing_service import PlatformIndexingService
from codemie.datasource.platform.assistant_datasource_processor import AssistantDatasourceProcessor


class TestPlatformIndexingService:
    """Tests for PlatformIndexingService."""

    def test_platform_datasources_contains_marketplace_assistants(self):
        """Test that PLATFORM_DATASOURCES contains marketplace_assistants."""
        assert 'marketplace_assistants' in PlatformIndexingService.PLATFORM_DATASOURCES
        assert PlatformIndexingService.PLATFORM_DATASOURCES['marketplace_assistants'] == AssistantDatasourceProcessor

    @patch('codemie.service.platform.platform_indexing_service.AssistantDatasourceProcessor')
    def test_sync_datasource_creates_processor_and_runs_indexing(self, mock_processor_class):
        """Test that _sync_datasource creates processor and runs indexing."""
        # Arrange
        mock_processor = Mock()
        mock_index = Mock()
        mock_index.current_state = 5
        mock_processor.index = mock_index
        mock_processor_class.return_value = mock_processor

        # Act
        result = PlatformIndexingService._sync_datasource('test_datasource', mock_processor_class)

        # Assert
        assert result == 5
        mock_processor_class.assert_called_once_with(datasource_name='test_datasource', user=None)
        mock_processor.process.assert_called_once()

    @patch('codemie.service.platform.platform_indexing_service.logger')
    @patch('codemie.service.platform.platform_indexing_service.AssistantDatasourceProcessor')
    def test_sync_all_handles_exceptions_gracefully(self, mock_processor_class, mock_logger):
        """Test that sync_all_platform_datasources handles exceptions and continues."""
        # Arrange
        mock_processor_class.side_effect = Exception("Test error")

        # PLATFORM_DATASOURCES captures the real class at definition time, so patch it directly
        # to ensure the mock processor is used during iteration.
        with patch.object(
            PlatformIndexingService,
            'PLATFORM_DATASOURCES',
            {'marketplace_assistants': mock_processor_class},
        ):
            # Act
            results = PlatformIndexingService.sync_all_platform_datasources()

        # Assert - should not raise, should return 0 for failed datasource
        assert 'marketplace_assistants' in results
        assert results['marketplace_assistants'] == 0

    @patch('codemie.service.platform.platform_indexing_service.AssistantDatasourceProcessor')
    def test_index_single_assistant_creates_processor_and_indexes(self, mock_processor_class):
        """Test that index_single_assistant creates processor and indexes entity."""
        # Arrange
        mock_processor = Mock()
        mock_processor_class.return_value = mock_processor

        # Act
        PlatformIndexingService.index_single_assistant('asst_123')

        # Assert
        mock_processor_class.assert_called_once_with(datasource_name='marketplace_assistants', user=None)
        mock_processor.index_single_entity.assert_called_once_with('asst_123', is_update=False)

    @patch('codemie.service.platform.platform_indexing_service.AssistantDatasourceProcessor')
    def test_index_single_assistant_with_is_update_flag(self, mock_processor_class):
        """Test that index_single_assistant passes is_update flag correctly."""
        # Arrange
        mock_processor = Mock()
        mock_processor_class.return_value = mock_processor

        # Act
        PlatformIndexingService.index_single_assistant('asst_123', is_update=True)

        # Assert
        mock_processor_class.assert_called_once_with(datasource_name='marketplace_assistants', user=None)
        mock_processor.index_single_entity.assert_called_once_with('asst_123', is_update=True)

    @patch('codemie.service.platform.platform_indexing_service.AssistantDatasourceProcessor')
    def test_remove_single_assistant_creates_processor_and_removes(self, mock_processor_class):
        """Test that remove_single_assistant creates processor and removes entity."""
        # Arrange
        mock_processor = Mock()
        mock_processor_class.return_value = mock_processor

        # Act
        PlatformIndexingService.remove_single_assistant('asst_123', 'Test Assistant')

        # Assert
        mock_processor_class.assert_called_once_with(datasource_name='marketplace_assistants', user=None)
        mock_processor.remove_single_entity.assert_called_once_with('asst_123', 'Test Assistant')
