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

import pytest
from unittest.mock import MagicMock, patch
from langchain_core.documents import Document

from codemie.datasource.base_datasource_processor import BaseDatasourceProcessor
from codemie.rest_api.models.guardrail import GuardrailEntity, GuardrailSource, Guardrail
from codemie.rest_api.models.index import GuardrailBlockedException, IndexInfo
from codemie.rest_api.security.user import User


class ConcreteDatasourceProcessor(BaseDatasourceProcessor):
    """Concrete implementation for testing purposes."""

    SOURCE = "test_source"

    @property
    def _index_name(self) -> str:
        return "test_index"

    def _init_loader(self):
        return MagicMock()

    def _init_index(self):
        self.index = MagicMock(spec=IndexInfo)
        self.index.id = "test_index_id"
        self.index.project_name = "test_project"


@pytest.fixture
def mock_user():
    """Create a mock user for testing."""
    user = MagicMock(spec=User)
    user.id = "user123"
    user.username = "test@example.com"
    return user


@pytest.fixture
def mock_index():
    """Create a mock index for testing."""
    index = MagicMock(spec=IndexInfo)
    index.id = "index123"
    index.project_name = "test_project"
    return index


@pytest.fixture
def processor(mock_user, mock_index):
    """Create a processor instance for testing."""
    processor = ConcreteDatasourceProcessor(
        datasource_name="test_datasource",
        user=mock_user,
        index=mock_index,
    )
    return processor


@pytest.fixture
def sample_documents():
    """Create sample documents for testing."""
    return {
        "doc1.txt": [
            Document(page_content="This is the first chunk", metadata={"source": "doc1.txt", "chunk_num": 1}),
            Document(page_content="This is the second chunk", metadata={"source": "doc1.txt", "chunk_num": 2}),
        ],
        "doc2.txt": [
            Document(page_content="Another document chunk", metadata={"source": "doc2.txt", "chunk_num": 1}),
        ],
    }


class TestApplyGuardrailsForDict:
    """Tests for _apply_guardrails_for_dict method."""

    def test_no_guardrails_returns_unchanged(self, processor: ConcreteDatasourceProcessor, sample_documents):
        """Test that documents are returned unchanged when no guardrails are present."""
        with patch.object(
            processor, '_validate_index_and_get_guardrails_for_index', return_value=(processor.index, [])
        ):
            result = processor._apply_guardrails_for_dict(sample_documents)

            assert result == sample_documents
            assert len(result) == 2

    def test_no_index_returns_unchanged(self, processor: ConcreteDatasourceProcessor, sample_documents):
        """Test that documents are returned unchanged when index is None."""
        with patch.object(processor, '_validate_index_and_get_guardrails_for_index', return_value=(None, None)):
            result = processor._apply_guardrails_for_dict(sample_documents)

            assert result == sample_documents

    @patch('codemie.datasource.base_datasource_processor.GuardrailService')
    def test_applies_guardrails_to_all_documents(
        self, mock_guardrail_service, processor: ConcreteDatasourceProcessor, sample_documents, mock_index
    ):
        """Test that guardrails are applied to all documents in the dict."""
        mock_guardrail = MagicMock(spec=Guardrail)
        mock_guardrail.id = "guardrail123"

        with patch.object(
            processor, '_validate_index_and_get_guardrails_for_index', return_value=(mock_index, [mock_guardrail])
        ):
            with patch.object(processor, '_apply_guardrails_to_documents') as mock_apply:
                processor._apply_guardrails_for_dict(sample_documents)

                # Should be called once for each document key (2 times)
                assert mock_apply.call_count == 2


