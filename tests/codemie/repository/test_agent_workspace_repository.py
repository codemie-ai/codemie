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

"""AgentWorkspaceRepository.delete_by_execution_id removes the workspace and its file rows."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from codemie.repository.agent_workspace_repository import AgentWorkspaceRepository


def _session_patch(workspace: object | None) -> tuple[MagicMock, MagicMock]:
    session = MagicMock()
    session.exec.return_value.first.return_value = workspace
    ctx = MagicMock()
    ctx.__enter__.return_value = session
    return ctx, session


def test_delete_by_execution_id_removes_files_then_workspace() -> None:
    workspace = MagicMock(id="ws-1")
    ctx, session = _session_patch(workspace)
    with patch("codemie.repository.agent_workspace_repository.Session", return_value=ctx):
        removed = AgentWorkspaceRepository().delete_by_execution_id("exec-1", "user-1")

    assert removed is True
    session.delete.assert_called_once_with(workspace)
    session.commit.assert_called_once()
    assert session.exec.call_count == 2  # workspace lookup, then bulk file delete


def test_delete_by_execution_id_without_workspace_is_noop() -> None:
    ctx, session = _session_patch(None)
    with patch("codemie.repository.agent_workspace_repository.Session", return_value=ctx):
        removed = AgentWorkspaceRepository().delete_by_execution_id("exec-1", "user-1")

    assert removed is False
    session.delete.assert_not_called()
