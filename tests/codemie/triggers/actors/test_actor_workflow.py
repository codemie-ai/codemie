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
from typing import Generator

from requests.exceptions import RequestException, Timeout, InvalidURL
from unittest.mock import MagicMock, call, patch

from codemie.triggers.actors.workflow import invoke_workflow
from codemie.triggers.config import BASE_API_URL

USER_ID = "test_user_id"
JOB_ID = "test_job_id"
WORKFLOW_ID = "test_workflow_id"
URL = f"{BASE_API_URL}/v1/workflows/{WORKFLOW_ID}/executions"


@pytest.fixture
def mock_post() -> Generator[MagicMock, None, None]:
    with patch("codemie.triggers.actors.workflow.requests.post") as mock_post:
        yield mock_post


@pytest.fixture
def mock_logger() -> Generator[MagicMock, None, None]:
    with patch("codemie.triggers.actors.workflow.logger") as mock_logger:
        yield mock_logger


@pytest.mark.parametrize(
    "exception", [Timeout("The request timed out"), InvalidURL("Invalid URL"), RequestException("Server error")]
)
def test_invoke_workflow_exceptions(mock_post: MagicMock, mock_logger: MagicMock, exception: Exception) -> None:
    expected_exception_msg = "Failed to invoke workflow. job_id: %s, workflow_id: %s, error: %s"
    mock_post.side_effect = exception

    invoke_workflow(workflow_id=WORKFLOW_ID, user_id=USER_ID, job_id=JOB_ID, task="Test Task")

    mock_logger.error.assert_called_once_with(expected_exception_msg, JOB_ID, WORKFLOW_ID, str(exception))


def test_invoke_workflow_success(mock_post: MagicMock, mock_logger: MagicMock) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response
    expected_info_processing = 'Invoking triggered actor "invoke_workflow". job_id: %s, workflow_id: %s, url: %s'
    expected_info_successful = 'Workflow invoked successfully. job_id: %s, workflow_id: %s'
    expected_calls = [
        call(expected_info_processing, JOB_ID, WORKFLOW_ID, URL),
        call(expected_info_successful, JOB_ID, WORKFLOW_ID),
    ]

    invoke_workflow(workflow_id=WORKFLOW_ID, user_id=USER_ID, job_id=JOB_ID, task="Test Task")

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert kwargs["url"] == URL
    assert kwargs["headers"]["user-id"] == USER_ID
    assert kwargs["json"]["user_input"] == "Test Task"

    assert mock_logger.info.call_args_list == expected_calls


def test_invoke_workflow_default_task(mock_post: MagicMock, mock_logger: MagicMock) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response
    expected_info_processing = 'Invoking triggered actor "invoke_workflow". job_id: %s, workflow_id: %s, url: %s'
    expected_info_successful = 'Workflow invoked successfully. job_id: %s, workflow_id: %s'
    expected_calls = [
        call(expected_info_processing, JOB_ID, WORKFLOW_ID, URL),
        call(expected_info_successful, JOB_ID, WORKFLOW_ID),
    ]

    invoke_workflow(workflow_id=WORKFLOW_ID, user_id=USER_ID, job_id=JOB_ID)

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert kwargs["json"]["user_input"] == "Do it"
    assert mock_logger.info.call_args_list == expected_calls
