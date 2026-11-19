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

"""Published artifacts under artifacts/ are immutable; only the publisher's own method writes them."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from codemie_tools.base.file_object import FileObject

from codemie.core.exceptions import ValidationException
from codemie.rest_api.models.agent_workspace import AgentWorkspace, AgentWorkspaceFile
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie.service.file_service.sandbox_links import SANDBOX_FILE_PREFIX
from codemie.service.file_service.workspace_path_rules import is_artifact_path

USER_ID = "user-1"
WORKSPACE_ID = "ws-1"
CONVERSATION_ID = "conv-1"
ARTIFACT = "artifacts/t/x.csv"


def _user() -> User:
    return User(id=USER_ID, username="u", name="U")


class _FakeRepository:
    def __init__(self) -> None:
        self.workspace = AgentWorkspace(id=WORKSPACE_ID, conversation_id=CONVERSATION_ID, user_id=USER_ID)
        self.files: list[AgentWorkspaceFile] = []

    def get_by_id_for_user(self, workspace_id, user_id):
        return self.workspace if workspace_id == WORKSPACE_ID else None

    def get_by_conversation_for_user(self, conversation_id, user_id):
        return self.workspace if conversation_id == CONVERSATION_ID else None

    def list_files(self, workspace_id):
        return [f for f in self.files if f.deleted_at is None]

    def get_file(self, workspace_id, path, include_deleted=False):
        return next((f for f in self.files if f.path == path), None)

    def save_file(self, workspace_file):
        if workspace_file not in self.files:
            self.files.append(workspace_file)
        return workspace_file

    def soft_delete_file(self, workspace_id, path):
        return self.get_file(workspace_id, path) is not None


@pytest.fixture
def repository() -> _FakeRepository:
    return _FakeRepository()


@pytest.fixture
def service(repository):
    svc = AgentWorkspaceService.__new__(AgentWorkspaceService)
    svc.repository = repository  # type: ignore[assignment]
    svc.file_repository = MagicMock()
    with patch.object(AgentWorkspaceService, "_get_conversation_uploaded_file_urls", return_value=[]):
        yield svc


class TestIsArtifactPath:
    @pytest.mark.parametrize("path", ["artifacts/t/x.csv", "artifacts/x", "artifacts"])
    def test_artifact_paths(self, path):
        assert is_artifact_path(path)

    @pytest.mark.parametrize("path", ["artifacts.csv", "my-artifacts/x.csv", "docs/artifacts/x.csv", "a.csv"])
    def test_other_paths(self, path):
        assert not is_artifact_path(path)


class TestArtifactsAreImmutable:
    def test_upsert_text_file_is_refused(self, service):
        with pytest.raises(ValidationException, match="artifact"):
            service.upsert_text_file(WORKSPACE_ID, ARTIFACT, "data", _user())
        service.file_repository.write_file.assert_not_called()

    def test_upsert_binary_file_is_refused(self, service):
        with pytest.raises(ValidationException, match="artifact"):
            service.upsert_binary_file(WORKSPACE_ID, ARTIFACT, b"data", _user())
        service.file_repository.write_file.assert_not_called()

    @pytest.mark.parametrize("path", ["artifacts", "./artifacts"])
    def test_a_file_named_like_the_artifacts_folder_is_refused(self, service, path):
        """A file at exactly `artifacts` would take the folder's blob name and block every later publish."""
        with pytest.raises(ValidationException, match="artifact"):
            service.upsert_text_file(WORKSPACE_ID, path, "data", _user())
        with pytest.raises(ValidationException, match="artifact"):
            service.upsert_binary_file(WORKSPACE_ID, path, b"data", _user())
        service.file_repository.write_file.assert_not_called()

    def test_edit_file_is_refused_before_reading(self, service):
        with patch.object(AgentWorkspaceService, "get_file_content") as read:
            with pytest.raises(ValidationException, match="artifact"):
                service.edit_file(WORKSPACE_ID, ARTIFACT, "a", "b", False, _user())
        read.assert_not_called()

    def test_delete_file_is_refused(self, service, repository):
        with pytest.raises(ValidationException, match="artifact"):
            service.delete_file(WORKSPACE_ID, "./" + ARTIFACT, _user())

    def test_script_sync_skips_artifacts_and_keeps_the_rest(self, service):
        files = [
            FileObject(name=ARTIFACT, mime_type="text/csv", owner="o", content=b"x"),
            FileObject(name="out.txt", mime_type="text/plain", owner="o", content=b"y"),
        ]

        synced = service._sync_execution_files(WORKSPACE_ID, files)

        assert [item.path for item in synced] == ["out.txt"]

    def test_upload_sync_skips_an_artifact_path(self, service, repository):
        """Defence in depth: today uploads normalize to a basename, but the skip must hold if that ever changes."""
        upload = FileObject(name="x.csv", mime_type="text/csv", owner=USER_ID)
        url = f"{SANDBOX_FILE_PREFIX}{upload.to_encoded_url()}"
        service.file_repository.read_file.return_value = FileObject(
            name="x.csv", mime_type="text/csv", owner=USER_ID, content=b"a,b\n"
        )

        with patch.object(AgentWorkspaceService, "_try_normalize_uploaded_file_path", return_value=ARTIFACT):
            synced = service.sync_uploaded_files(CONVERSATION_ID, [url], _user())

        assert synced == []
        assert repository.files == []


