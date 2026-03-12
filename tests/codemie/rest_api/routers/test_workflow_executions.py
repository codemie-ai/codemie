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

import datetime
import io
import zipfile
from typing import Dict

import pytest
from bs4 import BeautifulSoup
from fastapi import status, BackgroundTasks
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport, Response
from unittest.mock import patch, MagicMock

from codemie.core.workflow_models import (
    CreateWorkflowExecutionRequest,
    WorkflowExecution,
    WorkflowExecutionState,
    WorkflowExecutionStateResponse,
    WorkflowExecutionStatusEnum,
)
from codemie.core.workflow_models.workflow_config import WorkflowMode, WorkflowConfig
from codemie.rest_api.main import app
from codemie.rest_api.security.user import User

STATE_ID = "some_state_id"

WORKFLOW_NAME = "example_workflow_name"

USER_ID = "test_user_id"
WORKFLOW_ID = "test_workflow_id"
EXECUTION_ID = "test_execution_id"
WORKFLOW_EXECUTION = WorkflowExecution(workflow_id=WORKFLOW_ID, execution_id=EXECUTION_ID)
# Create a proper WorkflowConfig object for mocking
WORKFLOW_CONFIG = WorkflowConfig(
    name=WORKFLOW_NAME,
    description="Test workflow description",
    mode=WorkflowMode.SEQUENTIAL,  # Use SEQUENTIAL to avoid the autonomous check
    project="test_project",
)
USER = User(id=USER_ID)
WORKFLOW_EXECUTIONS_URL = f"/v1/workflows/{WORKFLOW_ID}/executions"
EXPORT_WORKFLOW_EXECUTION_URL = f"{WORKFLOW_EXECUTIONS_URL}/{EXECUTION_ID}/export"
client = TestClient(app)


@pytest.fixture
def request_headers() -> dict:
    return {"user-id": USER_ID, "username": USER.username, "name": USER.name}


@pytest.fixture
def mock_workflow_service() -> MagicMock:
    with patch("codemie.rest_api.routers.workflow_executions.WorkflowService") as mock_service:
        mock_ser = MagicMock()
        WORKFLOW_EXECUTION.name = WORKFLOW_NAME
        # Return WorkflowConfig object, not WorkflowExecution
        mock_ser.get_workflow.return_value = WORKFLOW_CONFIG
        mock_ser.create_workflow_execution.return_value = WORKFLOW_EXECUTION
        mock_service.return_value = mock_ser
        yield mock_service


@pytest.fixture
def mock_authentication(mocker) -> MagicMock:
    yield mocker.patch("codemie.rest_api.security.authentication")


@pytest.fixture
def mock_ability(mocker) -> MagicMock:
    yield mocker.patch("codemie.rest_api.routers.workflow_executions.Ability")


@pytest.fixture
def mock_request_summary_manager(mocker) -> MagicMock:
    yield mocker.patch("codemie.rest_api.routers.workflow_executions.request_summary_manager_module")


@pytest.fixture
def mock_workflow_executor(mocker) -> MagicMock:
    yield mocker.patch("codemie.rest_api.routers.workflow_executions.WorkflowExecutor")


@pytest.fixture
def mock_validate_remote_entities():
    with patch("codemie.rest_api.routers.workflow_executions._validate_remote_entities_and_raise") as mock_validate:
        mock_validate.return_value = None  # No validation errors
        yield mock_validate


@pytest.fixture
def mock_background_tasks() -> MagicMock:
    return MagicMock(spec=BackgroundTasks)


@pytest.mark.usefixtures(
    "mock_workflow_service",
    "mock_request_summary_manager",
    "mock_workflow_executor",
    "mock_background_tasks",
    "mock_validate_remote_entities",
)
@pytest.mark.asyncio
async def test_create_workflow_execution_success(
    mock_authentication: MagicMock,
    mock_ability: MagicMock,
    request_headers: dict,
) -> None:
    mock_ability.can.return_value = True
    mock_authentication.authenticate.return_value = USER
    request = CreateWorkflowExecutionRequest(user_input="input_data")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(WORKFLOW_EXECUTIONS_URL, json=request.model_dump(), headers=request_headers)

    assert response.status_code == 200
    assert response.json()["execution_id"] == WORKFLOW_EXECUTION.execution_id


