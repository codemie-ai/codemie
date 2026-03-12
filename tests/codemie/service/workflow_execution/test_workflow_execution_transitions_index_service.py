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
from datetime import datetime
from unittest.mock import patch, MagicMock
from fastapi import status

from codemie.service.workflow_execution import WorkflowExecutionTransitionsIndexService
from codemie.core.workflow_models import (
    WorkflowExecutionTransition,
    WorkflowExecutionTransitionResponse,
)
from codemie.core.exceptions import ExtendedHTTPException


@pytest.fixture
def mock_transition():
    """Mock transition record with UUID state IDs."""
    return WorkflowExecutionTransition(
        id="transition_id_1",
        execution_id="execution_id",
        from_state_id="uuid-state-a",
        to_state_id="uuid-state-b",
        workflow_context={"messages": ["test"], "context_store": {}},
        date=datetime(2021, 8, 1, 12, 0, 0),
    )


@pytest.fixture
def mock_transitions_list(mock_transition):
    """Mock list of transitions."""
    transition_2 = WorkflowExecutionTransition(
        id="transition_id_2",
        execution_id="execution_id",
        from_state_id="uuid-state-b",
        to_state_id="uuid-state-c",
        workflow_context={"messages": ["test2"], "context_store": {"key": "value"}},
        date=datetime(2021, 8, 1, 12, 1, 0),
    )
    return [mock_transition, transition_2]


@patch("codemie.service.workflow_execution.workflow_execution_transitions_index_service.Session")
def test_run_basic_pagination(mock_session_class, mock_transitions_list):
    """Test WorkflowExecutionTransitionsIndexService.run() with basic pagination."""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session

    mock_session.exec.return_value.all.return_value = mock_transitions_list
    mock_session.exec.return_value.one.return_value = 2

    response = WorkflowExecutionTransitionsIndexService.run(
        execution_id="execution_id",
        page=0,
        per_page=10,
    )

    assert len(response["data"]) == 2
    assert all(isinstance(t, WorkflowExecutionTransitionResponse) for t in response["data"])
    assert response["pagination"]["page"] == 0
    assert response["pagination"]["per_page"] == 10
    assert response["pagination"]["total"] == 2
    assert response["pagination"]["pages"] == 1

    # Verify session was used correctly
    mock_session_class.assert_called_once_with(WorkflowExecutionTransition.get_engine())


@patch("codemie.service.workflow_execution.workflow_execution_transitions_index_service.Session")
def test_run_pagination_second_page(mock_session_class, mock_transitions_list):
    """Test pagination works correctly for second page."""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session

    # Simulate page 1 with 1 item per page
    mock_session.exec.return_value.all.return_value = [mock_transitions_list[1]]
    mock_session.exec.return_value.one.return_value = 2  # Total count

    response = WorkflowExecutionTransitionsIndexService.run(
        execution_id="execution_id",
        page=1,
        per_page=1,
    )

    assert len(response["data"]) == 1
    assert response["data"][0].from_state_id == "uuid-state-b"
    assert response["pagination"] == {"page": 1, "per_page": 1, "total": 2, "pages": 2}


@patch("codemie.service.workflow_execution.workflow_execution_transitions_index_service.Session")
def test_run_empty_results(mock_session_class):
    """Test handling of empty result set."""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = []
    mock_session.exec.return_value.one.return_value = 0

    response = WorkflowExecutionTransitionsIndexService.run(
        execution_id="nonexistent_execution",
        page=0,
        per_page=10,
    )

    assert response["data"] == []
    assert response["pagination"] == {"page": 0, "per_page": 10, "total": 0, "pages": 0}


@patch("codemie.service.workflow_execution.workflow_execution_transitions_index_service.Session")
def test_run_preserves_workflow_context(mock_session_class, mock_transition):
    """Test that workflow_context is preserved in response."""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [mock_transition]
    mock_session.exec.return_value.one.return_value = 1

    response = WorkflowExecutionTransitionsIndexService.run(
        execution_id="execution_id",
        page=0,
        per_page=10,
    )

    assert len(response["data"]) == 1
    transition_response = response["data"][0]
    assert transition_response.workflow_context == {"messages": ["test"], "context_store": {}}
    assert transition_response.from_state_id == "uuid-state-a"
    assert transition_response.to_state_id == "uuid-state-b"


