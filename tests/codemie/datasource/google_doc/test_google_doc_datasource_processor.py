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

import hashlib
import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch, call, Mock

import pytest
from langchain_core.documents import Document

from codemie.rest_api.security.user import User
from codemie.datasource.google_doc.google_doc_datasource_processor import (
    GoogleDocDatasourceProcessor,
)

DOC_ID = "1_eWXiG3jBc7jJdU2TwSC8gpyc3P9WJCzEAG4Jk9wX2I"
TITLES = ["1.1.1. Title One;", "1.1.2. Title Two;"]
DOCS = [
    Document(
        metadata={
            "title": "Title One",
            "content": "Content One",
            "instructions": "",
            "reference": "1.1.1",
        },
        page_content="Content One",
    ),
    Document(
        metadata={
            "title": "Title Two",
            "content": "Content Two",
            "instructions": "",
            "reference": "1.1.2",
        },
        page_content="Content Two",
    ),
]


@pytest.fixture
def mock_loader():
    loader = Mock()
    attrs = {
        "load_with_extra.return_value": (DOCS, TITLES, DOC_ID),
    }
    loader.configure_mock(**attrs)
    return loader


@pytest.fixture
def mock_elastic():
    elastic = Mock()
    hits = {
        "total": {"value": 2, "relation": "eq"},
        "max_score": 1.0,
        "hits": [
            {
                "_index": "test-project-test-datasource",
                "_id": "1f6ba90b-7af8-4228-80bb-674b733e4e1f",
                "_score": 1.0,
                "_source": {
                    "content": "Content One",
                    "metadata": {
                        "title": "Title One",
                        "content": "Content One",
                        "reference": "1.1.1",
                    },
                },
            },
            {
                "_index": "test-project-test-datasource",
                "_id": "84d728ab-2f39-40e0-9868-73cb285b4769",
                "_score": 1.0,
                "_source": {
                    "content": "Content Two",
                    "metadata": {
                        "title": "Title Two",
                        "content": "Content Two",
                        "reference": "1.1.2",
                    },
                },
            },
        ],
    }
    attrs = {"search.return_value": {"hits": hits}}
    elastic.configure_mock(**attrs)
    return elastic


@pytest.fixture
def processor(mock_elastic, mock_loader):
    """Create a GoogleDocDatasourceProcessor with mocked dependencies."""
    with (
        patch("codemie.datasource.google_doc.google_doc_datasource_processor.GoogleDocLoader") as mock_loader_class,
    ):
        mock_loader_class.return_value = mock_loader

        processor = GoogleDocDatasourceProcessor(
            datasource_name="test-datasource",
            project_name="test-project",
            google_doc=f"https://docs.google.com/document/d/{DOC_ID}/edit",
            description="Test description",
            project_space_visible=True,
            user=User(id="1", username="theo", name="Theo"),
        )

        processor.client = mock_elastic
        mock_elastic.reset_mock()
        mock_loader.reset_mock()

        yield processor


