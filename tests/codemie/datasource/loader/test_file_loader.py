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
from unittest.mock import MagicMock
from codemie.datasource.loader.file_loader import FilesDatasourceLoader
from codemie_tools.base.file_object import FileObject
from langchain_core.documents import Document


@pytest.fixture
def sample_files_datasource_loader():
    return FilesDatasourceLoader(
        total_count_of_documents=10, files_paths=[MagicMock(name="file1.csv", owner="owner1")], csv_separator=","
    )


def test_files_datasource_loader_init(sample_files_datasource_loader):
    assert sample_files_datasource_loader.total_count_of_documents == 10
    assert sample_files_datasource_loader.file_repo is not None
    assert sample_files_datasource_loader.files_paths[0].owner == "owner1"
    assert sample_files_datasource_loader._csv_separator == ","


def test_fetch_remote_stats(sample_files_datasource_loader):
    """Test fetch_remote_stats returns both documents_count_key and total_documents."""
    stats = sample_files_datasource_loader.fetch_remote_stats()
    assert stats == {"documents_count_key": 10, "total_documents": 10}
    assert "documents_count_key" in stats
    assert "total_documents" in stats
    assert stats["documents_count_key"] == stats["total_documents"]


def test_lazy_load_csv(sample_files_datasource_loader):
    lazy_load_doc = []
    file = FileObject(name="file1.csv", content="col1,col2\nval1,val2\n", owner="owner1", mime_type="csv")
    sample_files_datasource_loader.file_repo.read_file = MagicMock(return_value=file)

    documents = sample_files_datasource_loader.lazy_load()

    for doc in documents:
        assert len(doc) == 1
        assert isinstance(doc[0], Document)
        assert doc[0].page_content == 'col1: val1\ncol2: val2'
        lazy_load_doc.append(doc)
    assert len(lazy_load_doc) == 1


def test_lazy_load_txt(sample_files_datasource_loader):
    file = FileObject(name="file1.txt", content="some,content\n", owner="owner1", mime_type="txt")
    sample_files_datasource_loader.file_repo.read_file = MagicMock(return_value=file)
    sample_files_datasource_loader.get_file_data = MagicMock(return_value=b"some,content\n")

    # Mock _lazy_load_documents to return a document
    sample_files_datasource_loader._lazy_load_documents = MagicMock(
        return_value=[Document(page_content="some,content\n", metadata={"source": "file1.txt"})]
    )

    documents = list(sample_files_datasource_loader.lazy_load())

    assert len(documents) == 1
    assert isinstance(documents[0], list)
    assert len(documents[0]) == 1
    assert documents[0][0].page_content == "some,content\n"


def test_lazy_load_documents(sample_files_datasource_loader):
    file = FileObject(name="file1.csv", content="col1,col2\nval1,val2\n", owner="owner1", mime_type="txt")
    sample_files_datasource_loader.file_repo.read_file = MagicMock(return_value=file)
    sample_files_datasource_loader.get_file_data = MagicMock(return_value=b"col1,col2\nval1,val2\n")

    documents = sample_files_datasource_loader._lazy_load_documents(file, "csv")

    assert len(documents) == 1
    assert isinstance(documents[0], Document)
    assert documents[0].page_content == "col1: val1\ncol2: val2"
    assert documents[0].metadata["source"] == "file1.csv"
    assert documents[0].metadata["row"] == 0


@pytest.mark.parametrize("file_ext", ["txt", "html", "epub", "ipynb", "msg", "docx", "xlsx"])
def test_lazy_load_with_new_loaders(sample_files_datasource_loader, file_ext):
    file = FileObject(name=f"file.{file_ext}", content="sample content", owner="owner1", mime_type=file_ext)
    sample_files_datasource_loader.file_repo.read_file = MagicMock(return_value=file)
    sample_files_datasource_loader.get_file_data = MagicMock(return_value=b"sample content")

    # Mock _lazy_load_documents to avoid actual file operations
    sample_files_datasource_loader._lazy_load_documents = MagicMock(return_value=[Document(page_content="test")])

    documents = list(sample_files_datasource_loader.lazy_load())

    # Verify _lazy_load_documents was called with correct parameters
    sample_files_datasource_loader._lazy_load_documents.assert_called_once_with(file, file_ext)
    assert len(documents) == 1
    assert documents[0][0].page_content == "test"


def test_fetch_remote_stats_has_total_documents(sample_files_datasource_loader):
    """Test that total_documents is present in stats."""
    stats = sample_files_datasource_loader.fetch_remote_stats()

    assert "total_documents" in stats
    assert "documents_count_key" in stats


def test_fetch_remote_stats_keys_match_count(sample_files_datasource_loader):
    """Test both keys have the same value as total_count_of_documents."""
    sample_files_datasource_loader.total_count_of_documents = 15
    stats = sample_files_datasource_loader.fetch_remote_stats()

    assert stats["documents_count_key"] == 15
    assert stats["total_documents"] == 15
