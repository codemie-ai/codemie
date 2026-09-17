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
    mocker.patch('codemie.triggers.actors.assistant.save_error_history_turn', new_callable=AsyncMock)
    httpx_mock.add_response(method='POST', url=_POST_URL, status_code=500)

    await invoke_assistant(
        assistant_id=_ASSISTANT_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        task='Do a task',
        url=_ASSISTANT_URL,
    )


@pytest.mark.asyncio
async def test_invoke_assistant_failure_no_cleanup_when_conversation_not_created(httpx_mock, mocker):
    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=None,
    )
    mocker.patch('codemie.triggers.actors.assistant.save_error_history_turn', new_callable=AsyncMock)
    httpx_mock.add_response(method='POST', url=_POST_URL, status_code=500)

    await invoke_assistant(
        assistant_id=_ASSISTANT_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        task='Do a task',
        url=_ASSISTANT_URL,
    )


@pytest.mark.asyncio
async def test_invoke_assistant_http_status_error_no_delete(httpx_mock, mocker):
    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=_CONVERSATION_ID,
    )
    mock_save_turn = mocker.patch('codemie.triggers.actors.assistant.save_error_history_turn', new_callable=AsyncMock)
    httpx_mock.add_response(method='POST', url=_POST_URL, status_code=401)

    await invoke_assistant(
        assistant_id=_ASSISTANT_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        task='Do a task',
        url=_ASSISTANT_URL,
    )

    mock_save_turn.assert_not_called()


@pytest.mark.asyncio
async def test_invoke_assistant_connect_error_persists_error_turn(httpx_mock, mocker):
    import httpx as httpx_lib

    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=_CONVERSATION_ID,
    )
    mock_save_turn = mocker.patch('codemie.triggers.actors.assistant.save_error_history_turn', new_callable=AsyncMock)
    httpx_mock.add_exception(httpx_lib.ConnectError('connection refused'), url=_POST_URL, method='POST')

    await invoke_assistant(
        assistant_id=_ASSISTANT_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        task='Do a task',
        url=_ASSISTANT_URL,
    )

    mock_save_turn.assert_called_once()
    call_kwargs = mock_save_turn.call_args.kwargs
    assert call_kwargs['conversation_id'] == _CONVERSATION_ID
    assert call_kwargs['assistant_id'] == _ASSISTANT_ID
    assert 'ConnectError' in call_kwargs['error_message']
    assert 'secret' not in call_kwargs['error_message'].lower()
    assert 'token=' not in call_kwargs['error_message']


@pytest.mark.asyncio
async def test_invoke_assistant_timeout_persists_error_turn(httpx_mock, mocker):
    import httpx as httpx_lib

    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=_CONVERSATION_ID,
    )
    mock_save_turn = mocker.patch('codemie.triggers.actors.assistant.save_error_history_turn', new_callable=AsyncMock)
    httpx_mock.add_exception(httpx_lib.TimeoutException('timed out'), url=_POST_URL, method='POST')

    await invoke_assistant(
        assistant_id=_ASSISTANT_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        task='Do a task',
        url=_ASSISTANT_URL,
    )

    mock_save_turn.assert_called_once()
    call_kwargs = mock_save_turn.call_args.kwargs
    assert 'TimeoutException' in call_kwargs['error_message']


@pytest.mark.asyncio
async def test_invoke_assistant_no_persist_when_no_conversation_id(httpx_mock, mocker):
    import httpx as httpx_lib

    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=None,
    )
    mock_save_turn = mocker.patch('codemie.triggers.actors.assistant.save_error_history_turn', new_callable=AsyncMock)
    httpx_mock.add_exception(httpx_lib.ConnectError('connection refused'), url=_POST_URL, method='POST')

    await invoke_assistant(
        assistant_id=_ASSISTANT_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        task='Do a task',
        url=_ASSISTANT_URL,
    )

    mock_save_turn.assert_not_called()


@pytest.mark.asyncio
async def test_invoke_assistant_log_does_not_leak_token(httpx_mock, mocker):
    import httpx as httpx_lib

    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=_CONVERSATION_ID,
    )
    mocker.patch('codemie.triggers.actors.assistant.save_error_history_turn', new_callable=AsyncMock)
    mock_logger_error = mocker.patch('codemie.triggers.actors.assistant.logger.error')
    # Exception message contains the token. If str(e) were logged instead of
    # type(e).__name__, the token would appear in the log output.
    httpx_mock.add_exception(
        httpx_lib.ConnectError('connection refused to http://server?token=secret-token'),
        url=_POST_URL,
        method='POST',
    )

    await invoke_assistant(
        assistant_id=_ASSISTANT_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        task='Do a task',
        url=_ASSISTANT_URL,
    )

    assert mock_logger_error.called
    all_logged = ' '.join(str(a) for a in mock_logger_error.call_args.args)
    assert 'ConnectError' in all_logged
    assert 'secret-token' not in all_logged
    assert 'token=' not in all_logged


@pytest.mark.asyncio
async def test_invoke_assistant_log_does_not_leak_url_on_status_error(httpx_mock, mocker):
    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=_CONVERSATION_ID,
    )
    mocker.patch('codemie.triggers.actors.assistant.save_error_history_turn', new_callable=AsyncMock)
    mock_logger_error = mocker.patch('codemie.triggers.actors.assistant.logger.error')
    httpx_mock.add_response(method='POST', url=_POST_URL, status_code=500)

    await invoke_assistant(
        assistant_id=_ASSISTANT_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        task='Do a task',
        url=_ASSISTANT_URL,
    )

    assert mock_logger_error.called
    all_logged = ' '.join(str(a) for a in mock_logger_error.call_args.args)
    assert '500' in all_logged
    assert 'http://mockserver' not in all_logged
