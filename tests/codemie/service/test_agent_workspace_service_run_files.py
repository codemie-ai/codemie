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

"""register_run_files seeds a workspace keyed by the execution id; sync_uploaded_files skips unsafe tokens."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from codemie_tools.base.file_object import FileObject
from sqlalchemy.exc import OperationalError

from codemie.core.exceptions import ValidationException
from codemie.core.models import UserEntity
from codemie.rest_api.models.agent_workspace import AgentWorkspace, AgentWorkspaceFile
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService

EXECUTION_ID = "exec-1"
USER_ID = "user-1"


def _user() -> User:
    return User(id=USER_ID, username="u", name="U")


def _entity() -> UserEntity:
    return UserEntity(user_id=USER_ID, username="u", name="U")


def _token(owner: str, name: str = "doc.pdf") -> str:
    return FileObject(name=name, mime_type="application/pdf", owner=owner).to_encoded_url()


class _FakeRepository:
    """In-memory stand-in for AgentWorkspaceRepository covering what the service touches."""

    def __init__(self) -> None:
        self.workspaces: dict[str, AgentWorkspace] = {}
        self.files: list[AgentWorkspaceFile] = []
        self.fail_on_save: Exception | None = None

    def get_by_conversation_for_user(self, conversation_id: str, user_id: str) -> AgentWorkspace | None:
        return self.workspaces.get(conversation_id)

    def save_workspace(self, workspace: AgentWorkspace) -> AgentWorkspace:
        if self.fail_on_save:
            raise self.fail_on_save
        workspace.id = f"ws-{workspace.conversation_id}"
        self.workspaces[workspace.conversation_id] = workspace
        return workspace

    def list_files(self, workspace_id: str) -> list[AgentWorkspaceFile]:
        return [f for f in self.files if f.workspace_id == workspace_id]

    def get_file(self, workspace_id: str, path: str, include_deleted: bool = False) -> AgentWorkspaceFile | None:
        return next((f for f in self.files if f.workspace_id == workspace_id and f.path == path), None)

    def save_file(self, workspace_file: AgentWorkspaceFile) -> AgentWorkspaceFile:
        if workspace_file not in self.files:
            self.files.append(workspace_file)
        return workspace_file

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


def test_register_run_files_with_empty_list_creates_no_workspace(
    service: AgentWorkspaceService, repository: _FakeRepository
) -> None:
    service.register_run_files(EXECUTION_ID, [], _entity())

    assert repository.workspaces == {}


def test_register_run_files_puts_files_in_workspace_keyed_by_execution_id(
    service: AgentWorkspaceService, repository: _FakeRepository
) -> None:
    service.register_run_files(EXECUTION_ID, [_token(USER_ID)], _entity())

    assert set(repository.workspaces) == {EXECUTION_ID}
    assert [f.blob_owner for f in repository.files] == [USER_ID]


def test_register_run_files_is_idempotent_per_execution_id(
    service: AgentWorkspaceService, repository: _FakeRepository
) -> None:
    urls = [_token(USER_ID)]

    service.register_run_files(EXECUTION_ID, urls, _entity())
    service.register_run_files(EXECUTION_ID, urls, _entity())

    assert len(repository.files) == 1


def test_register_run_files_with_existing_conversation_workspace_uses_execution_id_workspace(
    service: AgentWorkspaceService, repository: _FakeRepository
) -> None:
    repository.workspaces["conv-1"] = AgentWorkspace(id="ws-conv-1", conversation_id="conv-1", user_id=USER_ID)

    service.register_run_files(EXECUTION_ID, [_token(USER_ID)], _entity())

    assert repository.list_files("ws-conv-1") == []
    assert len(repository.list_files(f"ws-{EXECUTION_ID}")) == 1


def test_register_run_files_propagates_database_errors(
    service: AgentWorkspaceService, repository: _FakeRepository
) -> None:
    repository.fail_on_save = OperationalError("stmt", {}, Exception("db down"))

    with pytest.raises(OperationalError):
        service.register_run_files(EXECUTION_ID, [_token(USER_ID)], _entity())


def test_register_run_files_rejects_missing_blob(service: AgentWorkspaceService, repository: _FakeRepository) -> None:
    service.file_repository.read_file.side_effect = FileNotFoundError("gone")

    with pytest.raises(ValidationException):
        service.register_run_files(EXECUTION_ID, [_token(USER_ID)], _entity())

    assert repository.workspaces == {}


@pytest.mark.parametrize(
    "error",
    [KeyError("gone"), OSError("gone"), RuntimeError("NoSuchKey"), FileNotFoundError("gone")],
)
def test_register_run_files_maps_any_backend_missing_blob_error_to_validation_error(
    service: AgentWorkspaceService, repository: _FakeRepository, error: Exception
) -> None:
    service.file_repository.read_file.side_effect = error

    with pytest.raises(ValidationException):
        service.register_run_files(EXECUTION_ID, [_token(USER_ID)], _entity())

    assert repository.workspaces == {}


def test_register_run_files_accepts_own_workspace_owner(
    service: AgentWorkspaceService, repository: _FakeRepository
) -> None:
    repository.get_by_id_for_user = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]

    service.validate_run_files([_token("workspace-ws-9")], _user())


def test_register_run_files_with_real_user_entity_succeeds_for_owned_token(
    service: AgentWorkspaceService, repository: _FakeRepository
) -> None:
    service.register_run_files(EXECUTION_ID, [_token(USER_ID)], _entity())

    assert [f.blob_owner for f in repository.files] == [USER_ID]


def test_register_run_files_with_real_user_entity_rejects_missing_blob(service: AgentWorkspaceService) -> None:
    service.file_repository.read_file.side_effect = FileNotFoundError("gone")

    with pytest.raises(ValidationException):
        service.register_run_files(EXECUTION_ID, [_token(USER_ID)], _entity())


def test_validate_run_files_without_blob_check_does_not_read_blob(service: AgentWorkspaceService) -> None:
    service.validate_run_files([_token(USER_ID)], _user(), check_blob_exists=False)

    service.file_repository.read_file.assert_not_called()


def test_register_run_files_rejects_foreign_owner(service: AgentWorkspaceService, repository: _FakeRepository) -> None:
    with pytest.raises(ValidationException):
        service.validate_run_files([_token("someone-else")], _user())

    assert repository.workspaces == {}


def test_register_run_files_rejects_shared_conversation_file_without_token(
    service: AgentWorkspaceService, repository: _FakeRepository
) -> None:
    """Share grant (rule E) does not apply at run start: a sharer's workspace file is rejected."""
    repository.get_by_id_for_user = MagicMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(ValidationException):
        service.validate_run_files([_token("workspace-sharer-ws")], _user())

    assert repository.workspaces == {}


def test_register_run_files_rejects_path_traversal(service: AgentWorkspaceService, repository: _FakeRepository) -> None:
    with pytest.raises(ValidationException):
        service.register_run_files(EXECUTION_ID, [_token(USER_ID, "../victim/secret.pdf")], _entity())

    assert repository.workspaces == {}


def test_register_run_files_rejects_undecodable_token(
    service: AgentWorkspaceService, repository: _FakeRepository
) -> None:
    with pytest.raises(ValidationException):
        service.register_run_files(EXECUTION_ID, ["not-a-token"], _entity())


def test_sync_uploaded_files_skips_unsafe_token_and_registers_safe_one(
    service: AgentWorkspaceService, repository: _FakeRepository
) -> None:
    unsafe = _token(USER_ID, "../victim/secret.pdf")
    safe = _token(USER_ID, "ok.pdf")

    synced = service.sync_uploaded_files("conv-1", [unsafe, safe], _user())

    assert [f.blob_name for f in repository.files] == ["ok.pdf"]
    assert len(synced) == 1