class TestApplyGuardrailsForDocuments:
    """Tests for _apply_guardrails_for_documents method."""

    def test_no_guardrails_returns_unchanged(self, processor):
        """Test that documents list is returned unchanged when no guardrails."""
        documents = [
            Document(page_content="Test content", metadata={"source": "test.txt"}),
        ]

        with patch.object(
            processor, '_validate_index_and_get_guardrails_for_index', return_value=(processor.index, [])
        ):
            result = processor._apply_guardrails_for_documents(documents)

            assert result == documents

    @patch('codemie.datasource.base_datasource_processor.GuardrailService')
    def test_applies_guardrails_to_documents_list(
        self, mock_guardrail_service, processor: ConcreteDatasourceProcessor, mock_index
    ):
        """Test that guardrails are applied to a list of documents."""
        documents = [
            Document(page_content="Test content", metadata={"source": "test.txt"}),
        ]
        mock_guardrail = MagicMock(spec=Guardrail)

        with patch.object(
            processor, '_validate_index_and_get_guardrails_for_index', return_value=(mock_index, [mock_guardrail])
        ):
            with patch.object(processor, '_apply_guardrails_to_documents') as mock_apply:
                processor._apply_guardrails_for_documents(documents)

                mock_apply.assert_called_once_with(documents, mock_index, [mock_guardrail])


class TestApplyGuardrailsToDocuments:
    """Tests for _apply_guardrails_to_documents method."""

    @patch("codemie.datasource.base_datasource_processor.GuardrailService.apply_guardrails_for_entity")
    def test_modifies_document_content_in_place(
        self, mock_apply_guardrails, processor: ConcreteDatasourceProcessor, mock_index
    ):
        """Test that document content is modified in place after guardrail application."""
        documents = [
            Document(page_content="Original content", metadata={"source": "test.txt"}),
        ]
        mock_guardrail = MagicMock(spec=Guardrail)

        # Mock the guardrail service to return modified text
        mock_apply_guardrails.return_value = ("Modified content", None)  # No blocking

        processor._apply_guardrails_to_documents(documents, mock_index, [mock_guardrail])

        assert documents[0].page_content == "Modified content"

    @patch("codemie.datasource.base_datasource_processor.GuardrailService.apply_guardrails_for_entity")
    def test_raises_exception_when_content_blocked(
        self, mock_apply_guardrails, processor: ConcreteDatasourceProcessor, mock_index
    ):
        """Test that GuardrailBlockedException is raised when content is blocked."""
        documents = [
            Document(page_content="Blocked content", metadata={"source": "test.txt"}),
        ]
        mock_guardrail = MagicMock(spec=Guardrail)

        # Mock the guardrail service to return blocked reasons
        blocked_reasons = [{"policy": "contentPolicy", "type": "HATE", "reason": "BLOCKED"}]
        mock_apply_guardrails.return_value = ("BLOCKED", blocked_reasons)

        with pytest.raises(GuardrailBlockedException) as exc_info:
            processor._apply_guardrails_to_documents(documents, mock_index, [mock_guardrail])

        assert "Input blocked by guardrails" in str(exc_info.value)

    @patch("codemie.datasource.base_datasource_processor.GuardrailService.apply_guardrails_for_entity")
    def test_applies_guardrails_to_multiple_documents(
        self, mock_apply_guardrails, processor: ConcreteDatasourceProcessor, mock_index
    ):
        """Test that guardrails are applied to all documents in the list."""
        documents = [
            Document(page_content="Content 1", metadata={"source": "test1.txt"}),
            Document(page_content="Content 2", metadata={"source": "test2.txt"}),
            Document(page_content="Content 3", metadata={"source": "test3.txt"}),
        ]
        mock_guardrail = MagicMock(spec=Guardrail)

        mock_apply_guardrails.return_value = ("Modified content", None)

        processor._apply_guardrails_to_documents(documents, mock_index, [mock_guardrail])

        # Should be called once for each document
        assert mock_apply_guardrails.call_count == 3

        # All documents should have modified content
        for doc in documents:
            assert doc.page_content == "Modified content"


