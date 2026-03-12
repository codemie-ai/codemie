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
from unittest.mock import Mock, patch

from codemie.agents.assistant_agent import AIToolsAgent
from codemie.core.models import AssistantChatRequest
from codemie_tools.base.file_object import FileObject
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_user():
    user = Mock(spec=User)
    user.id = "test_user_id"
    user.name = "Test User"
    user.username = "Test User"
    return user


@pytest.fixture
def mock_request_with_file():
    request = Mock(spec=AssistantChatRequest)
    request.text = "This is a test request with attached files"
    request.file_names = ["encoded_file_url"]
    request.system_prompt = "Test system prompt"
    request.conversation_id = "test_conversation_id"
    request.history = []
    return request


@pytest.fixture
def mock_request_without_file():
    request = Mock(spec=AssistantChatRequest)
    request.text = "This is a test request without attached file"
    request.file_names = None
    request.system_prompt = "Test system prompt"
    request.conversation_id = "test_conversation_id"
    request.history = []
    return request


@pytest.fixture
def mock_file_object():
    file_obj = Mock(spec=FileObject)
    file_obj.name = "test_file.txt"
    return file_obj


def test_task_property_with_file_attachment(mock_user, mock_request_with_file, mock_file_object):
    """Tests the _task property when a file is attached to the request."""
    # Patch FileObject.from_encoded_url to return our mock file object
    with patch(
        'codemie.repository.base_file_repository.FileObject.from_encoded_url', return_value=mock_file_object
    ) as mock_from_encoded_url:
        # Create an AIToolsAgent instance with the mock request
        agent = AIToolsAgent(
            agent_name="TestAgent",
            description="A test agent",
            tools=[],
            request=mock_request_with_file,
            system_prompt="Test system prompt",
            request_uuid="test-uuid",
            user=mock_user,
            llm_model="test-llm-model",
        )

        # Access the _task property
        task = agent._task

        # Verify that from_encoded_url was called with the correct file name
        mock_from_encoded_url.assert_called_once_with("encoded_file_url")

        # Verify that the _task property returns the expected format
        expected_task = "This is a test request with attached files\n Attached files: test_file.txt"
        assert task == expected_task


def test_task_property_without_file_attachment(mock_user, mock_request_without_file):
    """Tests the _task property when no file is attached to the request."""
    # Create an AIToolsAgent instance with the mock request that has no file
    agent = AIToolsAgent(
        agent_name="TestAgent",
        description="A test agent",
        tools=[],
        request=mock_request_without_file,
        system_prompt="Test system prompt",
        request_uuid="test-uuid",
        user=mock_user,
        llm_model="test-llm-model",
    )

    # Access the _task property
    task = agent._task

    # Verify that the _task property returns only the request text
    expected_task = "This is a test request without attached file"
    assert task == expected_task


def test_task_property_with_multiline_request(mock_user):
    """Tests the _task property with multiline request text."""
    # Create a mock request with multiline text and file attachment
    mock_request_multiline = Mock(spec=AssistantChatRequest)
    mock_request_multiline.text = "This is a test request\nwith multiple lines\nand attached file"
    mock_request_multiline.file_names = ["encoded_file_url"]
    mock_request_multiline.system_prompt = "Test system prompt"
    mock_request_multiline.conversation_id = "test_conversation_id"
    mock_request_multiline.history = []

    # Create a mock file object with special characters in the name
    mock_file_object = Mock(spec=FileObject)
    mock_file_object.name = "test file with spaces.txt"

    # Patch FileObject.from_encoded_url
    with patch('codemie.repository.base_file_repository.FileObject.from_encoded_url', return_value=mock_file_object):
        # Create an AIToolsAgent instance
        agent = AIToolsAgent(
            agent_name="TestAgent",
            description="A test agent",
            tools=[],
            request=mock_request_multiline,
            system_prompt="Test system prompt",
            request_uuid="test-uuid",
            user=mock_user,
            llm_model="test-llm-model",
        )

        # Access the _task property
        task = agent._task

        # Verify that the _task property correctly handles multiline text and special characters
        expected_task = (
            "This is a test request\nwith multiple lines\nand attached file\n Attached files: test file with spaces.txt"
        )
        assert task == expected_task
