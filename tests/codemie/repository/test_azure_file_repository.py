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
from unittest.mock import patch, MagicMock, ANY
from azure.storage.blob import BlobServiceClient, ContainerClient, BlobClient
from codemie.repository.azure_file_repository import AzureFileRepository


@pytest.fixture
def setup_repository() -> tuple[AzureFileRepository, MagicMock]:
    with patch('azure.storage.blob.BlobServiceClient.from_connection_string') as mock_blob_service_client:
        mock_blob_service_client.return_value = MagicMock(spec=BlobServiceClient)
        repository = AzureFileRepository(connection_string="fake_connection_string")
        yield repository, mock_blob_service_client.return_value


@patch('azure.storage.blob.ContainerClient.get_blob_client', autospec=True)
@patch('azure.storage.blob.BlobClient.upload_blob', autospec=True)
def test_create_directory(
    mock_upload_blob: MagicMock,
    mock_get_blob_client: MagicMock,
    setup_repository: tuple[AzureFileRepository, MagicMock],
) -> None:
    repository, mock_blob_service_client = setup_repository

    # Set up mock objects
    mock_container_client = MagicMock(spec=ContainerClient)
    mock_blob_client = MagicMock(spec=BlobClient)
    mock_blob_service_client.get_container_client.return_value = mock_container_client
    mock_container_client.get_blob_client.return_value = mock_blob_client

    # Call the method under test
    dir_object = repository.create_directory("new_directory", "owner")

    # Assertions
    mock_container_client.get_blob_client.assert_called_once_with("new_directory/")
    mock_blob_client.upload_blob.assert_called_once_with("", content_settings=ANY, overwrite=True)
    assert dir_object.name == "new_directory"
    assert dir_object.owner == "owner"


@patch('azure.storage.blob.BlobClient.upload_blob', autospec=True)
@patch('azure.storage.blob.ContainerClient.get_blob_client', autospec=True)
def test_write_file(
    mock_get_blob_client: MagicMock,
    mock_upload_blob: MagicMock,
    setup_repository: tuple[AzureFileRepository, MagicMock],
) -> None:
    repository, mock_blob_service_client = setup_repository

    # Set up mock objects
    mock_container_client = MagicMock(spec=ContainerClient)
    mock_blob_client = MagicMock(spec=BlobClient)
    mock_blob_service_client.get_container_client.return_value = mock_container_client
    mock_container_client.get_blob_client.return_value = mock_blob_client

    # Call the method under test
    file_object = repository.write_file("file.txt", "text/plain", "owner", content="file content")

    # Assertions
    mock_container_client.get_blob_client.assert_called_once_with("file.txt")
    mock_blob_client.upload_blob.assert_called_once_with("file content", content_settings=ANY, overwrite=True)
    assert file_object.name == "file.txt"
    assert file_object.mime_type == "text/plain"
    assert file_object.owner == "owner"
    assert file_object.content == "file content"


@patch('azure.storage.blob.BlobClient.download_blob', autospec=True)
@patch('azure.storage.blob.BlobClient.get_blob_properties', autospec=True)
@patch('azure.storage.blob.ContainerClient.get_blob_client', autospec=True)
def test_read_file(
    mock_get_blob_client: MagicMock,
    mock_get_blob_properties: MagicMock,
    mock_download_blob: MagicMock,
    setup_repository: tuple[AzureFileRepository, MagicMock],
) -> None:
    repository, mock_blob_service_client = setup_repository

    # Set up mock objects
    mock_container_client = MagicMock(spec=ContainerClient)
    mock_blob_client = MagicMock(spec=BlobClient)
    mock_blob_service_client.get_container_client.return_value = mock_container_client
    mock_container_client.get_blob_client.return_value = mock_blob_client

    mock_blob_properties = MagicMock()
    mock_blob_properties.content_settings.content_type = "text/plain"
    mock_blob_client.get_blob_properties.return_value = mock_blob_properties
    mock_blob_client.download_blob.return_value.readall.return_value = "file content"

    # Call the method under test
    file_object = repository.read_file("file.txt", "owner")

    # Assertions
    mock_container_client.get_blob_client.assert_called_once_with("file.txt")
    mock_blob_client.get_blob_properties.assert_called_once()
    mock_blob_client.download_blob.return_value.readall.assert_called_once()
    assert file_object.name == "file.txt"
    assert file_object.mime_type == "text/plain"
    assert file_object.owner == "owner"
    assert file_object.content == "file content"


