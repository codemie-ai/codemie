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

import os
import uuid
from typing import Generator
import pytest
from unittest.mock import patch, mock_open, MagicMock
from codemie.repository.file_system_repository import FileSystemRepository


@pytest.fixture
def setup_repository() -> Generator:
    file_name = f"dummy_file-{uuid.uuid4()}.txt"
    mime_type = "txt"
    owner = f"owner-{uuid.uuid4()}"
    file_path = f'./my/fake/path/{uuid.uuid4()}'
    file_content = b"Test content"
    repo = FileSystemRepository()
    yield repo, file_name, mime_type, owner, file_path, file_content


@patch("builtins.open", new_callable=mock_open, read_data="Test content")
@patch("os.path.dirname")
def test_read_file(mock_dirname: patch, mock_file: patch, setup_repository: Generator) -> None:
    repo, file_name, mime_type, owner, file_path, file_content = setup_repository

    mock_dirname.return_value = file_path
    result = repo.read_file(file_name, owner=owner)

    assert result.content == file_content.decode('utf-8')
    assert result.path == file_path
    assert result.name == file_name
    mock_dirname.assert_called_once_with(f'./codemie-storage/{owner}/{file_name}')


@patch("builtins.open", new_callable=mock_open, read_data=b"%PDF-1.4...")
def test_read_pdf_file(
    mock_file: MagicMock, setup_repository: tuple[FileSystemRepository, str, str, str, str, bytes]
) -> None:
    repo, file_name, mime_type, owner, file_path, file_content = setup_repository
    pdf_file_name = "test_file.pdf"
    pdf_content = b"%PDF-1.4..."

    result = repo.read_file(pdf_file_name, owner=owner)

    assert result.content == pdf_content
    assert result.mime_type == "application/pdf"
    assert result.name == pdf_file_name


@patch("builtins.open", new_callable=mock_open, read_data=b"name,age\nAlice,30")
def test_read_csv_file(
    mock_file: MagicMock, setup_repository: tuple[FileSystemRepository, str, str, str, str, bytes]
) -> None:
    repo, file_name, mime_type, owner, file_path, file_content = setup_repository
    csv_file_name = "test_file.csv"
    csv_content = b"name,age\nAlice,30"

    result = repo.read_file(csv_file_name, owner=owner)

    assert result.content == csv_content
    assert result.mime_type == "text/csv"
    assert result.name == csv_file_name


@patch("builtins.open", new_callable=mock_open, read_data=b"Test content")
def test_write_file(mock_file: patch, setup_repository: Generator) -> None:
    repo, file_name, mime_type, owner, file_path, file_content = setup_repository

    result = repo.write_file(file_name, mime_type, owner, file_content)
    expected_path = os.path.normpath(f'./codemie-storage/{owner}/{file_name}')
    actual_path = os.path.normpath(result.path)

    assert result.content == file_content
    assert actual_path == expected_path
    assert result.name == file_name


@patch("builtins.open", new_callable=mock_open, read_data=b"name,age\nAlice,30")
def test_write_csv_file(mock_file: patch, setup_repository: Generator) -> None:
    repo, file_name, _, owner, _, _ = setup_repository
    csv_file_name = "test_file.csv"
    mime_type = "text/csv"
    file_content = b"name,age\nAlice,30"

    result = repo.write_file(csv_file_name, mime_type, owner, file_content)
    expected_path = os.path.normpath(f'./codemie-storage/{owner}/{csv_file_name}')
    actual_path = os.path.normpath(result.path)

    assert result.content == file_content
    assert result.mime_type == "text/csv"
    assert actual_path == expected_path
    assert result.name == csv_file_name


@patch("builtins.open", new_callable=mock_open, read_data=b"%PDF-1.4...")
def test_write_pdf_file(mock_file: patch, setup_repository: Generator) -> None:
    repo, file_name, _, owner, _, _ = setup_repository
    pdf_file_name = "test_file.pdf"
    mime_type = "application/pdf"
    file_content = b"%PDF-1.4..."

    result = repo.write_file(pdf_file_name, mime_type, owner, file_content)
    expected_path = os.path.normpath(f'./codemie-storage/{owner}/{pdf_file_name}')
    actual_path = os.path.normpath(result.path)

    assert result.content == file_content
    assert result.mime_type == "application/pdf"
    assert actual_path == expected_path
    assert result.name == pdf_file_name
