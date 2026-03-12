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
from codemie.chains.base import Thought
from codemie.agents.callbacks.agent_invoke_callback import AgentInvokeCallback


@pytest.fixture
def callback():
    return AgentInvokeCallback()


def test_thought_processing_with_new_thought(callback):
    thought = Thought(
        id="test-id",
        message="Test message",
        author_name="Test Author",
        children=[],
        author_type="Tool",
        parent_id=None,
        in_progress=False,
    )

    callback.thought_processing(thought)

    assert len(callback.thoughts) == 1
    assert callback.thoughts[0]['id'] == "test-id"
    assert callback.thoughts[0]['message'] == "Test message"
    assert callback.thoughts[0]['author_name'] == "Test Author"


def test_thought_processing_with_existing_thought(callback):
    # Create a child thought
    child_thought = Thought(
        id="child-id",
        message="Child message",
        author_name="Child Author",
        children=[],
        author_type="Tool",
        parent_id=None,
        in_progress=False,
    )

    # First thought
    thought = Thought(
        id="test-id",
        message="Initial message",
        author_name="Test Author",
        children=[],
        author_type="Tool",
        parent_id=None,
        in_progress=False,
    )

    callback.thought_processing(thought)

    # Updated thought with the same ID
    updated_thought = Thought(
        id="test-id",
        message=" Updated message",
        author_name="Test Author",
        children=[child_thought],  # Add a Thought object as child
        author_type="Tool",
        parent_id=None,
        in_progress=False,
    )

    callback.thought_processing(updated_thought)

    assert len(callback.thoughts) == 1
    assert callback.thoughts[0]['message'] == "Initial message Updated message"
    assert len(callback.thoughts[0]['children']) == 1
    assert callback.thoughts[0]['children'][0].id == "child-id"


def test_thought_processing_with_none(callback):
    callback.thought_processing(None)
    assert len(callback.thoughts) == 0


def test_set_current_thought(callback):
    callback.set_current_thought("test_tool")
    assert callback.current_thought is not None
    assert callback.current_thought.author_name == "Test Tool"
    assert callback.current_thought.author_type == "Tool"


def test_reset_current_thought(callback):
    callback.set_current_thought("test_tool")
    assert callback.current_thought is not None
    callback.reset_current_thought()
    assert callback.current_thought is None


def test_current_thought_property(callback):
    assert callback.current_thought is None
    callback.set_current_thought("test_tool")
    assert callback.current_thought is not None
    assert callback.current_thought.author_name == "Test Tool"


def test_escape_message(callback):
    message = "test}{message"
    escaped = callback._escape_message(message)
    assert escaped == "test}_{message"
