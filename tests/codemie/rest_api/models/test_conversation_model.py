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

from unittest.mock import patch, MagicMock

from codemie.rest_api.models.conversation import Conversation, ConversationListItem, GeneratedMessage
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


def _make_row(conversation_id, conversation_name, folder, update_date):
    """Build a mock DB row with named attributes matching the SQL query columns."""
    row = MagicMock()
    row.conversation_id = conversation_id
    row.conversation_name = conversation_name
    row.folder = folder
    row.assistant_ids = []
    row.initial_assistant_id = None
    row.pinned = False
    row.date = update_date
    row.update_date = update_date
    row.is_workflow_conversation = False
    return row


@patch('codemie.rest_api.models.conversation.get_session')
def test_conversation_search_by_name_and_user(mock_get_session):
    """Test search_by_name_and_user returns matching conversations."""
    user_id = 'user-123'
    query = 'admin'

    row1 = _make_row('conv-1', 'Admin Dashboard', '', datetime(2026, 4, 30, 12, 0, 0))
    row2 = _make_row('conv-2', 'Administrator Panel', 'Work', datetime(2026, 4, 29, 12, 0, 0))

    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [row1, row2]

    results = Conversation.search_by_name_and_user(user_id=user_id, query=query, limit=20)

    assert len(results) == 2
    assert isinstance(results[0], ConversationListItem)
    assert results[0].name == 'Admin Dashboard'
    assert results[1].name == 'Administrator Panel'
    assert results[1].folder == 'Work'

    # Verify the session was used
    mock_session.exec.assert_called_once()


@patch('codemie.rest_api.models.conversation.get_session')
def test_conversation_search_by_name_and_user_empty_results(mock_get_session):
    """Test search_by_name_and_user with no matches returns empty list."""
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = []

    results = Conversation.search_by_name_and_user(
        user_id='user-123',
        query='nonexistent',
        limit=20,
    )

    assert len(results) == 0
