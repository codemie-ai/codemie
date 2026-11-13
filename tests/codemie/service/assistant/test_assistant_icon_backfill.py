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

"""Tests for the assistant icon backfill."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from codemie_tools.base.file_object import FileObject

from codemie.service.assistant import assistant_icon_backfill
from codemie.service.assistant.assistant_icon_backfill import backfill_assistant_icons, main

_SVC = "codemie.service.assistant.assistant_icon_service"
_BUCKET = "test-bucket"
_BASE = "https://h/api/v1/files/"


def _token(owner="user-1", name="up.png", mime="image/png") -> str:
    return FileObject(name=name, mime_type=mime, owner=owner).to_encoded_url()


def _assistant(aid: str, icon_url: str, creator: str = "user-1") -> MagicMock:
    a = MagicMock()
    a.id = aid
    a.icon_url = icon_url
    a.created_by = SimpleNamespace(id=creator)
    a.project = "demo"
    return a


@pytest.fixture
def env(mocker):
    from codemie.configs import config

    mocker.patch.object(config, "CODEMIE_STORAGE_BUCKET_NAME", _BUCKET)
    repo = MagicMock()
    repo.read_file.return_value = FileObject(name="up.png", mime_type="image/png", owner="user-1", content=b"PNG")
    repo.write_file.side_effect = lambda name, mime_type, owner, content=None: FileObject(
        name=name, mime_type=mime_type, owner=owner
    )
    mocker.patch.object(assistant_icon_backfill.Assistant, "get_by_icon_url", return_value=[])
    mocker.patch(f"{_SVC}.FileRepositoryFactory").return_value.get_current_repository.return_value = repo
    is_admin = mocker.patch.object(assistant_icon_backfill, "_is_admin_of_any", return_value=False)
    return SimpleNamespace(repo=repo, mocker=mocker, is_admin=is_admin)


def _serve(env, assistants):
    """Serve the assistants whose icon_url still references a stored file, keyset-paged like the real query."""

    def batch(after_id, limit):
        rows = [a for a in assistants if "/v1/files/" in a.icon_url and (after_id is None or a.id > after_id)]
        return sorted(rows, key=lambda a: a.id)[:limit]

    env.mocker.patch.object(assistant_icon_backfill.Assistant, "get_uploaded_icon_batch", side_effect=batch)


def test_backfill_converts_uploaded_icon_and_second_run_is_noop(env) -> None:
    a = _assistant("a1", _BASE + _token())
    a.update.side_effect = lambda **kw: None
    _serve(env, [a])

    first = backfill_assistant_icons()
    second = backfill_assistant_icons()

    expected = FileObject(name="assistants/a1/icon.png", mime_type="image/png", owner=_BUCKET).to_encoded_url()
    assert a.icon_url == _BASE + expected
    a.update.assert_called_once_with(validate=False, touch_timestamp=False)
    assert (first.converted, second.converted, second.skipped) == (1, 0, 1)
    env.repo.write_file.assert_called_once()


def test_missing_file_leaves_url_unchanged_and_counts_missing(env) -> None:
    url = _BASE + _token()
    a = _assistant("a1", url)
    env.repo.read_file.side_effect = FileNotFoundError("gone")
    _serve(env, [a])

    result = backfill_assistant_icons()

    assert a.icon_url == url
    a.update.assert_not_called()
    assert (result.converted, result.missing) == (0, 1)


def test_external_url_is_not_selected_or_changed(env) -> None:
    a = _assistant("a1", "https://example.com/icon.png")
    _serve(env, [a])

    result = backfill_assistant_icons()

    assert result.converted == result.skipped == result.missing == 0
    a.update.assert_not_called()


def test_non_image_is_skipped(env) -> None:
    url = _BASE + _token(mime="application/pdf", name="x.pdf")
    a = _assistant("a1", url)
    _serve(env, [a])

    result = backfill_assistant_icons()

    assert a.icon_url == url and result.skipped == 1
    env.repo.write_file.assert_not_called()


def test_dry_run_writes_nothing(env) -> None:
    url = _BASE + _token()
    a = _assistant("a1", url)
    _serve(env, [a])

    result = backfill_assistant_icons(dry_run=True)

    assert result.converted == 1 and a.icon_url == url
    a.update.assert_not_called()
    env.repo.write_file.assert_not_called()


def test_main_dry_run_flag(env) -> None:
    a = _assistant("a1", _BASE + _token())
    _serve(env, [a])

    assert main(["--dry-run"]) == 0
    env.repo.write_file.assert_not_called()


def test_backfill_stops_with_error_when_cursor_does_not_advance(env) -> None:
    a = _assistant("a1", _BASE + _token(owner=_BUCKET, name="assistants/a1/icon.png"))
    env.mocker.patch.object(assistant_icon_backfill.Assistant, "get_uploaded_icon_batch", return_value=[a])

    with pytest.raises(RuntimeError, match="cursor did not advance"):
        backfill_assistant_icons()


def test_file_of_another_owner_is_not_published(env) -> None:
    url = _BASE + _token(owner="victim")
    a = _assistant("a1", url, creator="author")
    _serve(env, [a])

    result = backfill_assistant_icons()

    assert a.icon_url == url and (result.converted, result.skipped) == (0, 1)
    env.repo.read_file.assert_not_called()
    env.repo.write_file.assert_not_called()
    a.update.assert_not_called()


def test_icon_uploaded_by_an_admin_of_the_assistant_is_published(env) -> None:
    a = _assistant("a1", _BASE + _token(owner="project-admin"), creator="author")
    env.is_admin.return_value = True
    _serve(env, [a])

    result = backfill_assistant_icons()

    env.is_admin.assert_called_once_with("project-admin", {"demo"})
    assert result.converted == 1
    env.repo.write_file.assert_called_once()


def test_legacy_creator_field_counts_as_the_creator(env) -> None:
    a = _assistant("a1", _BASE + _token(owner="old-author"), creator="unused")
    a.created_by = None
    a.creator = "old-author"
    _serve(env, [a])

    result = backfill_assistant_icons()

    assert result.converted == 1
    env.is_admin.assert_not_called()


def test_bucket_owned_workflow_schema_is_not_published(env) -> None:
    url = _BASE + _token(owner=_BUCKET, name="workflows/w1.svg", mime="image/svg+xml")
    a = _assistant("a1", url, creator="author")
    _serve(env, [a])

    result = backfill_assistant_icons()

    assert a.icon_url == url and result.skipped == 1
    env.repo.write_file.assert_not_called()


def _share_by_icon_url(env, assistants):
    """get_by_icon_url as the database answers it: by the assistants' current icon_url."""
    env.mocker.patch.object(
        assistant_icon_backfill.Assistant,
        "get_by_icon_url",
        side_effect=lambda url: [a for a in assistants if a.icon_url == url],
    )


