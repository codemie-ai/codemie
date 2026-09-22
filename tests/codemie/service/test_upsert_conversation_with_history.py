# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Unit tests for import_source write-once semantics and header-to-source mapping."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import Response
from starlette.requests import Request

from codemie.rest_api.models.conversation import Conversation, GeneratedMessage, UpsertHistoryRequest
from codemie.rest_api.routers.conversation import upsert_conversation_history
from codemie.service.conversation_service import ConversationService

_resolve_import_source = ConversationService.resolve_chat_import_source


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = "user-1"
    user.name = "Test User"
    return user


def _make_request(folder=None):
    return UpsertHistoryRequest(
        assistant_id="asst-1",
        folder=folder,
        history=[GeneratedMessage(history_index=0, message="Hello", role="User")],
    )


def _make_conversation(import_source=None):
    return Conversation(
        id="conv-1",
        conversation_id="conv-1",
        user_id="user-1",
        user_name="Test User",
        history=[],
        assistant_ids=["asst-1"],
        initial_assistant_id="asst-1",
        import_source=import_source,
    )


# --- _resolve_import_source (router helper) ---


@pytest.mark.parametrize(
    "client_type,cli_header,expected",
    [
        ("codemie-claude", None, "claude_cli"),
        ("codemie-claude-acp", None, "claude_cli"),
        ("codemie-claude", "1.2.3", "claude_cli"),
        ("codemie-claude-acp", "1.2.3", "claude_cli"),
        ("claude-desktop", None, "claude_desktop"),
        ("claude-desktop", "1.2.3", "claude_desktop"),
        ("codemie-codex", None, "codex"),
        ("codemie-gemini", None, "gemini"),
        ("codemie-copilot", None, "copilot_cli"),
        ("codemie-opencode", None, "opencode"),
        ("codemie-pi", None, "pi"),
        ("codemie-kimi", None, "kimi"),
        ("codemie-kimi-acp", None, "kimi"),
        ("codemie-codex", "1.2.3", "codex"),
        ("unknown-client", "1.2.3", "claude_code"),
        (None, "1.2.3", "claude_code"),
        ("unknown-client", None, None),
        (None, None, None),
        ("", "", None),
    ],
)
def test_resolve_import_source_mapping(client_type, cli_header, expected):
    assert _resolve_import_source(client_type, cli_header) == expected


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"X-CodeMie-Client": "codemie-kimi", "X-CodeMie-CLI": "1.2.3"}, "kimi"),
        ({"X-CodeMie-Client": "unknown-client", "X-CodeMie-CLI": "1.2.3"}, "claude_code"),
        ({"X-CodeMie-CLI": "1.2.3"}, "claude_code"),
        ({"X-CodeMie-Client": "unknown-client"}, None),
        ({}, None),
    ],
)
@patch("codemie.rest_api.routers.conversation.ConversationService.upsert_conversation_with_history")
@patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", return_value=None)
def test_upsert_history_resolves_headers_before_calling_service(mock_find, mock_upsert, headers, expected, mock_user):
    mock_upsert.return_value = {
        "conversation_id": "conv-1",
        "new_messages": 1,
        "total_messages": 1,
        "created": True,
    }
    request = Request(
        {
            "type": "http",
            "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
        }
    )

    result = upsert_conversation_history(
        "conv-1",
        _make_request(),
        Response(),
        request,
        mock_user,
    )

    assert result.conversation_id == "conv-1"
    mock_find.assert_called_once_with("conv-1")
    mock_upsert.assert_called_once_with(
        conversation_id="conv-1",
        request=_make_request(),
        user=mock_user,
        import_source=expected,
    )


# --- ConversationService.upsert_conversation_with_history ---


@patch("codemie.service.conversation_service.ConversationService._upsert_conversation_metrics")
@patch("codemie.service.conversation_service.ConversationService._handle_conversation_folder")
@patch("codemie.service.conversation_service.ConversationService._append_new_messages", return_value=[])
@patch("codemie.rest_api.models.conversation.Conversation.update")
@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
def test_import_source_set_on_existing_conversation_when_null(
    mock_find, mock_update, mock_append, mock_handle_folder, mock_metrics
):
    """When an existing conversation has no import_source, it is written from the header-derived value."""
    conv = _make_conversation(import_source=None)
    mock_find.return_value = conv

    ConversationService.upsert_conversation_with_history(
        "conv-1", _make_request(), MagicMock(id="user-1", name="Test User"), import_source="claude_desktop"
    )

    assert conv.import_source == "claude_desktop"


@patch("codemie.service.conversation_service.ConversationService._upsert_conversation_metrics")
@patch("codemie.service.conversation_service.ConversationService._handle_conversation_folder")
@patch("codemie.service.conversation_service.ConversationService._append_new_messages", return_value=[])
@patch("codemie.rest_api.models.conversation.Conversation.update")
@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
def test_import_source_not_overwritten_on_existing_conversation(
    mock_find, mock_update, mock_append, mock_handle_folder, mock_metrics
):
    """When a conversation already has an import_source, a new value is NOT written."""
    conv = _make_conversation(import_source="claude_desktop")
    mock_find.return_value = conv

    ConversationService.upsert_conversation_with_history(
        "conv-1", _make_request(), MagicMock(id="user-1", name="Test User"), import_source="codex"
    )

    assert conv.import_source == "claude_desktop"


def test_import_source_passed_to_new_conversation(mock_user):
    """When a conversation is created, import_source is stored on the new instance."""
    conv = ConversationService._create_conversation_with_history(
        conversation_id="new-conv", user=mock_user, request=_make_request(), import_source="gemini"
    )
    assert conv.import_source == "gemini"


@patch("codemie.service.conversation_service.ConversationService._upsert_conversation_metrics")
@patch("codemie.service.conversation_service.ConversationService._handle_conversation_folder")
@patch("codemie.service.conversation_service.ConversationService._append_new_messages", return_value=[])
@patch("codemie.rest_api.models.conversation.Conversation.update")
@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
def test_no_write_when_import_source_is_none(mock_find, mock_update, mock_append, mock_handle_folder, mock_metrics):
    """When import_source is None (unknown client), the conversation import_source remains None."""
    conv = _make_conversation(import_source=None)
    mock_find.return_value = conv

    ConversationService.upsert_conversation_with_history(
        "conv-1", _make_request(), MagicMock(id="user-1", name="Test User"), import_source=None
    )

    assert conv.import_source is None
    assert mock_update.call_count == 1
