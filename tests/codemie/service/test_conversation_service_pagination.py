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

from datetime import datetime
from unittest.mock import MagicMock, patch

from codemie.service.conversation_service import ConversationService
from codemie.rest_api.models.conversation import ConversationListItem, Conversation


class TestGetUserConversationsPaginated:
    """Tests for get_user_conversations_paginated method."""

    @patch("codemie.service.conversation_service.get_session")
    def test_returns_conversation_list_items(self, mock_get_session):
        """Test that method returns list of ConversationListItem."""
        # Mock conversation objects
        mock_conv = MagicMock()
        mock_conv.conversation_id = "conv-1"
        mock_conv.get_conversation_name.return_value = "Test Conv"
        mock_conv.folder = "folder-1"
        mock_conv.assistant_ids = ["a1"]
        mock_conv.initial_assistant_id = "a1"
        mock_conv.pinned = False
        mock_conv.update_date = datetime(2025, 1, 15)
        mock_conv.date = datetime(2025, 1, 10)
        mock_conv.is_workflow_conversation = False

        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = [mock_conv]
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        result = ConversationService.get_user_conversations_paginated(user_id="user-123", page=0, per_page=10)

        assert len(result) == 1
        assert isinstance(result[0], ConversationListItem)
        assert result[0].id == "conv-1"

    @patch("codemie.service.conversation_service.get_session")
    def test_pagination_multiple_pages(self, mock_get_session):
        """Test pagination with 5 items, 2 per page = 3 pages."""

        def make_mock_conv(conv_id):
            m = MagicMock()
            m.conversation_id = conv_id
            m.get_conversation_name.return_value = f"Conv {conv_id}"
            m.folder = None
            m.assistant_ids = ["a1"]
            m.initial_assistant_id = "a1"
            m.pinned = False
            m.update_date = datetime(2025, 1, 15)
            m.date = datetime(2025, 1, 10)
            m.is_workflow_conversation = False
            return m

        # Simulate 5 conversations in DB
        all_convs = [make_mock_conv(f"conv-{i}") for i in range(5)]

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        # Page 0: items 0,1
        mock_session.exec.return_value.all.return_value = all_convs[0:2]
        page0 = ConversationService.get_user_conversations_paginated(user_id="user-123", page=0, per_page=2)
        assert len(page0) == 2
        assert page0[0].id == "conv-0"
        assert page0[1].id == "conv-1"

        # Page 1: items 2,3
        mock_session.exec.return_value.all.return_value = all_convs[2:4]
        page1 = ConversationService.get_user_conversations_paginated(user_id="user-123", page=1, per_page=2)
        assert len(page1) == 2
        assert page1[0].id == "conv-2"
        assert page1[1].id == "conv-3"

        # Page 2: item 4 (last page, 1 item)
        mock_session.exec.return_value.all.return_value = all_convs[4:5]
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
        }
        row = MagicMock()
        for k, v in data.items():
            setattr(row, k, v)
        row._mapping = data
        return row

    @patch("codemie.service.conversation_service.materialize_history")
    @patch("codemie.service.conversation_service.get_session")
    def test_returns_history_slice(self, mock_get_session, mock_materialize):
        """Test that method returns paginated history."""
        mock_session = MagicMock()

        # Call 1: Metadata query
        mock_result_meta = MagicMock()
        mock_result_meta.first.return_value = self._get_mock_row()

        # Call 2: History slice query
        mock_result_slice = MagicMock()
        mock_result_slice.all.return_value = [({"role": "User", "message": "Hello"},)]

        mock_session.exec.side_effect = [mock_result_meta, mock_result_slice]

        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
        mock_materialize.side_effect = lambda msgs, _: msgs

        result = ConversationService.get_conversation_history_slice(conversation_id="conv-123", page=0, per_page=50)

        assert result is not None
        assert isinstance(result, Conversation)
        assert result.user_id == "user-123"
        assert result.assistant_ids == ["a1"]
        assert result.conversation_name == "Test Conv"
        assert result.folder == "folder-1"
        assert len(result.history) == 1

    @patch("codemie.service.conversation_service.get_session")
    def test_returns_none_if_not_found(self, mock_get_session):
        """Test that None is returned when conversation not found."""
        mock_session = MagicMock()

        mock_result_meta = MagicMock()
        mock_result_meta.first.return_value = None
        mock_session.exec.return_value = mock_result_meta

        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        result = ConversationService.get_conversation_history_slice(conversation_id="non-existent", page=0, per_page=50)

        assert result is None

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

        # Mock Metadata (reused)
        mock_result_meta = MagicMock()
        mock_result_meta.first.return_value = self._get_mock_row()

        # Helper to create slice result
        def create_slice_result(msgs):
            res = MagicMock()
            res.all.return_value = [(m,) for m in msgs]
            return res

        # We will make 4 calls to the service. Each call makes 2 DB queries (meta + slice).
        mock_session.exec.side_effect = [
            # Page 0
            mock_result_meta,
            create_slice_result(all_messages[0:2]),
            # Page 1
            mock_result_meta,
            create_slice_result(all_messages[2:4]),
            # Page 2
            mock_result_meta,
            create_slice_result(all_messages[4:5]),
            # Page 3
            mock_result_meta,
            create_slice_result([]),
        ]

        # Page 0: messages 0,1
        page0 = ConversationService.get_conversation_history_slice(conversation_id="conv-123", page=0, per_page=2)
        assert len(page0.history) == 2
        assert page0.history[0].message == "Message 0"

        # Page 1: messages 2,3
        page1 = ConversationService.get_conversation_history_slice(conversation_id="conv-123", page=1, per_page=2)
        assert len(page1.history) == 2
        assert page1.history[0].message == "Message 2"

        # Page 2: message 4
        page2 = ConversationService.get_conversation_history_slice(conversation_id="conv-123", page=2, per_page=2)
        assert len(page2.history) == 1
        assert page2.history[0].message == "Message 4"

        # Page 3: empty
        page3 = ConversationService.get_conversation_history_slice(conversation_id="conv-123", page=3, per_page=2)
        assert len(page3.history) == 0

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

        # Mock metadata response
        mock_meta = MagicMock()
        mock_meta._mapping = {
            "conversation_id": "conv-123",
            # 'history' field is deliberately excluded as the real meta_stmt does not select it
        }
        mock_meta.initial_assistant_id = None
        mock_session.exec.return_value.first.return_value = mock_meta

        # Mock slice response (empty list is fine for SQL check)
        mock_session.exec.return_value.all.return_value = []

        page = 2
        per_page = 5
        ConversationService.get_conversation_history_slice("conv-123", page, per_page)

        assert mock_session.exec.call_count >= 2
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
        mock_meta._mapping = {"conversation_id": "conv-123"}  # No 'history' key
        mock_meta.initial_assistant_id = None
        mock_session.exec.return_value.first.return_value = mock_meta

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
        mock_session.exec.return_value.all.return_value = mock_rows

        # Passthrough for materialize
        mock_materialize.side_effect = lambda msgs, _: msgs

        result = ConversationService.get_conversation_history_slice("conv-123", 0, 10)

        assert result is not None
        assert len(result.history) == 1
        assert result.history[0].message == "Hello"