@pytest.mark.asyncio
async def test_create_workflow_execution_access_denied() -> None:
    request = CreateWorkflowExecutionRequest(user_input="input_data")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(WORKFLOW_EXECUTIONS_URL, json=request.model_dump())

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.usefixtures(
    "mock_workflow_service",
    "mock_request_summary_manager",
    "mock_background_tasks",
    "mock_validate_remote_entities",
)
@pytest.mark.asyncio
async def test_create_workflow_execution_internal_server_error(
    mock_authentication: MagicMock,
    mock_ability: MagicMock,
    request_headers: dict,
    mock_workflow_executor: MagicMock,
):
    expected_error_details = "An unexpected error occurred during workflow execution"
    mock_ability.can.return_value = True
    mock_authentication.authenticate.return_value = USER
    mock_workflow_executor.create_executor.side_effect = ValueError()
    request = CreateWorkflowExecutionRequest(user_input="input_data")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(WORKFLOW_EXECUTIONS_URL, json=request.model_dump(), headers=request_headers)

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert expected_error_details in response.json()["error"]["details"]


@pytest.mark.asyncio
@patch("codemie.rest_api.routers.workflow_executions.WorkflowService.get_workflow")
@patch("codemie.service.workflow_execution.WorkflowExecutionStatesIndexService.run")
async def test_index_workflow_executions_states(
    mock_run: MagicMock,
    mock_get_workflow: MagicMock,
    mock_ability: MagicMock,
    mock_authentication: MagicMock,
    request_headers: dict,
):
    # Given
    workflow_id = "test_workflow_id"
    execution_id = "test_execution_id"
    mock_get_workflow.return_value = True
    mock_ability.can.return_value = True
    mock_authentication.authenticate.return_value = USER

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # When
        await ac.get(f"/v1/workflows/{workflow_id}/executions/{execution_id}/states", headers=request_headers)

    # Then
    mock_run.assert_called_once_with(
        execution_id=execution_id, page=0, per_page=10, retrieve_model=WorkflowExecutionStateResponse
    )


@pytest.fixture
def real_workflow_state():
    return WorkflowExecutionState(
        status=WorkflowExecutionStatusEnum.SUCCEEDED,
        task="task",
        output="output",
        name=STATE_ID,
        execution_id="some_execution_id",
        completed_at=datetime.datetime.strptime("2023-10-10T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"),
    )


@pytest.mark.usefixtures("mock_workflow_service")
@patch("codemie.service.workflow_execution.WorkflowExecutionStatesIndexService.run")
@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.security.authentication.authenticate")
@pytest.mark.asyncio
async def test_export_workflow_execution_without_slash_in_state_name(
    mock_authentication, mock_ability, mock_run, request_headers: dict, real_workflow_state
):
    expected_status_name_lower = WorkflowExecutionStatusEnum.SUCCEEDED.value.lower()
    expected_file_info = f"attachment; filename={WORKFLOW_NAME}_{EXECUTION_ID}.zip"
    expected_file_name = f"2023-10-10_00-00-00.000_{STATE_ID}_{expected_status_name_lower}.md"
    mock_authentication.authenticate.return_value = USER
    mock_ability.return_value.can.return_value = True
    mock_run.return_value = {"data": [real_workflow_state]}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(EXPORT_WORKFLOW_EXECUTION_URL, headers=request_headers)

    assert response.status_code == 200
    assert expected_file_info in response.headers["Content-Disposition"]
    mock_run.assert_called_once_with(
        execution_id=EXECUTION_ID,
        per_page=10000,
        include_thoughts=False,
    )
    with zipfile.ZipFile(io.BytesIO(response.content), 'r') as zip_file:
        assert expected_file_name in zip_file.namelist()
        with zip_file.open(expected_file_name) as file:
            content = file.read().decode()
            assert content == "output"


