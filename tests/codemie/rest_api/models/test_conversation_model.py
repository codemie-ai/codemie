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

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.conversation import ChatTurnData, Conversation, ConversationListItem, GeneratedMessage
from datetime import datetime
from codemie.core.models import ChatRole


def test_conversation_finished_at_defaults_none():
    conversation = Conversation(
        id="conv-1",
        conversation_id="conv-1",
        user_id="user-1",
        history=[],
    )
    assert conversation.finished_at is None


def test_guard_finished_raises_409_when_finished_at_set():
    from codemie.service.conversation_service import _guard_finished

    conversation = Conversation(
        id="conv-1",
        conversation_id="conv-1",
        user_id="user-1",
        history=[],
        finished_at=datetime(2026, 8, 11, 12, 0, 0),
    )
    with pytest.raises(ExtendedHTTPException) as exc_info:
        _guard_finished(conversation)
    assert exc_info.value.code == 409


def test_guard_finished_noop_when_not_finished():
    from codemie.service.conversation_service import _guard_finished

    conversation = Conversation(
        id="conv-1",
        conversation_id="conv-1",
        user_id="user-1",
        history=[],
    )
    _guard_finished(conversation)  # must not raise


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


def test_update_chat_history_replaces_only_latest_variant_for_same_turn():
    conversation = Conversation(
        conversation_id="test_id",
        history=[
            GeneratedMessage(history_index=20, message="User Msg 1", role=ChatRole.USER),
            GeneratedMessage(history_index=20, message="AI Msg 1", role=ChatRole.ASSISTANT),
            GeneratedMessage(history_index=20, message="User Msg 2", role=ChatRole.USER),
            GeneratedMessage(history_index=20, message="AI Msg 2", role=ChatRole.ASSISTANT),
        ],
    )

    conversation.update_chat_history(
        ChatTurnData(
            user_query="User Msg 3",
            user_query_raw="User Msg 3",
            assistant_id="assistant-id",
            assistant_response="AI Msg 3",
            thoughts=[],
            history_index=20,
            time_elapsed=0,
            input_tokens=0,
            output_tokens=0,
            file_names=[],
            money_spent=0.0,
        ),
        project="test-project",
        replace_latest_variant=True,
    )

    assert [message.message for message in conversation.history] == [
        "User Msg 1",
        "AI Msg 1",
        "User Msg 3",
        "AI Msg 3",
    ]


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


def _make_row(conversation_id, conversation_name, folder, update_date, finished_at=None):
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
    row.finished_at = finished_at
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
def test_conversation_search_by_name_and_user_includes_finished_at(mock_get_session):
    row = _make_row(
        'conv-1',
        'Admin Dashboard',
        '',
        datetime(2026, 4, 30, 12, 0, 0),
        finished_at=datetime(2026, 4, 30, 13, 0, 0),
    )

    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [row]

    results = Conversation.search_by_name_and_user(user_id='user-123', query='admin', limit=20)

    assert results[0].finished_at == datetime(2026, 4, 30, 13, 0, 0)


@patch('codemie.rest_api.models.conversation.get_session')
def test_get_user_conversations_includes_finished_at(mock_get_session):
    row = _make_row(
        'conv-1',
        'Admin Dashboard',
        '',
        datetime(2026, 4, 30, 12, 0, 0),
        finished_at=datetime(2026, 4, 30, 13, 0, 0),
    )
    row.very_first_msg_at = None
    row.very_last_msg_at = None
    row.assistant_icon = None
    row.assistant_names = []

    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [row]

    results = Conversation.get_user_conversations(user_id='user-123')

    assert results[0].finished_at == datetime(2026, 4, 30, 13, 0, 0)


@patch('codemie.rest_api.models.conversation.get_session')
def test_get_all_conversations_admin_filters_by_is_finished(mock_get_session):
    mock_session = MagicMock()
    row = MagicMock()
    row.conversation_id = "conv-1"
    row.conversation_name = "Test"
    row.folder = None
    row.pinned = False
    row.date = datetime(2026, 8, 11, 10, 0, 0)
    row.update_date = None
    row.assistant_ids = []
    row.initial_assistant_id = None
    row.is_workflow_conversation = False
    row.finished_at = None

    mock_session.exec.side_effect = [
        MagicMock(one=MagicMock(return_value=2)),
        MagicMock(all=MagicMock(return_value=[row])),
    ]
    mock_get_session.return_value.__enter__.return_value = mock_session

    items, total = Conversation.get_all_conversations_admin(is_finished=False, started_after=None, page=0, per_page=20)
    assert total == 2
    assert len(items) == 1
    assert items[0].finished_at is None


@patch('codemie.rest_api.models.conversation.get_session')
def test_get_all_conversations_admin_filters_by_project(mock_get_session):
    mock_session = MagicMock()
    row = MagicMock()
    row.conversation_id = "conv-1"
    row.conversation_name = "Test"
    row.folder = None
    row.pinned = False
    row.date = datetime(2026, 8, 11, 10, 0, 0)
    row.update_date = None
    row.assistant_ids = []
    row.initial_assistant_id = None
    row.is_workflow_conversation = False
    row.finished_at = None

    mock_session.exec.side_effect = [
        MagicMock(one=MagicMock(return_value=1)),
        MagicMock(all=MagicMock(return_value=[row])),
    ]
    mock_get_session.return_value.__enter__.return_value = mock_session

    items, total = Conversation.get_all_conversations_admin(
        is_finished=None, started_after=None, project="my-app", page=0, per_page=20
    )
    assert total == 1
    assert len(items) == 1


@patch('codemie.rest_api.models.conversation.get_session')
def test_get_all_conversations_admin_does_not_select_history(mock_get_session):
    mock_session = MagicMock()
    mock_session.exec.side_effect = [
        MagicMock(one=MagicMock(return_value=0)),
        MagicMock(all=MagicMock(return_value=[])),
    ]
    mock_get_session.return_value.__enter__.return_value = mock_session

    Conversation.get_all_conversations_admin(page=0, per_page=20)

    paginated_stmt = mock_session.exec.call_args_list[1].args[0]
    selected = [getattr(col, "key", None) or getattr(col, "name", str(col)) for col in paginated_stmt.selected_columns]
    assert "history" not in selected
    assert "conversation_id" in selected
    assert "finished_at" in selected


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


def test_conversation_exists_returns_true_when_found():
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.exec.return_value.first.return_value = "conv-id"

    with patch("codemie.rest_api.models.conversation.Session", return_value=mock_session):
        result = Conversation.exists("existing-id")

    assert result is True


def test_conversation_exists_returns_false_when_not_found():
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.exec.return_value.first.return_value = None

    with patch("codemie.rest_api.models.conversation.Session", return_value=mock_session):
        result = Conversation.exists("nonexistent-id")

    assert result is False


def test_get_existing_ids_returns_matching_set():
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.exec.return_value.all.return_value = ["id-1", "id-2"]

    with patch("codemie.rest_api.models.conversation.Session", return_value=mock_session):
        result = Conversation.get_existing_ids(["id-1", "id-2", "id-3"])

    assert result == {"id-1", "id-2"}


def test_get_existing_ids_returns_empty_set_for_empty_input():
    result = Conversation.get_existing_ids([])
    assert result == set()
