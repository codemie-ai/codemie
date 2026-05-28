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

from unittest.mock import MagicMock, patch


class TestBaseDatasourceProcessorLLMContext:
    """Verify that _setup_processing_context passes the index object to set_llm_context."""

    def _make_processor(self, project_space_visible: bool):
        processor = MagicMock()
        processor.index = MagicMock(spec=['id', 'project_name', 'project_space_visible'])
        processor.index.project_name = "test-proj"
        processor.index.project_space_visible = project_space_visible
        processor.user = MagicMock()
        processor.user.id = "user-abc"
        processor.is_full_reindex = False
        processor.is_incremental_reindex = False
        processor.is_resume_indexing = False
        processor.request_uuid = "req-uuid-1"
        processor.callbacks = []
        return processor

    @patch('codemie.service.llm_service.utils.set_llm_context')
    @patch('codemie.datasource.base_datasource_processor.set_logging_info')
    @patch('codemie.datasource.base_datasource_processor.DatasourceMonitoringCallback')
    def test_private_datasource_passes_index_to_set_llm_context(
        self, mock_callback_cls, mock_set_logging, mock_set_llm_context
    ):
        from codemie.datasource.base_datasource_processor import BaseDatasourceProcessor

        processor = self._make_processor(project_space_visible=False)

        BaseDatasourceProcessor._setup_processing_context(processor)

        mock_set_llm_context.assert_called_once_with(processor.index, None, processor.user)

    @patch('codemie.service.llm_service.utils.set_llm_context')
    @patch('codemie.datasource.base_datasource_processor.set_logging_info')
    @patch('codemie.datasource.base_datasource_processor.DatasourceMonitoringCallback')
    def test_shared_datasource_passes_index_to_set_llm_context(
        self, mock_callback_cls, mock_set_logging, mock_set_llm_context
    ):
        from codemie.datasource.base_datasource_processor import BaseDatasourceProcessor

        processor = self._make_processor(project_space_visible=True)

        BaseDatasourceProcessor._setup_processing_context(processor)

        mock_set_llm_context.assert_called_once_with(processor.index, None, processor.user)