@patch("codemie.service.workflow_execution.WorkflowExecutionStatesIndexService.run")
@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.security.authentication.authenticate")
@pytest.mark.asyncio
async def test_export_workflow_execution_with_slash_in_state_name(
    mock_authentication, mock_ability, mock_run, request_headers: dict, real_workflow_state, mock_workflow_service
):
    expected_status_name_lower = WorkflowExecutionStatusEnum.SUCCEEDED.value.lower()
    mock_authentication.authenticate.return_value = USER
    mock_ability.return_value.can.return_value = True
    state_name_with_slash = "AI/Run Thoughts"
    expected_file_info = f"attachment; filename={WORKFLOW_NAME.lower()}_{EXECUTION_ID}.zip"
    expected_sanitized_state_name = state_name_with_slash.lower().replace("/", "_")
    expected_sanitized_file_name = (
        f"2023-10-10_00-00-00.000_{expected_sanitized_state_name}_{expected_status_name_lower}.md"
    )
    real_workflow_state.name = state_name_with_slash
    mock_run.return_value = {"data": [real_workflow_state]}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(EXPORT_WORKFLOW_EXECUTION_URL, headers=request_headers)

    assert response.status_code == 200
    assert expected_file_info in response.headers["Content-Disposition"]
    mock_run.assert_called_once_with(execution_id=EXECUTION_ID, per_page=10000, include_thoughts=False)
    # Additional checks to verify the ZIP content
    with zipfile.ZipFile(io.BytesIO(response.content), 'r') as zip_file:
        # Ensure no directories are created
        zip_file_names = zip_file.namelist()
        for name in zip_file_names:
            assert '/' not in name, "Slash not properly escaped"
        assert expected_sanitized_file_name in zip_file.namelist()
        with zip_file.open(expected_sanitized_file_name) as file:
            content = file.read().decode()
            assert content == "output"


async def _verify_zip_content(expected_filename: str, response: Response, output_format: str) -> None:
    # Additional checks to verify the ZIP content
    with zipfile.ZipFile(io.BytesIO(response.content), 'r') as zip_file:
        assert expected_filename in zip_file.namelist()

    with zipfile.ZipFile(io.BytesIO(response.content), 'r') as zip_file:
        with zip_file.open(expected_filename) as file:
            content = file.read().decode()
            md_format_check = "output" in content
            if output_format == "html":
                soup = BeautifulSoup(content, 'html.parser')
                extracted_text = soup.get_text()
                json_format_check = "key" in extracted_text and "value" in extracted_text
            else:
                json_format_check = '"key": "value"' in content

            assert md_format_check or json_format_check


@pytest.mark.parametrize(
    "output_format, combined, expected_filename",
    [
        ("md", True, f"{WORKFLOW_NAME}_{EXECUTION_ID}.md"),
        ("md", False, f"2023-10-10_00-00-00.000_{STATE_ID}_{WorkflowExecutionStatusEnum.SUCCEEDED.value.lower()}.md"),
        ("html", True, f"{WORKFLOW_NAME}_{EXECUTION_ID}.html"),
        (
            "html",
            False,
            f"2023-10-10_00-00-00.000_{STATE_ID}_{WorkflowExecutionStatusEnum.SUCCEEDED.value.lower()}.html",
        ),
        # Add default case explicitly
        ("md", False, f"2023-10-10_00-00-00.000_{STATE_ID}_{WorkflowExecutionStatusEnum.SUCCEEDED.value.lower()}.md"),
    ],
    ids=("combined_md", "individual_md", "combined_html", "individual_html", "default_case"),
)
@pytest.mark.usefixtures("mock_workflow_service")
@patch("codemie.service.workflow_execution.WorkflowExecutionStatesIndexService.run")
@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.security.authentication.authenticate")
@pytest.mark.asyncio
async def test_export_workflow_execution(
    mock_authentication: MagicMock,
    mock_ability: MagicMock,
    mock_run: MagicMock,
    request_headers: Dict[str, str],
    real_workflow_state: WorkflowExecutionState,
    output_format: str,
    combined: bool,
    expected_filename: str,
) -> None:
    mock_authentication.authenticate.return_value = USER
    mock_ability.return_value.can.return_value = True
    mock_run.return_value = {"data": [real_workflow_state]}
    expected_file_info = f"attachment; filename={WORKFLOW_NAME}_{EXECUTION_ID}.zip"
    export_url = f"{EXPORT_WORKFLOW_EXECUTION_URL}?output_format={output_format}&combined={str(combined).lower()}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(export_url, headers=request_headers)

    assert response.status_code == 200
    assert expected_file_info in response.headers["Content-Disposition"]
    mock_run.assert_called_once_with(execution_id=EXECUTION_ID, per_page=10000, include_thoughts=False)
    await _verify_zip_content(expected_filename, response, output_format)


