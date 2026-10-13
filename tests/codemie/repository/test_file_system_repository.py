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

import uuid
from pathlib import Path
from typing import Generator
import pytest
from unittest.mock import patch, mock_open, MagicMock
from codemie.configs import config
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
    expected_path = str(Path(config.FILES_STORAGE_DIR).resolve() / owner / file_name)
    mock_dirname.assert_called_once_with(expected_path)


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
    expected_path = str(Path(config.FILES_STORAGE_DIR).resolve() / owner / file_name)

    assert result.content == file_content
    assert result.path == expected_path
    assert result.name == file_name


@patch("builtins.open", new_callable=mock_open, read_data=b"name,age\nAlice,30")
def test_write_csv_file(mock_file: patch, setup_repository: Generator) -> None:
    repo, file_name, _, owner, _, _ = setup_repository
    csv_file_name = "test_file.csv"
    mime_type = "text/csv"
    file_content = b"name,age\nAlice,30"

    result = repo.write_file(csv_file_name, mime_type, owner, file_content)
    expected_path = str(Path(config.FILES_STORAGE_DIR).resolve() / owner / csv_file_name)

    assert result.content == file_content
    assert result.mime_type == "text/csv"
    assert result.path == expected_path
    assert result.name == csv_file_name


@patch("builtins.open", new_callable=mock_open, read_data=b"%PDF-1.4...")
def test_write_pdf_file(mock_file: patch, setup_repository: Generator) -> None:
    repo, file_name, _, owner, _, _ = setup_repository
    pdf_file_name = "test_file.pdf"
    mime_type = "application/pdf"
    file_content = b"%PDF-1.4..."

    result = repo.write_file(pdf_file_name, mime_type, owner, file_content)
    expected_path = str(Path(config.FILES_STORAGE_DIR).resolve() / owner / pdf_file_name)

    assert result.content == file_content
    assert result.mime_type == "application/pdf"
    assert result.path == expected_path
    assert result.name == pdf_file_name


class TestCreateDirectory:
    def test_creates_directory_with_exist_ok_to_avoid_toctou_race(self, setup_repository: Generator) -> None:
        """makedirs must be called with exist_ok=True: two upload threads for the same brand-new
        user can both pass the pre-check before either creates the directory, so the second
        call landing on an already-created directory must not raise."""
        repo, file_name, _, owner, _, _ = setup_repository

        with patch("os.path.exists", return_value=False), patch("os.makedirs") as mock_makedirs:
            repo.create_directory(name=file_name, owner=owner)

        assert mock_makedirs.call_args.kwargs.get("exist_ok") is True

    def test_create_directory_is_idempotent_when_directory_already_exists(
        self, setup_repository: Generator, tmp_path: Path
    ) -> None:
        repo, file_name, _, owner, _, _ = setup_repository

        with patch.object(config, "FILES_STORAGE_DIR", str(tmp_path)):
            repo.create_directory(name=file_name, owner=owner)
            # A concurrent second call for the same owner must not raise FileExistsError.
            result = repo.create_directory(name=file_name, owner=owner)

        assert result.owner == owner


def test_hash_prefixed_skill_blob_name_round_trips(tmp_path: Path) -> None:
    # Skill companion blobs are stored as '<sha256[:32]>/<path>'; the slash must create a subdirectory.
    prefix = "ab12cd34ef56ab12" * 2
    name = f"{prefix}/scripts/run.py"
    owner = "skill-s1"

    with patch.object(config, "FILES_STORAGE_DIR", str(tmp_path)):
        repo = FileSystemRepository()
        repo.write_file(name=name, mime_type="text/x-python", owner=owner, content=b"print(1)")
        result = repo.read_file(name, owner=owner)

    assert (tmp_path / owner / prefix / "scripts" / "run.py").read_bytes() == b"print(1)"
    assert result.content in (b"print(1)", "print(1)")
