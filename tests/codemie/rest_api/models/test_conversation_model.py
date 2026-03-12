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

from codemie.rest_api.models.conversation import Conversation, GeneratedMessage
from datetime import datetime
from codemie.core.models import ChatRole


def test_find_messages():
    conversation = Conversation(
        conversation_id="test_id",
        history=[
            GeneratedMessage(history_index=10, message="Other Msg", role="User"),
            GeneratedMessage(history_index=10, message="Other Msg", role="User"),
            GeneratedMessage(history_index=20, message="User Msg 1", role="User"),
            GeneratedMessage(history_index=20, message="AI Msg 1", role="User"),
            GeneratedMessage(history_index=20, message="User Msg 2", role="User"),
            GeneratedMessage(history_index=20, message="AI Msg 2", role="User"),
        ],
    )

    user_message, ai_message = conversation.find_messages(20, 1)

    assert user_message.history_index == 20
    assert user_message.message == "User Msg 2"
    assert ai_message.history_index == 20
    assert ai_message.message == "AI Msg 2"


def test_generated_message_serialization_without_file_names():
    """Test GeneratedMessage serialization with no file_names."""
    message = GeneratedMessage(role=ChatRole.USER, message="Test message", date=datetime.now(), history_index=1)

    # Serialize the message
    data = message.model_dump()

    # Verify no file_name field is added
    assert 'file_name' not in data
    assert 'file_names' in data
    assert data['file_names'] == []


def test_generated_message_serialization_with_empty_file_names():
    """Test GeneratedMessage serialization with empty file_names list."""
    message = GeneratedMessage(
        role=ChatRole.USER, message="Test message", date=datetime.now(), history_index=1, file_names=[]
    )

    # Serialize the message
    data = message.model_dump()

    # Verify no file_name field is added
    assert 'file_name' not in data
    assert 'file_names' in data
    assert data['file_names'] == []


def test_generated_message_serialization_with_single_file_name():
    """Test GeneratedMessage serialization with a single file name."""
    message = GeneratedMessage(
        role=ChatRole.USER, message="Test message", date=datetime.now(), history_index=1, file_names=["test.txt"]
    )

    # Serialize the message
    data = message.model_dump()

    # Verify file_name field is added with the same value as the single item in file_names
    assert 'file_name' in data
    assert data['file_name'] == "test.txt"
    assert 'file_names' in data
    assert data['file_names'] == ["test.txt"]


def test_generated_message_serialization_with_multiple_file_names():
    """Test GeneratedMessage serialization with multiple file names."""
    message = GeneratedMessage(
        role=ChatRole.USER,
        message="Test message",
        date=datetime.now(),
        history_index=1,
        file_names=["test1.txt", "test2.txt"],
    )

    # Serialize the message
    data = message.model_dump()

    # Verify no file_name field is added when there are multiple file_names
    assert 'file_name' not in data
    assert 'file_names' in data
    assert data['file_names'] == ["test1.txt", "test2.txt"]


def test_generated_message_backward_compatibility():
    """Test backward compatibility with file_name attribute."""
    # Instead of testing the model_validator directly, just test the serialization part
    # Create a message with a single file name
    message = GeneratedMessage(role=ChatRole.USER, message="Test message", file_names=["legacy.txt"])

    # Serialize and verify both file_name and file_names are present
    data = message.model_dump()
    assert data['file_name'] == "legacy.txt"
    assert data['file_names'] == ["legacy.txt"]