@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.security.authentication.authenticate")
@patch("codemie.service.workflow_service.WorkflowService.get_workflow")
@patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowUpdateOutputService.run")
@pytest.mark.asyncio
async def test_update_workflow_execution_output(
    mock_update_output_service,
    mock_get_wf,
    mock_authentication,
    mock_ability,
    request_headers,
):
    mock_authentication.authenticate.return_value = USER
    mock_ability.return_value.can.return_value = True
    mock_update_output_service.return_value = True

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put(
            f"{WORKFLOW_EXECUTIONS_URL}/test_execution/output",
            json={
                "output": "New Output",
                "state_id": "Test State ID",
            },
            headers=request_headers,
        )

        assert response.json()['message'] == "Workflow execution output has been edited successfully"
        mock_update_output_service.assert_called_once_with(
            execution_id="test_execution", state_id="Test State ID", new_output="New Output"
        )


@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.security.authentication.authenticate")
@patch("codemie.service.workflow_service.WorkflowService.get_workflow")
@patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowUpdateOutputService.run")
@pytest.mark.asyncio
async def test_update_workflow_execution_output_no_permissions(
    mock_update_output_service,
    mock_get_wf,
    mock_authentication,
    mock_ability,
    request_headers,
):
    mock_authentication.authenticate.return_value = USER
    mock_ability.return_value.can.return_value = False

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put(
            f"{WORKFLOW_EXECUTIONS_URL}/test_execution/output",
            json={
                "output": "New Output 2",
                "state_id": "Test State ID 2",
            },
            headers=request_headers,
        )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Access denied"


@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.security.authentication.authenticate")
@patch("codemie.service.workflow_service.WorkflowService.get_workflow")
@patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowUpdateOutputService.run")
@pytest.mark.asyncio
async def test_update_workflow_execution_output_error(
    mock_update_output_service,
    mock_get_wf,
    mock_authentication,
    mock_ability,
    request_headers,
):
    mock_authentication.authenticate.return_value = USER
    mock_ability.return_value.can.return_value = True
    mock_update_output_service.side_effect = Exception("Error")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put(
            f"{WORKFLOW_EXECUTIONS_URL}/test_execution/output",
            json={
                "output": "New Output 2",
                "state_id": "Test State ID 2",
            },
            headers=request_headers,
        )

    assert response.status_code == 500
    assert response.json()["error"]["message"] == "Workflow Output Editing Error"


@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.security.authentication.authenticate")
@patch("codemie.service.workflow_service.WorkflowService.get_workflow")
@patch(
    "codemie.service.workflow_execution.workflow_output_change_request_service.WorkflowOutputChangeRequestService.run"
)
@pytest.mark.asyncio
async def test_request_workflow_execution_output_changes(
    mock_changes_service,
    mock_get_wf,
    mock_authentication,
    mock_ability,
    request_headers,
):
    mock_authentication.authenticate.return_value = USER
    mock_ability.return_value.can.return_value = True
    mock_changes_service.return_value = "New Output"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put(
            f"{WORKFLOW_EXECUTIONS_URL}/test_execution/output/request_changes",
            json={
                "original_output": "New Output",
                "request": "Change the output",
            },
            headers=request_headers,
        )

        assert response.status_code == 200
        assert response.json()['message'] == "New Output"
        mock_changes_service.assert_called_once_with(original_output="New Output", changes_request="Change the output")


