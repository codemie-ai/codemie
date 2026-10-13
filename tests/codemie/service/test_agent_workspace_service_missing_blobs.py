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

"""A workspace file whose stored blob is gone is skipped with a warning; it never fails a script run."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codemie.service import agent_workspace_service as service_module
from codemie.service.agent_workspace_service import AgentWorkspaceService

MISSING_WARNING = "Skipped 'skills/foo/missing.py': its stored file could not be read."


def _row(path: str, blob_name: str, blob_owner: str = "skill-sk1"):
    return SimpleNamespace(path=path, blob_name=blob_name, blob_owner=blob_owner, mime_type="text/plain")


@pytest.fixture
def service() -> AgentWorkspaceService:
    svc = AgentWorkspaceService.__new__(AgentWorkspaceService)
    svc.repository = MagicMock()
    svc.file_repository = MagicMock()
    workspace = MagicMock(id="ws-1", conversation_id="conv-1")
    svc.get_workspace = MagicMock(return_value=workspace)  # type: ignore[method-assign]
    svc._get_conversation_uploaded_files = MagicMock(return_value=[])  # type: ignore[method-assign]
    svc.repository.list_files.return_value = [
        _row("ok.py", "ab/ok.py", "workspace-ws-1"),
        _row("skills/foo/missing.py", "cd/missing.py"),
    ]

    def read_file(file_name, owner, mime_type=None):
        if file_name == "cd/missing.py":
            raise FileNotFoundError(file_name)
        return SimpleNamespace(content="needle in ok.py")

    svc.file_repository.read_file.side_effect = read_file
    return svc


def test_input_files_skip_a_missing_blob_with_a_warning(service: AgentWorkspaceService) -> None:
    result = service.get_workspace_input_files("ws-1", MagicMock(id="u1"))

    assert [f.name for f in result.files] == ["ok.py"]
    assert result.warnings == [MISSING_WARNING]


def test_a_script_run_proceeds_and_reports_the_skipped_file(service: AgentWorkspaceService) -> None:
    service._sync_execution_files = MagicMock(return_value=[])  # type: ignore[method-assign]
    with (
        patch.object(service_module, "WorkspaceScriptRunner") as runner_cls,
        patch.object(service_module, "resolve_tool_calling_settings", return_value=None),
    ):
        runner_cls.return_value.execute_script.return_value = "done"
        runner_cls.return_value.last_execution_files = []
        response = service.execute_workspace_script("ws-1", "ok.py", MagicMock(id="u1"))

    assert response.output == "done"
    assert response.warnings == [MISSING_WARNING]
    assert [f.name for f in runner_cls.call_args.kwargs["input_files"]] == ["ok.py"]


def test_grep_skips_an_unreadable_file_and_still_finds_matches(service: AgentWorkspaceService) -> None:
    matches = service.grep_files("ws-1", "needle", MagicMock(id="u1"))

    assert [m.file_path for m in matches] == ["ok.py"]
