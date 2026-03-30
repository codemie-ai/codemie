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
Tests for ConversationService pagination methods.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from codemie.service.conversation_service import ConversationService
from codemie.rest_api.models.conversation import Conversation, ConversationListItem


class TestGetUserConversationsPaginated:
    """Tests for get_user_conversations_paginated method.

    The method now uses a raw SQL query that projects only the scalar columns
    needed for ConversationListItem (no full history column) plus SQL MIN/MAX
    subqueries for timestamp bounds. Mocks must provide SQL-row-style attributes.
    """

    @staticmethod
    def _make_mock_row(conv_id: str, name: str = "Test Conv", **kwargs) -> MagicMock:
        """Return a mock representing one raw SQL result row."""
        row = MagicMock()
        row.conversation_id = conv_id
        row.conversation_name = name
        row.first_message = ""
        row.folder = kwargs.get("folder", None)
        row.assistant_ids = kwargs.get("assistant_ids", ["a1"])
        row.initial_assistant_id = kwargs.get("initial_assistant_id", "a1")
        row.pinned = kwargs.get("pinned", False)
        row.update_date = kwargs.get("update_date", datetime(2025, 1, 15))
        row.date = kwargs.get("date", datetime(2025, 1, 10))
        row.is_workflow_conversation = kwargs.get("is_workflow_conversation", False)
        row.very_first_msg_at = kwargs.get("very_first_msg_at", None)
        row.very_last_msg_at = kwargs.get("very_last_msg_at", None)
        return row

    @patch("codemie.service.conversation_service.get_session")
    def test_returns_conversation_list_items(self, mock_get_session):
        """Method returns a list of ConversationListItem from raw SQL rows."""
        mock_row = self._make_mock_row("conv-1", name="Test Conv", folder="folder-1")

        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = [mock_row]
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        result = ConversationService.get_user_conversations_paginated(user_id="user-123", page=0, per_page=10)

        assert len(result) == 1
        assert isinstance(result[0], ConversationListItem)
        assert result[0].id == "conv-1"

    @patch("codemie.service.conversation_service.get_session")
    def test_returns_timestamp_bounds_in_list_items(self, mock_get_session):
        """List items include very_first_msg_at/very_last_msg_at from SQL MIN/MAX subqueries."""
        t1 = datetime(2025, 1, 15, 8, 0, tzinfo=timezone.utc)
        t3 = datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc)

        mock_row = self._make_mock_row(
            "conv-ts",
            name="Conv TS",
            update_date=t3,
            date=t1,
            very_first_msg_at=t1,
            very_last_msg_at=t3,
        )

        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = [mock_row]
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        result = ConversationService.get_user_conversations_paginated(user_id="user-123", page=0, per_page=10)

        assert len(result) == 1
        assert result[0].very_first_msg_at == t1
        assert result[0].very_last_msg_at == t3

    @patch("codemie.service.conversation_service.get_session")
    def test_pagination_multiple_pages(self, mock_get_session):
        """Test pagination with 5 items, 2 per page = 3 pages."""
        all_rows = [self._make_mock_row(f"conv-{i}", name=f"Conv conv-{i}") for i in range(5)]

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        # Page 0: items 0,1
        mock_session.exec.return_value.all.return_value = all_rows[0:2]
        page0 = ConversationService.get_user_conversations_paginated(user_id="user-123", page=0, per_page=2)
        assert len(page0) == 2
        assert page0[0].id == "conv-0"
        assert page0[1].id == "conv-1"

        # Page 1: items 2,3
        mock_session.exec.return_value.all.return_value = all_rows[2:4]
        page1 = ConversationService.get_user_conversations_paginated(user_id="user-123", page=1, per_page=2)
        assert len(page1) == 2
        assert page1[0].id == "conv-2"
        assert page1[1].id == "conv-3"

        # Page 2: item 4 (last page, 1 item)
        mock_session.exec.return_value.all.return_value = all_rows[4:5]
        page2 = ConversationService.get_user_conversations_paginated(user_id="user-123", page=2, per_page=2)
        assert len(page2) == 1
        assert page2[0].id == "conv-4"

        # Page 3: empty (beyond data)
        mock_session.exec.return_value.all.return_value = []
        page3 = ConversationService.get_user_conversations_paginated(user_id="user-123", page=3, per_page=2)
        assert len(page3) == 0