@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.security.authentication.authenticate")
@patch("codemie.service.workflow_service.WorkflowService.get_workflow")
@patch(
    "codemie.service.workflow_execution.workflow_output_change_request_service.WorkflowOutputChangeRequestService.run"
)
@pytest.mark.asyncio
async def test_request_workflow_execution_output_changes_no_permissions(
    mock_changes_service,
    mock_get_wf,
    mock_authentication,
    mock_ability,
    request_headers,
):
    mock_authentication.authenticate.return_value = USER
    mock_ability.return_value.can.return_value = False

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put(
            f"{WORKFLOW_EXECUTIONS_URL}/test_execution/output/request_changes",
            json={
                "original_output": "New Output",
                "request": "Change the output",
            },
            headers=request_headers,
        )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Access denied"


@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.security.authentication.authenticate")
@patch("codemie.service.workflow_service.WorkflowService.get_workflow")
@patch(
    "codemie.service.workflow_execution.workflow_output_change_request_service.WorkflowOutputChangeRequestService.run"
)
@pytest.mark.asyncio
async def test_request_workflow_execution_output_changes_error(
    mock_changes_service,
    mock_get_wf,
    mock_authentication,
    mock_ability,
    request_headers,
):
    mock_authentication.authenticate.return_value = USER
    mock_ability.return_value.can.return_value = True
    mock_changes_service.side_effect = Exception("Error")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put(
            f"{WORKFLOW_EXECUTIONS_URL}/test_execution/output/request_changes",
            json={
                "original_output": "New Output",
                "request": "Change the output",
            },
            headers=request_headers,
        )

    assert response.status_code == 500
    assert response.json()["error"]["message"] == "Workflow Output Change Request Error"


@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.security.authentication.authenticate")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowService")
@pytest.mark.asyncio
async def test_delete_workflow_execution_with_chat_id_fails(
    mock_workflow_service,
    mock_authentication,
    mock_ability,
    request_headers,
):
    """Test that deleting a workflow execution with conversation_id is blocked"""
    mock_authentication.return_value = USER
    mock_ability.return_value.can.return_value = True

    execution_with_chat = WorkflowExecution(
        workflow_id=WORKFLOW_ID, execution_id=EXECUTION_ID, conversation_id="test-chat-id"
    )
    mock_workflow_service.return_value.find_workflow_execution_by_id.return_value = execution_with_chat

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.delete(
            f"{WORKFLOW_EXECUTIONS_URL}/{EXECUTION_ID}",
            headers=request_headers,
        )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Cannot delete workflow execution"
    assert "conversation" in response.json()["error"]["details"].lower()


@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.security.authentication.authenticate")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowService")
@pytest.mark.asyncio
async def test_delete_workflow_execution_without_chat_id_succeeds(
    mock_workflow_service,
    mock_authentication,
    mock_ability,
    request_headers,
):
    """Test that deleting a workflow execution without conversation_id succeeds"""
    mock_authentication.return_value = USER
    mock_ability.return_value.can.return_value = True

    execution_without_chat = WorkflowExecution(workflow_id=WORKFLOW_ID, execution_id=EXECUTION_ID, conversation_id=None)
    mock_workflow_service.return_value.find_workflow_execution_by_id.return_value = execution_without_chat
    mock_workflow_service.return_value.delete_workflow_execution.return_value = {"status": "deleted"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.delete(
            f"{WORKFLOW_EXECUTIONS_URL}/{EXECUTION_ID}",
            headers=request_headers,
        )

    assert response.status_code == 200
    assert response.json()["message"] == "Execution removed"
    mock_workflow_service.return_value.delete_workflow_execution.assert_called_once()


@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.security.authentication.authenticate")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowService")
@pytest.mark.asyncio
async def test_delete_workflow_execution_not_found(
    mock_workflow_service,
    mock_authentication,
    mock_ability,
    request_headers,
):
    """Test that deleting a non-existent workflow execution returns 404"""
    mock_authentication.return_value = USER
    mock_ability.return_value.can.return_value = True
    mock_workflow_service.return_value.find_workflow_execution_by_id.return_value = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.delete(
            f"{WORKFLOW_EXECUTIONS_URL}/{EXECUTION_ID}",
            headers=request_headers,
        )

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Execution you are trying to delete is Not Found"