def test_clone_converts_when_the_original_is_processed_first(env) -> None:
    url = _BASE + _token(owner="user-1")
    original = _assistant("a1", url, creator="user-1")
    clone = _assistant("a2", url, creator="cloner")
    for a in (original, clone):
        a.update.side_effect = lambda **kw: None
    assistants = [original, clone]
    _share_by_icon_url(env, assistants)
    _serve(env, assistants)

    result = backfill_assistant_icons()

    assert (result.converted, result.skipped, result.missing) == (2, 0, 0)
    assert original.icon_url.endswith(
        FileObject(name="assistants/a1/icon.png", mime_type="image/png", owner=_BUCKET).to_encoded_url()
    )
    assert clone.icon_url.endswith(
        FileObject(name="assistants/a2/icon.png", mime_type="image/png", owner=_BUCKET).to_encoded_url()
    )


def test_clone_converts_when_the_clone_is_processed_first(env) -> None:
    url = _BASE + _token(owner="user-1")
    clone = _assistant("a1", url, creator="cloner")
    original = _assistant("a2", url, creator="user-1")
    for a in (original, clone):
        a.update.side_effect = lambda **kw: None
    assistants = [clone, original]
    _share_by_icon_url(env, assistants)
    _serve(env, assistants)

    result = backfill_assistant_icons()

    assert (result.converted, result.skipped) == (2, 0)
    assert env.repo.write_file.call_count == 2


def test_dry_run_counts_each_sharing_assistant_once(env) -> None:
    url = _BASE + _token(owner="user-1")
    assistants = [_assistant("a1", url, creator="user-1"), _assistant("a2", url, creator="cloner")]
    _share_by_icon_url(env, assistants)
    _serve(env, assistants)

    result = backfill_assistant_icons(dry_run=True)

    assert (result.converted, result.skipped) == (2, 0)
    env.repo.write_file.assert_not_called()


class TestIsAdminOfAny:
    """The owner-role lookup behind _owner_may_publish."""

    @pytest.fixture
    def repos(self, mocker):
        mocker.patch("codemie.clients.postgres.get_session").return_value.__enter__.return_value = "session"
        users = mocker.patch.object(assistant_icon_backfill, "user_repository")
        projects = mocker.patch.object(assistant_icon_backfill, "user_project_repository")
        projects.is_admin.return_value = False
        return users, projects

    @pytest.mark.parametrize(
        "flags", [{"is_admin": True, "is_maintainer": False}, {"is_admin": False, "is_maintainer": True}]
    )
    def test_platform_admin_or_maintainer(self, repos, flags) -> None:
        users, _ = repos
        users.get_by_id.return_value = SimpleNamespace(**flags)

        assert assistant_icon_backfill._is_admin_of_any("u", {"demo"}) is True

    def test_project_admin(self, repos) -> None:
        users, projects = repos
        users.get_by_id.return_value = SimpleNamespace(is_admin=False, is_maintainer=False)
        projects.is_admin.side_effect = lambda session, user_id, project: project == "demo"

        assert assistant_icon_backfill._is_admin_of_any("u", {"other", "demo"}) is True

    def test_plain_member_is_not_an_admin(self, repos) -> None:
        users, _ = repos
        users.get_by_id.return_value = SimpleNamespace(is_admin=False, is_maintainer=False)

        assert assistant_icon_backfill._is_admin_of_any("u", {"demo"}) is False

    def test_unknown_owner_or_lookup_error_is_not_an_admin(self, repos) -> None:
        users, _ = repos
        users.get_by_id.return_value = None
        assert assistant_icon_backfill._is_admin_of_any("u", {"demo"}) is False

        users.get_by_id.side_effect = RuntimeError("bad id")
        assert assistant_icon_backfill._is_admin_of_any("u", {"demo"}) is False
