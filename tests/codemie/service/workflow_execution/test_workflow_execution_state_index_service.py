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

import pytest
from unittest.mock import patch, MagicMock

from codemie.service.workflow_execution import WorkflowExecutionStatesIndexService
from codemie.core.workflow_models import (
    WorkflowExecutionState,
    WorkflowExecutionStateThought,
    WorkflowExecutionStateWithThougths,
)


@pytest.fixture
def mock_result():
    return WorkflowExecutionState(
        execution_id="execution_id",
        name="Test State",
        date="2021-08-01T00:00:00Z",
        status="Not Started",
        task="test task",
    )


@pytest.fixture
def mock_thoughts_result():
    return [
        WorkflowExecutionStateThought(
            execution_state_id="execution_state_id",
            parent_id=None,
            author_name="author_name",
            author_type="author_type",
            content="content",
        )
    ]


@pytest.mark.parametrize(
    "state_name_prefix, states_status_filter, include_thoughts",
    [
        (None, None, True),
        ("Test", None, True),
        (None, ["Completed", "Failed"], True),
        ("Test", ["Completed", "Failed"], True),
        (None, None, False),
        ("Test", None, False),
        (None, ["Completed", "Failed"], False),
        ("Test", ["Completed", "Failed"], False),
    ],
)
@patch("codemie.core.workflow_models.WorkflowExecutionStateThought.get_root")
@patch("codemie.service.workflow_execution.workflow_execution_states_index_service.Session")
def test_run(
    mock_session_class,
    mock_get_thoughts,
    mock_result,
    mock_thoughts_result,
    state_name_prefix,
    states_status_filter,
    include_thoughts,
):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [mock_result]
    mock_session.exec.return_value.one.return_value = 1
    mock_get_thoughts.return_value = mock_thoughts_result

    response = WorkflowExecutionStatesIndexService.run(
        execution_id="execution_id",
        page=1,
        per_page=20,
        include_thoughts=include_thoughts,
        state_name_prefix=state_name_prefix,
        states_status_filter=states_status_filter,
    )

    assert isinstance(response["data"][0], WorkflowExecutionStateWithThougths)
    assert bool(response["data"][0].thoughts) == include_thoughts
    assert response["pagination"] == {"page": 1, "per_page": 20, "total": 1, "pages": 1}

    # Verify whether get_root is called based on include_thoughts
    assert bool(mock_get_thoughts.call_count) == include_thoughts
    # Verify session was used correctly
    mock_session_class.assert_called_once_with(WorkflowExecutionState.get_engine())