class TestValidateIndexAndGetGuardrailsForIndex:
    """Tests for _validate_index_and_get_guardrails_for_index method."""

    def test_returns_none_when_no_index(self, processor):
        """Test that None is returned when index is not set."""
        processor.index = None

        index, guardrails = processor._validate_index_and_get_guardrails_for_index()

        assert index is None
        assert guardrails is None

    def test_returns_none_when_no_index_id(self, processor: ConcreteDatasourceProcessor, mock_index):
        """Test that None is returned when index has no ID."""
        mock_index.id = None
        processor.index = mock_index

        index, guardrails = processor._validate_index_and_get_guardrails_for_index()

        assert index is None
        assert guardrails is None

    @patch("codemie.datasource.base_datasource_processor.GuardrailService.get_effective_guardrails_for_entity")
    def test_returns_guardrails_for_valid_index(
        self, mock_get_guardrails, processor: ConcreteDatasourceProcessor, mock_index
    ):
        """Test that guardrails are retrieved for a valid index."""
        processor.index = mock_index
        mock_guardrails = [MagicMock(spec=Guardrail)]
        mock_get_guardrails.return_value = mock_guardrails

        index, guardrails = processor._validate_index_and_get_guardrails_for_index()

        assert index == mock_index
        assert guardrails == mock_guardrails

        # Verify the service was called with correct parameters
        mock_get_guardrails.assert_called_once_with(
            GuardrailEntity.KNOWLEDGEBASE,
            mock_index.id,
            mock_index.project_name,
            GuardrailSource.INPUT,
        )


class TestEndToEnd:
    """E2E tests for guardrail functionality."""

    @patch("codemie.datasource.base_datasource_processor.GuardrailService.get_effective_guardrails_for_entity")
    @patch("codemie.datasource.base_datasource_processor.GuardrailService.apply_guardrails_for_entity")
    def test_end_to_end_guardrail_application(
        self,
        mock_apply_guardrails,
        mock_get_guardrails,
        processor: ConcreteDatasourceProcessor,
        mock_index,
        sample_documents,
    ):
        """Test complete guardrail application flow from dict to individual documents."""
        mock_guardrail = MagicMock(spec=Guardrail)
        mock_guardrail.id = "guardrail123"

        # Mock get_effective_guardrails_for_entity to return a guardrail
        mock_get_guardrails.return_value = [mock_guardrail]

        # Mock apply_guardrails_for_entity to return modified content
        def mock_apply_side_effect(*args, **kwargs):
            # Extract input text from args or kwargs
            # Signature: apply_guardrails_for_entity(entity_type, entity_id, project_name, input, source, guardrails=None)
            input_text = args[3] if len(args) > 3 else kwargs.get('input', '')
            return (f"MODIFIED: {input_text}", None)

        mock_apply_guardrails.side_effect = mock_apply_side_effect

        processor.index = mock_index

        # Apply guardrails
        result = processor._apply_guardrails_for_dict(sample_documents)

        # Verify all documents were modified
        for docs in result.values():
            for doc in docs:
                assert doc.page_content.startswith("MODIFIED:")

        # Verify the mocks were called
        mock_get_guardrails.assert_called_once_with(
            GuardrailEntity.KNOWLEDGEBASE,
            mock_index.id,
            mock_index.project_name,
            GuardrailSource.INPUT,
        )
        # Should be called 3 times (total number of document chunks)
        assert mock_apply_guardrails.call_count == 3

    @patch("codemie.datasource.base_datasource_processor.GuardrailService.get_effective_guardrails_for_entity")
    @patch("codemie.datasource.base_datasource_processor.GuardrailService.apply_guardrails_for_entity")
    def test_blocked_content_stops_processing(
        self,
        mock_apply_guardrails,
        mock_get_guardrails,
        processor: ConcreteDatasourceProcessor,
        mock_index,
        sample_documents,
    ):
        """Test that blocked content raises exception and stops processing."""
        mock_guardrail = MagicMock(spec=Guardrail)

        # Mock get_effective_guardrails_for_entity to return a guardrail
        mock_get_guardrails.return_value = [mock_guardrail]

        # Mock apply_guardrails_for_entity to return blocked content
        blocked_reasons = [{"policy": "contentPolicy", "reason": "BLOCKED"}]
        mock_apply_guardrails.return_value = ("BLOCKED", blocked_reasons)

        processor.index = mock_index

        # Should raise GuardrailBlockedException on first blocked content
        with pytest.raises(GuardrailBlockedException) as exc_info:
            processor._apply_guardrails_for_dict(sample_documents)

        # Verify exception message
        assert "Input blocked by guardrails" in str(exc_info.value)

        # Verify get_guardrails was called
        mock_get_guardrails.assert_called_once_with(
            GuardrailEntity.KNOWLEDGEBASE,
            mock_index.id,
            mock_index.project_name,
            GuardrailSource.INPUT,
        )

        # Verify apply_guardrails was called at least once (should stop after first block)
        assert mock_apply_guardrails.call_count >= 1


