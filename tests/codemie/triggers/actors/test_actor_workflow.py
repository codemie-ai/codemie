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

import json
import pytest
from typing import Generator
from unittest.mock import call, patch

import httpx

from codemie.triggers.actors.workflow import invoke_workflow
from codemie.triggers.config import BASE_API_URL

USER_ID = "test_user_id"
JOB_ID = "test_job_id"
WORKFLOW_ID = "test_workflow_id"
URL = f"{BASE_API_URL}/v1/workflows/{WORKFLOW_ID}/executions"

_MOCK_SIGN_HEADERS = {
    'X-Bind-Key': 'mock-sig',
    'X-Bind-Nonce': 'mock-nonce',
    'X-Bind-Timestamp': '1000000000',
    'user-id': USER_ID,
}


@pytest.fixture
def mock_bind_key():
    with patch('codemie.triggers.actors.workflow.sign_internal_request', return_value=_MOCK_SIGN_HEADERS):
        yield


@pytest.fixture
def mock_logger() -> Generator:
    with patch("codemie.triggers.actors.workflow.logger") as mock_logger:
        yield mock_logger


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception",
    [
        httpx.ReadTimeout("The request timed out"),
        httpx.InvalidURL("Invalid URL"),
        httpx.RequestError("Server error"),
    ],
)
async def test_invoke_workflow_exceptions(httpx_mock, mock_bind_key, mock_logger, exception) -> None:
    expected_exception_msg = "Failed to invoke workflow. job_id: %s, workflow_id: %s, error: %s"
    httpx_mock.add_exception(exception)

    await invoke_workflow(workflow_id=WORKFLOW_ID, user_id=USER_ID, job_id=JOB_ID, task="Test Task")

    mock_logger.error.assert_called_once_with(expected_exception_msg, JOB_ID, WORKFLOW_ID, str(exception))


@pytest.mark.asyncio
async def test_invoke_workflow_success(httpx_mock, mock_bind_key, mock_logger) -> None:
    httpx_mock.add_response(method='POST', url=URL, status_code=200)
    expected_info_processing = 'Invoking triggered actor "invoke_workflow". job_id: %s, workflow_id: %s, url: %s'
    expected_info_successful = 'Workflow invoked successfully. job_id: %s, workflow_id: %s'
    expected_calls = [
        call(expected_info_processing, JOB_ID, WORKFLOW_ID, URL),
        call(expected_info_successful, JOB_ID, WORKFLOW_ID),
    ]

    await invoke_workflow(workflow_id=WORKFLOW_ID, user_id=USER_ID, job_id=JOB_ID, task="Test Task")

    requests_made = httpx_mock.get_requests()
    assert len(requests_made) == 1
    assert str(requests_made[0].url) == URL
    assert requests_made[0].headers['user-id'] == USER_ID
    assert requests_made[0].headers['X-Bind-Key'] == 'mock-sig'
    assert requests_made[0].headers['X-Bind-Nonce'] == 'mock-nonce'
    assert requests_made[0].headers['X-Bind-Timestamp'] == '1000000000'
    assert json.loads(requests_made[0].content)['user_input'] == 'Test Task'
    assert mock_logger.info.call_args_list == expected_calls


@pytest.mark.asyncio
async def test_invoke_workflow_default_task(httpx_mock, mock_bind_key, mock_logger) -> None:
    httpx_mock.add_response(method='POST', url=URL, status_code=200)
    expected_info_processing = 'Invoking triggered actor "invoke_workflow". job_id: %s, workflow_id: %s, url: %s'
    expected_info_successful = 'Workflow invoked successfully. job_id: %s, workflow_id: %s'
    expected_calls = [
        call(expected_info_processing, JOB_ID, WORKFLOW_ID, URL),
        call(expected_info_successful, JOB_ID, WORKFLOW_ID),
    ]

    await invoke_workflow(workflow_id=WORKFLOW_ID, user_id=USER_ID, job_id=JOB_ID)

    requests_made = httpx_mock.get_requests()
    assert json.loads(requests_made[0].content)['user_input'] == 'Do it'
    assert mock_logger.info.call_args_list == expected_calls