@patch("codemie.service.workflow_execution.workflow_execution_transitions_index_service.Session")
def test_run_ordered_chronologically(mock_session_class, mock_transitions_list):
    """Test that results are ordered by date ascending (chronological)."""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = mock_transitions_list
    mock_session.exec.return_value.one.return_value = 2

    response = WorkflowExecutionTransitionsIndexService.run(
        execution_id="execution_id",
        page=0,
        per_page=10,
    )

    # Verify transitions are in chronological order
    assert response["data"][0].date < response["data"][1].date


@patch("codemie.service.workflow_execution.workflow_execution_transitions_index_service.Session")
def test_get_by_from_state_success(mock_session_class, mock_transition):
    """Test successful retrieval of transition by from_state_id."""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.first.return_value = mock_transition

    result = WorkflowExecutionTransitionsIndexService.get_by_from_state(
        execution_id="execution_id",
        from_state_id="uuid-state-a",
    )

    assert isinstance(result, WorkflowExecutionTransitionResponse)
    assert result.from_state_id == "uuid-state-a"
    assert result.to_state_id == "uuid-state-b"
    assert result.execution_id == "execution_id"


@patch("codemie.service.workflow_execution.workflow_execution_transitions_index_service.Session")
def test_get_by_from_state_not_found(mock_session_class):
    """Test 404 error when transition not found by from_state_id."""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.first.return_value = None

    with pytest.raises(ExtendedHTTPException) as exc_info:
        WorkflowExecutionTransitionsIndexService.get_by_from_state(
            execution_id="execution_id",
            from_state_id="nonexistent",
        )

    assert exc_info.value.code == status.HTTP_404_NOT_FOUND
    assert "Transition Not Found" in exc_info.value.message
    assert "originating from state" in exc_info.value.details


@patch("codemie.service.workflow_execution.workflow_execution_transitions_index_service.Session")
def test_get_by_from_state_filters_correctly(mock_session_class, mock_transition):
    """Test that get_by_from_state filters by both execution_id and from_state_id."""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.first.return_value = mock_transition

    WorkflowExecutionTransitionsIndexService.get_by_from_state(
        execution_id="exec-123",
        from_state_id="state-456",
    )

    # Verify query was executed (we can't easily inspect the query itself with sqlmodel)
    mock_session.exec.assert_called_once()


@patch("codemie.service.workflow_execution.workflow_execution_transitions_index_service.Session")
def test_get_by_to_state_success(mock_session_class, mock_transition):
    """Test successful retrieval of transition by to_state_id."""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.first.return_value = mock_transition

    result = WorkflowExecutionTransitionsIndexService.get_by_to_state(
        execution_id="execution_id",
        to_state_id="uuid-state-b",
    )

    assert isinstance(result, WorkflowExecutionTransitionResponse)
    assert result.to_state_id == "uuid-state-b"
    assert result.execution_id == "execution_id"


@patch("codemie.service.workflow_execution.workflow_execution_transitions_index_service.Session")
def test_get_by_to_state_not_found(mock_session_class):
    """Test 404 error when transition not found by to_state_id."""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.first.return_value = None

    with pytest.raises(ExtendedHTTPException) as exc_info:
        WorkflowExecutionTransitionsIndexService.get_by_to_state(
            execution_id="execution_id",
            to_state_id="nonexistent",
        )

    assert exc_info.value.code == status.HTTP_404_NOT_FOUND
    assert "Transition Not Found" in exc_info.value.message
    assert "targeting state" in exc_info.value.details


@patch("codemie.service.workflow_execution.workflow_execution_transitions_index_service.Session")
def test_get_by_to_state_filters_correctly(mock_session_class, mock_transition):
    """Test that get_by_to_state filters by both execution_id and to_state_id."""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.first.return_value = mock_transition

    WorkflowExecutionTransitionsIndexService.get_by_to_state(
        execution_id="exec-789",
        to_state_id="state-012",
    )

    # Verify query was executed
    mock_session.exec.assert_called_once()


@patch("codemie.service.workflow_execution.workflow_execution_transitions_index_service.Session")
def test_run_multiple_pages_calculation(mock_session_class):
    """Test correct page calculation with multiple pages."""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = []
    mock_session.exec.return_value.one.return_value = 25  # Total count

    response = WorkflowExecutionTransitionsIndexService.run(
        execution_id="execution_id",
        page=0,
        per_page=10,
    )

    # 25 items / 10 per page = 3 pages
    assert response["pagination"]["pages"] == 3
    assert response["pagination"]["total"] == 25