class TestLoadAndProcessDocumentsEmbeddingDrain:
    """Tests for the try/finally embedding cost drain in _load_and_process_documents."""

    def _make_mock_store(self, consume_last_usage_return):
        mock_store = MagicMock()
        mock_store._store._create_index_if_not_exists.return_value = None
        mock_store.embeddings.consume_last_usage.return_value = consume_last_usage_return
        return mock_store

    @patch('codemie.datasource.base_datasource_processor.request_summary_manager')
    @patch('codemie.datasource.base_datasource_processor.ElasticSearchClient')
    @patch('codemie.datasource.base_datasource_processor.llm_service')
    @patch.object(ConcreteDatasourceProcessor, '_update_complete_state_estimate')
    @patch.object(ConcreteDatasourceProcessor, '_process_batch', return_value=1)
    @patch.object(ConcreteDatasourceProcessor, '_get_store_by_index')
    def test_finally_drains_proxy_cost_after_success(
        self,
        mock_get_store,
        mock_process_batch,
        mock_update_state,
        mock_llm_service,
        mock_es_client,
        mock_rsm,
        mock_user,
        mock_index,
    ):
        """When consume_last_usage returns usage, update_llm_run is called in finally."""
        from codemie.enterprise.litellm.llm_factory import EmbeddingUsage

        usage = EmbeddingUsage(input_tokens=200, cost=0.005)
        mock_store = self._make_mock_store(usage)
        mock_get_store.return_value = mock_store
        mock_llm_service.get_embedding_deployment_name.return_value = "emb-model"
        mock_es_client.get_client.return_value.indices.exists.return_value.meta.status = 404
        mock_index.embeddings_model = "text-embedding-3-small"
        mock_index.complete_state = 0

        processor = ConcreteDatasourceProcessor("ds", mock_user, mock_index, request_uuid="req-456")
        processor.index = mock_index

        loader = MagicMock()
        loader.lazy_load.return_value = iter([Document(page_content="hello")])

        processor._load_and_process_documents(loader=loader, index=mock_index, batch_size=10)

        llm_runs = [c.kwargs['llm_run'] for c in mock_rsm.update_llm_run.call_args_list]
        drain_runs = [r for r in llm_runs if r.money_spent == pytest.approx(0.005)]
        assert len(drain_runs) == 1
        assert drain_runs[0].input_tokens == 200
        assert drain_runs[0].output_tokens == 0

    @patch('codemie.datasource.base_datasource_processor.request_summary_manager')
    @patch('codemie.datasource.base_datasource_processor.ElasticSearchClient')
    @patch('codemie.datasource.base_datasource_processor.llm_service')
    @patch.object(ConcreteDatasourceProcessor, '_update_complete_state_estimate')
    @patch.object(ConcreteDatasourceProcessor, '_get_store_by_index')
    def test_finally_drains_proxy_cost_even_if_batch_raises(
        self,
        mock_get_store,
        mock_update_state,
        mock_llm_service,
        mock_es_client,
        mock_rsm,
        mock_user,
        mock_index,
    ):
        """The drain fires even when the batch loop raises an exception."""
        from codemie.enterprise.litellm.llm_factory import EmbeddingUsage

        usage = EmbeddingUsage(input_tokens=100, cost=0.002)
        mock_store = self._make_mock_store(usage)
        mock_get_store.return_value = mock_store
        mock_llm_service.get_embedding_deployment_name.return_value = "emb-model"
        mock_es_client.get_client.return_value.indices.exists.return_value.meta.status = 404
        mock_index.embeddings_model = "text-embedding-3-small"
        mock_index.complete_state = 0

        processor = ConcreteDatasourceProcessor("ds", mock_user, mock_index, request_uuid="req-789")
        processor.index = mock_index

        with patch.object(ConcreteDatasourceProcessor, '_process_batch', side_effect=RuntimeError("batch failed")):
            loader = MagicMock()
            loader.lazy_load.return_value = iter([Document(page_content="doc")] * 15)

            with pytest.raises(RuntimeError):
                processor._load_and_process_documents(loader=loader, index=mock_index, batch_size=10)

        llm_runs = [c.kwargs['llm_run'] for c in mock_rsm.update_llm_run.call_args_list]
        drain_runs = [r for r in llm_runs if r.money_spent == pytest.approx(0.002)]
        assert len(drain_runs) == 1

    @patch('codemie.datasource.base_datasource_processor.request_summary_manager')
    @patch('codemie.datasource.base_datasource_processor.ElasticSearchClient')
    @patch('codemie.datasource.base_datasource_processor.llm_service')
    @patch.object(ConcreteDatasourceProcessor, '_update_complete_state_estimate')
    @patch.object(ConcreteDatasourceProcessor, '_process_batch', return_value=1)
    @patch.object(ConcreteDatasourceProcessor, '_get_store_by_index')
    def test_no_llm_run_from_drain_when_consume_last_usage_returns_none(
        self,
        mock_get_store,
        mock_process_batch,
        mock_update_state,
        mock_llm_service,
        mock_es_client,
        mock_rsm,
        mock_user,
        mock_index,
    ):
        """When consume_last_usage returns None, no LLMRun is emitted from the drain."""
        mock_store = self._make_mock_store(None)
        mock_get_store.return_value = mock_store
        mock_llm_service.get_embedding_deployment_name.return_value = "emb-model"
        mock_es_client.get_client.return_value.indices.exists.return_value.meta.status = 404
        mock_index.embeddings_model = "text-embedding-3-small"
        mock_index.complete_state = 0

        processor = ConcreteDatasourceProcessor("ds", mock_user, mock_index, request_uuid="req-000")
        processor.index = mock_index

        loader = MagicMock()
        loader.lazy_load.return_value = iter([Document(page_content="hello")])

        processor._load_and_process_documents(loader=loader, index=mock_index, batch_size=10)

        mock_rsm.update_llm_run.assert_not_called()

    @patch('codemie.datasource.base_datasource_processor.request_summary_manager')
    @patch('codemie.datasource.base_datasource_processor.ElasticSearchClient')
    @patch('codemie.datasource.base_datasource_processor.llm_service')
    @patch.object(ConcreteDatasourceProcessor, '_update_complete_state_estimate')
    @patch.object(ConcreteDatasourceProcessor, '_process_batch', return_value=1)
    @patch.object(ConcreteDatasourceProcessor, '_get_store_by_index')
    def test_no_llm_run_from_drain_when_no_request_uuid(
        self,
        mock_get_store,
        mock_process_batch,
        mock_update_state,
        mock_llm_service,
        mock_es_client,
        mock_rsm,
        mock_user,
        mock_index,
    ):
        """When request_uuid is None, the drain does not emit a LLMRun."""
        from codemie.enterprise.litellm.llm_factory import EmbeddingUsage

        usage = EmbeddingUsage(input_tokens=100, cost=0.002)
        mock_store = self._make_mock_store(usage)
        mock_get_store.return_value = mock_store
        mock_llm_service.get_embedding_deployment_name.return_value = "emb-model"
        mock_es_client.get_client.return_value.indices.exists.return_value.meta.status = 200
        mock_index.embeddings_model = "text-embedding-3-small"
        mock_index.complete_state = 0

        processor = ConcreteDatasourceProcessor("ds", mock_user, mock_index, request_uuid=None)
        processor.index = mock_index

        loader = MagicMock()
        loader.lazy_load.return_value = iter([Document(page_content="hello")])

        processor._load_and_process_documents(loader=loader, index=mock_index, batch_size=10)

        mock_rsm.update_llm_run.assert_not_called()


