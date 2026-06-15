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
from unittest.mock import AsyncMock, ANY
from codemie.triggers.actors.conversation import create_conversation, update_conversation
from codemie.core.models import UpdateConversationRequest

_USER_ID = 'test_user_id'
_MOCK_SIGN_HEADERS = {
    'X-Bind-Key': 'mock-sig',
    'X-Bind-Nonce': 'mock-nonce',
    'X-Bind-Timestamp': '1000000000',
    'user-id': _USER_ID,
}


@pytest.mark.asyncio
async def test_update_conversation(httpx_mock, mocker):
    mocker.patch('codemie.triggers.actors.conversation.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)

    conversation_id = 'test_conversation_id'
    update_request = UpdateConversationRequest(name='test_conversation_name')
    job_id = 'test_job_id'
    url = 'http://localhost:8080'

    httpx_mock.add_response(method='PUT', url=f'{url}/v1/conversations/{conversation_id}', status_code=200)

    await update_conversation(conversation_id, update_request, _USER_ID, job_id, url)

    requests_made = httpx_mock.get_requests()
    assert len(requests_made) == 1
    assert requests_made[0].headers['user-id'] == _USER_ID
    assert requests_made[0].headers['X-Bind-Key'] == 'mock-sig'
    assert requests_made[0].headers['X-Bind-Nonce'] == 'mock-nonce'
    assert requests_made[0].headers['X-Bind-Timestamp'] == '1000000000'


@pytest.mark.asyncio
async def test_create_conversation_forwards_url_to_update(httpx_mock, mocker):
    """Regression: create_conversation must pass the caller url to update_conversation."""
    mocker.patch('codemie.triggers.actors.conversation.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mock_update = mocker.patch('codemie.triggers.actors.conversation.update_conversation', new_callable=AsyncMock)

    custom_url = 'http://custom-server:9090'
    httpx_mock.add_response(
        method='POST', url=f'{custom_url}/v1/conversations', status_code=200, json={'id': 'conv-123'}
    )

    result = await create_conversation('asst-id', 'My Conversation', 'user-1', 'job-1', url=custom_url)

    assert result == 'conv-123'
    mock_update.assert_called_once_with('conv-123', ANY, 'user-1', 'job-1', url=custom_url)
