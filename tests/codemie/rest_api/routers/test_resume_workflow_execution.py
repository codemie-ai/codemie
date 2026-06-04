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

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock

from codemie.core.workflow_models.workflow_execution import ResumeWorkflowExecutionRequest
from codemie.rest_api.main import app
from codemie.rest_api.security.user import User

WORKFLOW_ID = 'test_workflow_id'
EXECUTION_ID = 'test_execution_id'
USER_ID = 'test_user_id'
USER = User(id=USER_ID)
RESUME_URL = f'/v1/workflows/{WORKFLOW_ID}/executions/{EXECUTION_ID}/resume'


def test_resume_request_accepts_file_names():
    req = ResumeWorkflowExecutionRequest(file_names=['encoded-url-1', 'encoded-url-2'])
    assert req.file_names == ['encoded-url-1', 'encoded-url-2']


def test_resume_request_file_names_defaults_to_none():
    req = ResumeWorkflowExecutionRequest()
    assert req.file_names is None


def test_resume_request_file_names_accepts_empty_list():
    req = ResumeWorkflowExecutionRequest(file_names=[])
    assert req.file_names == []


@patch('codemie.rest_api.routers.workflow_executions.WorkflowExecutor')
@patch('codemie.rest_api.routers.workflow_executions.Ability')
@patch('codemie.rest_api.security.authentication.authenticate')
@patch('codemie.rest_api.routers.workflow_executions.WorkflowService')
@patch('codemie.rest_api.routers.workflow_executions.request_summary_manager_module')
@pytest.mark.asyncio
async def test_resume_router_passes_file_names_to_executor(
    mock_request_summary_manager,
    mock_workflow_service_cls,
    mock_authenticate,
    mock_ability_cls,
    mock_workflow_executor_cls,
):
    """Verify file_names from request body are forwarded to WorkflowExecutor.create_executor."""
    mock_authenticate.return_value = USER
    mock_ability_cls.return_value.can.return_value = True

    mock_service = MagicMock()
    mock_service.get_workflow.return_value = MagicMock(mode='sequential', project='test')
    mock_execution = MagicMock()
    mock_execution.execution_id = EXECUTION_ID
    mock_execution.history = []
    mock_service.find_workflow_execution_by_id.return_value = mock_execution
    mock_workflow_service_cls.return_value = mock_service

    mock_executor_instance = MagicMock()
    mock_workflow_executor_cls.create_executor.return_value = mock_executor_instance

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as ac:
        response = await ac.put(
            RESUME_URL,
            json={'user_input': 'my message', 'file_names': ['url-1', 'url-2']},
            headers={'user-id': USER_ID},
        )

    assert response.status_code == 200
    mock_workflow_executor_cls.create_executor.assert_called_once()
    assert mock_workflow_executor_cls.create_executor.call_args.kwargs['file_names'] == ['url-1', 'url-2']


@patch('codemie.rest_api.routers.workflow_executions.WorkflowExecutor')
@patch('codemie.rest_api.routers.workflow_executions.Ability')
@patch('codemie.rest_api.security.authentication.authenticate')
@patch('codemie.rest_api.routers.workflow_executions.WorkflowService')
@patch('codemie.rest_api.routers.workflow_executions.request_summary_manager_module')
@pytest.mark.asyncio
async def test_resume_router_passes_empty_file_names_when_none(
    mock_request_summary_manager,
    mock_workflow_service_cls,
    mock_authenticate,
    mock_ability_cls,
    mock_workflow_executor_cls,
):
    """Verify that when file_names is omitted, an empty list is passed to create_executor."""
    mock_authenticate.return_value = USER
    mock_ability_cls.return_value.can.return_value = True

    mock_service = MagicMock()
    mock_service.get_workflow.return_value = MagicMock(mode='sequential', project='test')
    mock_execution = MagicMock()
    mock_execution.execution_id = EXECUTION_ID
    mock_execution.history = []
    mock_service.find_workflow_execution_by_id.return_value = mock_execution
    mock_workflow_service_cls.return_value = mock_service

    mock_executor_instance = MagicMock()
    mock_workflow_executor_cls.create_executor.return_value = mock_executor_instance

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as ac:
        response = await ac.put(
            RESUME_URL,
            json={'user_input': 'msg'},
            headers={'user-id': USER_ID},
        )

    assert response.status_code == 200
    mock_workflow_executor_cls.create_executor.assert_called_once()
    assert mock_workflow_executor_cls.create_executor.call_args.kwargs['file_names'] == []
