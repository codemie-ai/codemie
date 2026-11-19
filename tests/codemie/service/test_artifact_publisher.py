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

"""ArtifactPublisherService: one place that validates, names and stores produced files."""

from __future__ import annotations

import base64
import io
import re
import zipfile
from unittest.mock import MagicMock, patch

import pytest
from codemie_tools.base.artifact_publishing import PublishContext
from codemie_tools.base.file_object import FileObject

from codemie.rest_api.models.agent_workspace import AgentWorkspace, AgentWorkspaceFile
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie.service.file_service.sandbox_links import SANDBOX_FILE_PREFIX
from codemie.service.artifact_publisher import ArtifactPublisherService

USER_ID = "user-1"
WORKSPACE_ID = "ws-1"
CONVERSATION_ID = "conv-1"
TURN_ID = "turn-1"
CTX = PublishContext(user_id=USER_ID, conversation_id=CONVERSATION_ID, turn_id=TURN_ID)


class _FakeRepository:
    def __init__(self) -> None:
        self.workspace = AgentWorkspace(id=WORKSPACE_ID, conversation_id=CONVERSATION_ID, user_id=USER_ID)
        self.files: list[AgentWorkspaceFile] = []

    def get_by_conversation_for_user(self, conversation_id, user_id):
        return self.workspace if conversation_id == CONVERSATION_ID else None

    def get_by_id_for_user(self, workspace_id, user_id):
        return self.workspace if (workspace_id, user_id) == (WORKSPACE_ID, USER_ID) else None

    def list_files(self, workspace_id, include_deleted=False):
        return list(self.files)

    def get_file(self, workspace_id, path, include_deleted=False):
        return next((f for f in self.files if f.path == path), None)

    def save_file(self, workspace_file):
        if workspace_file not in self.files:
            self.files.append(workspace_file)
        return workspace_file


@pytest.fixture
def repository() -> _FakeRepository:
    return _FakeRepository()


@pytest.fixture
def workspace_service(repository):
    svc = AgentWorkspaceService.__new__(AgentWorkspaceService)
    svc.repository = repository  # type: ignore[assignment]
    svc.file_repository = MagicMock()
    return svc


@pytest.fixture
def file_repository():
    repo = MagicMock()
    repo.write_file.side_effect = lambda name, mime_type, content, owner: FileObject(
        name=name, mime_type=mime_type, owner=owner
    )
    return repo


@pytest.fixture
def publisher(file_repository, workspace_service):
    return ArtifactPublisherService(file_repository, workspace_service=workspace_service)


def _decode(link: str) -> FileObject:
    return FileObject.from_encoded_url(link[len(SANDBOX_FILE_PREFIX) :])


def _xlsx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
    return buffer.getvalue()


def test_publishes_into_turn_folder(publisher, repository):
    result = publisher.publish(CTX, "report.csv", content=b"a,b\n1,2\n")

    assert result.ok
    assert result.name == "report.csv"
    assert result.size == 8
    assert result.mime == "text/csv"
    assert _decode(result.link).name == f"artifacts/{TURN_ID}/report.csv"
    assert [f.path for f in repository.files] == [f"artifacts/{TURN_ID}/report.csv"]


def test_oversize_base64_refused_before_decode(publisher, repository):
    with (
        patch("codemie.service.artifact_publisher.config.FILES_STORAGE_MAX_UPLOAD_SIZE", 10),
        patch("codemie.service.artifact_publisher.base64.b64decode") as decode,
    ):
        result = publisher.publish(CTX, "big.bin", content="A" * 400, base64_content=True)

    assert not result.ok
    assert "big.bin" in result.reason
    decode.assert_not_called()
    assert repository.files == []


def test_invalid_base64_gives_reason(publisher, repository):
    result = publisher.publish(CTX, "data.bin", content="not base64!!", base64_content=True)

    assert not result.ok
    assert "data.bin" in result.reason
    assert repository.files == []


@pytest.mark.parametrize("as_bytes", [False, True])
def test_line_wrapped_base64_is_accepted(publisher, as_bytes):
    encoded = base64.b64encode(_xlsx_bytes()).decode()
    wrapped = "\r\n".join(encoded[i : i + 20] for i in range(0, len(encoded), 20))

    result = publisher.publish(
        CTX, "sheet.xlsx", content=wrapped.encode() if as_bytes else wrapped, base64_content=True
    )

    assert result.ok


def test_fake_zip_xlsx_refused(publisher, repository):
    fake = base64.b64encode(b"this is not a zip").decode()

    result = publisher.publish(CTX, "sheet.xlsx", content=fake, base64_content=True)

    assert not result.ok
    assert "sheet.xlsx" in result.reason
    assert repository.files == []


def test_real_xlsx_accepted(publisher):
    result = publisher.publish(CTX, "sheet.xlsx", content=base64.b64encode(_xlsx_bytes()), base64_content=True)

    assert result.ok


def test_empty_base64_payload_refused(publisher, repository):
    result = publisher.publish(CTX, "empty.txt", content="", base64_content=True)

    assert not result.ok
    assert result.reason == "`empty.txt` was not saved: it is empty"
    assert repository.files == []


def test_zero_byte_file_is_published(publisher, repository, tmp_path):
    """A code-executor export of a real empty file (an empty CSV, a marker) is stored and linked, as on main."""
    source = tmp_path / "marker.csv"
    source.write_bytes(b"")

    by_path = publisher.publish(CTX, "marker.csv", path=str(source))
    by_bytes = publisher.publish(CTX, "empty.txt", content=b"")

    assert by_path.ok and by_path.size == 0
    assert by_bytes.ok and by_bytes.size == 0
    assert [f.path for f in repository.files] == ["artifacts/turn-1/marker.csv", "artifacts/turn-1/empty.txt"]