class TestGetConversationHistorySlice:
    """Tests for get_conversation_history_slice method."""

    def _get_mock_row(self):
        """Helper to get a mock row with default data."""
        data = {
            "id": "conv-123",
            "conversation_id": "conv-123",
            "conversation_name": "Test Conv",
            "llm_model": "gpt-4",
            "folder": "folder-1",
            "pinned": False,
            "user_id": "user-123",
            "user_name": "User Name",
            "assistant_ids": ["a1"],
            "assistant_data": [],
            "initial_assistant_id": "a1",
            "final_user_mark": None,
            "final_operator_mark": None,
            "project": "proj-1",
            "mcp_server_single_usage": False,
            "is_workflow_conversation": False,
            "conversation_details": None,
            "assistant_details": None,
            "user_abilities": None,
            "date": datetime(2025, 1, 1),
            "update_date": datetime(2025, 1, 2),
            "total_count": 1,
            "very_first_msg_at": None,
            "very_last_msg_at": None,
        }
        row = MagicMock()
        for k, v in data.items():
            setattr(row, k, v)
        row._mapping = data
        return row

    @patch("codemie.service.conversation_service.materialize_history")
    @patch("codemie.service.conversation_service.get_session")
    def test_returns_history_slice(self, mock_get_session, mock_materialize):
        """Test that method returns paginated history and total count."""
        mock_session = MagicMock()

        # Call 1: Meta (counts + bounds in one statement)
        mock_result_meta = MagicMock()
        mock_result_meta.first.return_value = self._get_mock_row()

        # Call 2: History slice query
        mock_result_slice = MagicMock()
        mock_result_slice.all.return_value = [({"role": "User", "message": "Hello"},)]

        mock_session.exec.side_effect = [mock_result_meta, mock_result_slice]

        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        mock_materialize.side_effect = lambda msgs, _: msgs

        conversation, total, first_ts, last_ts = ConversationService.get_conversation_history_slice(
            conversation_id="conv-123", page=0, per_page=50
        )

        assert conversation is not None
        assert isinstance(conversation, Conversation)
        assert conversation.user_id == "user-123"
        assert conversation.assistant_ids == ["a1"]
        assert conversation.conversation_name == "Test Conv"
        assert conversation.folder == "folder-1"
        assert len(conversation.history) == 1
        assert total == 1
        assert first_ts is None
        assert last_ts is None

    @patch("codemie.service.conversation_service.get_session")
    def test_returns_none_if_not_found(self, mock_get_session):
        """Test that (None, 0) is returned when conversation not found."""
        mock_session = MagicMock()

        mock_result_meta = MagicMock()
        mock_result_meta.first.return_value = None
        mock_session.exec.return_value = mock_result_meta

        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        conversation, total, first_ts, last_ts = ConversationService.get_conversation_history_slice(
            conversation_id="non-existent", page=0, per_page=50
        )

        assert conversation is None
        assert total == 0
        assert first_ts is None
        assert last_ts is None

    @patch("codemie.service.conversation_service.materialize_history")
    @patch("codemie.service.conversation_service.get_session")
    def test_history_pagination_multiple_pages(self, mock_get_session, mock_materialize):
        """Test history pagination with 5 messages, 2 per page = 3 pages."""
        # 5 messages in history
        all_messages = [{"role": "User", "message": f"Message {i}"} for i in range(5)]

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        mock_materialize.side_effect = lambda msgs, _: msgs

        # Meta row: total_count lives on the same row as conversation columns (single meta query).
        meta_row = self._get_mock_row()
        meta_row._mapping = {**meta_row._mapping, "total_count": 5}
        meta_row.total_count = 5

        mock_result_meta = MagicMock()
        mock_result_meta.first.return_value = meta_row

        # Helper to create slice result
        def create_slice_result(msgs):
            res = MagicMock()
            res.all.return_value = [(m,) for m in msgs]
            return res

        # Four service calls × 2 DB execs each (meta + slice).
        mock_session.exec.side_effect = [
            mock_result_meta,
            create_slice_result(all_messages[0:2]),
            mock_result_meta,
            create_slice_result(all_messages[2:4]),
            mock_result_meta,
            create_slice_result(all_messages[4:5]),
            mock_result_meta,
            create_slice_result([]),
        ]

        # Page 0: messages 0,1
        conv0, total0, *_ = ConversationService.get_conversation_history_slice(
            conversation_id="conv-123", page=0, per_page=2
        )
        assert len(conv0.history) == 2
        assert conv0.history[0].message == "Message 0"
        assert total0 == 5

        # Page 1: messages 2,3
        conv1, total1, *_ = ConversationService.get_conversation_history_slice(
            conversation_id="conv-123", page=1, per_page=2
        )
        assert len(conv1.history) == 2
        assert conv1.history[0].message == "Message 2"
        assert total1 == 5

        # Page 2: message 4
        conv2, total2, *_ = ConversationService.get_conversation_history_slice(
            conversation_id="conv-123", page=2, per_page=2
        )
        assert len(conv2.history) == 1
        assert conv2.history[0].message == "Message 4"
        assert total2 == 5

        # Page 3: empty
        conv3, total3, *_ = ConversationService.get_conversation_history_slice(
            conversation_id="conv-123", page=3, per_page=2
        )
        assert len(conv3.history) == 0
        assert total3 == 5

    @patch("codemie.service.conversation_service.materialize_history")
    @patch("codemie.service.conversation_service.get_session")
    def test_sql_query_uses_conversation_id(self, mock_get_session, mock_materialize):
        """
        Test that the generated SQL query filters by 'conversation_id' column, not 'id'.
        This prevents empty results when PK 'id' and business key 'conversation_id' differ
        or have type mismatches (UUID vs String) in raw SQL.
        """
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        # Mock meta row (single query: conversation columns + total_count + bounds)
        mock_meta = MagicMock()
        mock_meta._mapping = {
            "conversation_id": "conv-123",
            "total_count": 0,
            "very_first_msg_at": None,
            "very_last_msg_at": None,
        }
        mock_meta.initial_assistant_id = None
        mock_meta.total_count = 0
        mock_meta.very_first_msg_at = None
        mock_meta.very_last_msg_at = None
        mock_result_meta = MagicMock()
        mock_result_meta.first.return_value = mock_meta

        # Mock slice response (empty list is fine for SQL check)
        mock_result_slice = MagicMock()
        mock_result_slice.all.return_value = []

        mock_session.exec.side_effect = [mock_result_meta, mock_result_slice]

        page = 2
        per_page = 5
        ConversationService.get_conversation_history_slice("conv-123", page, per_page)

        assert mock_session.exec.call_count == 2
        slice_call_args = mock_session.exec.call_args_list[1]
        sql_statement = str(slice_call_args[0][0])

        assert "WHERE c.conversation_id = :cid" in sql_statement, "SQL should filter by 'conversation_id', not 'id'"

        assert "LIMIT :lim" in sql_statement
        assert "OFFSET :off" in sql_statement

    @patch("codemie.service.conversation_service.materialize_history")
    @patch("codemie.service.conversation_service.get_session")
    def test_row_extraction_logic(self, mock_get_session, mock_materialize):
        """
        Test that JSON data is correctly extracted from SQLAlchemy Row objects.
        Simulates `session.exec(text(...))` returning a list of Row objects (tuple-like).
        """
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_meta = MagicMock()
        mock_meta._mapping = {
            "conversation_id": "conv-123",
            "total_count": 1,
            "very_first_msg_at": None,
            "very_last_msg_at": None,
        }
        mock_meta.initial_assistant_id = None
        mock_meta.total_count = 1
        mock_meta.very_first_msg_at = None
        mock_meta.very_last_msg_at = None
        mock_result_meta = MagicMock()
        mock_result_meta.first.return_value = mock_meta

        message_data = {
            "role": "User",
            "message": "Hello",
            "history_index": 0,
            "date": datetime(2025, 1, 1).isoformat(),
        }

        # Create a mock that behaves like a Row/Tuple with __getitem__
        class MockRow:
            def __getitem__(self, index):
                if index == 0:
                    return message_data
                raise IndexError

        mock_rows = [MockRow()]
        mock_result_slice = MagicMock()
        mock_result_slice.all.return_value = mock_rows

        mock_session.exec.side_effect = [mock_result_meta, mock_result_slice]

        # Passthrough for materialize
        mock_materialize.side_effect = lambda msgs, _: msgs

        conversation, total, first_ts, last_ts = ConversationService.get_conversation_history_slice("conv-123", 0, 10)

        assert conversation is not None
        assert len(conversation.history) == 1
        assert conversation.history[0].message == "Hello"
        assert total == 1
        assert first_ts is None
        assert last_ts is None

    @patch("codemie.service.conversation_service.materialize_history")
    @patch("codemie.service.conversation_service.get_session")
    def test_returns_sql_timestamp_bounds(self, mock_get_session, mock_materialize):
        """SQL MIN/MAX subquery values for very_first_msg_at/very_last_msg_at are forwarded."""
        t_first = datetime(2025, 6, 1, 8, 0, tzinfo=timezone.utc)
        t_last = datetime(2025, 6, 1, 18, 0, tzinfo=timezone.utc)

        meta_row = self._get_mock_row()
        meta_row.very_first_msg_at = t_first
        meta_row.very_last_msg_at = t_last
        meta_row._mapping = {**meta_row._mapping, "very_first_msg_at": t_first, "very_last_msg_at": t_last}

        mock_result_meta = MagicMock()
        mock_result_meta.first.return_value = meta_row

        mock_result_slice = MagicMock()
        mock_result_slice.all.return_value = []

        mock_session = MagicMock()
        mock_session.exec.side_effect = [mock_result_meta, mock_result_slice]
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        mock_materialize.side_effect = lambda msgs, _: msgs

        conversation, total, first_ts, last_ts = ConversationService.get_conversation_history_slice(
            conversation_id="conv-123", page=0, per_page=50
        )

        assert first_ts == t_first
        assert last_ts == t_last