@patch('azure.storage.blob.BlobClient.download_blob', autospec=True)
@patch('azure.storage.blob.BlobClient.get_blob_properties', autospec=True)
@patch('azure.storage.blob.ContainerClient.get_blob_client', autospec=True)
def test_read_pdf_file(
    mock_get_blob_client: MagicMock,
    mock_get_blob_properties: MagicMock,
    mock_download_blob: MagicMock,
    setup_repository: tuple[AzureFileRepository, MagicMock],
) -> None:
    repository, mock_blob_service_client = setup_repository

    # Set up mock objects
    mock_container_client = MagicMock(spec=ContainerClient)
    mock_blob_client = MagicMock(spec=BlobClient)
    mock_blob_service_client.get_container_client.return_value = mock_container_client
    mock_container_client.get_blob_client.return_value = mock_blob_client

    mock_blob_properties = MagicMock()
    mock_blob_properties.content_settings.content_type = "application/pdf"
    mock_blob_client.get_blob_properties.return_value = mock_blob_properties
    mock_blob_client.download_blob.return_value.readall.return_value = b"%PDF-1.4..."

    # Call the method under test
    file_object = repository.read_file("test_file.pdf", owner="owner")

    # Assertions
    mock_container_client.get_blob_client.assert_called_once_with("test_file.pdf")
    mock_blob_client.get_blob_properties.assert_called_once()
    mock_blob_client.download_blob.return_value.readall.assert_called_once()
    assert file_object.name == "test_file.pdf"
    assert file_object.mime_type == "application/pdf"
    assert file_object.owner == "owner"
    assert file_object.content == b"%PDF-1.4..."


@patch('azure.storage.blob.BlobClient.download_blob', autospec=True)
@patch('azure.storage.blob.BlobClient.get_blob_properties', autospec=True)
@patch('azure.storage.blob.ContainerClient.get_blob_client', autospec=True)
def test_read_csv_file(
    mock_get_blob_client: MagicMock,
    mock_get_blob_properties: MagicMock,
    mock_download_blob: MagicMock,
    setup_repository: tuple[AzureFileRepository, MagicMock],
) -> None:
    repository, mock_blob_service_client = setup_repository

    # Set up mock objects
    mock_container_client = MagicMock(spec=ContainerClient)
    mock_blob_client = MagicMock(spec=BlobClient)
    mock_blob_service_client.get_container_client.return_value = mock_container_client
    mock_container_client.get_blob_client.return_value = mock_blob_client

    mock_blob_properties = MagicMock()
    mock_blob_properties.content_settings.content_type = "text/csv"
    mock_blob_client.get_blob_properties.return_value = mock_blob_properties
    mock_blob_client.download_blob.return_value.readall.return_value = b"name,age\nAlice,30"

    # Call the method under test
    file_object = repository.read_file("test_file.csv", owner="owner")

    # Assertions
    mock_container_client.get_blob_client.assert_called_once_with("test_file.csv")
    mock_blob_client.get_blob_properties.assert_called_once()
    mock_blob_client.download_blob.return_value.readall.assert_called_once()
    assert file_object.name == "test_file.csv"
    assert file_object.mime_type == "text/csv"
    assert file_object.owner == "owner"
    assert file_object.content == b"name,age\nAlice,30"


@patch('azure.storage.blob.BlobClient.upload_blob', autospec=True)
@patch('azure.storage.blob.ContainerClient.get_blob_client', autospec=True)
def test_write_csv_file(
    mock_get_blob_client: MagicMock,
    mock_upload_blob: MagicMock,
    setup_repository: tuple[AzureFileRepository, MagicMock],
) -> None:
    repository, mock_blob_service_client = setup_repository

    # Set up mock objects
    mock_container_client = MagicMock(spec=ContainerClient)
    mock_blob_client = MagicMock(spec=BlobClient)
    mock_blob_service_client.get_container_client.return_value = mock_container_client
    mock_container_client.get_blob_client.return_value = mock_blob_client

    # Call the method under test
    file_object = repository.write_file("test_file.csv", "text/csv", "owner", content=b"name,age\nAlice,30")

    # Assertions
    mock_container_client.get_blob_client.assert_called_once_with("test_file.csv")
    mock_blob_client.upload_blob.assert_called_once_with(b"name,age\nAlice,30", content_settings=ANY, overwrite=True)
    assert file_object.name == "test_file.csv"
    assert file_object.mime_type == "text/csv"
    assert file_object.owner == "owner"
    assert file_object.content == b"name,age\nAlice,30"


@patch('azure.storage.blob.BlobClient.upload_blob', autospec=True)
@patch('azure.storage.blob.ContainerClient.get_blob_client', autospec=True)
def test_write_pdf_file(
    mock_get_blob_client: MagicMock,
    mock_upload_blob: MagicMock,
    setup_repository: tuple[AzureFileRepository, MagicMock],
) -> None:
    repository, mock_blob_service_client = setup_repository

    # Set up mock objects
    mock_container_client = MagicMock(spec=ContainerClient)
    mock_blob_client = MagicMock(spec=BlobClient)
    mock_blob_service_client.get_container_client.return_value = mock_container_client
    mock_container_client.get_blob_client.return_value = mock_blob_client

    # Call the method under test
    file_object = repository.write_file("test_file.pdf", "application/pdf", "owner", content=b"%PDF-1.4...")

    # Assertions
    mock_container_client.get_blob_client.assert_called_once_with("test_file.pdf")
    mock_blob_client.upload_blob.assert_called_once_with(b"%PDF-1.4...", content_settings=ANY, overwrite=True)
    assert file_object.name == "test_file.pdf"
    assert file_object.mime_type == "application/pdf"
    assert file_object.owner == "owner"
    assert file_object.content == b"%PDF-1.4..."
