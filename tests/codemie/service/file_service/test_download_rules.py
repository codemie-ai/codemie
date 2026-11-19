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

import pytest

from codemie_tools.base.file_object import FileObject

from codemie.configs import config
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


def _share(mocker, tokens: set[str]) -> None:
    """A conversation shared by `sharer` whose history carries exactly `tokens`."""
    mocker.patch(
        "codemie.service.file_service.download_rules.SharedConversation.get_by_fields",
        return_value=MagicMock(conversation_id="conv-1", shared_by_user_id="sharer"),
    )
    mocker.patch(
        "codemie.service.file_service.download_rules.Conversation.find_by_id",
        return_value=MagicMock(history=[MagicMock()]),
    )
    mocker.patch(
        "codemie.service.file_service.download_rules.collect_message_file_tokens",
        return_value=tokens,
    )


def test_share_grants_sharers_own_token_in_history(mocker) -> None:
    _share(mocker, {"tok"})

    assert can_download(_file("sharer", "abc_doc.pdf"), _user(), "share-token", "tok", _repo()) is True


def test_share_refuses_foreign_user_token_pasted_into_history(mocker) -> None:
    _share(mocker, {"tok"})

    assert can_download(_file("someone-else"), _user(), "share-token", "tok", _repo()) is False


def test_share_refuses_foreign_workspace_token_pasted_into_history(mocker) -> None:
    _share(mocker, {"tok"})
    repo = _repo()
    repo.get_by_conversation_for_user.return_value = MagicMock(id="ws-sharer")

    assert can_download(_file("workspace-other", "artifacts/t1/x.xlsx"), _user(), "share-token", "tok", repo) is False
    repo.find_by_blob.assert_called_once_with("ws-sharer", "workspace-other", "artifacts/t1/x.xlsx", "application/pdf")


def test_share_grants_artifact_registered_in_sharers_workspace(mocker) -> None:
    _share(mocker, set())
    repo = _repo()
    repo.get_by_conversation_for_user.return_value = MagicMock(id="conv-1")
    repo.find_by_blob.return_value = MagicMock()

    assert can_download(_file("workspace-conv-1", "artifacts/t1/x.xlsx"), _user(), "share-token", "tok", repo) is True
    repo.get_by_conversation_for_user.assert_called_once_with("conv-1", "sharer")


def test_share_keeps_legacy_mcp_images_links(mocker) -> None:
    _share(mocker, set())

    assert can_download(_file("mcp_images", "abc.png"), _user(), "share-token", "tok", _repo()) is True


def test_rejects_foreign_owner() -> None:
    assert can_download(_file("someone-else"), _user(), None, "tok", _repo()) is False


def test_rejects_unsafe_shape_even_for_own_owner() -> None:
    assert can_download(_file("user-1", "../victim/secret.pdf"), _user(), None, "tok", _repo()) is False


def test_accepts_source_blob_referenced_by_the_users_own_workspace() -> None:
    repo = _repo()
    repo.user_references_blob.return_value = True

    assert can_download(_file("skill-sk1", "ab12/a.py"), _user(), None, "tok", repo) is True
    repo.user_references_blob.assert_called_once_with("user-1", "skill-sk1", "ab12/a.py")


def test_rejects_source_blob_no_workspace_of_the_user_references() -> None:
    repo = _repo()
    repo.user_references_blob.return_value = False

    assert can_download(_file("skill-sk1", "ab12/a.py"), _user(), None, "tok", repo) is False


def test_other_owners_never_consult_the_source_blob_rule() -> None:
    repo = _repo()

    can_download(_file("somebody-else"), _user(), None, "tok", repo)

    repo.user_references_blob.assert_not_called()


_BUCKET = "test-bucket"


def _icon_setup(mocker, *, found=True, can_read=True, find_error=None, parents=()):
    from codemie.configs import config

    mocker.patch.object(config, "CODEMIE_STORAGE_BUCKET_NAME", _BUCKET)
    find = mocker.patch(
        "codemie.service.file_service.download_rules.Assistant.find_by_id",
        return_value=MagicMock() if found else None,
        side_effect=find_error,
    )
    mocker.patch("codemie.service.file_service.download_rules.Ability.can", return_value=can_read)
    mocker.patch("codemie.service.file_service.download_rules.Assistant.get_global_parents", return_value=list(parents))
    return find


def _icon(name: str = "assistants/a1/icon.png", owner: str = _BUCKET) -> FileObject:
    return FileObject(name=name, mime_type="image/png", owner=owner)


def test_reader_can_download_assistant_icon(mocker) -> None:
    find = _icon_setup(mocker)

    assert can_download(_icon(), _user(), None, "tok", _repo()) is True
    find.assert_called_once_with("a1")


def test_non_reader_denied_assistant_icon(mocker) -> None:
    _icon_setup(mocker, can_read=False)

    assert can_download(_icon(), _user(), None, "tok", _repo()) is False


def test_assistant_icon_denied_when_assistant_missing(mocker) -> None:
    _icon_setup(mocker, found=False)

    assert can_download(_icon(), _user(), None, "tok", _repo()) is False


