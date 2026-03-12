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
from codemie.triggers.actors.conversation import update_conversation
from codemie.core.models import UpdateConversationRequest


@pytest.fixture
def mock_put(mocker):
    return mocker.patch('requests.put')


def test_update_conversation(mock_put):
    conversation_id = 'test_conversation_id'
    update_request = UpdateConversationRequest(name='test_conversation_name')
    user_id = 'test_user_id'
    job_id = 'test_job_id'
    url = 'http://localhost:8080'

    update_conversation(conversation_id, update_request, user_id, job_id, url)

    headers = {
        'Content-Type': 'application/json',
        'user-id': user_id,
    }
    data = update_request.model_dump()

    mock_put.assert_called_once_with(
        url=f'{url}/v1/conversations/{conversation_id}', headers=headers, json=data, timeout=600
    )