class TestGoogleDocDatasourceProcessor:
    def test_parse_google_doc_id(self):
        """Test parsing of Google Doc ID from URL."""
        with patch("codemie.datasource.google_doc.google_doc_datasource_processor.ElasticSearchClient"):
            processor = GoogleDocDatasourceProcessor(
                datasource_name="test",
                project_name="test",
                google_doc="https://docs.google.com/document/d/abc123/edit",
            )

            # Test valid URL
            doc_id = processor._parse_google_doc_id("https://docs.google.com/document/d/abc123/edit")
            assert doc_id == "abc123"

            # Test invalid URL
            doc_id = processor._parse_google_doc_id("https://docs.google.com/invalid/url")
            assert doc_id == ""

    def test_init_index(self, processor):
        """Test initialization of the index."""
        with patch("codemie.datasource.google_doc.google_doc_datasource_processor.IndexInfo") as mock_kb_info:
            mock_index = MagicMock()
            mock_kb_info.new.return_value = mock_index

            # Test when index is already set
            processor.index = MagicMock()
            processor._init_index()
            mock_kb_info.new.assert_not_called()

            # Test when index is not set
            processor.index = None
            processor._init_index()

            mock_kb_info.new.assert_called_once()
            assert processor.index == mock_index

    @patch("uuid.uuid4")
    def test_add_texts(self, mock_uuid, processor, mock_elastic):
        """Test adding texts to the index."""
        mock_uuid.side_effect = [
            uuid.UUID("12345678-1234-5678-1234-567812345678"),
            uuid.UUID("87654321-4321-8765-4321-876543210987"),
        ]

        texts = ["content1", "content2"]
        metadatas = [{"title": "title1"}, {"title": "title2"}]

        with (
            patch("codemie.datasource.google_doc.google_doc_datasource_processor.IndexInfo") as mock_kb_info,
            patch("codemie.datasource.google_doc.google_doc_datasource_processor.bulk") as mock_bulk_func,
        ):
            mock_bulk_func.return_value = (2, 0)
            mock_index = MagicMock()
            mock_kb_info.new.return_value = mock_index
            processor.client = mock_elastic
            processor._init_index()
            requests = [
                {
                    "_op_type": "index",
                    "_index": "test-project-test-datasource",
                    "content": "content1",
                    "metadata": {"title": "title1"},
                    "_id": "12345678-1234-5678-1234-567812345678",
                },
                {
                    "_op_type": "index",
                    "_index": "test-project-test-datasource",
                    "content": "content2",
                    "metadata": {"title": "title2"},
                    "_id": "87654321-4321-8765-4321-876543210987",
                },
            ]

            processor._add_texts(texts, metadatas)

            mock_bulk_func.assert_called_once_with(
                mock_elastic,
                requests,
                stats_only=True,
                refresh=True,
            )

            assert processor.index.move_progress.call_count == 2
            processor.index.move_progress.assert_has_calls(
                [
                    call(chunks_count=1, processed_file="title1"),
                    call(chunks_count=1, processed_file="title2"),
                ]
            )

    @patch("codemie.datasource.base_datasource_processor.ensure_application_exists")
    def test_process(self, mock_ensure_app, processor, mock_loader):
        """Test the _process method."""
        with (
            patch("codemie.datasource.google_doc.google_doc_datasource_processor.IndexInfo") as mock_kb_info,
            patch.object(processor, "_add_documents") as mock_add_documents,
            patch.object(processor, "_update_kb_info") as mock_update_kb_info,
            patch.object(processor, "_save_table_of_contents") as mock_save_toc,
        ):
            mock_index = MagicMock()
            mock_kb_info.new.return_value = mock_index

            # Mock guardrail validation to return no guardrails
            processor._validate_index_and_get_guardrails_for_index = MagicMock(return_value=(processor.index, []))
            processor.process()

            mock_loader.load_with_extra.assert_called_once()
            mock_add_documents.assert_called_once_with(DOCS)
            mock_update_kb_info.assert_called_once_with(DOC_ID)
            mock_save_toc.assert_called_once_with(TITLES)

    def test_on_process_end(self, processor, mock_elastic):
        """Test the _on_process_end method."""
        processor.is_full_reindex = True
        processor._on_process_end()

        # Reset and test when is_full_reindex is False
        mock_elastic.reset_mock()
        processor.is_full_reindex = False
        processor._on_process_end()

        # Check that delete_by_query was not called
        mock_elastic.delete_by_query.assert_not_called()

    def test_update_metadata(self, processor, mock_elastic):
        """Test updating metadata."""
        with patch.object(processor, "get_metadata", return_value={"existing_key": "existing_value"}):
            processor._update_metadata({"new_key": "new_value"})

            mock_elastic.indices.put_mapping.assert_called_once_with(
                index=processor._index_name,
                body={"_meta": {"existing_key": "existing_value", "new_key": "new_value"}},
            )

    def test_get_documents_by_checksum(self, processor):
        """Test getting documents by checksum."""
        with patch.object(
            processor,
            "_read_chapters",
            return_value=[
                {"reference": "ref1", "title": "Title 1", "content": "Content 1"},
                {
                    "reference": "ref1-sub",
                    "title": "Title 1.1",
                    "content": "Content 1",
                },  # Same content as ref1
                {"reference": "ref2", "title": "Title 2", "content": "Content 2"},
            ],
        ):
            result = processor.get_documents_by_checksum(["ref1", "ref2"])

            # Calculate expected checksums
            checksum1 = hashlib.sha512("Content 1".encode("utf-8")).hexdigest()
            checksum2 = hashlib.sha512("Content 2".encode("utf-8")).hexdigest()

            # Check results
            assert len(result) == 2
            assert checksum1 in result
            assert checksum2 in result
            assert result[checksum1]["title"] == "Title 1; Title 1.1"  # Merged titles
            assert result[checksum1]["content"] == "Content 1"
            assert result[checksum2]["title"] == "Title 2"
            assert result[checksum2]["content"] == "Content 2"

    def test_get_table_of_contents(self, processor):
        """Test getting table of contents."""
        with patch.object(
            processor,
            "get_metadata",
            return_value={"table_of_contents": ["Chapter 1", "Chapter 2"]},
        ):
            result = processor.get_table_of_contents()
            assert result == ["Chapter 1", "Chapter 2"]

        # Test when table_of_contents is not in metadata
        with patch.object(processor, "get_metadata", return_value={}):
            result = processor.get_table_of_contents()
            assert result == []

    @patch("codemie.datasource.google_doc.google_doc_datasource_processor.datetime")
    def test_update_kb_info(self, mock_datetime, processor):
        """Test updating KB info."""
        mock_now = datetime(2023, 1, 1, 12, 0, 0)
        mock_datetime.now.return_value = mock_now

        with patch.object(processor, "_update_metadata") as mock_update_metadata:
            processor._update_kb_info("doc123")

            mock_update_metadata.assert_called_once_with({"kb_index_timestamp": mock_now})

    def test_on_process_start(self, processor):
        """Test the _on_process_start method."""
        # Test when is_resume_indexing is True
        processor.is_resume_indexing = True
        with pytest.raises(
            NotImplementedError,
            match="Resume indexing is not supported for llm_routing_google",
        ):
            processor._on_process_start()

        # Test when is_incremental_reindex is True
        processor.is_resume_indexing = False
        processor.is_incremental_reindex = True
        with pytest.raises(
            NotImplementedError,
            match="Incremental reindex is not supported for llm_routing_google",
        ):
            processor._on_process_start()

        # Test when both are False
        processor.is_resume_indexing = False
        processor.is_incremental_reindex = False
        # Should not raise an exception
        processor._on_process_start()
