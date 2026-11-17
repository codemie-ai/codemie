# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""The workspace archive: every file the workspace lists, in one zip."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from codemie_tools.base.file_object import FileObject

from codemie.configs import config
from codemie.core.exceptions import ValidationException
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie.service.workspace_archive import WorkspaceArchiveFailedError, WorkspaceArchiveTooLargeError

USER = MagicMock(id="u1")
SECRET = "s3://bucket/owner/secret-key"


def _row(path: str, content: bytes | str = b"x", *, fail: bool = False):
    return SimpleNamespace(
        path=path,
        blob_name=f"blob/{path}",
        blob_owner="workspace-ws-1",
        mime_type="text/plain",
        _content=content,
        _fail=fail,
    )


def _zip(archive) -> zipfile.ZipFile:
    return zipfile.ZipFile(BytesIO(archive.content))


@pytest.fixture
def service() -> AgentWorkspaceService:
    svc = AgentWorkspaceService.__new__(AgentWorkspaceService)
    svc.repository = MagicMock()
    svc.file_repository = MagicMock()
    svc.repository.get_by_id_for_user.return_value = SimpleNamespace(id="ws-1", conversation_id="conv-1", name="Ws")
    svc.repository.list_files.return_value = []
    svc._get_conversation_uploaded_files = MagicMock(return_value=[])  # type: ignore[method-assign]
    return svc


@pytest.fixture(autouse=True)
def readable_conversation():
    with (
        patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=MagicMock()),
        patch("codemie.service.workspace_archive.Ability") as ability,
    ):
        ability.return_value.can.return_value = True
        yield ability


def _serve(svc: AgentWorkspaceService, *rows) -> None:
    by_blob = {r.blob_name: r for r in rows}

    def read_file(file_name, owner, mime_type=None):
        row = by_blob[file_name]
        if row._fail:
            raise RuntimeError(SECRET)
        return FileObject(name=file_name, mime_type=mime_type or "", owner=owner, content=row._content)

    svc.file_repository.read_file.side_effect = read_file


def _with_db(svc: AgentWorkspaceService, *rows) -> None:
    svc.repository.list_files.return_value = list(rows)
    _serve(svc, *rows)


def test_build_workspace_archive_keeps_binary_and_text_bytes(service) -> None:
    _with_db(service, _row("bin.dat", b"\x00\xff\x01"), _row("t.txt", b"hello"), _row("s.txt", "str-content"))

    archive = service.build_workspace_archive("ws-1", USER)

    z = _zip(archive)
    assert z.read("bin.dat") == b"\x00\xff\x01"
    assert z.read("t.txt") == b"hello"
    assert z.read("s.txt") == b"str-content"
    assert archive.included_count == 3
    assert archive.skipped_paths == []
    assert archive.total_bytes == 3 + 5 + 11


def test_build_workspace_archive_reads_blobs_as_raw_bytes(service) -> None:
    _with_db(service, _row("a.txt", b"a\r\n\xff"))

    service.build_workspace_archive("ws-1", USER)

    kwargs = service.file_repository.read_file.call_args.kwargs
    assert kwargs["mime_type"] == "application/octet-stream"


def test_build_workspace_archive_keeps_nested_names(service) -> None:
    _with_db(service, _row("a/b/c.txt"), _row("top.txt"))

    z = _zip(service.build_workspace_archive("ws-1", USER))

    assert sorted(z.namelist()) == ["a/b/c.txt", "top.txt"]


def test_build_workspace_archive_includes_virtual_upload(service) -> None:
    virtual = _row("upload.pdf", b"pdf")
    service._get_conversation_uploaded_files.return_value = [virtual]
    _serve(service, virtual)

    z = _zip(service.build_workspace_archive("ws-1", USER))

    assert z.read("upload.pdf") == b"pdf"


def test_build_workspace_archive_db_row_wins_over_virtual_with_same_path(service) -> None:
    db = _row("same.txt", b"db")
    virtual = _row("same.txt", b"virtual")
    virtual.blob_name = "blob/virtual-same.txt"
    service.repository.list_files.return_value = [db]
    service._get_conversation_uploaded_files.return_value = [virtual]
    _serve(service, db, virtual)

    z = _zip(service.build_workspace_archive("ws-1", USER))

    assert z.namelist() == ["same.txt"]
    assert z.read("same.txt") == b"db"