@patch("codemie.rest_api.routers.workflow_executions._validate_workflow_supports_files_and_raise")
@patch("codemie.rest_api.routers.workflow_executions._validate_remote_entities_and_raise")
@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.routers.workflow_executions.request_summary_manager_module")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowExecutor")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowService")
@patch("codemie.rest_api.security.authentication.authenticate")
def test_create_workflow_execution_with_session_id(
    mock_authentication,
    mock_workflow_service,
    mock_workflow_executor,
    mock_request_summary,
    mock_ability,
    mock_validate_remote,
    mock_validate_files,
    request_headers,
    mock_background_tasks,
):
    """Test that session_id is properly passed to WorkflowExecutor when provided."""
    # Arrange
    mock_authentication.return_value = USER
    mock_workflow_service_instance = MagicMock()
    mock_workflow_service_instance.get_workflow.return_value = WORKFLOW_CONFIG
    mock_workflow_service_instance.create_workflow_execution.return_value = WORKFLOW_EXECUTION
    mock_workflow_service.return_value = mock_workflow_service_instance

    mock_ability_instance = MagicMock()
    mock_ability_instance.can.return_value = True
    mock_ability.return_value = mock_ability_instance

    mock_executor = MagicMock()
    mock_executor.stream = MagicMock()
    mock_workflow_executor.create_executor.return_value = mock_executor

    # Create request with session_id
    request_data = CreateWorkflowExecutionRequest(user_input='test input', session_id='custom-session-123')

    # Act
    from codemie.rest_api.routers.workflow_executions import create_workflow_execution
    from fastapi import Request

    raw_request = MagicMock(spec=Request)
    create_workflow_execution(
        request=request_data,
        workflow_id=WORKFLOW_ID,
        background_tasks=mock_background_tasks,
        raw_request=raw_request,
        user=USER,
    )

    # Assert - Verify WorkflowExecutor.create_executor was called with session_id
    mock_workflow_executor.create_executor.assert_called_once()
    call_kwargs = mock_workflow_executor.create_executor.call_args[1]
    assert 'session_id' in call_kwargs
    assert call_kwargs['session_id'] == 'custom-session-123'


