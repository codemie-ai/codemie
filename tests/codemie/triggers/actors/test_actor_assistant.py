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

import unittest
from unittest.mock import patch, MagicMock, ANY

from requests import RequestException

from codemie.configs import logger
from codemie.triggers.actors.assistant import invoke_assistant

_MOCK_BIND_KEY = 'test-bind-key'
_EXPECTED_HEADERS = {
    'Content-Type': 'application/json',
    'user-id': 'user-id',
    'X-Bind-Key': _MOCK_BIND_KEY,
}


class TestInvokeAssistant(unittest.TestCase):
    @patch('codemie.triggers.actors.assistant.get_bind_key', return_value=_MOCK_BIND_KEY)
    @patch('codemie.triggers.actors.conversation.create_conversation')
    @patch('codemie.triggers.actors.assistant.requests.post')
    def test_invoke_assistant_success(self, mock_post, mock_create_conversation, mock_get_bind_key):
        """Test successful invocation of assistant."""
        mock_create_conversation.return_value = 'conversation-id'
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        invoke_assistant(
            assistant_id='assistant-id',
            user_id='user-id',
            job_id='job-id',
            task='Do a task',
            url="http://mockserver:8080",
        )

        mock_create_conversation.assert_not_called()
        mock_post.assert_called_once_with(
            url='http://mockserver:8080/v1/assistants/assistant-id/model',
            headers=_EXPECTED_HEADERS,
            json={'text': 'Do a task', 'content_raw': '<p>Do a task</p>', 'stream': False, 'conversation_id': ANY},
            timeout=600,
        )
        self.assertTrue(logger.hasHandlers())

    @patch('codemie.triggers.actors.assistant.get_bind_key', return_value=_MOCK_BIND_KEY)
    @patch('codemie.triggers.actors.assistant.requests.post')
    def test_invoke_assistant_post_request_failure(self, mock_post, mock_get_bind_key):
        """Test invocation of assistant when POST request fails."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = RequestException("Failed request")
        mock_post.return_value = mock_response

        invoke_assistant(
            assistant_id='assistant-id',
            user_id='user-id',
            job_id='job-id',
            task='Do a task',
            url="http://mockserver:8080",
        )

        mock_post.assert_called_once_with(
            url='http://mockserver:8080/v1/assistants/assistant-id/model',
            headers=_EXPECTED_HEADERS,
            json={'text': 'Do a task', 'content_raw': '<p>Do a task</p>', 'stream': False, 'conversation_id': ANY},
            timeout=600,
        )
        self.assertTrue(logger.hasHandlers())