def test_build_workspace_archive_rejects_non_owner_without_reading(service) -> None:
    service.repository.get_by_id_for_user.return_value = None

    with pytest.raises(ValidationException):
        service.build_workspace_archive("ws-other", USER)

    service.file_repository.read_file.assert_not_called()


def test_build_workspace_archive_empty_workspace_is_rejected(service) -> None:
    with pytest.raises(ValidationException, match="Workspace has no downloadable files"):
        service.build_workspace_archive("ws-1", USER)


def test_build_workspace_archive_file_count_cap_stops_before_reading(service, monkeypatch) -> None:
    monkeypatch.setattr(config, "WORKSPACE_DOWNLOAD_MAX_FILES", 2)
    _with_db(service, _row("1.txt"), _row("2.txt"), _row("3.txt"))

    with pytest.raises(WorkspaceArchiveTooLargeError):
        service.build_workspace_archive("ws-1", USER)

    service.file_repository.read_file.assert_not_called()


def test_build_workspace_archive_byte_cap_stops_reading(service, monkeypatch) -> None:
    monkeypatch.setattr(config, "WORKSPACE_DOWNLOAD_MAX_BYTES", 10)
    _with_db(service, _row("1.txt", b"123456"), _row("2.txt", b"123456"), _row("3.txt", b"123456"))

    with pytest.raises(WorkspaceArchiveTooLargeError):
        service.build_workspace_archive("ws-1", USER)

    assert service.file_repository.read_file.call_count == 2


def test_build_workspace_archive_partial_failure_lists_path_only(service) -> None:
    _with_db(service, _row("good.txt", b"ok"), _row("bad/one.txt", fail=True))

    archive = service.build_workspace_archive("ws-1", USER)

    z = _zip(archive)
    assert z.read("good.txt") == b"ok"
    manifest = z.read("_SKIPPED_FILES.txt").decode()
    assert "bad/one.txt" in manifest
    assert "secret-key" not in manifest
    assert "workspace-ws-1" not in manifest
    assert archive.skipped_paths == ["bad/one.txt"]
    assert archive.included_count == 1


def test_build_workspace_archive_manifest_name_collision_gets_suffix(service) -> None:
    _with_db(service, _row("_SKIPPED_FILES.txt", b"mine"), _row("bad.txt", fail=True))

    z = _zip(service.build_workspace_archive("ws-1", USER))

    assert z.read("_SKIPPED_FILES.txt") == b"mine"
    assert "bad.txt" in z.read("_SKIPPED_FILES_1.txt").decode()


def test_build_workspace_archive_all_files_failing_raises(service) -> None:
    _with_db(service, _row("a.txt", fail=True), _row("b.txt", fail=True))

    with pytest.raises(WorkspaceArchiveFailedError, match="None of the workspace files could be read"):
        service.build_workspace_archive("ws-1", USER)


def test_build_workspace_archive_unsafe_names_are_skipped_unread(service) -> None:
    _with_db(
        service,
        _row("../evil.txt"),
        _row("/abs.txt"),
        _row("codemie_runtime_sdk.py"),
        _row("fine.txt", b"ok"),
    )

    archive = service.build_workspace_archive("ws-1", USER)

    assert _zip(archive).read("fine.txt") == b"ok"
    assert archive.skipped_paths == ["../evil.txt", "/abs.txt", "codemie_runtime_sdk.py"]
    assert [c.kwargs["file_name"] for c in service.file_repository.read_file.call_args_list] == ["blob/fine.txt"]
    assert not any(".." in n or n.startswith("/") for n in _zip(archive).namelist())


@pytest.mark.parametrize(
    ("name", "expected"),
    [('My "ws"/é', None), ("", "ws-1"), ("   ", "ws-1")],
)
def test_build_workspace_archive_filename_is_header_safe(service, name, expected) -> None:
    service.repository.get_by_id_for_user.return_value = SimpleNamespace(id="ws-1", conversation_id="c", name=name)
    _with_db(service, _row("a.txt"))

    filename = service.build_workspace_archive("ws-1", USER).filename

    assert re.fullmatch(r"[A-Za-z0-9._-]+\.zip", filename)
    if expected:
        assert filename == f"{expected}.zip"


# --- audit log and metric -------------------------------------------------------------------------------------------

MODULE = "codemie.service.workspace_archive"