@patch("codemie.rest_api.routers.workflow_executions._validate_workflow_supports_files_and_raise")
@patch("codemie.rest_api.routers.workflow_executions._validate_remote_entities_and_raise")
@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.routers.workflow_executions.request_summary_manager_module")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowExecutor")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowService")
@patch("codemie.rest_api.security.authentication.authenticate")
def test_create_workflow_execution_without_session_id(
    mock_authentication,
    mock_workflow_service,
    mock_workflow_executor,
    mock_request_summary,
    mock_ability,
    mock_validate_remote,
    mock_validate_files,
    request_headers,
    mock_background_tasks,
):
    """Test backward compatibility: session_id=None when not provided."""
    # Arrange
    mock_authentication.return_value = USER
    mock_workflow_service_instance = MagicMock()
    mock_workflow_service_instance.get_workflow.return_value = WORKFLOW_CONFIG
    mock_workflow_service_instance.create_workflow_execution.return_value = WORKFLOW_EXECUTION
    mock_workflow_service.return_value = mock_workflow_service_instance

    mock_ability_instance = MagicMock()
    mock_ability_instance.can.return_value = True
    mock_ability.return_value = mock_ability_instance

    mock_executor = MagicMock()
    mock_executor.stream = MagicMock()
    mock_workflow_executor.create_executor.return_value = mock_executor

    # Create request WITHOUT session_id (backward compatibility)
    request_data = CreateWorkflowExecutionRequest(
        user_input='test input'
        # No session_id provided
    )

    # Act
    from codemie.rest_api.routers.workflow_executions import create_workflow_execution
    from fastapi import Request

    raw_request = MagicMock(spec=Request)
    create_workflow_execution(
        request=request_data,
        workflow_id=WORKFLOW_ID,
        background_tasks=mock_background_tasks,
        raw_request=raw_request,
        user=USER,
    )

    # Assert - Verify session_id is None (backward compatible)
    mock_workflow_executor.create_executor.assert_called_once()
    call_kwargs = mock_workflow_executor.create_executor.call_args[1]
    assert 'session_id' in call_kwargs
    assert call_kwargs['session_id'] is None


@patch("codemie.rest_api.routers.workflow_executions._validate_workflow_supports_files_and_raise")
@patch("codemie.rest_api.routers.workflow_executions._validate_remote_entities_and_raise")
@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.routers.workflow_executions.request_summary_manager_module")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowExecutor")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowService")
@patch("codemie.rest_api.security.authentication.authenticate")
def test_create_workflow_execution_session_id_with_streaming(
    mock_authentication,
    mock_workflow_service,
    mock_workflow_executor,
    mock_request_summary,
    mock_ability,
    mock_validate_remote,
    mock_validate_files,
    request_headers,
    mock_background_tasks,
):
    """Test that session_id works correctly with streaming mode."""
    # Arrange
    mock_authentication.return_value = USER
    mock_workflow_service_instance = MagicMock()
    mock_workflow_service_instance.get_workflow.return_value = WORKFLOW_CONFIG
    mock_workflow_service_instance.create_workflow_execution.return_value = WORKFLOW_EXECUTION
    mock_workflow_service.return_value = mock_workflow_service_instance

    mock_ability_instance = MagicMock()
    mock_ability_instance.can.return_value = True
    mock_ability.return_value = mock_ability_instance

    mock_executor = MagicMock()
    mock_executor.stream_to_client = MagicMock()
    mock_workflow_executor.create_executor.return_value = mock_executor

    # Create request with session_id and stream=True
    request_data = CreateWorkflowExecutionRequest(
        user_input='test input', session_id='streaming-session-456', stream=True
    )

    # Act
    from codemie.rest_api.routers.workflow_executions import create_workflow_execution
    from fastapi import Request

    raw_request = MagicMock(spec=Request)
    raw_request.state = MagicMock()

    # Mock the streaming response behavior
    with patch('codemie.rest_api.routers.workflow_executions.StreamingResponse'):
        create_workflow_execution(
            request=request_data,
            workflow_id=WORKFLOW_ID,
            background_tasks=mock_background_tasks,
            raw_request=raw_request,
            user=USER,
        )

    # Assert - Verify session_id passed in streaming mode
    mock_workflow_executor.create_executor.assert_called_once()
    call_kwargs = mock_workflow_executor.create_executor.call_args[1]
    assert 'session_id' in call_kwargs
    assert call_kwargs['session_id'] == 'streaming-session-456'
    assert call_kwargs['thought_queue'] is not None  # DualQueue in streaming mode


# ===== Tags Tests (PROJ-10326) =====