def test_assistant_icon_denied_when_lookup_raises(mocker) -> None:
    _icon_setup(mocker, find_error=RuntimeError("db down"))

    assert can_download(_icon(), _user(), None, "tok", _repo()) is False


def test_sub_assistant_icon_allowed_when_user_may_read_a_global_parent(mocker) -> None:
    sub, parent = MagicMock(name="sub"), MagicMock(name="parent")
    _icon_setup(mocker, parents=[parent])
    mocker.patch(
        "codemie.service.file_service.download_rules.Ability.can",
        side_effect=lambda action, entity: entity is parent,
    )
    mocker.patch("codemie.service.file_service.download_rules.Assistant.find_by_id", return_value=sub)

    assert can_download(_icon(), _user(), None, "tok", _repo()) is True


def test_sub_assistant_icon_denied_when_user_cannot_read_the_global_parent(mocker) -> None:
    _icon_setup(mocker, can_read=False, parents=[MagicMock()])

    assert can_download(_icon(), _user(), None, "tok", _repo()) is False


def test_sub_assistant_icon_denied_when_no_global_parent_lists_it(mocker) -> None:
    _icon_setup(mocker, can_read=False, parents=[])

    assert can_download(_icon(), _user(), None, "tok", _repo()) is False


def test_sub_assistant_icon_denied_when_parent_lookup_raises(mocker) -> None:
    _icon_setup(mocker, can_read=False)
    mocker.patch(
        "codemie.service.file_service.download_rules.Assistant.get_global_parents", side_effect=RuntimeError("db down")
    )

    assert can_download(_icon(), _user(), None, "tok", _repo()) is False


def test_direct_reader_does_not_trigger_parent_lookup(mocker) -> None:
    _icon_setup(mocker)
    lookup = mocker.patch("codemie.service.file_service.download_rules.Assistant.get_global_parents")

    assert can_download(_icon(), _user(), None, "tok", _repo()) is True
    lookup.assert_not_called()


@pytest.mark.parametrize(
    "name",
    ["assistants//icon.png", "assistants/a1/other.png", "assistants/a1/x/icon.png", "assistants/icon.png"],
)
def test_malformed_assistant_icon_names_denied(mocker, name) -> None:
    find = _icon_setup(mocker)

    assert can_download(_icon(name), _user(), None, "tok", _repo()) is False
    find.assert_not_called()


def test_assistant_icon_denied_for_non_bucket_owner(mocker) -> None:
    find = _icon_setup(mocker)

    assert can_download(_icon(owner="someone-else"), _user(), None, "tok", _repo()) is False
    find.assert_not_called()


def test_workflow_schema_handled_by_rule_b_not_assistant_lookup(mocker) -> None:
    find = _icon_setup(mocker)
    mocker.patch("codemie.service.file_service.download_rules.WorkflowConfig.find_by_id", return_value=MagicMock())

    assert can_download(_icon("workflows/w1.svg"), _user(), None, "tok", _repo()) is True
    find.assert_not_called()


def test_memory_snapshot_in_bucket_namespace_denied(mocker) -> None:
    find = _icon_setup(mocker)

    assert can_download(_icon("memory_snapshots/snap.json.gz"), _user(), None, "tok", _repo()) is False
    find.assert_not_called()


ICON_NAME = "workflow-icons/" + "b" * 32 + ".png"
_ICON_TARGET = "codemie.service.file_service.download_rules.WorkflowConfig"


def _workflow_icon(name: str = ICON_NAME) -> FileObject:
    return FileObject(name=name, mime_type="image/png", owner=config.CODEMIE_STORAGE_BUCKET_NAME)


def test_viewer_of_referencing_workflow_can_download_icon(mocker) -> None:
    workflow_model = mocker.patch(_ICON_TARGET)
    workflow_model.get_by_icon_url.return_value = [MagicMock()]
    mocker.patch("codemie.core.ability.Ability.can", return_value=True)

    assert can_download(_workflow_icon(), _user(), None, "tok", _repo()) is True
    workflow_model.get_by_icon_url.assert_called_once_with(_workflow_icon().to_encoded_url())


def test_referencing_workflow_without_view_access_denies_icon(mocker) -> None:
    workflow_model = mocker.patch(_ICON_TARGET)
    workflow_model.get_by_icon_url.return_value = [MagicMock()]
    mocker.patch("codemie.core.ability.Ability.can", return_value=False)

    assert can_download(_workflow_icon(), _user(), None, "tok", _repo()) is False


def test_icon_no_workflow_references_denies_non_owner(mocker) -> None:
    workflow_model = mocker.patch(_ICON_TARGET)
    workflow_model.get_by_icon_url.return_value = []

    assert can_download(_workflow_icon(), _user(), None, "tok", _repo()) is False


def test_bucket_non_icon_file_skips_icon_rule(mocker) -> None:
    workflow_model = mocker.patch(_ICON_TARGET)
    mocker.patch("codemie.core.ability.Ability.can", return_value=False)

    assert can_download(_workflow_icon("memory/snapshot.json"), _user(), None, "tok", _repo()) is False
    workflow_model.get_by_icon_url.assert_not_called()
