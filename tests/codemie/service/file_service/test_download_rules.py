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

"""Direct tests for the download ownership rules."""

from __future__ import annotations

from unittest.mock import MagicMock

from codemie_tools.base.file_object import FileObject

from codemie.rest_api.security.user import User
from codemie.service.file_service.download_rules import can_download


def _user(user_id: str = "user-1") -> User:
    return User(id=user_id, username="u", name="U")


def _file(owner: str, name: str = "doc.pdf") -> FileObject:
    return FileObject(name=name, mime_type="application/pdf", owner=owner)


def _repo() -> MagicMock:
    repo = MagicMock()
    repo.get_by_id_for_user.return_value = None
    repo.get_by_conversation_for_user.return_value = None
    repo.find_by_blob.return_value = None
    return repo


def test_accepts_own_id() -> None:
    assert can_download(_file("user-1"), _user(), None, "tok", _repo()) is True


def test_accepts_own_workspace() -> None:
    repo = _repo()
    repo.get_by_id_for_user.return_value = MagicMock()

    assert can_download(_file("workspace-ws1"), _user(), None, "tok", repo) is True
    repo.get_by_id_for_user.assert_called_once_with("ws1", "user-1")


def test_accepts_share_grant(mocker) -> None:
    shared = MagicMock(conversation_id="conv-1", shared_by_user_id="sharer")
    mocker.patch(
        "codemie.service.file_service.download_rules.SharedConversation.get_by_fields",
        return_value=shared,
    )
    conversation = MagicMock(history=[MagicMock()])
    mocker.patch(
        "codemie.service.file_service.download_rules.Conversation.find_by_id",
        return_value=conversation,
    )
    mocker.patch(
        "codemie.service.file_service.download_rules.collect_message_file_tokens",
        return_value={"tok"},
    )

    assert can_download(_file("someone-else"), _user(), "share-token", "tok", _repo()) is True


def test_rejects_foreign_owner() -> None:
    assert can_download(_file("someone-else"), _user(), None, "tok", _repo()) is False


def test_rejects_unsafe_shape_even_for_own_owner() -> None:
    assert can_download(_file("user-1", "../victim/secret.pdf"), _user(), None, "tok", _repo()) is False