# ===================== _create_or_update_scheduler timezone tests =====================


@patch("codemie.service.settings.scheduler_settings_service.SchedulerSettingsService.handle_schedule")
def test_create_or_update_scheduler_passes_timezone(mock_handle, processor):
    processor.index.repo_name = "my-repo"
    processor._create_or_update_scheduler(cron_expression="0 9 * * *", timezone="Europe/Warsaw")

    mock_handle.assert_called_once()
    _, kwargs = mock_handle.call_args
    assert kwargs["timezone"] == "Europe/Warsaw"


@patch("codemie.service.settings.scheduler_settings_service.SchedulerSettingsService.handle_schedule")
def test_create_or_update_scheduler_no_timezone_passes_none(mock_handle, processor):
    processor.index.repo_name = "my-repo"
    processor._create_or_update_scheduler(cron_expression="0 9 * * *")

    mock_handle.assert_called_once()
    _, kwargs = mock_handle.call_args
    assert kwargs["timezone"] is None


@patch("codemie.service.settings.scheduler_settings_service.SchedulerSettingsService.handle_schedule")
def test_create_or_update_scheduler_none_cron_skips_handle(mock_handle, processor):
    processor.cron_expression = None
    processor._create_or_update_scheduler()

    mock_handle.assert_not_called()
