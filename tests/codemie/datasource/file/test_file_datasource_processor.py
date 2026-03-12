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
from codemie.datasource.file.file_datasource_processor import FileDatasourceProcessor, FILE_PATH_DATA_NT
from codemie.datasource.loader.file_loader import FilesDatasourceLoader
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.security.user import User
from codemie.datasource.datasources_config import FILE_CONFIG
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


@pytest.fixture
def document_json():
    return Document(page_content='{}', metadata={'source': 'test.json'})


@pytest.fixture
def document_non_json():
    return Document(page_content='text', metadata={'source': 'test.txt'})


@pytest.fixture
def sample_file_datasource_processor():
    user = User(id="test_user", username="testuser", name="testname")
    files_paths = [FILE_PATH_DATA_NT(name="file1.csv", owner="owner1")]
    return FileDatasourceProcessor(
        datasource_name="test_datasource",
        user=user,
        files_paths=files_paths,
        project_name="test_project",
        description="test_description",
        project_space_visible=True,
        csv_separator=",",
        csv_start_row=1,
        csv_rows_per_document=1,
    )


@pytest.fixture
def sample_documents():
    doc1 = Document(page_content="test", metadata={"source": "file1.txt"})
    doc2 = Document(page_content="test", metadata={"source": "file2.csv"})
    doc3 = Document(page_content="test", metadata={"source": "file3.pdf"})
    doc_list_csv = [
        Document(page_content="test", metadata={"source": "file4.csv"}),
        Document(page_content="test", metadata={"source": "file5.csv"}),
    ]
    doc_list_other = [
        Document(page_content="test", metadata={"source": "file6.pdf"}),
        Document(page_content="test", metadata={"source": "file7.pdf"}),
    ]
    json1 = Document(page_content="test", metadata={"source": "file1.json"})
    json2 = Document(page_content="test", metadata={"source": "file1.json"})
    return [doc1, doc2, doc3, doc_list_csv, doc_list_other, json1, json2]


def test_file_datasource_processor_init(sample_file_datasource_processor):
    assert sample_file_datasource_processor.datasource_name == "test_datasource"
    assert sample_file_datasource_processor.user.username == "testuser"
    assert sample_file_datasource_processor.project_name == "test_project"
    assert sample_file_datasource_processor.description == "test_description"
    assert sample_file_datasource_processor.project_space_visible is True
    assert sample_file_datasource_processor.csv_separator == ","
    assert sample_file_datasource_processor.csv_start_row == 1
    assert sample_file_datasource_processor.csv_rows_per_document == 1


def test_started_message(sample_file_datasource_processor):
    expected_message = "Indexing of test_datasource has started in the background"
    assert sample_file_datasource_processor.started_message == expected_message


def test_index_name(sample_file_datasource_processor):
    from codemie.core.models import KnowledgeBase

    processor = sample_file_datasource_processor
    expected_index_name = KnowledgeBase(
        name=f"{processor.project_name}-{processor.datasource_name}", type=processor.INDEX_TYPE
    ).get_identifier()
    assert processor._index_name == expected_index_name


@patch.object(IndexInfo, 'create_from_file_processor')
def test_init_index(mock_create_from_file_processor, sample_file_datasource_processor):
    sample_file_datasource_processor.index = None
    sample_file_datasource_processor._init_index()
    mock_create_from_file_processor.assert_called_once_with(
        sample_file_datasource_processor, sample_file_datasource_processor.user
    )


@patch.object(FilesDatasourceLoader, '__init__', return_value=None)
def test_init_loader(mock_files_datasource_loader, sample_file_datasource_processor):
    sample_file_datasource_processor._init_loader()
    mock_files_datasource_loader.assert_called_once_with(
        total_count_of_documents=1,
        files_paths=sample_file_datasource_processor.files_paths,
        csv_separator=sample_file_datasource_processor.csv_separator,
        request_uuid=None,
    )


def test_pre_process_csv(sample_file_datasource_processor):
    documents = [Document(page_content="content", metadata={"source": "source"})]
    pre_processed_documents = sample_file_datasource_processor._pre_process_csv(documents)
    assert len(pre_processed_documents) == 1
    assert pre_processed_documents[0].metadata["row"] == "row 1"


