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

"""Tests for AssistantIconService.localize_icon."""

from __future__ import annotations

from unittest.mock import MagicMock
from urllib.parse import quote

import pytest
from codemie_tools.base.file_object import FileObject

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_icon_service import AssistantIconService

_MODULE = "codemie.service.assistant.assistant_icon_service"
_BUCKET = "test-bucket"
_BASE = "https://h/api/v1/files/"


def _user() -> User:
    return User(id="user-1", username="u", name="U")


def _token(owner="user-1", name="up.png", mime="image/png") -> str:
    return FileObject(name=name, mime_type=mime, owner=owner).to_encoded_url()


@pytest.fixture
def env(mocker):
    from codemie.configs import config

    mocker.patch.object(config, "CODEMIE_STORAGE_BUCKET_NAME", _BUCKET)
    repo = MagicMock()
    repo.read_file.return_value = FileObject(name="up.png", mime_type="image/png", owner="user-1", content=b"PNG")
    repo.write_file.side_effect = lambda name, mime_type, owner, content=None: FileObject(
        name=name, mime_type=mime_type, owner=owner
    )
    mocker.patch(f"{_MODULE}.FileRepositoryFactory").return_value.get_current_repository.return_value = repo
    can = mocker.patch(f"{_MODULE}.can_download", return_value=True)
    mocker.patch(f"{_MODULE}.AgentWorkspaceRepository")
    return repo, can


def test_owned_file_is_copied_and_url_rewritten(env) -> None:
    repo, _ = env

    result = AssistantIconService.localize_icon("a1", _BASE + _token(), None, _user())

    repo.write_file.assert_called_once_with(
        name="assistants/a1/icon.png", mime_type="image/png", owner=_BUCKET, content=b"PNG"
    )
    expected = FileObject(name="assistants/a1/icon.png", mime_type="image/png", owner=_BUCKET).to_encoded_url()
    assert result == _BASE + expected


def test_file_readable_by_saver_is_copied(env) -> None:
    repo, can = env

    AssistantIconService.localize_icon("a1", _BASE + _token(owner="someone-else"), None, _user())

    assert can.call_args.args[0].owner == "someone-else"
    repo.write_file.assert_called_once()


def test_unreadable_file_raises_400_and_writes_nothing(env) -> None:
    repo, can = env
    can.return_value = False

    with pytest.raises(ExtendedHTTPException) as exc:
        AssistantIconService.localize_icon("a1", _BASE + _token(owner="someone-else"), None, _user())

    assert exc.value.code == 400
    repo.write_file.assert_not_called()


def test_non_image_raises_400(env) -> None:
    repo, _ = env

    with pytest.raises(ExtendedHTTPException) as exc:
        AssistantIconService.localize_icon("a1", _BASE + _token(mime="application/pdf", name="x.pdf"), None, _user())

    assert exc.value.code == 400
    repo.write_file.assert_not_called()


@pytest.mark.parametrize("value", [None, "", "https://example.com/icon.png"])
def test_empty_and_external_urls_pass_through(env, value) -> None:
    repo, _ = env

    assert AssistantIconService.localize_icon("a1", value, None, _user()) == value
    repo.read_file.assert_not_called()


def test_already_entity_icon_passes_through_without_io(env) -> None:
    repo, _ = env
    url = _BASE + _token(owner=_BUCKET, name="assistants/a1/icon.png")

    assert AssistantIconService.localize_icon("a1", url, None, _user()) == url
    repo.read_file.assert_not_called()
    repo.write_file.assert_not_called()


def test_other_assistants_entity_icon_is_copied(env) -> None:
    repo, _ = env
    url = _BASE + _token(owner=_BUCKET, name="assistants/src/icon.png")

    result = AssistantIconService.localize_icon("a1", url, None, _user())

    assert result != url
    assert repo.write_file.call_args.kwargs["name"] == "assistants/a1/icon.png"


def test_kept_icon_the_saver_cannot_download_stays_unchanged(env) -> None:
    repo, can = env
    can.return_value = False
    url = _BASE + _token(owner="someone-else")

    assert AssistantIconService.localize_icon("a1", url, url, _user()) == url
    repo.write_file.assert_not_called()


def test_kept_icon_the_saver_can_download_is_converted(env) -> None:
    repo, _ = env
    url = _BASE + _token()

    result = AssistantIconService.localize_icon("a1", url, url, _user())

    expected = FileObject(name="assistants/a1/icon.png", mime_type="image/png", owner=_BUCKET).to_encoded_url()
    assert result == _BASE + expected
    repo.write_file.assert_called_once()


@pytest.mark.parametrize(
    "url",
    [
        _BASE + "not-a-token",
        _BASE + _token(mime="application/pdf", name="x.pdf"),
    ],
)
def test_kept_icon_that_is_not_a_usable_image_stays_unchanged(env, url) -> None:
    repo, _ = env

    assert AssistantIconService.localize_icon("a1", url, url, _user()) == url
    repo.write_file.assert_not_called()


def test_kept_icon_with_missing_source_file_stays_unchanged(env) -> None:
    repo, _ = env
    repo.read_file.side_effect = FileNotFoundError("gone")
    url = _BASE + _token()

    assert AssistantIconService.localize_icon("a1", url, url, _user()) == url
    repo.write_file.assert_not_called()


def test_url_encoded_token_is_accepted(env) -> None:
    repo, _ = env

    AssistantIconService.localize_icon("a1", _BASE + quote(_token(), safe=""), None, _user())

    repo.write_file.assert_called_once()


def test_missing_source_file_raises_400(env) -> None:
    repo, _ = env
    repo.read_file.side_effect = FileNotFoundError("gone")

    with pytest.raises(ExtendedHTTPException) as exc:
        AssistantIconService.localize_icon("a1", _BASE + _token(), None, _user())

    assert exc.value.code == 400
    repo.write_file.assert_not_called()
