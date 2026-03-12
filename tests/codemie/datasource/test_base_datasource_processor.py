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