@patch("codemie.rest_api.routers.workflow_executions._validate_workflow_supports_files_and_raise")
@patch("codemie.rest_api.routers.workflow_executions._validate_remote_entities_and_raise")
@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.routers.workflow_executions.request_summary_manager_module")
@patch("codemie.rest_api.routers.workflow_executions.set_disable_prompt_cache")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowExecutor")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowService")
@patch("codemie.rest_api.security.authentication.authenticate")
def test_create_workflow_execution_with_tags(
    mock_authentication,
    mock_workflow_service,
    mock_workflow_executor,
    mock_set_disable_cache,
    mock_request_summary,
    mock_ability,
    mock_validate_remote,
    mock_validate_files,
    request_headers,
    mock_background_tasks,
):
    """Test that tags are properly passed to WorkflowExecutor when provided."""
    mock_authentication.return_value = USER
    mock_workflow_service_instance = MagicMock()
    mock_workflow_service_instance.get_workflow.return_value = WORKFLOW_CONFIG
    mock_workflow_service_instance.create_workflow_execution.return_value = WORKFLOW_EXECUTION
    mock_workflow_service.return_value = mock_workflow_service_instance

    mock_ability_instance = MagicMock()
    mock_ability_instance.can.return_value = True
    mock_ability.return_value = mock_ability_instance

    mock_executor = MagicMock()
    mock_executor.stream = MagicMock()
    mock_workflow_executor.create_executor.return_value = mock_executor

    request_data = CreateWorkflowExecutionRequest(user_input='test input', tags=['experiment', 'customer_X'])

    from codemie.rest_api.routers.workflow_executions import create_workflow_execution
    from fastapi import Request

    raw_request = MagicMock(spec=Request)
    create_workflow_execution(
        request=request_data,
        workflow_id=WORKFLOW_ID,
        background_tasks=mock_background_tasks,
        raw_request=raw_request,
        user=USER,
    )

    mock_workflow_executor.create_executor.assert_called_once()
    call_kwargs = mock_workflow_executor.create_executor.call_args[1]
    assert 'tags' in call_kwargs
    assert call_kwargs['tags'] == ['experiment', 'customer_X']


@patch("codemie.rest_api.routers.workflow_executions._validate_workflow_supports_files_and_raise")
@patch("codemie.rest_api.routers.workflow_executions._validate_remote_entities_and_raise")
@patch("codemie.rest_api.routers.workflow_executions.Ability")
@patch("codemie.rest_api.routers.workflow_executions.request_summary_manager_module")
@patch("codemie.rest_api.routers.workflow_executions.set_disable_prompt_cache")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowExecutor")
@patch("codemie.rest_api.routers.workflow_executions.WorkflowService")
@patch("codemie.rest_api.security.authentication.authenticate")
def test_create_workflow_execution_without_tags(
    mock_authentication,
    mock_workflow_service,
    mock_workflow_executor,
    mock_set_disable_cache,
    mock_request_summary,
    mock_ability,
    mock_validate_remote,
    mock_validate_files,
    request_headers,
    mock_background_tasks,
):
    """Test backward compatibility: tags=None when not provided."""
    mock_authentication.return_value = USER
    mock_workflow_service_instance = MagicMock()
    mock_workflow_service_instance.get_workflow.return_value = WORKFLOW_CONFIG
    mock_workflow_service_instance.create_workflow_execution.return_value = WORKFLOW_EXECUTION
    mock_workflow_service.return_value = mock_workflow_service_instance

    mock_ability_instance = MagicMock()
    mock_ability_instance.can.return_value = True
    mock_ability.return_value = mock_ability_instance

    mock_executor = MagicMock()
    mock_executor.stream = MagicMock()
    mock_workflow_executor.create_executor.return_value = mock_executor

    request_data = CreateWorkflowExecutionRequest(user_input='test input')

    from codemie.rest_api.routers.workflow_executions import create_workflow_execution
    from fastapi import Request

    raw_request = MagicMock(spec=Request)
    create_workflow_execution(
        request=request_data,
        workflow_id=WORKFLOW_ID,
        background_tasks=mock_background_tasks,
        raw_request=raw_request,
        user=USER,
    )

    mock_workflow_executor.create_executor.assert_called_once()
    call_kwargs = mock_workflow_executor.create_executor.call_args[1]
    assert 'tags' in call_kwargs
    assert call_kwargs['tags'] is None
