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
from unittest.mock import ANY, MagicMock
from codemie.triggers.actors.conversation import create_conversation, update_conversation
from codemie.core.models import UpdateConversationRequest

_MOCK_BIND_KEY = 'test-bind-key'


@pytest.fixture
def mock_put(mocker):
    return mocker.patch('requests.put')


def test_update_conversation(mock_put, mocker):
    mocker.patch('codemie.triggers.actors.conversation.get_bind_key', return_value=_MOCK_BIND_KEY)

    conversation_id = 'test_conversation_id'
    update_request = UpdateConversationRequest(name='test_conversation_name')
    user_id = 'test_user_id'
    job_id = 'test_job_id'
    url = 'http://localhost:8080'

    update_conversation(conversation_id, update_request, user_id, job_id, url)

    headers = {
        'Content-Type': 'application/json',
        'user-id': user_id,
        'X-Bind-Key': _MOCK_BIND_KEY,
    }
    data = update_request.model_dump()

    mock_put.assert_called_once_with(
        url=f'{url}/v1/conversations/{conversation_id}', headers=headers, json=data, timeout=600
    )


def test_create_conversation_forwards_url_to_update(mocker):
    """Regression: create_conversation must pass the caller url to update_conversation."""
    mocker.patch('codemie.triggers.actors.conversation.get_bind_key', return_value=_MOCK_BIND_KEY)
    mock_update = mocker.patch('codemie.triggers.actors.conversation.update_conversation')

    mock_response = MagicMock()
    mock_response.json.return_value = {'id': 'conv-123'}
    mock_response.raise_for_status.return_value = None
    mocker.patch('requests.post', return_value=mock_response)

    custom_url = 'http://custom-server:9090'
    result = create_conversation('asst-id', 'My Conversation', 'user-1', 'job-1', url=custom_url)

    assert result == 'conv-123'
    mock_update.assert_called_once_with('conv-123', ANY, 'user-1', 'job-1', url=custom_url)
