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

"""GET /v1/workspaces/{workspace_id}/files/download-all: one zip of the workspace, errors mapped without leaks."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from codemie_tools.base.file_object import FileObject

from codemie.core.exceptions import ExtendedHTTPException, ValidationException
from codemie.rest_api.routers import agent_workspace
from codemie.rest_api.routers.agent_workspace import download_all_workspace_files, download_workspace_file
from codemie.rest_api.security.user import User
from codemie.service.workspace_archive import (
    WorkspaceArchive,
    WorkspaceArchiveFailedError,
    WorkspaceArchiveTooLargeError,
)

ROUTER = "codemie.rest_api.routers.agent_workspace"
USER = User(id="u1", username="u", name="u")
SECRET = "s3://bucket/owner/secret-key"


def _archive(skipped: list[str] | None = None) -> WorkspaceArchive:
    return WorkspaceArchive(b"PK-zip", "My_ws.zip", 1, skipped or [], 6)


@pytest.fixture
def service() -> Iterator[MagicMock]:
    with patch(f"{ROUTER}.workspace_service") as mocked:
        mocked.build_workspace_archive.return_value = _archive()
        yield mocked


def test_download_all_returns_zip_with_safe_header(service) -> None:
    response = download_all_workspace_files("ws-1", USER)

    assert response.media_type == "application/zip"
    assert response.headers["content-disposition"] == 'attachment; filename="My_ws.zip"'
    assert response.body == b"PK-zip"
    assert "x-workspace-skipped-count" not in response.headers
    service.build_workspace_archive.assert_called_once_with("ws-1", USER)


def test_download_all_reports_skipped_count(service) -> None:
    service.build_workspace_archive.return_value = _archive(["a.txt", "b.txt"])

    response = download_all_workspace_files("ws-1", USER)

    assert response.headers["x-workspace-skipped-count"] == "2"


def test_download_all_empty_workspace_is_400(service) -> None:
    service.build_workspace_archive.side_effect = ValidationException("Workspace has no downloadable files")

    with pytest.raises(ExtendedHTTPException) as caught:
        download_all_workspace_files("ws-1", USER)

    assert caught.value.code == 400
    assert caught.value.details == "Workspace has no downloadable files"


def test_download_all_foreign_workspace_looks_like_a_missing_one(service) -> None:
    service.build_workspace_archive.side_effect = ValidationException("Workspace 'ws-x' not found")

    with pytest.raises(ExtendedHTTPException) as foreign:
        download_all_workspace_files("ws-x", USER)
    with pytest.raises(ExtendedHTTPException) as missing:
        download_all_workspace_files("ws-x", USER)

    assert foreign.value.code == 400
    assert (foreign.value.message, foreign.value.details) == (missing.value.message, missing.value.details)


def test_download_all_over_cap_is_413_with_limit_in_details(service) -> None:
    service.build_workspace_archive.side_effect = WorkspaceArchiveTooLargeError("at most 200 files limit")

    with pytest.raises(ExtendedHTTPException) as caught:
        download_all_workspace_files("ws-1", USER)

    assert caught.value.code == 413
    assert caught.value.details == "at most 200 files limit"
    assert "individual files" in caught.value.help


def test_download_all_unreadable_workspace_is_500_with_its_message(service) -> None:
    service.build_workspace_archive.side_effect = WorkspaceArchiveFailedError(
        "None of the workspace files could be read"
    )

    with pytest.raises(ExtendedHTTPException) as caught:
        download_all_workspace_files("ws-1", USER)

    assert caught.value.code == 500
    assert caught.value.details == "None of the workspace files could be read"


def test_download_all_unexpected_error_does_not_leak_its_text(service) -> None:
    service.build_workspace_archive.side_effect = RuntimeError(SECRET)

    with pytest.raises(ExtendedHTTPException) as caught:
        download_all_workspace_files("ws-1", USER)

    assert caught.value.code == 500
    assert "secret-key" not in f"{caught.value.message} {caught.value.details} {caught.value.help}"


def test_single_file_download_is_unchanged(service) -> None:
    service.download_file.return_value = FileObject(name="a.txt", mime_type="text/plain", owner="o", content=b"hi")

    response = download_workspace_file("ws-1", "dir/a.txt", USER)

    assert response.body == b"hi"
    assert response.media_type == "text/plain"
    assert response.headers["content-disposition"] == 'attachment; filename="a.txt"'


def test_download_all_route_is_registered() -> None:
    assert any(
        route.path == "/v1/workspaces/{workspace_id}/files/download-all" and "GET" in route.methods
        for route in agent_workspace.router.routes
    )
