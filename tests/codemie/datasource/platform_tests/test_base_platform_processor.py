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

import pytest
import tiktoken
from langchain_core.documents import Document

from codemie.datasource.platform.assistant_datasource_processor import AssistantDatasourceProcessor

MODULE = "codemie.datasource.platform.base_platform_processor"


def _doc(entity_id: str, chunk_num: int = 1) -> Document:
    return Document(
        page_content=f"content {entity_id} #{chunk_num}",
        metadata={"id": entity_id, "name": f"name {entity_id}", "chunk_num": chunk_num},
    )


def _chunks(entity_id: str, count: int) -> list[Document]:
    return [_doc(entity_id, n) for n in range(1, count + 1)]


def _whole(entity_id: str, name: str, body: str) -> Document:
    """Whole (unsplit) entity document as produced by the platform loader."""
    return Document(
        page_content=body,
        metadata={
            "id": entity_id,
            "name": name,
            "source": name,
            "popularity_score": 0.5,
            "chunk_header": f"Name: {name}\n\nPage url with details: /#/assistants/{entity_id}",
        },
    )


_ENC = tiktoken.get_encoding("o200k_base")


def _stored_ids(store) -> list[list[str]]:
    return [c.kwargs["ids"] for c in store.add_documents.call_args_list]


@pytest.fixture
def processor():
    with (
        patch(f"{MODULE}.IndexInfo") as mock_index_info,
        patch(f"{MODULE}.ElasticSearchClient.get_client", return_value=Mock()),
    ):
        mock_index_info.filter_by_project_and_repo.return_value = []
        proc = AssistantDatasourceProcessor(datasource_name="marketplace_assistants")
    proc.index = Mock()
    proc.index.embeddings_model = "ada-002"
    proc.client = Mock()
    return proc


@pytest.fixture
def store():
    mock_store = Mock()
    with (
        patch(f"{MODULE}.llm_service") as mock_llm,
        patch.object(AssistantDatasourceProcessor, "_get_store_by_index", return_value=mock_store),
    ):
        mock_llm.get_embedding_deployment_name.return_value = "ada-002"
        yield mock_store


class TestAddDocumentsChunks:
    """EPMCDME-13933: marketplace chunks are stored per assistant, stale chunks removed, failures isolated."""

    def test_chunk_ids_include_chunk_num(self, processor, store):
        # Act
        processor._add_documents(_chunks("a", 2))

        # Assert
        assert _stored_ids(store) == [["a_1", "a_2"]]

    def test_progress_counts_chunks_per_assistant(self, processor, store):
        # Act
        processor._add_documents(_chunks("a", 2) + _chunks("b", 1))

        # Assert
        assert [c.kwargs for c in processor.index.move_progress.call_args_list] == [
            {"chunks_count": 2, "processed_file": "name a"},
            {"chunks_count": 1, "processed_file": "name b"},
        ]

    def test_stale_cleanup_query_excludes_new_ids(self, processor, store):
        # Act
        processor._add_documents(_chunks("a", 2) + _chunks("b", 1))

        # Assert
        processor.client.delete_by_query.assert_called_once()
        kwargs = processor.client.delete_by_query.call_args.kwargs
        query = kwargs["body"]["query"]["bool"]
        assert query["filter"] == [{"terms": {"metadata.id.keyword": ["a", "b"]}}]
        assert query["must_not"] == [{"ids": {"values": ["a_1", "a_2", "b_1"]}}]
        assert kwargs["conflicts"] == "proceed"

    @patch(f"{MODULE}.STORAGE_CONFIG")
    def test_groups_packed_into_batches(self, mock_storage_config, processor, store):
        # Arrange
        mock_storage_config.embeddings_max_docs_count = 2

        # Act
        processor._add_documents(_chunks("a", 1) + _chunks("b", 1) + _chunks("c", 1))

        # Assert
        assert _stored_ids(store) == [["a_1", "b_1"], ["c_1"]]

    @patch(f"{MODULE}.STORAGE_CONFIG")
    def test_large_group_forms_own_batch(self, mock_storage_config, processor, store):
        # Arrange
        mock_storage_config.embeddings_max_docs_count = 2

        # Act
        processor._add_documents(_chunks("a", 3) + _chunks("b", 1))

        # Assert
        assert _stored_ids(store) == [["a_1", "a_2", "a_3"], ["b_1"]]

    @patch(f"{MODULE}.logger")
    def test_batch_failure_skips_whole_assistant(self, mock_logger, processor, store):
        # Arrange
        def add_documents(documents, ids):
            if "bad_2" in ids:
                raise ValueError("Error code: 400 - maximum context length")
            return ids

        store.add_documents.side_effect = add_documents

        # Act
        processor._add_documents(_chunks("a", 1) + _chunks("bad", 2) + _chunks("c", 1))

        # Assert
        assert ["a_1"] in _stored_ids(store)
        assert ["c_1"] in _stored_ids(store)
        processed = [c.kwargs["processed_file"] for c in processor.index.move_progress.call_args_list]
        assert processed == ["name a", "name c"]
        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args.kwargs["extra"]["entity_id"] == "bad"
        cleaned = [
            c.kwargs["body"]["query"]["bool"]["filter"][0]["terms"]["metadata.id.keyword"]
            for c in processor.client.delete_by_query.call_args_list
        ]
        assert sorted(sum(cleaned, [])) == ["a", "c"]

    def test_cleanup_failure_logged_not_raised(self, processor, store):
        # Arrange
        processor.client.delete_by_query.side_effect = RuntimeError("es down")

        # Act
        processor._add_documents(_chunks("a", 1))

        # Assert
        processor.index.move_progress.assert_called_once_with(chunks_count=1, processed_file="name a")

    @patch(f"{MODULE}.logger")
    def test_all_documents_fail_no_progress_no_raise(self, mock_logger, processor, store):
        # Arrange
        store.add_documents.side_effect = ValueError("400")

        # Act
        processor._add_documents(_chunks("a", 1) + _chunks("b", 1))

        # Assert
        processor.index.move_progress.assert_not_called()
        processor.client.delete_by_query.assert_not_called()
        assert mock_logger.warning.call_count == 2

    def test_skip_logging_works_with_real_logger(self, processor, store):
        # Arrange: no logger mock, so reserved LogRecord keys in `extra` would raise
        store.add_documents.side_effect = ValueError("400")

        # Act
        processor._add_documents(_chunks("a", 1) + _chunks("b", 1))

        # Assert
        processor.index.move_progress.assert_not_called()

    def test_update_path_propagates_error(self, processor, store):
        # Arrange
        store.add_documents.side_effect = ValueError("400")

        # Act / Assert
        with pytest.raises(ValueError):
            processor._add_documents(_chunks("a", 2), is_update=True)
        processor.index.move_progress.assert_not_called()

    def test_update_path_stores_all_chunks_and_cleans_up(self, processor, store):
        # Act
        processor._add_documents(_chunks("a", 3), is_update=True)

        # Assert
        assert _stored_ids(store) == [["a_1", "a_2", "a_3"]]
        processor.client.delete_by_query.assert_called_once()
        processor.index.move_progress.assert_not_called()