class SourceKeyedProcessor(ConcreteDatasourceProcessor):
    """Uses the production metadata key so document grouping matches real connectors."""

    SOURCE = "source"


class MetadataStrippingProcessor(SourceKeyedProcessor):
    """Mirrors SharePoint, Jira and the other connectors that rebuild chunk metadata."""

    def _process_chunk(self, chunk: str, chunk_metadata, document: Document) -> Document:
        return Document(
            page_content=chunk,
            metadata={"source": document.metadata.get("source", "")},
        )


class TestChunkIdentity:
    """Chunk numbering is the base class's contract, not the subclass's."""

    def test_single_chunk_documents_are_numbered(self, mock_user, mock_index):
        processor = SourceKeyedProcessor("ds", mock_user, mock_index)
        page = Document(page_content="short page", metadata={"source": "https://host/file.pdf"})

        result = processor._split_documents([page])

        assert [doc.metadata["chunk_num"] for doc in result["https://host/file.pdf"]] == [1]

    def test_numbering_continues_across_batches(self, mock_user, mock_index):
        processor = SourceKeyedProcessor("ds", mock_user, mock_index)
        source = "https://host/file.pdf"
        first_page = Document(page_content="page one text", metadata={"source": source})
        second_page = Document(page_content="page two text", metadata={"source": source})

        first_batch = processor._split_documents([first_page])
        second_batch = processor._split_documents([second_page])

        assert [doc.metadata["chunk_num"] for doc in first_batch[source]] == [1]
        assert [doc.metadata["chunk_num"] for doc in second_batch[source]] == [2]

    def test_metadata_stripping_subclass_still_gets_chunk_num(self, mock_user, mock_index):
        processor = MetadataStrippingProcessor("ds", mock_user, mock_index)
        source = "https://host/file.docx"
        document = Document(page_content="body", metadata={"source": source, "title": "T"})

        result = processor._split_documents([document])

        assert result[source][0].metadata["chunk_num"] == 1

    def test_numbering_is_independent_per_file(self, mock_user, mock_index):
        processor = SourceKeyedProcessor("ds", mock_user, mock_index)
        first = Document(page_content="alpha", metadata={"source": "https://host/a.pdf"})
        second = Document(page_content="beta", metadata={"source": "https://host/b.pdf"})

        result = processor._split_documents([first, second])

        assert result["https://host/a.pdf"][0].metadata["chunk_num"] == 1
        assert result["https://host/b.pdf"][0].metadata["chunk_num"] == 1

    def test_documents_sharing_a_source_share_one_sequence(self, mock_user, mock_index):
        """A compound file (.msg body plus attachments, a .zip's members) yields documents
        that share one source but carry different file_path values. Chunk identity at
        retrieval is source plus chunk_num, so those documents must share one sequence."""
        processor = SourceKeyedProcessor("ds", mock_user, mock_index)
        source = "https://host/mail.msg"
        body = Document(page_content="mail body", metadata={"source": source})
        attachment = Document(
            page_content="attachment text",
            metadata={"source": source, "file_path": "report.pdf"},
        )

        result = processor._split_documents([body, attachment])

        identities = [
            (doc.metadata["source"], doc.metadata["chunk_num"]) for chunks in result.values() for doc in chunks
        ]
        assert len(identities) == 2
        assert len(set(identities)) == 2

    def test_chunk_numbering_restarts_on_a_new_run(self, mock_user, mock_index):
        """The counter spans one indexing run. A processor reused for a second run must
        restart, or the second run's chunks are numbered past the first run's."""
        processor = SourceKeyedProcessor("ds", mock_user, mock_index)
        source = "https://host/file.pdf"
        page = Document(page_content="page text", metadata={"source": source})

        processor._split_documents([page])
        processor._reset_chunk_numbering()
        second_run = processor._split_documents([page])

        assert [doc.metadata["chunk_num"] for doc in second_run[source]] == [1]

    def test_chunk_counters_initialized_on_construction(self, mock_user, mock_index):
        """The reset in the indexing entry point is only meaningful if the attribute is a
        real, always-present piece of state rather than lazily conjured on first use."""
        processor = SourceKeyedProcessor("ds", mock_user, mock_index)

        assert processor._chunk_counters == {}


