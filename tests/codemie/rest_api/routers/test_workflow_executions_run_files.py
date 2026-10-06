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

"""Validation of run input files on the create-workflow-execution route."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from codemie_tools.base.file_object import FileObject
from fastapi import BackgroundTasks, Request

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.workflow_models import CreateWorkflowExecutionRequest
from codemie.core.workflow_models.workflow_config import WorkflowMode
from codemie.rest_api.routers.workflow_executions import create_workflow_execution
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService

ROUTER = "codemie.rest_api.routers.workflow_executions"
USER_ID = "user-1"


def _token(owner: str, name: str = "doc.pdf") -> str:
    return FileObject(name=name, mime_type="application/pdf", owner=owner).to_encoded_url()


def _call(file_names: list[str], conversation_id: str | None = None) -> None:
    request = CreateWorkflowExecutionRequest(user_input="hi", file_names=file_names, conversation_id=conversation_id)
    create_workflow_execution(
        request=request,
        workflow_id="wf-1",
        background_tasks=MagicMock(spec=BackgroundTasks),
        raw_request=MagicMock(spec=Request),
        user=User(id=USER_ID, username="u", name="u"),
    )


@pytest.fixture
def mocks() -> Iterator[MagicMock]:
    workflow_config = MagicMock()
    workflow_config.mode = WorkflowMode.SEQUENTIAL
    workflow_config.bedrock = None
    with (
        patch(f"{ROUTER}.WorkflowService") as workflow_service,
        patch(f"{ROUTER}.Ability") as ability,
        patch(f"{ROUTER}.request_summary_manager_module"),
        patch(f"{ROUTER}.WorkflowExecutor"),
        patch(f"{ROUTER}.AgentWorkspaceService") as workspace_service,
        patch(f"{ROUTER}._validate_remote_entities_and_raise"),
    ):
        # Keep the real validation; only persistence is mocked.
        real = AgentWorkspaceService.__new__(AgentWorkspaceService)
        real.repository = MagicMock()
        real.file_repository = MagicMock()
        workspace_service.return_value.validate_run_files.side_effect = real.validate_run_files
        workspace_service.real = real
        workflow_service.return_value.get_workflow.return_value = workflow_config
        ability.return_value.can.return_value = True
        yield workspace_service


def _assert_rejected(mocks: MagicMock, file_names: list[str]) -> None:
    with pytest.raises(ExtendedHTTPException) as exc_info:
        _call(file_names, conversation_id="conv-1")
    assert exc_info.value.code == 400
    mocks.return_value.sync_uploaded_files.assert_not_called()


def test_rejects_more_files_than_cap(mocks: MagicMock) -> None:
    with patch.object(config, "WORKFLOW_RUN_FILES_MAX_COUNT", 2):
        _assert_rejected(mocks, [_token(USER_ID, f"f{i}.pdf") for i in range(3)])


def test_rejects_malformed_token(mocks: MagicMock) -> None:
    _assert_rejected(mocks, ["not-a-token"])


def test_rejects_traversal_token(mocks: MagicMock) -> None:
    _assert_rejected(mocks, [_token(USER_ID, "../secret.pdf")])


def test_rejects_foreign_owner_token(mocks: MagicMock) -> None:
    with patch("codemie.service.agent_workspace_service.can_download", return_value=False):
        _assert_rejected(mocks, [_token("someone-else")])


def test_rejects_shared_conversation_file_without_token(mocks: MagicMock) -> None:
    """Share grant (rule E) does not apply at run start: a sharer's workspace file gets a 400."""
    mocks.real.repository.get_by_id_for_user.return_value = None

    _assert_rejected(mocks, [_token("workspace-sharer-ws")])


def test_accepts_own_token_and_seeds_conversation_workspace(mocks: MagicMock) -> None:
    token = _token(USER_ID)

    _call([token], conversation_id="conv-1")

    mocks.return_value.sync_uploaded_files.assert_called_once()
    assert mocks.return_value.sync_uploaded_files.call_args.kwargs["file_urls"] == [token]


def test_accepts_own_workspace_token(mocks: MagicMock) -> None:
    mocks.real.repository.get_by_id_for_user.return_value = MagicMock()

    _call([_token("workspace-ws1")], conversation_id="conv-1")

    mocks.return_value.sync_uploaded_files.assert_called_once()


def test_cap_boundary_is_accepted(mocks: MagicMock) -> None:
    with patch.object(config, "WORKFLOW_RUN_FILES_MAX_COUNT", 2):
        _call([_token(USER_ID, f"f{i}.pdf") for i in range(2)], conversation_id="conv-1")
    mocks.return_value.sync_uploaded_files.assert_called_once()
