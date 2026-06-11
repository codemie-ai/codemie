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

"""Integration-like tests for marketplace usage tracking in create_workflow_execution.

Only the repository layer is mocked; the router → service path runs for real.
BackgroundTasks run synchronously via TestClient.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from codemie.core.workflow_models import CreateWorkflowExecutionRequest, WorkflowExecution
from codemie.core.workflow_models.workflow_config import WorkflowConfig, WorkflowMode
from codemie.rest_api.main import app
from codemie.rest_api.security.user import User

_WORKFLOW_ID = "wf-marketplace-123"
_USER_ID = "user-marketplace-456"
_PROJECT = "marketplace-project"

_GLOBAL_WORKFLOW = WorkflowConfig(
    id=_WORKFLOW_ID,
    name="Global Workflow",
    description="Published to marketplace",
    mode=WorkflowMode.SEQUENTIAL,
    project=_PROJECT,
    is_global=True,
)
_PRIVATE_WORKFLOW = WorkflowConfig(
    id=_WORKFLOW_ID,
    name="Private Workflow",
    description="Not published",
    mode=WorkflowMode.SEQUENTIAL,
    project=_PROJECT,
    is_global=False,
)

_USER = User(id=_USER_ID, project_names=[_PROJECT])
assert _USER.current_project == _PROJECT
_EXECUTION = WorkflowExecution(workflow_id=_WORKFLOW_ID, execution_id="exec-marketplace-id")

_URL = f"/v1/workflows/{_WORKFLOW_ID}/executions"
_HEADERS = {"user-id": _USER_ID, "username": "testuser", "name": "Test User"}

_CONFIG_REPO = "codemie.service.workflow_config.workflow_marketplace_service._workflow_config_repository"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def mock_infrastructure(mocker: MagicMock) -> None:
    """Patch everything except the repository layer."""
    mocker.patch(
        "codemie.rest_api.routers.workflow_executions.WorkflowService.get_workflow",
        return_value=_GLOBAL_WORKFLOW,
    )
    mocker.patch(
        "codemie.rest_api.routers.workflow_executions.WorkflowService.create_workflow_execution",
        return_value=_EXECUTION,
    )
    mocker.patch(
        "codemie.rest_api.routers.workflow_executions.Ability",
    ).return_value.can.return_value = True
    mocker.patch("codemie.rest_api.routers.workflow_executions.request_summary_manager_module")
    mocker.patch("codemie.rest_api.routers.workflow_executions._validate_remote_entities_and_raise")
    mocker.patch("codemie.rest_api.routers.workflow_executions.WorkflowExecutor")
    mocker.patch(
        "codemie.rest_api.security.authentication.authenticate",
        return_value=_USER,
    )


@pytest.mark.usefixtures("mock_infrastructure")
@patch(_CONFIG_REPO)
def test_track_usage_called_for_global_workflow(
    mock_config_repo: MagicMock,
    client: TestClient,
) -> None:
    """When workflow is_global=True, recompute_unique_users_count must be called."""
    response = client.post(
        _URL,
        json=CreateWorkflowExecutionRequest(user_input="hello").model_dump(),
        headers=_HEADERS,
    )

    assert response.status_code == 200
    mock_config_repo.recompute_unique_users_count.assert_called_once_with(str(_GLOBAL_WORKFLOW.id))


@pytest.mark.usefixtures("mock_infrastructure")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowService.get_workflow", return_value=_PRIVATE_WORKFLOW)
@patch(
    "codemie.rest_api.routers.workflow_executions.WorkflowService.create_workflow_execution", return_value=_EXECUTION
)
@patch(_CONFIG_REPO)
def test_track_usage_not_called_for_private_workflow(
    mock_config_repo: MagicMock,
    _mock_create: MagicMock,
    _mock_get: MagicMock,
    client: TestClient,
) -> None:
    """When workflow is_global=False, recompute_unique_users_count must not be called."""
    response = client.post(
        _URL,
        json=CreateWorkflowExecutionRequest(user_input="hello").model_dump(),
        headers=_HEADERS,
    )

    assert response.status_code == 200
    mock_config_repo.recompute_unique_users_count.assert_not_called()
