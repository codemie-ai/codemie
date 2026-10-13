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

"""Workspace writes refuse the script bridge's reserved paths; reads and deletes of them still work (AC20)."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from codemie_tools.base.file_object import FileObject

from codemie.core.exceptions import ValidationException
from codemie.rest_api.models.agent_workspace import AgentWorkspace, AgentWorkspaceFile
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService

USER_ID = "user-1"
WORKSPACE_ID = "ws-1"
CONVERSATION_ID = "conv-1"
RESERVED_PATHS = [
    ".codemie_bridge/x.txt",
    "codemie_runtime_sdk.py",
    ".codemie_bridge\\x",
    ".CODEMIE_BRIDGE/x.txt",
    "\uff0ecodemie_bridge/x.txt",
    "Codemie_Runtime_SDK.py",
]


def _user() -> User:
    return User(id=USER_ID, username="u", name="U")


class _FakeRepository:
    """In-memory stand-in for AgentWorkspaceRepository covering what the service touches."""

    def __init__(self) -> None:
        self.workspace = AgentWorkspace(id=WORKSPACE_ID, conversation_id=CONVERSATION_ID, user_id=USER_ID)
        self.files: list[AgentWorkspaceFile] = []

    def get_by_id_for_user(self, workspace_id: str, user_id: str) -> AgentWorkspace | None:
        return self.workspace if workspace_id == WORKSPACE_ID else None

    def get_by_conversation_for_user(self, conversation_id: str, user_id: str) -> AgentWorkspace | None:
        return self.workspace if conversation_id == CONVERSATION_ID else None

    def list_files(self, workspace_id: str) -> list[AgentWorkspaceFile]:
        return [f for f in self.files if f.workspace_id == workspace_id and f.deleted_at is None]

    def get_file(self, workspace_id: str, path: str, include_deleted: bool = False) -> AgentWorkspaceFile | None:
        return next((f for f in self.files if f.workspace_id == workspace_id and f.path == path), None)

    def save_file(self, workspace_file: AgentWorkspaceFile) -> AgentWorkspaceFile:
        if workspace_file not in self.files:
            self.files.append(workspace_file)
        return workspace_file

    def soft_delete_file(self, workspace_id: str, path: str) -> bool:
        return self.get_file(workspace_id, path) is not None

    def __getattr__(self, name: str) -> MagicMock:
        return MagicMock()


@pytest.fixture
def repository() -> _FakeRepository:
    return _FakeRepository()


@pytest.fixture
def service(repository: _FakeRepository) -> Iterator[AgentWorkspaceService]:
    svc = AgentWorkspaceService.__new__(AgentWorkspaceService)
    svc.repository = repository  # type: ignore[assignment]
    svc.file_repository = MagicMock()
    svc.file_repository.read_file.return_value = MagicMock(content=b"data")
    with patch(
        "codemie.service.agent_workspace_service.AgentWorkspaceService._get_conversation_uploaded_file_urls",
        return_value=[],
    ):
        yield svc


def _file_row(path: str) -> AgentWorkspaceFile:
    return AgentWorkspaceFile(
        workspace_id=WORKSPACE_ID,
        path=path,
        blob_owner="owner",
        blob_name=path,
        mime_type="text/plain",
        checksum="c",
        size=1,
    )


class TestSingleFileWritesAreRefused:
    @pytest.mark.parametrize("path", RESERVED_PATHS)
    def test_upsert_text_file_refuses_reserved_path(self, service, path):
        with pytest.raises(ValidationException, match="reserved"):
            service.upsert_text_file(WORKSPACE_ID, path, "data", _user())

        service.file_repository.write_file.assert_not_called()

    def test_upsert_binary_file_refuses_runtime_sdk(self, service):
        with pytest.raises(ValidationException, match="reserved"):
            service.upsert_binary_file(WORKSPACE_ID, "codemie_runtime_sdk.py", b"data", _user())

        service.file_repository.write_file.assert_not_called()

    def test_edit_file_refuses_existing_reserved_row(self, service, repository):
        repository.files.append(_file_row("codemie_runtime_sdk.py"))
        content = MagicMock(is_binary=False, content="old", path="codemie_runtime_sdk.py")

        with patch.object(AgentWorkspaceService, "get_file_content", return_value=content):
            with pytest.raises(ValidationException, match="reserved"):
                service.edit_file(WORKSPACE_ID, "codemie_runtime_sdk.py", "old", "new", False, _user())

        service.file_repository.write_file.assert_not_called()

    def test_runtime_sdk_name_inside_a_folder_is_allowed(self, service):
        item = service.upsert_text_file(WORKSPACE_ID, "skills/demo/codemie_runtime_sdk.py", "data", _user())

        assert item.path == "skills/demo/codemie_runtime_sdk.py"


class TestBatchWritesSkipReservedFiles:
    def test_execution_results_skip_reserved_files(self, service):
        files = [
            FileObject(name="codemie_runtime_sdk.py", mime_type="text/x-python", owner="o", content=b"x"),
            FileObject(name="out.txt", mime_type="text/plain", owner="o", content=b"y"),
        ]

        with patch("codemie.service.agent_workspace_service.logger") as logger_mock:
            synced = service._sync_execution_files(WORKSPACE_ID, files)

        assert [item.path for item in synced] == ["out.txt"]
        assert logger_mock.warning.called

    def test_uploaded_files_skip_reserved_name(self, service, repository):
        tokens = [
            FileObject(name="codemie_runtime_sdk.py", mime_type="text/x-python", owner=USER_ID).to_encoded_url(),
            FileObject(name="doc.pdf", mime_type="application/pdf", owner=USER_ID).to_encoded_url(),
        ]

        synced = service.sync_uploaded_files(CONVERSATION_ID, tokens, _user())

        assert [item.path for item in synced] == ["doc.pdf"]
        assert [f.path for f in repository.files] == ["doc.pdf"]

    def test_uploads_with_format_characters_are_kept_and_unusable_names_do_not_fail_the_batch(
        self, service, repository
    ):
        tokens = [
            FileObject(name="\U0001f468\u200d\U0001f469.txt", mime_type="text/plain", owner=USER_ID).to_encoded_url(),
            FileObject(name="a\x01b.txt", mime_type="text/plain", owner=USER_ID).to_encoded_url(),
            FileObject(name="doc.pdf", mime_type="application/pdf", owner=USER_ID).to_encoded_url(),
        ]

        synced = service.sync_uploaded_files(CONVERSATION_ID, tokens, _user())

        assert sorted(item.path for item in synced) == sorted(["\U0001f468\u200d\U0001f469.txt", "doc.pdf"])

    def test_uploaded_token_pointing_at_a_skill_blob_is_not_referenced(self, service, repository):
        tokens = [
            FileObject(name="ab12cd34ef56ab12/a.py", mime_type="text/x-python", owner="skill-other").to_encoded_url(),
            FileObject(name="doc.pdf", mime_type="application/pdf", owner=USER_ID).to_encoded_url(),
        ]

        synced = service.sync_uploaded_files(CONVERSATION_ID, tokens, _user())

        assert [item.path for item in synced] == ["doc.pdf"]
        assert all(f.blob_owner != "skill-other" for f in repository.files)

    def test_execution_files_with_unusable_names_are_skipped_not_fatal(self, service):
        files = [
            FileObject(name="a\x01b.txt", mime_type="text/plain", owner="o", content=b"x"),
            FileObject(name="out.txt", mime_type="text/plain", owner="o", content=b"y"),
        ]

        synced = service._sync_execution_files(WORKSPACE_ID, files)

        assert [item.path for item in synced] == ["out.txt"]

    def test_generated_files_skip_skill_owned_and_unsafe_references(self, service, repository):
        refs = [
            FileObject(name="ab12/a.py", mime_type="text/x-python", owner="skill-other"),
            FileObject(name="../x/secret.pdf", mime_type="application/pdf", owner="o"),
        ]

        with (
            patch.object(AgentWorkspaceService, "_decode_workspace_file_url", side_effect=refs),
            patch.object(AgentWorkspaceService, "_get_conversation_uploaded_files", return_value=[]),
        ):
            registered = service.register_generated_files(CONVERSATION_ID, ["t1", "t2"], _user())

        assert registered == []
        assert repository.files == []

    def test_generated_files_skip_reserved_path(self, service, repository):
        generated = FileObject(name="r.json", path=".codemie_bridge/r.json", mime_type="application/json", owner="o")

        with (
            patch.object(AgentWorkspaceService, "_decode_workspace_file_url", return_value=generated),
            patch.object(AgentWorkspaceService, "_get_conversation_uploaded_files", return_value=[]),
        ):
            registered = service.register_generated_files(CONVERSATION_ID, ["token"], _user())

        assert registered == []
        assert repository.files == []


class TestDeletesAreRefusedAndReadsAreNot:
    @pytest.mark.parametrize("path", RESERVED_PATHS)
    def test_deleting_a_reserved_path_is_refused(self, service, repository, path):
        repository.files.append(_file_row(path))

        with pytest.raises(ValidationException, match="reserved"):
            service.delete_file(WORKSPACE_ID, path, _user())

    def test_a_reserved_name_inside_a_folder_can_be_deleted(self, service, repository):
        repository.files.append(_file_row("skills/demo/.codemie_bridge/x"))

        result = service.delete_file(WORKSPACE_ID, "skills/demo/.codemie_bridge/x", _user())

        assert result.deleted is True

    def test_reading_a_reserved_path_is_not_restricted(self, service, repository):
        repository.files.append(_file_row("codemie_runtime_sdk.py"))

        content = service.get_file_content(WORKSPACE_ID, "codemie_runtime_sdk.py", _user())

        assert content.path == "codemie_runtime_sdk.py"


class TestVirtualUploadsSkipReservedNames:
    """Chat uploads are also listed without being persisted; a reserved name must not surface that way either."""

    def test_list_files_hides_reserved_upload(self, service):
        tokens = [
            FileObject(name="codemie_runtime_sdk.py", mime_type="text/x-python", owner=USER_ID).to_encoded_url(),
            FileObject(name="doc.pdf", mime_type="application/pdf", owner=USER_ID).to_encoded_url(),
        ]

        with patch.object(AgentWorkspaceService, "_get_conversation_uploaded_file_urls", return_value=tokens):
            listed = service.list_files(WORKSPACE_ID, _user())

        assert [item.path for item in listed] == ["doc.pdf"]

    def test_input_file_view_hides_reserved_upload(self, service):
        tokens = [FileObject(name="codemie_runtime_sdk.py", mime_type="text/x-python", owner=USER_ID).to_encoded_url()]

        with patch.object(AgentWorkspaceService, "_get_conversation_uploaded_file_urls", return_value=tokens):
            virtual_files = service._get_conversation_uploaded_files(CONVERSATION_ID)

        assert virtual_files == []

    def test_input_file_view_hides_unsafe_and_source_owned_uploads(self, service):
        tokens = [
            FileObject(name="doc.pdf", mime_type="application/pdf", owner="../victim").to_encoded_url(),
            FileObject(name="a.py", mime_type="text/x-python", owner="skill-sk1").to_encoded_url(),
            FileObject(name="ok.pdf", mime_type="application/pdf", owner=USER_ID).to_encoded_url(),
        ]

        with patch.object(AgentWorkspaceService, "_get_conversation_uploaded_file_urls", return_value=tokens):
            virtual_files = service._get_conversation_uploaded_files(CONVERSATION_ID)

        assert [(f.path, f.blob_owner) for f in virtual_files] == [("ok.pdf", USER_ID)]