def test_process_chunks(sample_file_datasource_processor):
    documents = [Document(page_content="content", metadata={"source": "source"})]
    sample_file_datasource_processor._process_chunks(documents)
    assert documents[0].metadata["chunk_num"] == 1


@patch("codemie.datasource.file.file_datasource_processor.RecursiveCharacterTextSplitter.from_tiktoken_encoder")
def test_get_splitter(mock_from_tiktoken_encoder, sample_file_datasource_processor):
    sample_file_datasource_processor._get_splitter()
    mock_from_tiktoken_encoder.assert_called_once_with(
        encoding_name="o200k_base", chunk_size=1500, disallowed_special={}, chunk_overlap=100
    )


@patch.object(FileDatasourceProcessor, '_get_splitter')
def test_split_documents(mock_get_splitter, sample_file_datasource_processor):
    mock_get_splitter.return_value.split_text = MagicMock(return_value=["chunk1", "chunk2"])
    documents = [Document(page_content="content", metadata={"source": "source"})]
    result = sample_file_datasource_processor._split_documents(documents)
    assert "source" in result
    assert len(result["source"]) == 2
    assert result["source"][0].page_content == "chunk1"
    assert result["source"][1].page_content == "chunk2"


def test_segregate_documents_input(sample_documents, sample_file_datasource_processor):
    list_of_docs, single_docs, json_docs = sample_file_datasource_processor._segregate_documents_input(sample_documents)

    assert len(json_docs) == 2
    assert len(list_of_docs) == 4
    assert len(single_docs) == 3
    assert all(isinstance(doc, Document) for doc in single_docs)
    assert all(isinstance(doc, Document) for doc in list_of_docs)


def test_get_splitter_for_non_json_document(sample_file_datasource_processor, document_non_json):
    splitter = sample_file_datasource_processor._get_splitter(document_non_json)
    assert isinstance(splitter, RecursiveCharacterTextSplitter)
    assert splitter._chunk_size == FILE_CONFIG.chunk_size
    assert splitter._chunk_overlap == FILE_CONFIG.chunk_overlap


@pytest.mark.parametrize(
    "source,content,expected_single,expected_json",
    [
        ("test.txt", "test content", 1, 0),
        ("document.pdf", "pdf content", 1, 0),
        ("data.csv", "csv content", 1, 0),
        ("test.json", '{"key": "value"}', 0, 1),
    ],
)
def test_process_single_document_by_file_type(
    sample_file_datasource_processor, source, content, expected_single, expected_json
):
    """Test processing document with different file types."""
    # Arrange
    document = Document(page_content=content, metadata={"source": source})
    single_docs = []
    json_docs = []

    # Act
    sample_file_datasource_processor._process_single_document(document, single_docs, json_docs)

    # Assert
    assert len(single_docs) == expected_single
    assert len(json_docs) == expected_json
    if expected_single:
        assert single_docs[0] == document
    if expected_json:
        assert json_docs[0] == document


def test_process_single_document_missing_source_metadata(sample_file_datasource_processor):
    """Test processing document with missing source metadata logs warning."""
    # Arrange
    document = Document(page_content="test content", metadata={})
    single_docs = []
    json_docs = []

    # Act
    with patch("codemie.datasource.file.file_datasource_processor.logger") as mock_logger:
        sample_file_datasource_processor._process_single_document(document, single_docs, json_docs)

        # Assert
        mock_logger.warning.assert_called_once()
        assert "Skipping document with missing source metadata" in mock_logger.warning.call_args[0][0]
        assert len(single_docs) == 0
        assert len(json_docs) == 0


@pytest.mark.parametrize(
    "documents,expected_count,has_row_metadata",
    [
        # CSV files - should be pre-processed with row metadata
        (
            [
                Document(page_content="row1", metadata={"source": "data.csv"}),
                Document(page_content="row2", metadata={"source": "data.csv"}),
            ],
            2,
            True,
        ),
        # Non-CSV files - should be passed through as-is
        (
            [
                Document(page_content="content1", metadata={"source": "file1.txt"}),
                Document(page_content="content2", metadata={"source": "file1.txt"}),
            ],
            2,
            False,
        ),
        # PDF files - should be passed through as-is
        (
            [
                Document(page_content="page1", metadata={"source": "doc.pdf"}),
                Document(page_content="page2", metadata={"source": "doc.pdf"}),
            ],
            2,
            False,
        ),
    ],
)
def test_process_document_list_with_different_file_types(
    sample_file_datasource_processor, documents, expected_count, has_row_metadata
):
    """Test processing list of documents with different file types."""
    # Arrange
    list_of_docs = []

    # Act
    sample_file_datasource_processor._process_document_list(documents, list_of_docs)

    # Assert
    assert len(list_of_docs) == expected_count
    if has_row_metadata:
        assert all("row" in doc.metadata for doc in list_of_docs)