class TestIndexSingleEntity:
    def test_single_entity_publish_propagates_error(self, processor, store):
        # Arrange
        processor.loader = Mock()
        processor.loader.load_single_entity.return_value = _whole("new", "name new", "Description: short")
        store.add_documents.side_effect = ValueError("400")

        # Act / Assert
        with pytest.raises(ValueError):
            processor.index_single_entity("new", is_update=False)
        processor.index.move_progress.assert_not_called()

    def test_single_entity_publish_moves_progress_on_success(self, processor, store):
        # Arrange
        processor.loader = Mock()
        processor.loader.load_single_entity.return_value = _whole("new", "name new", "Description: short")

        # Act
        processor.index_single_entity("new", is_update=False)

        # Assert
        assert _stored_ids(store) == [["new_1"]]
        processor.index.move_progress.assert_called_once_with(chunks_count=1, processed_file="name new")

    def test_single_entity_not_found_is_noop(self, processor, store):
        # Arrange
        processor.loader = Mock()
        processor.loader.load_single_entity.return_value = None

        # Act
        processor.index_single_entity("missing")

        # Assert
        store.add_documents.assert_not_called()


class TestSplitEntityDocuments:
    """Platform entities are split through the base chunking pipeline (EPMCDME-13933)."""

    def test_short_entity_single_chunk_with_header(self, processor):
        # Act
        chunks = processor._split_entity_documents([_whole("a", "A", "Description: hi")])

        # Assert
        assert len(chunks) == 1
        assert chunks[0].metadata["chunk_num"] == 1
        assert chunks[0].page_content == "Name: A\n\nPage url with details: /#/assistants/a\n\nDescription: hi"
        assert "chunk_header" not in chunks[0].metadata
        assert chunks[0].metadata["id"] == "a"

    def test_long_entity_numbered_chunks_with_header(self, processor):
        # Arrange
        body = "Description: " + " ".join(f"w{i}" for i in range(20000))
        body += "\n\nTools: " + ", ".join(f"tool_{i}" for i in range(3000))

        # Act
        chunks = processor._split_entity_documents([_whole("a", "A", body)])

        # Assert
        assert len(chunks) > 1
        assert [c.metadata["chunk_num"] for c in chunks] == list(range(1, len(chunks) + 1))
        for chunk in chunks:
            assert chunk.page_content.startswith("Name: A\n\nPage url with details: /#/assistants/a")
            assert len(_ENC.encode(chunk.page_content, disallowed_special=())) <= 2000
            assert "chunk_header" not in chunk.metadata
        assert any("w19999" in c.page_content for c in chunks)
        assert any("tool_2999" in c.page_content for c in chunks)

    def test_numbering_restarts_per_entity_with_same_name(self, processor):
        # Act
        chunks = processor._split_entity_documents(
            [_whole("a", "Same", "Description: one"), _whole("b", "Same", "Description: two")]
        )

        # Assert
        assert [(c.metadata["id"], c.metadata["chunk_num"]) for c in chunks] == [("a", 1), ("b", 1)]


class TestProcessGuardrails:
    def test_guardrails_applied_to_final_chunks_including_header(self, processor):
        # Arrange
        processor.loader = Mock()
        processor.loader.lazy_load.return_value = [_whole("a", "A", "Description: hi")]
        guarded = []

        def guardrails(documents):
            guarded.extend(documents)
            return documents

        with (
            patch.object(processor, "_apply_guardrails_for_documents", side_effect=guardrails),
            patch.object(processor, "_add_documents") as add_documents,
        ):
            # Act
            processor._process()

        # Assert: guardrails saw exactly the chunks that get embedded, header included
        assert [d.page_content for d in guarded] == [
            "Name: A\n\nPage url with details: /#/assistants/a\n\nDescription: hi"
        ]
        assert add_documents.call_args.args[0] == guarded