@pytest.fixture
def audit():
    with patch(f"{MODULE}.logger") as log, patch(f"{MODULE}.BaseMonitoringService.send_count_metric") as metric:
        yield SimpleNamespace(log=log, metric=metric)


def _assert_audited(audit, outcome: str, *, included: int, skipped: int, total: int) -> None:
    audit.log.info.assert_called_once()
    line = audit.log.info.call_args.args[0]
    for expected in (
        "u1",
        "ws-1",
        f"included={included}",
        f"skipped={skipped}",
        f"total_bytes={total}",
        f"outcome={outcome}",
    ):
        assert expected in line
    audit.metric.assert_called_once()
    kwargs = audit.metric.call_args.kwargs
    assert kwargs["name"] == "workspace_archive_download"
    assert kwargs["attributes"] == {"outcome": outcome, "user_id": "u1"}


def test_build_workspace_archive_audit_success(service, audit) -> None:
    _with_db(service, _row("a.txt", b"abc"))

    service.build_workspace_archive("ws-1", USER)

    _assert_audited(audit, "success", included=1, skipped=0, total=3)


def test_build_workspace_archive_audit_partial(service, audit) -> None:
    _with_db(service, _row("a.txt", b"abc"), _row("b.txt", fail=True))

    service.build_workspace_archive("ws-1", USER)

    _assert_audited(audit, "partial", included=1, skipped=1, total=3)


def test_build_workspace_archive_audit_empty(service, audit) -> None:
    with pytest.raises(ValidationException):
        service.build_workspace_archive("ws-1", USER)

    _assert_audited(audit, "empty", included=0, skipped=0, total=0)


def test_build_workspace_archive_audit_too_large_by_count(service, audit, monkeypatch) -> None:
    monkeypatch.setattr(config, "WORKSPACE_DOWNLOAD_MAX_FILES", 1)
    _with_db(service, _row("1.txt"), _row("2.txt"))

    with pytest.raises(WorkspaceArchiveTooLargeError):
        service.build_workspace_archive("ws-1", USER)

    _assert_audited(audit, "too_large", included=0, skipped=0, total=0)


def test_build_workspace_archive_audit_too_large_by_bytes(service, audit, monkeypatch) -> None:
    monkeypatch.setattr(config, "WORKSPACE_DOWNLOAD_MAX_BYTES", 10)
    _with_db(service, _row("1.txt", b"123456"), _row("2.txt", b"123456"))

    with pytest.raises(WorkspaceArchiveTooLargeError):
        service.build_workspace_archive("ws-1", USER)

    _assert_audited(audit, "too_large", included=1, skipped=0, total=12)


def test_build_workspace_archive_audit_failed_when_every_read_fails(service, audit) -> None:
    _with_db(service, _row("a.txt", fail=True))

    with pytest.raises(WorkspaceArchiveFailedError):
        service.build_workspace_archive("ws-1", USER)

    _assert_audited(audit, "failed", included=0, skipped=1, total=0)


def test_build_workspace_archive_audit_failed_for_non_owner(service, audit) -> None:
    service.repository.get_by_id_for_user.return_value = None

    with pytest.raises(ValidationException):
        service.build_workspace_archive("ws-other", USER)

    audit.log.info.assert_called_once()
    assert "outcome=failed" in audit.log.info.call_args.args[0]
    assert audit.metric.call_args.kwargs["attributes"]["outcome"] == "failed"


def test_build_workspace_archive_audit_failed_for_unexpected_error(service, audit) -> None:
    _with_db(service, _row("a.txt"))

    with patch(f"{MODULE}.zipfile.ZipFile", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            service.build_workspace_archive("ws-1", USER)

    _assert_audited(audit, "failed", included=0, skipped=0, total=0)


def test_build_workspace_archive_audit_metric_failure_does_not_change_result(service, audit) -> None:
    audit.metric.side_effect = RuntimeError("metrics down")
    _with_db(service, _row("a.txt", b"abc"))

    archive = service.build_workspace_archive("ws-1", USER)

    assert _zip(archive).read("a.txt") == b"abc"
    audit.log.warning.assert_called()


def test_build_workspace_archive_rejects_unreadable_conversation(service, readable_conversation) -> None:
    _with_db(service, _row("a.txt"))
    readable_conversation.return_value.can.return_value = False

    with pytest.raises(ValidationException, match="Workspace 'ws-1' not found"):
        service.build_workspace_archive("ws-1", USER)

    service.file_repository.read_file.assert_not_called()