def test_process_document_list_empty_list(sample_file_datasource_processor):
    """Test processing empty document list logs warning."""
    # Arrange
    documents = []
    list_of_docs = []

    # Act
    with patch("codemie.datasource.file.file_datasource_processor.logger") as mock_logger:
        sample_file_datasource_processor._process_document_list(documents, list_of_docs)

        # Assert
        mock_logger.warning.assert_called_once()
        assert "Skipping empty document list" in mock_logger.warning.call_args[0][0]
        assert len(list_of_docs) == 0


def test_process_document_list_missing_source_metadata(sample_file_datasource_processor):
    """Test processing document list with missing source metadata."""
    # Arrange
    documents = [Document(page_content="content", metadata={})]
    list_of_docs = []

    # Act
    with patch("codemie.datasource.file.file_datasource_processor.logger") as mock_logger:
        sample_file_datasource_processor._process_document_list(documents, list_of_docs)

        # Assert
        mock_logger.warning.assert_called_once()
        assert "missing source metadata" in mock_logger.warning.call_args[0][0]
        assert len(list_of_docs) == 0


def test_segregate_calls_process_single_document(sample_file_datasource_processor):
    """Test that _segregate_documents_input calls _process_single_document."""
    # Arrange
    document = Document(page_content="test", metadata={"source": "file.txt"})

    # Act
    with patch.object(sample_file_datasource_processor, "_process_single_document") as mock_process_single:
        sample_file_datasource_processor._segregate_documents_input([document])

        # Assert
        mock_process_single.assert_called_once()


def test_segregate_calls_process_document_list(sample_file_datasource_processor):
    """Test that _segregate_documents_input calls _process_document_list."""
    # Arrange
    document_list = [
        Document(page_content="test1", metadata={"source": "file.txt"}),
        Document(page_content="test2", metadata={"source": "file.txt"}),
    ]

    # Act
    with patch.object(sample_file_datasource_processor, "_process_document_list") as mock_process_list:
        sample_file_datasource_processor._segregate_documents_input([document_list])

        # Assert
        mock_process_list.assert_called_once()


@pytest.mark.parametrize(
    "input_docs,expected_single,expected_json,expected_list",
    [
        # Mixed document types
        (
            [
                Document(page_content="single", metadata={"source": "single.txt"}),
                Document(page_content='{"key": "value"}', metadata={"source": "data.json"}),
                [
                    Document(page_content="list1", metadata={"source": "list.pdf"}),
                    Document(page_content="list2", metadata={"source": "list.pdf"}),
                ],
            ],
            1,
            1,
            2,
        ),
        # Documents with missing metadata - should be skipped
        ([Document(page_content="test", metadata={})], 0, 0, 0),
        # Only single documents
        (
            [
                Document(page_content="test1", metadata={"source": "file1.txt"}),
                Document(page_content="test2", metadata={"source": "file2.pdf"}),
            ],
            2,
            0,
            0,
        ),
        # Only JSON documents
        (
            [
                Document(page_content='{"a": 1}', metadata={"source": "file1.json"}),
                Document(page_content='{"b": 2}', metadata={"source": "file2.json"}),
            ],
            0,
            2,
            0,
        ),
    ],
)
def test_segregate_documents_input_scenarios(
    sample_file_datasource_processor, input_docs, expected_single, expected_json, expected_list
):
    """Test segregating documents with different input scenarios."""
    # Act
    with patch("codemie.datasource.file.file_datasource_processor.logger"):
        list_of_docs, single_docs, json_docs = sample_file_datasource_processor._segregate_documents_input(input_docs)

    # Assert
    assert len(single_docs) == expected_single
    assert len(json_docs) == expected_json
    assert len(list_of_docs) == expected_list