class TestPublishArtifactFile:
    def test_writes_the_artifact_and_returns_a_blob_link(self, service, repository):
        link = service.publish_artifact_file(CONVERSATION_ID, _user(), ARTIFACT, b"a,b\n", "text/csv")

        file_object = FileObject.from_encoded_url(link[len(SANDBOX_FILE_PREFIX) :])
        assert (file_object.owner, file_object.name, file_object.mime_type) == (
            f"workspace-{WORKSPACE_ID}",
            ARTIFACT,
            "text/csv",
        )
        assert [f.path for f in repository.files] == [ARTIFACT]
        service.file_repository.write_file.assert_called_once()

    def test_the_publishers_mime_wins_over_the_extension_guess(self, service, repository):
        link = service.publish_artifact_file(
            CONVERSATION_ID, _user(), "artifacts/t/notes.txt", b"# notes", "text/markdown"
        )

        assert repository.get_file(WORKSPACE_ID, "artifacts/t/notes.txt").mime_type == "text/markdown"
        assert service.file_repository.write_file.call_args.kwargs["mime_type"] == "text/markdown"
        assert FileObject.from_encoded_url(link[len(SANDBOX_FILE_PREFIX) :]).mime_type == "text/markdown"

    def test_post_turn_registration_of_the_same_link_adds_no_second_row(self, service, repository):
        link = service.publish_artifact_file(CONVERSATION_ID, _user(), ARTIFACT, b"a,b\n", "text/csv")

        with patch.object(AgentWorkspaceService, "_get_conversation_uploaded_files", return_value=[]):
            registered = service.register_generated_files(CONVERSATION_ID, [link], _user())

        assert registered == []
        assert len(repository.files) == 1

    def test_post_turn_registration_of_another_blob_cannot_replace_an_artifact(self, service, repository):
        service.publish_artifact_file(CONVERSATION_ID, _user(), ARTIFACT, b"a,b\n", "text/csv")
        original = repository.get_file(WORKSPACE_ID, ARTIFACT)
        owner, name = original.blob_owner, original.blob_name
        service.file_repository.read_file.side_effect = FileNotFoundError
        # Same artifact path, another owner's blob: a link pasted into the reply.
        foreign = FileObject(name=ARTIFACT, mime_type="text/csv", owner=USER_ID)
        link = f"{SANDBOX_FILE_PREFIX}{foreign.to_encoded_url()}"

        with patch.object(AgentWorkspaceService, "_get_conversation_uploaded_files", return_value=[]):
            registered = service.register_generated_files(CONVERSATION_ID, [link], _user())

        assert registered == []
        assert (original.blob_owner, original.blob_name) == (owner, name)
        assert len(repository.files) == 1


class TestTurnArtifactTokens:
    def test_lists_only_the_files_published_in_that_turn(self, service, repository):
        mine = service.publish_artifact_file(CONVERSATION_ID, _user(), "artifacts/t1/a.csv", b"1", "text/csv")
        service.publish_artifact_file(CONVERSATION_ID, _user(), "artifacts/t2/b.csv", b"2", "text/csv")
        service.publish_artifact_file(CONVERSATION_ID, _user(), "artifacts/t10/c.csv", b"3", "text/csv")

        assert service.turn_artifact_tokens(CONVERSATION_ID, USER_ID, "t1") == [mine[len(SANDBOX_FILE_PREFIX) :]]

    def test_a_conversation_without_a_workspace_has_none(self, service):
        assert service.turn_artifact_tokens("other-conv", USER_ID, "t1") == []


def test_only_user_messages_count_as_conversation_uploads():
    from codemie.core.constants import ChatRole
    from codemie.rest_api.models.conversation import GeneratedMessage

    history = [
        GeneratedMessage(role=ChatRole.USER, message="q", file_names=["upload"]),
        GeneratedMessage(role=ChatRole.ASSISTANT, message="a", file_names=["artifact"]),
    ]
    with patch("codemie.rest_api.models.conversation.Conversation.find_by_id") as find:
        find.return_value.history = history

        assert AgentWorkspaceService._get_conversation_uploaded_file_urls("conv-1") == ["upload"]
