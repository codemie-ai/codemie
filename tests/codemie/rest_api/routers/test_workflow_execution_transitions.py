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
Test Area: Workflow Execution Transitions API

Tests for workflow execution transition endpoints:
- GET /v1/workflows/{workflow_id}/executions/{execution_id}/transitions
- GET /v1/workflows/{workflow_id}/executions/{execution_id}/transitions/from/{state_id}
- GET /v1/workflows/{workflow_id}/executions/{execution_id}/transitions/to/{state_id}
"""

import pytest
from datetime import datetime
from fastapi import status
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from codemie.rest_api.main import app
from codemie.rest_api.security.user import User
from codemie.core.workflow_models import WorkflowExecutionTransitionResponse
from codemie.core.workflow_models.workflow_config import WorkflowMode, WorkflowConfig

USER_ID = "test_user_id"
WORKFLOW_ID = "test_workflow_id"
EXECUTION_ID = "test_execution_id"
WORKFLOW_NAME = "test_workflow"
STATE_ID_FROM = "uuid-state-from"
STATE_ID_TO = "uuid-state-to"

WORKFLOW_CONFIG = WorkflowConfig(
    id=WORKFLOW_ID,
    name=WORKFLOW_NAME,
    description="Test workflow",
    mode=WorkflowMode.SEQUENTIAL,
    project="test_project",
)

USER = User(id=USER_ID)

client = TestClient(app)


@pytest.fixture
def request_headers() -> dict:
    """Create request headers with user authentication."""
    return {"user-id": USER_ID, "username": USER.username, "name": USER.name}


@pytest.fixture
def mock_workflow_service() -> MagicMock:
    """Mock WorkflowService."""
    with patch("codemie.rest_api.routers.workflow_executions.WorkflowService") as mock_service:
        # Create mock execution that belongs to the workflow
        mock_execution = MagicMock()
        mock_execution.id = EXECUTION_ID
        mock_execution.execution_id = EXECUTION_ID
        mock_execution.workflow_id = WORKFLOW_ID  # Critical: must match workflow_id in URL

        mock_ser = MagicMock()
        mock_ser.get_workflow.return_value = WORKFLOW_CONFIG
        mock_ser.find_workflow_execution_by_id.return_value = mock_execution
        mock_service.return_value = mock_ser
        yield mock_service


@pytest.fixture
def mock_ability_allow():
    """Mock Ability to allow access."""
    with patch("codemie.rest_api.routers.workflow_executions.Ability") as mock_ability:
        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance
        yield mock_ability


@pytest.fixture
def mock_ability_deny():
    """Mock Ability to deny access."""
    with patch("codemie.rest_api.routers.workflow_executions.Ability") as mock_ability:
        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = False
        mock_ability.return_value = mock_ability_instance
        yield mock_ability


@pytest.fixture
def mock_transitions_service():
    """Mock WorkflowExecutionTransitionsIndexService."""
    with patch("codemie.rest_api.routers.workflow_executions.WorkflowExecutionTransitionsIndexService") as mock_service:
        yield mock_service


# GET /workflows/{workflow_id}/executions/{execution_id}/transitions


def test_get_transitions_success(request_headers, mock_workflow_service, mock_ability_allow, mock_transitions_service):
    """Test successful retrieval of workflow execution transitions."""
    # Mock service response
    mock_transitions_data = [
        WorkflowExecutionTransitionResponse(
            id="trans_1",
            execution_id=EXECUTION_ID,
            from_state_id="uuid-node-a",
            to_state_id="uuid-node-b",
            workflow_context={"messages": ["test"]},
            date=datetime(2021, 8, 1, 12, 0, 0),
        ),
        WorkflowExecutionTransitionResponse(
            id="trans_2",
            execution_id=EXECUTION_ID,
            from_state_id="uuid-node-b",
            to_state_id="uuid-node-c",
            workflow_context={"messages": ["test2"]},
            date=datetime(2021, 8, 1, 12, 1, 0),
        ),
    ]

    mock_transitions_service.run.return_value = {
        "data": mock_transitions_data,
        "pagination": {"page": 0, "per_page": 10, "total": 2, "pages": 1},
    }

    # Make request
    response = client.get(
        f"/v1/workflows/{WORKFLOW_ID}/executions/{EXECUTION_ID}/transitions",
        headers=request_headers,
    )

    # Assertions
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "data" in data
    assert "pagination" in data
    assert len(data["data"]) == 2
    assert data["data"][0]["from_state_id"] == "uuid-node-a"
    assert data["data"][0]["to_state_id"] == "uuid-node-b"
    assert data["pagination"]["total"] == 2

    # Verify service was called correctly
    mock_transitions_service.run.assert_called_once_with(
        execution_id=EXECUTION_ID,
        page=0,
        per_page=10,
    )


def test_get_transitions_with_pagination(
    request_headers, mock_workflow_service, mock_ability_allow, mock_transitions_service
):
    """Test transitions retrieval with pagination parameters."""
    mock_transitions_service.run.return_value = {
        "data": [],
        "pagination": {"page": 2, "per_page": 5, "total": 15, "pages": 3},
    }

    response = client.get(
        f"/v1/workflows/{WORKFLOW_ID}/executions/{EXECUTION_ID}/transitions?page=2&per_page=5",
        headers=request_headers,
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["pagination"]["page"] == 2
    assert data["pagination"]["per_page"] == 5

    mock_transitions_service.run.assert_called_once_with(
        execution_id=EXECUTION_ID,
        page=2,
        per_page=5,
    )


def test_get_transitions_workflow_not_found(request_headers, mock_ability_allow):
    """Test 404 response when workflow doesn't exist."""
    with patch("codemie.rest_api.routers.workflow_executions.WorkflowService") as mock_service:
        mock_ser = MagicMock()
        mock_ser.get_workflow.side_effect = KeyError("Workflow not found")
        mock_service.return_value = mock_ser

        response = client.get(
            f"/v1/workflows/nonexistent/executions/{EXECUTION_ID}/transitions",
            headers=request_headers,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert "error" in data
        assert data["error"]["message"] == "Workflow Not Found"


def test_get_transitions_forbidden(request_headers, mock_workflow_service, mock_ability_deny):
    """Test 401 response when user lacks READ permission."""
    response = client.get(
        f"/v1/workflows/{WORKFLOW_ID}/executions/{EXECUTION_ID}/transitions",
        headers=request_headers,
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert "error" in data
    assert data["error"]["message"] == "Access denied"


def test_get_transitions_empty_results(
    request_headers, mock_workflow_service, mock_ability_allow, mock_transitions_service
):
    """Test empty result set returns proper response."""
    mock_transitions_service.run.return_value = {
        "data": [],
        "pagination": {"page": 0, "per_page": 10, "total": 0, "pages": 0},
    }

    response = client.get(
        f"/v1/workflows/{WORKFLOW_ID}/executions/{EXECUTION_ID}/transitions",
        headers=request_headers,
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["data"] == []
    assert data["pagination"]["total"] == 0


def test_get_transitions_preserves_workflow_context(
    request_headers, mock_workflow_service, mock_ability_allow, mock_transitions_service
):
    """Test that complex workflow_context is preserved in response."""
    complex_context = {
        "messages": ["msg1", "msg2"],
        "context_store": {"key1": "val1"},
        "nested": {"deep": {"data": [1, 2, 3]}},
    }

    mock_transitions_data = [
        WorkflowExecutionTransitionResponse(
            id="trans_1",
            execution_id=EXECUTION_ID,
            from_state_id="uuid-node-a",
            to_state_id="uuid-node-b",
            workflow_context=complex_context,
            date=datetime(2021, 8, 1, 12, 0, 0),
        ),
    ]

    mock_transitions_service.run.return_value = {
        "data": mock_transitions_data,
        "pagination": {"page": 0, "per_page": 10, "total": 1, "pages": 1},
    }

    response = client.get(
        f"/v1/workflows/{WORKFLOW_ID}/executions/{EXECUTION_ID}/transitions",
        headers=request_headers,
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["data"][0]["workflow_context"] == complex_context


# GET /workflows/{workflow_id}/executions/{execution_id}/transitions/from/{state_id}


def test_get_transition_from_state_success(
    request_headers, mock_workflow_service, mock_ability_allow, mock_transitions_service
):
    """Test successful retrieval of transition by from_state_id."""
    mock_transition = WorkflowExecutionTransitionResponse(
        id="trans_1",
        execution_id=EXECUTION_ID,
        from_state_id=STATE_ID_FROM,
        to_state_id=STATE_ID_TO,
        workflow_context={"messages": ["test"]},
        date=datetime(2021, 8, 1, 12, 0, 0),
    )

    mock_transitions_service.get_by_from_state.return_value = mock_transition

    response = client.get(
        f"/v1/workflows/{WORKFLOW_ID}/executions/{EXECUTION_ID}/transitions/from/{STATE_ID_FROM}",
        headers=request_headers,
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["from_state_id"] == STATE_ID_FROM
    assert data["to_state_id"] == STATE_ID_TO
    assert data["execution_id"] == EXECUTION_ID

    mock_transitions_service.get_by_from_state.assert_called_once_with(
        execution_id=EXECUTION_ID,
        from_state_id=STATE_ID_FROM,
    )


def test_get_transition_from_state_not_found(
    request_headers, mock_workflow_service, mock_ability_allow, mock_transitions_service
):
    """Test 404 response when transition not found by from_state_id."""
    from codemie.core.exceptions import ExtendedHTTPException

    mock_transitions_service.get_by_from_state.side_effect = ExtendedHTTPException(
        code=status.HTTP_404_NOT_FOUND,
        message="Transition Not Found",
        details="No transition found originating from state",
    )

    response = client.get(
        f"/v1/workflows/{WORKFLOW_ID}/executions/{EXECUTION_ID}/transitions/from/nonexistent",
        headers=request_headers,
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert "error" in data
    assert data["error"]["message"] == "Transition Not Found"


def test_get_transition_from_state_workflow_not_found(request_headers, mock_ability_allow):
    """Test 404 response when workflow doesn't exist for from_state lookup."""
    with patch("codemie.rest_api.routers.workflow_executions.WorkflowService") as mock_service:
        mock_ser = MagicMock()
        mock_ser.get_workflow.side_effect = KeyError("Workflow not found")
        mock_service.return_value = mock_ser

        response = client.get(
            f"/v1/workflows/nonexistent/executions/{EXECUTION_ID}/transitions/from/{STATE_ID_FROM}",
            headers=request_headers,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


def test_get_transition_from_state_forbidden(request_headers, mock_workflow_service, mock_ability_deny):
    """Test 401 response when user lacks READ permission for from_state lookup."""
    response = client.get(
        f"/v1/workflows/{WORKFLOW_ID}/executions/{EXECUTION_ID}/transitions/from/{STATE_ID_FROM}",
        headers=request_headers,
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert "error" in data
    assert data["error"]["message"] == "Access denied"


# GET /workflows/{workflow_id}/executions/{execution_id}/transitions/to/{state_id}


def test_get_transition_to_state_success(
    request_headers, mock_workflow_service, mock_ability_allow, mock_transitions_service
):
    """Test successful retrieval of transition by to_state_id."""
    mock_transition = WorkflowExecutionTransitionResponse(
        id="trans_1",
        execution_id=EXECUTION_ID,
        from_state_id=STATE_ID_FROM,
        to_state_id=STATE_ID_TO,
        workflow_context={"messages": ["test"]},
        date=datetime(2021, 8, 1, 12, 0, 0),
    )

    mock_transitions_service.get_by_to_state.return_value = mock_transition

    response = client.get(
        f"/v1/workflows/{WORKFLOW_ID}/executions/{EXECUTION_ID}/transitions/to/{STATE_ID_TO}",
        headers=request_headers,
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["from_state_id"] == STATE_ID_FROM
    assert data["to_state_id"] == STATE_ID_TO
    assert data["execution_id"] == EXECUTION_ID

    mock_transitions_service.get_by_to_state.assert_called_once_with(
        execution_id=EXECUTION_ID,
        to_state_id=STATE_ID_TO,
    )


def test_get_transition_to_state_not_found(
    request_headers, mock_workflow_service, mock_ability_allow, mock_transitions_service
):
    """Test 404 response when transition not found by to_state_id."""
    from codemie.core.exceptions import ExtendedHTTPException

    mock_transitions_service.get_by_to_state.side_effect = ExtendedHTTPException(
        code=status.HTTP_404_NOT_FOUND,
        message="Transition Not Found",
        details="No transition found targeting state",
    )

    response = client.get(
        f"/v1/workflows/{WORKFLOW_ID}/executions/{EXECUTION_ID}/transitions/to/nonexistent",
        headers=request_headers,
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert "error" in data
    assert data["error"]["message"] == "Transition Not Found"


def test_get_transition_to_state_workflow_not_found(request_headers, mock_ability_allow):
    """Test 404 response when workflow doesn't exist for to_state lookup."""
    with patch("codemie.rest_api.routers.workflow_executions.WorkflowService") as mock_service:
        mock_ser = MagicMock()
        mock_ser.get_workflow.side_effect = KeyError("Workflow not found")
        mock_service.return_value = mock_ser

        response = client.get(
            f"/v1/workflows/nonexistent/executions/{EXECUTION_ID}/transitions/to/{STATE_ID_TO}",
            headers=request_headers,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


def test_get_transition_to_state_forbidden(request_headers, mock_workflow_service, mock_ability_deny):
    """Test 401 response when user lacks READ permission for to_state lookup."""
    response = client.get(
        f"/v1/workflows/{WORKFLOW_ID}/executions/{EXECUTION_ID}/transitions/to/{STATE_ID_TO}",
        headers=request_headers,
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert "error" in data
    assert data["error"]["message"] == "Access denied"


# Edge cases and integration tests


def test_transitions_with_none_state_ids(
    request_headers, mock_workflow_service, mock_ability_allow, mock_transitions_service
):
    """Test transitions with None values for state IDs (start/end transitions)."""
    mock_transitions_data = [
        WorkflowExecutionTransitionResponse(
            id="trans_start",
            execution_id=EXECUTION_ID,
            from_state_id=None,  # Start transition
            to_state_id="uuid-first-node",
            workflow_context={},
            date=datetime(2021, 8, 1, 12, 0, 0),
        ),
        WorkflowExecutionTransitionResponse(
            id="trans_end",
            execution_id=EXECUTION_ID,
            from_state_id="uuid-last-node",
            to_state_id=None,  # End transition
            workflow_context={},
            date=datetime(2021, 8, 1, 12, 5, 0),
        ),
    ]

    mock_transitions_service.run.return_value = {
        "data": mock_transitions_data,
        "pagination": {"page": 0, "per_page": 10, "total": 2, "pages": 1},
    }

    response = client.get(
        f"/v1/workflows/{WORKFLOW_ID}/executions/{EXECUTION_ID}/transitions",
        headers=request_headers,
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["data"][0]["from_state_id"] is None
    assert data["data"][1]["to_state_id"] is None
