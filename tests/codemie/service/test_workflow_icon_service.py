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

from unittest.mock import MagicMock

import pytest
from codemie_tools.base.file_object import FileObject

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.workflow_icon_service import WorkflowIconService

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture
def repo(mocker) -> MagicMock:
    factory = mocker.patch("codemie.service.workflow_icon_service.FileRepositoryFactory")
    repository = MagicMock()
    factory.get_current_repository.return_value = repository
    return repository


def test_rejects_png_declared_file_with_non_image_bytes(repo) -> None:
    with pytest.raises(ExtendedHTTPException) as exc:
        WorkflowIconService.store(b"%PDF-1.4 not an image", "image/png")

    assert exc.value.code == 400
    assert "type" in exc.value.message.lower()
    repo.write_file.assert_not_called()


def test_rejects_empty_file(repo) -> None:
    with pytest.raises(ExtendedHTTPException) as exc:
        WorkflowIconService.store(b"", "image/png")

    assert exc.value.code == 400
    repo.write_file.assert_not_called()


def test_rejects_undeclared_mime_type(repo) -> None:
    with pytest.raises(ExtendedHTTPException) as exc:
        WorkflowIconService.store(b"GIF89a", "image/gif")

    assert exc.value.code == 400
    assert "type" in exc.value.message.lower()
    repo.write_file.assert_not_called()


def test_stores_valid_svg(repo) -> None:
    WorkflowIconService.store(b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>', "image/svg+xml")

    assert repo.write_file.call_args.kwargs["name"].endswith(".svg")


def test_stores_valid_png_under_workflow_icon_prefix(repo) -> None:
    result = WorkflowIconService.store(PNG, "image/png")

    kwargs = repo.write_file.call_args.kwargs
    assert kwargs["name"].startswith("workflow-icons/")
    assert kwargs["name"].endswith(".png")
    assert kwargs["owner"] == config.CODEMIE_STORAGE_BUCKET_NAME
    assert kwargs["mime_type"] == "image/png"
    assert kwargs["content"] == PNG
    assert result is repo.write_file.return_value


def test_icon_exists_checks_namespace_and_file_presence(repo) -> None:
    name = "workflow-icons/" + "a" * 32 + ".png"
    url = FileObject(name=name, mime_type="image/png", owner=config.CODEMIE_STORAGE_BUCKET_NAME).to_encoded_url()

    assert WorkflowIconService.exists(url) is True
    repo.read_file.assert_called_once_with(name, config.CODEMIE_STORAGE_BUCKET_NAME)

    repo.read_file.side_effect = FileNotFoundError
    assert WorkflowIconService.exists(url) is False


def test_icon_exists_rejects_traversal_and_foreign_owner(repo) -> None:
    traversal = FileObject(
        name="workflow-icons/../../secret.png", mime_type="image/png", owner=config.CODEMIE_STORAGE_BUCKET_NAME
    ).to_encoded_url()
    foreign = FileObject(
        name="workflow-icons/" + "a" * 32 + ".png", mime_type="image/png", owner="user-1"
    ).to_encoded_url()

    assert WorkflowIconService.exists(traversal) is False
    assert WorkflowIconService.exists(foreign) is False
    assert WorkflowIconService.is_icon_file("user-1", "workflow-icons/" + "a" * 32 + ".png") is False
    repo.read_file.assert_not_called()


def test_is_workflow_icon_file_rejects_name_with_trailing_newline() -> None:
    name = "workflow-icons/" + "a" * 32 + ".png\n"

    assert WorkflowIconService.is_icon_file(config.CODEMIE_STORAGE_BUCKET_NAME, name) is False


def test_icon_exists_reports_storage_errors_as_missing(repo) -> None:
    name = "workflow-icons/" + "a" * 32 + ".png"
    url = FileObject(name=name, mime_type="image/png", owner=config.CODEMIE_STORAGE_BUCKET_NAME).to_encoded_url()
    repo.read_file.side_effect = RuntimeError("storage unavailable")

    assert WorkflowIconService.exists(url) is False