def test_duplicate_names_get_counter(publisher, repository):
    names = [publisher.publish(CTX, "report.csv", content=b"x").name for _ in range(3)]

    assert names == ["report.csv", "report (2).csv", "report (3).csv"]
    assert [f.path for f in repository.files] == [
        f"artifacts/{TURN_ID}/report.csv",
        f"artifacts/{TURN_ID}/report (2).csv",
        f"artifacts/{TURN_ID}/report (3).csv",
    ]


@pytest.mark.parametrize("name", ["../../etc/report.csv", "a/b/report.csv", "a\\b\\report.csv"])
def test_path_shaped_name_reduced_to_last_segment(publisher, repository, name):
    result = publisher.publish(CTX, name, content=b"x")

    assert result.name == "report.csv"
    assert [f.path for f in repository.files] == [f"artifacts/{TURN_ID}/report.csv"]


def test_no_conversation_stores_per_user_with_warning(publisher, file_repository, repository):
    ctx = PublishContext(user_id=USER_ID, conversation_id=None, turn_id=TURN_ID)

    with patch("codemie.service.artifact_publisher.logger") as log:
        result = publisher.publish(ctx, "report.csv", content=b"x")

    assert result.ok
    stored = _decode(result.link)
    assert re.fullmatch(r"[0-9a-f-]{36}_report\.csv", stored.name)
    assert stored.owner == USER_ID
    assert repository.files == []
    log.warning.assert_called_once()
    assert "report.csv" in log.warning.call_args.args[0]


def test_no_user_gives_reason_and_stores_nothing(publisher, file_repository, repository):
    ctx = PublishContext(user_id="", conversation_id=CONVERSATION_ID, turn_id=TURN_ID)

    result = publisher.publish(ctx, "report.csv", content=b"x")

    assert not result.ok
    assert "report.csv" in result.reason
    file_repository.write_file.assert_not_called()
    assert repository.files == []


def test_disk_path_sized_by_stat_before_read(publisher, repository, tmp_path):
    source = tmp_path / "big.csv"
    source.write_bytes(b"0123456789abc")

    with (
        patch("codemie.service.artifact_publisher.config.FILES_STORAGE_MAX_UPLOAD_SIZE", 10),
        patch("codemie.service.artifact_publisher.Path.read_bytes") as read,
    ):
        result = publisher.publish(CTX, "big.csv", path=str(source))

    assert not result.ok
    assert "big.csv" in result.reason
    read.assert_not_called()
    assert repository.files == []


def test_disk_path_published(publisher, tmp_path):
    source = tmp_path / "out.txt"
    source.write_bytes(b"hello")

    result = publisher.publish(CTX, "out.txt", path=str(source))

    assert result.ok
    assert result.size == 5


def test_storage_error_returns_reason(publisher, workspace_service):
    workspace_service.file_repository.write_file.side_effect = RuntimeError("boom")

    result = publisher.publish(CTX, "report.csv", content=b"x")

    assert not result.ok
    assert "report.csv" in result.reason


def test_publish_then_register_generated_files_keeps_one_row(publisher, workspace_service, repository):
    result = publisher.publish(CTX, "report.csv", content=b"x")

    with patch.object(AgentWorkspaceService, "_get_conversation_uploaded_files", return_value=[]):
        registered = workspace_service.register_generated_files(CONVERSATION_ID, [result.link], User(id=USER_ID))

    assert registered == []
    assert len(repository.files) == 1


def test_path_validation_failure_gives_its_reason_not_a_storage_error(publisher, repository):
    name = "a" * 2000 + ".csv"

    with patch("codemie.service.artifact_publisher.logger") as log:
        result = publisher.publish(CTX, name, content=b"x")

    assert not result.ok
    assert "storage error" not in result.reason
    assert result.reason.startswith(f"`{name}` was not saved:")
    log.exception.assert_not_called()


def test_concurrent_same_name_publishes_keep_both(file_repository, workspace_service, repository):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    real_get_file = repository.get_file

    def slow_get_file(*args, **kwargs):
        found = real_get_file(*args, **kwargs)
        time.sleep(0.05)  # widens the check-then-write window two parallel tool calls share
        return found

    repository.get_file = slow_get_file  # type: ignore[method-assign]
    start = threading.Barrier(2)

    def publish(_):
        start.wait()
        # A fresh service per call, as each toolkit builds its own publisher.
        return ArtifactPublisherService(file_repository, workspace_service).publish(CTX, "report.csv", content=b"x")

    with ThreadPoolExecutor(2) as pool:
        names = sorted(result.name for result in pool.map(publish, range(2)))

    assert names == ["report (2).csv", "report.csv"]
    assert len(repository.files) == 2


@pytest.mark.parametrize("turn_id", ["", "  "])
def test_empty_turn_id_never_publishes_into_an_empty_folder(publisher, repository, turn_id):
    ctx = PublishContext(user_id=USER_ID, conversation_id=CONVERSATION_ID, turn_id=turn_id)

    result = publisher.publish(ctx, "report.csv", content=b"x")

    assert result.ok
    assert re.fullmatch(r"artifacts/[0-9a-f]{32}/report\.csv", repository.files[0].path)
