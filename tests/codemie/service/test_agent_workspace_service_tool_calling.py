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

"""The workspace service hands the script runner the bridge timeout that the resolver returns."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from codemie.service import agent_workspace_service as service_module
from codemie.service.agent_workspace_service import AgentWorkspaceService


@pytest.fixture
def runner_class() -> Iterator[MagicMock]:
    with patch.object(service_module, "WorkspaceScriptRunner") as runner_cls:
        runner_cls.return_value.execute_script.return_value = "ok"
        runner_cls.return_value.last_execution_files = []
        yield runner_cls


@pytest.fixture
def service() -> AgentWorkspaceService:
    svc = AgentWorkspaceService.__new__(AgentWorkspaceService)
    svc.repository = MagicMock()
    svc.file_repository = MagicMock()
    workspace = MagicMock()
    workspace.id = "ws-1"
    workspace.conversation_id = "conv-1"
    svc.get_workspace = MagicMock(return_value=workspace)  # type: ignore[method-assign]
    svc.get_workspace_input_files = MagicMock(return_value=[])  # type: ignore[method-assign]
    svc._sync_execution_files = MagicMock(return_value=[])  # type: ignore[method-assign]
    return svc


@pytest.mark.parametrize("resolved", [None, 45.0])
def test_runner_receives_the_resolved_timeout(
    service: AgentWorkspaceService, runner_class: MagicMock, resolved: float | None
) -> None:
    with patch.object(service_module, "resolve_tool_calling_timeout", return_value=resolved):
        service.execute_workspace_script("ws-1", "run.py", MagicMock(id="user-1"))

    runner_class.assert_called_once()
    assert runner_class.call_args.kwargs["tool_calling_timeout"] == resolved
