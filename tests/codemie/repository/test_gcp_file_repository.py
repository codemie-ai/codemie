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
from unittest.mock import patch, MagicMock
from codemie.repository.gcp_file_repository import GCPFileRepository

MIME_TEXT_PLAIN = "text/plain"
BUCKET_NAME_NEW = "new_bucket"
BUCKET_NAME_EXISTING = "existing_bucket"
FILE_NAME_NEW = "new_file.txt"
FILE_NAME_TEST = "test.txt"
FILE_CONTENT = "Hello GCP"
FILE_CONTENT_READ = "File Content"


@pytest.fixture
def setup_repository() -> tuple[GCPFileRepository, MagicMock]:
    with patch('codemie.repository.gcp_file_repository.storage') as mock_storage:
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_storage.Client.return_value.lookup_bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob
        repository = GCPFileRepository()
        yield repository, mock_storage, mock_bucket, mock_blob


@pytest.mark.usefixtures("setup_repository")
def test_read_pdf_file(setup_repository: tuple[GCPFileRepository, MagicMock, MagicMock, MagicMock]) -> None:
    repository, mock_storage, mock_bucket, mock_blob = setup_repository

    # Set up mock properties and download response
    mock_blob.download_as_bytes.return_value = b"%PDF-1.4..."
    mock_blob.content_type = "application/pdf"

    # Call the method under test
    file_object = repository.read_file(file_name="test_file.pdf", owner="existing_bucket")

    # Assertions
    mock_bucket.blob.assert_called_with("test_file.pdf")
    assert file_object.content == b"%PDF-1.4..."
    assert file_object.mime_type == "application/pdf"


@pytest.mark.usefixtures("setup_repository")
def test_read_csv_file(setup_repository: tuple[GCPFileRepository, MagicMock, MagicMock, MagicMock]) -> None:
    repository, mock_storage, mock_bucket, mock_blob = setup_repository

    # Set up mock properties and download response
    mock_blob.download_as_bytes.return_value = b"name,age\nAlice,30"
    mock_blob.content_type = "text/csv"

    # Call the method under test
    file_object = repository.read_file(file_name="test_file.csv", owner="existing_bucket")

    # Assertions
    mock_bucket.blob.assert_called_with("test_file.csv")
    assert file_object.content == "name,age\nAlice,30"
    assert file_object.mime_type == "text/csv"


@patch('codemie.repository.gcp_file_repository.storage')
def test_bucket_creation(mock_storage: MagicMock) -> None:
    # Setup - bucket does not exist
    mock_storage.Client().lookup_bucket.return_value = None
    mock_storage.Client().create_bucket.return_value = MagicMock()
    repo = GCPFileRepository()

    # Action - simulate writing a file which triggers bucket creation
    repo.write_file(
        name=FILE_NAME_NEW,
        mime_type=MIME_TEXT_PLAIN,
        owner=BUCKET_NAME_NEW,
        content=FILE_CONTENT,
    )

    # Asserts
    mock_storage.Client().create_bucket.assert_called_once_with(bucket_or_name=BUCKET_NAME_NEW, location='US')
    mock_storage.Client().lookup_bucket.assert_called_with(BUCKET_NAME_NEW)


@patch('codemie.repository.gcp_file_repository.storage')
def test_write_file(mock_storage: MagicMock) -> None:
    # Setup - bucket exists
    mock_bucket = MagicMock()
    mock_storage.Client().lookup_bucket.return_value = mock_bucket
    repo = GCPFileRepository()

    # Action
    repo.write_file(
        name=FILE_NAME_TEST,
        mime_type=MIME_TEXT_PLAIN,
        owner=BUCKET_NAME_EXISTING,
        content=FILE_CONTENT,
    )

    # Asserts
    mock_bucket.blob.assert_called_with(FILE_NAME_TEST)
    mock_bucket.blob().upload_from_string.assert_called_with(FILE_CONTENT, content_type=MIME_TEXT_PLAIN)


@patch('codemie.repository.gcp_file_repository.storage')
def test_read_file(mock_storage: MagicMock) -> None:
    # Setup
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_blob.download_as_bytes.return_value = FILE_CONTENT_READ.encode('utf-8')
    mock_blob.content_type = MIME_TEXT_PLAIN
    mock_bucket.blob.return_value = mock_blob
    mock_storage.Client().lookup_bucket.return_value = mock_bucket
    repo = GCPFileRepository()

    # Action
    file_object = repo.read_file(file_name=FILE_NAME_TEST, owner=BUCKET_NAME_EXISTING)

    # Asserts
    mock_bucket.blob.assert_called_with(FILE_NAME_TEST)
    assert file_object.content == FILE_CONTENT_READ
    assert file_object.mime_type == MIME_TEXT_PLAIN


@patch('codemie.repository.gcp_file_repository.storage')
def test_write_csv_file(mock_storage: MagicMock) -> None:
    # Setup - bucket exists
    mock_bucket = MagicMock()
    mock_storage.Client().lookup_bucket.return_value = mock_bucket
    repo = GCPFileRepository()

    csv_file_name = "test_file.csv"
    csv_content = b"name,age\nAlice,30"

    # Action
    repo.write_file(
        name=csv_file_name,
        mime_type="text/csv",
        owner=BUCKET_NAME_EXISTING,
        content=csv_content,
    )

    # Asserts
    mock_bucket.blob.assert_called_with(csv_file_name)
    mock_bucket.blob().upload_from_string.assert_called_with(csv_content, content_type="text/csv")


@patch('codemie.repository.gcp_file_repository.storage')
def test_write_pdf_file(mock_storage: MagicMock) -> None:
    # Setup - bucket exists
    mock_bucket = MagicMock()
    mock_storage.Client().lookup_bucket.return_value = mock_bucket
    repo = GCPFileRepository()

    pdf_file_name = "test_file.pdf"
    pdf_content = b"%PDF-1.4..."

    # Action
    repo.write_file(
        name=pdf_file_name,
        mime_type="application/pdf",
        owner=BUCKET_NAME_EXISTING,
        content=pdf_content,
    )

    # Asserts
    mock_bucket.blob.assert_called_with(pdf_file_name)
    mock_bucket.blob().upload_from_string.assert_called_with(pdf_content, content_type="application/pdf")
