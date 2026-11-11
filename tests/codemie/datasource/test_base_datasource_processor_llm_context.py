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

import pytest

from codemie.service.spend.origin_context import current_origin
from codemie.service.spend.traffic_class import TrafficOrigin


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

    @pytest.mark.parametrize(
        ("email", "username", "expected"),
        [
            ("a@x.com", "alice", "alice"),
            ("a@x.com", "", "a@x.com"),
            ("", "", "user-abc"),
        ],
    )
    @patch('codemie.service.llm_service.utils.set_llm_context')
    @patch('codemie.datasource.base_datasource_processor.set_logging_info')
    @patch('codemie.datasource.base_datasource_processor.DatasourceMonitoringCallback')
    def test_logging_username_prefers_username_then_email_then_id(
        self, mock_callback_cls, mock_set_logging, mock_set_llm_context, email, username, expected
    ):
        from codemie.datasource.base_datasource_processor import BaseDatasourceProcessor

        processor = self._make_processor(project_space_visible=False)
        processor.user.email = email
        processor.user.username = username

        BaseDatasourceProcessor._setup_processing_context(processor)

        mock_set_logging.assert_called_once_with(uuid="req-uuid-1", user_id="user-abc", username=expected)


class TestBaseDatasourceProcessorSpendOrigin:
    """The indexing origin is in force for the whole run, including where the embedding store is built."""

    def _make_processor(self):
        processor = MagicMock()
        processor.index.project_name = "test-proj"
        processor._pre_scheduled = True
        processor.datasource_name = "test-datasource"
        return processor

    @patch('codemie.datasource.base_datasource_processor.get_elasticsearch')
    @patch('codemie.datasource.base_datasource_processor.propagated_span')
    def test_origin_is_visible_where_the_embedding_store_is_built(self, mock_span, mock_get_elasticsearch):
        """The store is built on the run's own thread, so a construction-time read sees the origin."""
        from codemie.datasource.base_datasource_processor import BaseDatasourceProcessor

        processor = self._make_processor()
        observed = []
        mock_get_elasticsearch.side_effect = lambda *_args, **_kwargs: observed.append(current_origin())
        mock_span.return_value.__enter__ = MagicMock(return_value=None)
        mock_span.return_value.__exit__ = MagicMock(return_value=False)
        processor._process.side_effect = lambda: BaseDatasourceProcessor._get_store_by_index("idx", "model")

        BaseDatasourceProcessor.process(processor)

        assert observed == [TrafficOrigin.DATASOURCE_INDEXING]
        assert current_origin() is None
