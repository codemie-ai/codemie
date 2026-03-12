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

"""
Test suite for delete_on_completion parameter passing through the router layer.

Tests that delete_on_completion is properly forwarded from the API request
to WorkflowExecutor.create_executor() in both background and streaming modes.
"""

from unittest.mock import patch, MagicMock

from codemie.core.workflow_models import CreateWorkflowExecutionRequest, WorkflowExecution
from codemie.core.workflow_models.workflow_config import WorkflowMode, WorkflowConfig
from codemie.rest_api.security.user import User

WORKFLOW_ID = "test_workflow_id"
EXECUTION_ID = "test_execution_id"
USER_ID = "test_user_id"
WORKFLOW_NAME = "example_workflow_name"

WORKFLOW_EXECUTION = WorkflowExecution(workflow_id=WORKFLOW_ID, execution_id=EXECUTION_ID)
WORKFLOW_CONFIG = WorkflowConfig(
    name=WORKFLOW_NAME,
    description="Test workflow description",
    mode=WorkflowMode.SEQUENTIAL,
    project="test_project",
)
USER = User(id=USER_ID)


@patch("codemie.rest_api.routers.workflow_executions._validate_workflow_supports_files_and_raise")
@patch("codemie.rest_api.routers.workflow_executions._validate_remote_entities_and_raise")
@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.routers.workflow_executions.request_summary_manager_module")
@patch("codemie.rest_api.routers.workflow_executions.set_disable_prompt_cache")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowExecutor")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowService")
@patch("codemie.rest_api.security.authentication.authenticate")
def test_create_execution_passes_delete_on_completion_true_background(
    mock_authentication,
    mock_workflow_service,
    mock_workflow_executor,
    mock_set_disable_cache,
    mock_request_summary,
    mock_ability,
    mock_validate_remote,
    mock_validate_files,
):
    """delete_on_completion=True is passed to create_executor in background mode."""
    mock_authentication.return_value = USER

    mock_service_instance = MagicMock()
    mock_service_instance.get_workflow.return_value = WORKFLOW_CONFIG
    mock_service_instance.create_workflow_execution.return_value = WORKFLOW_EXECUTION
    mock_workflow_service.return_value = mock_service_instance

    mock_ability.return_value.can.return_value = True

    mock_executor = MagicMock()
    mock_executor.stream = MagicMock()
    mock_workflow_executor.create_executor.return_value = mock_executor

    request_data = CreateWorkflowExecutionRequest(user_input='test input', delete_on_completion=True)

    from codemie.rest_api.routers.workflow_executions import create_workflow_execution
    from fastapi import BackgroundTasks, Request

    raw_request = MagicMock(spec=Request)
    background_tasks = MagicMock(spec=BackgroundTasks)

    create_workflow_execution(
        request=request_data,
        workflow_id=WORKFLOW_ID,
        background_tasks=background_tasks,
        raw_request=raw_request,
        user=USER,
    )

    mock_workflow_executor.create_executor.assert_called_once()
    call_kwargs = mock_workflow_executor.create_executor.call_args[1]
    assert call_kwargs['delete_on_completion'] is True


@patch("codemie.rest_api.routers.workflow_executions._validate_workflow_supports_files_and_raise")
@patch("codemie.rest_api.routers.workflow_executions._validate_remote_entities_and_raise")
@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.routers.workflow_executions.request_summary_manager_module")
@patch("codemie.rest_api.routers.workflow_executions.set_disable_prompt_cache")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowExecutor")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowService")
@patch("codemie.rest_api.security.authentication.authenticate")
def test_create_execution_passes_delete_on_completion_false_by_default(
    mock_authentication,
    mock_workflow_service,
    mock_workflow_executor,
    mock_set_disable_cache,
    mock_request_summary,
    mock_ability,
    mock_validate_remote,
    mock_validate_files,
):
    """delete_on_completion defaults to False when not provided (backward compatibility)."""
    mock_authentication.return_value = USER

    mock_service_instance = MagicMock()
    mock_service_instance.get_workflow.return_value = WORKFLOW_CONFIG
    mock_service_instance.create_workflow_execution.return_value = WORKFLOW_EXECUTION
    mock_workflow_service.return_value = mock_service_instance

    mock_ability.return_value.can.return_value = True

    mock_executor = MagicMock()
    mock_executor.stream = MagicMock()
    mock_workflow_executor.create_executor.return_value = mock_executor

    request_data = CreateWorkflowExecutionRequest(user_input='test input')

    from codemie.rest_api.routers.workflow_executions import create_workflow_execution
    from fastapi import BackgroundTasks, Request

    raw_request = MagicMock(spec=Request)
    background_tasks = MagicMock(spec=BackgroundTasks)

    create_workflow_execution(
        request=request_data,
        workflow_id=WORKFLOW_ID,
        background_tasks=background_tasks,
        raw_request=raw_request,
        user=USER,
    )

    mock_workflow_executor.create_executor.assert_called_once()
    call_kwargs = mock_workflow_executor.create_executor.call_args[1]
    assert call_kwargs['delete_on_completion'] is False


@patch("codemie.rest_api.routers.workflow_executions._validate_workflow_supports_files_and_raise")
@patch("codemie.rest_api.routers.workflow_executions._validate_remote_entities_and_raise")
@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.routers.workflow_executions.request_summary_manager_module")
@patch("codemie.rest_api.routers.workflow_executions.set_disable_prompt_cache")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowExecutor")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowService")
@patch("codemie.rest_api.security.authentication.authenticate")
def test_create_execution_passes_delete_on_completion_true_streaming(
    mock_authentication,
    mock_workflow_service,
    mock_workflow_executor,
    mock_set_disable_cache,
    mock_request_summary,
    mock_ability,
    mock_validate_remote,
    mock_validate_files,
):
    """delete_on_completion=True is passed to create_executor in streaming mode."""
    mock_authentication.return_value = USER

    mock_service_instance = MagicMock()
    mock_service_instance.get_workflow.return_value = WORKFLOW_CONFIG
    mock_service_instance.create_workflow_execution.return_value = WORKFLOW_EXECUTION
    mock_workflow_service.return_value = mock_service_instance

    mock_ability.return_value.can.return_value = True

    mock_executor = MagicMock()
    mock_executor.stream_to_client = MagicMock()
    mock_workflow_executor.create_executor.return_value = mock_executor

    request_data = CreateWorkflowExecutionRequest(user_input='test input', stream=True, delete_on_completion=True)

    from codemie.rest_api.routers.workflow_executions import create_workflow_execution
    from fastapi import BackgroundTasks, Request

    raw_request = MagicMock(spec=Request)
    raw_request.state = MagicMock()
    background_tasks = MagicMock(spec=BackgroundTasks)

    with patch('codemie.rest_api.routers.workflow_executions.StreamingResponse'):
        create_workflow_execution(
            request=request_data,
            workflow_id=WORKFLOW_ID,
            background_tasks=background_tasks,
            raw_request=raw_request,
            user=USER,
        )

    mock_workflow_executor.create_executor.assert_called_once()
    call_kwargs = mock_workflow_executor.create_executor.call_args[1]
    assert call_kwargs['delete_on_completion'] is True