class TestChunkNumberingResetsPerRun:
    """The reset lives in the indexing entry point, so it is tested through it."""

    @patch('codemie.datasource.base_datasource_processor.request_summary_manager')
    @patch('codemie.datasource.base_datasource_processor.ElasticSearchClient')
    @patch('codemie.datasource.base_datasource_processor.llm_service')
    @patch.object(SourceKeyedProcessor, '_update_complete_state_estimate')
    @patch.object(SourceKeyedProcessor, '_get_store_by_index')
    def test_second_run_restarts_numbering(
        self,
        mock_get_store,
        mock_update_state,
        mock_llm_service,
        mock_es_client,
        mock_rsm,
        mock_user,
        mock_index,
    ):
        mock_store = MagicMock()
        mock_store._store._create_index_if_not_exists.return_value = None
        mock_store.embeddings.consume_last_usage.return_value = None
        mock_get_store.return_value = mock_store
        mock_llm_service.get_embedding_deployment_name.return_value = "emb-model"
        mock_es_client.get_client.return_value.indices.exists.return_value.meta.status = 404
        mock_index.embeddings_model = "text-embedding-3-small"
        mock_index.complete_state = 0

        processor = SourceKeyedProcessor("ds", mock_user, mock_index)
        processor.index = mock_index

        source = "https://host/file.pdf"
        assigned_numbers = []

        def split_and_record(docs, index, store):
            for chunks in processor._split_documents(docs).values():
                assigned_numbers.extend(doc.metadata["chunk_num"] for doc in chunks)
            return len(docs)

        with patch.object(SourceKeyedProcessor, '_process_batch', side_effect=split_and_record):
            for _ in range(2):
                loader = MagicMock()
                loader.lazy_load.return_value = iter([Document(page_content="page text", metadata={"source": source})])
                processor._load_and_process_documents(loader=loader, index=mock_index, batch_size=10)

        assert assigned_numbers == [1, 1]
