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

"""
Tests for the AIToolsAgent's _task property when no file is attached.
"""

import pytest
from uuid import uuid4

from codemie.agents.assistant_agent import AIToolsAgent
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.security.user import User


@pytest.fixture
def user():
    """Create a test user."""
    return User(
        id="1",  # String ID to comply with User model validation
        username="test_user",
        name="Test User",
        roles=[],
    )


@pytest.fixture
def agent_params():
    """Common parameters for creating an AIToolsAgent."""
    return {
        "agent_name": "test-agent",
        "description": "Test agent description",
        "tools": [],
        "system_prompt": "You are a helpful assistant.",
        "request_uuid": str(uuid4()),
        "llm_model": "gpt-3.5-turbo",
    }


def test_task_property_without_file_attachment(user, agent_params):
    """Tests the _task property when no file is attached to the request."""
    # Create an AssistantChatRequest with text but no file
    request_text = "Please answer this question"
    request = AssistantChatRequest(text=request_text, file_name=None, sender_id=user.id)

    # Create an AIToolsAgent instance
    agent = AIToolsAgent(request=request, user=user, **agent_params)

    # Access the _task property
    task = agent._task

    # Verify that the task property returns only the request text
    assert task == request_text


def test_task_property_with_empty_text(user, agent_params):
    """Tests the _task property with an empty request text."""
    # Create an AssistantChatRequest with empty text
    request = AssistantChatRequest(text="", file_name=None, sender_id=user.id)

    # Create an AIToolsAgent instance
    agent = AIToolsAgent(request=request, user=user, **agent_params)

    # Access the _task property
    task = agent._task

    # Verify that the task property returns an empty string
    assert task == ""


def test_task_property_with_empty_string_filename(user, agent_params):
    """Tests the _task property with file_name as empty string."""
    # Create an AssistantChatRequest with text and empty string file_name
    request_text = "Please answer this question"
    request = AssistantChatRequest(text=request_text, file_name="", sender_id=user.id)

    # Create an AIToolsAgent instance
    agent = AIToolsAgent(request=request, user=user, **agent_params)

    # Access the _task property
    task = agent._task

    # Verify that the task property returns only the request text
    assert task == request_text
