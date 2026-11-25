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

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.tools import ToolException

from codemie.service.generated_files_reader import WorkspaceGeneratedFiles
from codemie_tools.data_management.code_executor.reserved_paths import RUNTIME_SDK_FILE_NAME
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import BRIDGE_DIR_NAME


def _row(path, size=3, blob="b", checksum="sum"):
    return SimpleNamespace(
        path=path,
        size=size,
        update_date=datetime(2026, 1, 1),
        blob_name=blob,
        blob_owner="o",
        mime_type="application/json",
        checksum=checksum,
    )


def _repo(rows, workspace_for=("conv", "u")):
    repo = MagicMock()

    def get(conversation_id, user_id):
        return SimpleNamespace(id="ws") if (conversation_id, user_id) == workspace_for else None

    repo.get_by_conversation_for_user.side_effect = get
    repo.list_files.return_value = rows
    return repo


def test_lists_every_workspace_file_of_bound_conversation():
    repo = _repo(
        [
            _row("artifacts/t1/r.json"),
            _row("note.txt"),
            _row("reports/q1/out.json"),
            _row("artifacts/t1/deep/y.txt"),
        ]
    )
    infos = WorkspaceGeneratedFiles("conv", "u", repository=repo).list_generated()
    assert [(i.path, i.name, i.size) for i in infos] == [
        ("artifacts/t1/r.json", "r.json", 3),
        ("note.txt", "note.txt", 3),
        ("reports/q1/out.json", "out.json", 3),
        ("artifacts/t1/deep/y.txt", "y.txt", 3),
    ]
    repo.list_files.assert_called_once_with("ws")


def test_reserved_paths_are_not_listed():
    repo = _repo([_row(f"{BRIDGE_DIR_NAME}/x.py"), _row(RUNTIME_SDK_FILE_NAME), _row("ok.txt")])
    infos = WorkspaceGeneratedFiles("conv", "u", repository=repo).list_generated()
    assert [i.path for i in infos] == ["ok.txt"]


def test_info_carries_blob_identity():
    repo = _repo([_row("a.txt", blob="blob1")])
    info = WorkspaceGeneratedFiles("conv", "u", repository=repo).list_generated()[0]
    assert info.blob == ("o", "blob1", "application/json")


def test_info_carries_content_checksum():
    repo = _repo([_row("a.txt", checksum="abc")])
    assert WorkspaceGeneratedFiles("conv", "u", repository=repo).list_generated()[0].checksum == "abc"


def test_read_nested_file_by_info():
    repo = _repo([_row("reports/q1/out.json", blob="blob2")])
    files = MagicMock()
    files.read_file.return_value = SimpleNamespace(content=b"{}")
    src = WorkspaceGeneratedFiles("conv", "u", repository=repo, file_repository=files)
    assert src.read_generated(src.list_generated()[0]) == b"{}"
    files.read_file.assert_called_once_with(file_name="blob2", owner="o", mime_type="application/json")


def test_other_conversation_returns_empty():
    repo = _repo([_row("artifacts/t1/r.json")])
    assert WorkspaceGeneratedFiles("other", "u", repository=repo).list_generated() == []
    assert WorkspaceGeneratedFiles("conv", "intruder", repository=repo).list_generated() == []
    repo.list_files.assert_not_called()


def test_oversize_file_refused_without_read(monkeypatch):
    monkeypatch.setattr("codemie.service.generated_files_reader.config.FILES_STORAGE_MAX_UPLOAD_SIZE", 10)
    repo = _repo([_row("artifacts/t1/big.bin", size=11)])
    files = MagicMock()
    src = WorkspaceGeneratedFiles("conv", "u", repository=repo, file_repository=files)
    info = src.list_generated()[0]
    with pytest.raises(ToolException):
        src.read_generated(info)
    files.read_file.assert_not_called()


def test_missing_size_row_still_limited_after_read(monkeypatch):
    monkeypatch.setattr("codemie.service.generated_files_reader.config.FILES_STORAGE_MAX_UPLOAD_SIZE", 10)
    repo = _repo([_row("artifacts/t1/big.bin", size=None)])
    files = MagicMock()
    files.read_file.return_value = SimpleNamespace(content=b"x" * 11)
    src = WorkspaceGeneratedFiles("conv", "u", repository=repo, file_repository=files)
    info = src.list_generated()[0]
    with pytest.raises(ToolException):
        src.read_generated(info)


def test_read_returns_bytes():
    repo = _repo([_row("artifacts/t1/r.json", blob="blob1")])
    files = MagicMock()
    files.read_file.return_value = SimpleNamespace(content=b'{"a":1}')
    src = WorkspaceGeneratedFiles("conv", "u", repository=repo, file_repository=files)
    assert src.read_generated(src.list_generated()[0]) == b'{"a":1}'
    files.read_file.assert_called_once_with(file_name="blob1", owner="o", mime_type="application/json")
