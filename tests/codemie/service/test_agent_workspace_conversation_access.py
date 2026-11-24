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

"""A workspace keyed to a conversation the caller cannot read serves no conversation-file bytes (EPMCDME-17679)."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from codemie_tools.base.file_object import FileObject

from codemie.core.exceptions import ValidationException
from codemie.rest_api.models.agent_workspace import AgentWorkspace, AgentWorkspaceFile
from codemie.rest_api.models.conversation import ChatRole
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService

CALLER_ID = "user-b"
BLOB_OWNER = "user-a"
WORKSPACE_ID = "ws-1"
CONVERSATION_ID = "conv-1"
NOT_FOUND = f"Workspace '{WORKSPACE_ID}' not found"


def _caller() -> User:
    return User(id=CALLER_ID, username="b", name="B")


class _FakeRepository:
    def __init__(self) -> None:
        self.workspace = AgentWorkspace(id=WORKSPACE_ID, conversation_id=CONVERSATION_ID, user_id=CALLER_ID)

    def get_by_id_for_user(self, workspace_id: str, user_id: str) -> AgentWorkspace | None:
        return self.workspace if workspace_id == WORKSPACE_ID else None

    def get_by_conversation_for_user(self, conversation_id: str, user_id: str) -> AgentWorkspace | None:
        return self.workspace if conversation_id == CONVERSATION_ID else None

    def list_files(self, workspace_id: str) -> list[AgentWorkspaceFile]:
        return []

    def get_file(self, workspace_id: str, path: str, include_deleted: bool = False) -> AgentWorkspaceFile | None:
        return None

    def __getattr__(self, name: str) -> MagicMock:
        return MagicMock()


def _conversation() -> SimpleNamespace:
    encoded = FileObject(name="a.txt", mime_type="text/plain", owner=BLOB_OWNER).to_encoded_url()
    return SimpleNamespace(history=[SimpleNamespace(role=ChatRole.USER, file_names=[encoded])])


@pytest.fixture
def service() -> AgentWorkspaceService:
    svc = AgentWorkspaceService.__new__(AgentWorkspaceService)
    svc.repository = _FakeRepository()  # type: ignore[assignment]
    svc.file_repository = MagicMock()
    svc.file_repository.read_file.return_value = MagicMock(content=b"secret-bytes")
    return svc


@pytest.fixture
def ability() -> Iterator[MagicMock]:
    with patch("codemie.service.workspace_archive.Ability") as patched:
        patched.return_value.can.return_value = True
        yield patched


@pytest.fixture
def conversation() -> Iterator[MagicMock]:
    with (
        patch("codemie.rest_api.models.conversation.Conversation.find_owner_ref", return_value=_conversation()),
        patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=_conversation()) as patched,
    ):
        yield patched


def _uploaded_path(service: AgentWorkspaceService) -> str:
    return service._get_conversation_uploaded_files(CONVERSATION_ID)[0].path


def test_foreign_conversation_content_and_download_refused(service, ability, conversation) -> None:
    ability.return_value.can.return_value = False
    path = _uploaded_path(service)

    with pytest.raises(ValidationException, match=NOT_FOUND):
        service.get_file_content(WORKSPACE_ID, path, _caller())
    with pytest.raises(ValidationException, match=NOT_FOUND):
        service.download_file(WORKSPACE_ID, path, _caller())

    service.file_repository.read_file.assert_not_called()


def test_foreign_conversation_other_read_surfaces_refused(service, ability, conversation) -> None:
    ability.return_value.can.return_value = False
    path = _uploaded_path(service)
    caller = _caller()

    with pytest.raises(ValidationException, match=NOT_FOUND):
        service.get_file_sandbox_url(WORKSPACE_ID, path, caller)
    with pytest.raises(ValidationException, match=NOT_FOUND):
        service.list_files(WORKSPACE_ID, caller)
    with pytest.raises(ValidationException, match=NOT_FOUND):
        service.grep_files(WORKSPACE_ID, "secret", caller)
    with pytest.raises(ValidationException, match=NOT_FOUND):
        service.get_workspace_input_files(WORKSPACE_ID, caller)
    with pytest.raises(ValidationException):
        service.get_workspace_by_conversation(CONVERSATION_ID, caller)

    service.file_repository.read_file.assert_not_called()


def test_own_conversation_file_is_served(service, ability, conversation) -> None:
    path = _uploaded_path(service)

    content = service.get_file_content(WORKSPACE_ID, path, _caller())
    downloaded = service.download_file(WORKSPACE_ID, path, _caller())

    assert content.content == "secret-bytes"
    assert downloaded.content == b"secret-bytes"
    assert service.file_repository.read_file.call_args.kwargs["owner"] == BLOB_OWNER


def test_gate_allows_whatever_ability_allows(service, ability, conversation) -> None:
    path = _uploaded_path(service)

    content = service.get_file_content(WORKSPACE_ID, path, _caller())

    assert content.content == "secret-bytes"
    assert service.file_repository.read_file.call_args.kwargs["owner"] != CALLER_ID


def test_missing_conversation_keeps_working(service, ability) -> None:
    with patch("codemie.rest_api.models.conversation.Conversation.find_owner_ref", return_value=None):
        workspace = service.get_workspace(WORKSPACE_ID, _caller())

    assert workspace.id == WORKSPACE_ID
    ability.assert_not_called()
