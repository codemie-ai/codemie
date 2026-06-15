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
from unittest.mock import AsyncMock

from codemie.triggers.actors.assistant import invoke_assistant

_ASSISTANT_URL = 'http://mockserver:8080'
_ASSISTANT_ID = 'assistant-id'
_USER_ID = 'user-id'
_JOB_ID = 'job-id'
_CONVERSATION_ID = 'conversation-id'
_POST_URL = f'{_ASSISTANT_URL}/v1/assistants/{_ASSISTANT_ID}/model'

_MOCK_SIGN_HEADERS = {
    'X-Bind-Key': 'mock-sig',
    'X-Bind-Nonce': 'mock-nonce',
    'X-Bind-Timestamp': '1000000000',
    'user-id': _USER_ID,
}


@pytest.mark.asyncio
async def test_invoke_assistant_success(httpx_mock, mocker):
    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=_CONVERSATION_ID,
    )
    httpx_mock.add_response(method='POST', url=_POST_URL, status_code=200)

    await invoke_assistant(
        assistant_id=_ASSISTANT_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        task='Do a task',
        url=_ASSISTANT_URL,
    )

    requests_made = httpx_mock.get_requests()
    assert len(requests_made) == 1
    assert str(requests_made[0].url) == _POST_URL
    assert requests_made[0].headers['user-id'] == _USER_ID
    assert requests_made[0].headers['X-Bind-Key'] == 'mock-sig'
    assert requests_made[0].headers['X-Bind-Nonce'] == 'mock-nonce'
    assert requests_made[0].headers['X-Bind-Timestamp'] == '1000000000'
    assert json.loads(requests_made[0].content) == {
        'text': 'Do a task',
        'content_raw': '<p>Do a task</p>',
        'stream': False,
        'conversation_id': _CONVERSATION_ID,
    }


@pytest.mark.asyncio
async def test_invoke_assistant_uses_scheduler_prefix(httpx_mock, mocker):
    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mock_create = mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=_CONVERSATION_ID,
    )
    httpx_mock.add_response(method='POST', url=_POST_URL, status_code=200)

    await invoke_assistant(
        assistant_id=_ASSISTANT_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        task='Do a task',
        url=_ASSISTANT_URL,
        trigger_source='Scheduler',
    )

    mock_create.assert_called_once_with(
        assistant_id=_ASSISTANT_ID,
        conversation_name='Scheduler: assistant-id',
        user_id=_USER_ID,
        job_id=_JOB_ID,
        url=_ASSISTANT_URL,
    )


@pytest.mark.asyncio
async def test_invoke_assistant_post_request_failure(httpx_mock, mocker):
    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=_CONVERSATION_ID,
    )
    mock_delete = mocker.patch('codemie.triggers.actors.assistant.delete_conversation', new_callable=AsyncMock)
    httpx_mock.add_response(method='POST', url=_POST_URL, status_code=500)

    await invoke_assistant(
        assistant_id=_ASSISTANT_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        task='Do a task',
        url=_ASSISTANT_URL,
    )

    mock_delete.assert_called_once_with(_CONVERSATION_ID, _USER_ID, _JOB_ID, _ASSISTANT_URL)


@pytest.mark.asyncio
async def test_invoke_assistant_failure_no_cleanup_when_conversation_not_created(httpx_mock, mocker):
    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=None,
    )
    mock_delete = mocker.patch('codemie.triggers.actors.assistant.delete_conversation', new_callable=AsyncMock)
    httpx_mock.add_response(method='POST', url=_POST_URL, status_code=500)

    await invoke_assistant(
        assistant_id=_ASSISTANT_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        task='Do a task',
        url=_ASSISTANT_URL,
    )

    mock_delete.assert_not_called()
