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
from unittest.mock import MagicMock, patch
from codemie.service.conversation_checkpoint_service import ConversationCheckpointService
from codemie.agents.tool_confirmation.models import ToolCallPendingEvent


@pytest.fixture
def mock_conversation():
    conv = MagicMock()
    conv.pending_checkpoint = None
    conv.pending_tool_call = None
    return conv


@pytest.fixture
def svc():
    return ConversationCheckpointService()


def test_save_checkpoint_writes_to_db(svc, mock_conversation):
    checkpoint_data = {"channel_values": {"messages": []}, "ts": "2026-01-01"}
    with patch(
        "codemie.service.conversation_checkpoint_service.Conversation.find_by_conversation_id",
        return_value=mock_conversation,
    ):
        svc.save_checkpoint("conv_1", checkpoint_data)
    assert mock_conversation.pending_checkpoint == checkpoint_data
    mock_conversation.update.assert_called_once()


def test_save_pending_tool_call_writes_to_db(svc, mock_conversation):
    tool_call = ToolCallPendingEvent(
        pending_tool_call_id="call_abc",
        tool_name="search",
        tool_args={"q": "test"},
    )
    with patch(
        "codemie.service.conversation_checkpoint_service.Conversation.find_by_conversation_id",
        return_value=mock_conversation,
    ):
        svc.save_pending_tool_call("conv_1", tool_call)
    assert mock_conversation.pending_tool_call == tool_call.model_dump()
    mock_conversation.update.assert_called_once()


def test_get_checkpoint_returns_stored_value(svc, mock_conversation):
    mock_conversation.pending_checkpoint = {"ts": "2026-01-01"}
    with patch(
        "codemie.service.conversation_checkpoint_service.Conversation.find_by_conversation_id",
        return_value=mock_conversation,
    ):
        result = svc.get_checkpoint("conv_1")
    assert result == {"ts": "2026-01-01"}


def test_get_checkpoint_returns_none_when_empty(svc, mock_conversation):
    mock_conversation.pending_checkpoint = None
    with patch(
        "codemie.service.conversation_checkpoint_service.Conversation.find_by_conversation_id",
        return_value=mock_conversation,
    ):
        result = svc.get_checkpoint("conv_1")
    assert result is None


def test_get_pending_tool_call_returns_parsed_event(svc, mock_conversation):
    mock_conversation.pending_tool_call = {
        "pending_tool_call_id": "call_abc",
        "tool_name": "search",
        "tool_args": {"q": "test"},
    }
    with patch(
        "codemie.service.conversation_checkpoint_service.Conversation.find_by_conversation_id",
        return_value=mock_conversation,
    ):
        result = svc.get_pending_tool_call("conv_1")
    assert isinstance(result, ToolCallPendingEvent)
    assert result.pending_tool_call_id == "call_abc"


def test_get_pending_tool_call_returns_none_when_empty(svc, mock_conversation):
    mock_conversation.pending_tool_call = None
    with patch(
        "codemie.service.conversation_checkpoint_service.Conversation.find_by_conversation_id",
        return_value=mock_conversation,
    ):
        result = svc.get_pending_tool_call("conv_1")
    assert result is None


def test_save_checkpoint_raises_when_conversation_not_found(svc):
    with patch(
        "codemie.service.conversation_checkpoint_service.Conversation.find_by_conversation_id",
        return_value=None,
    ):
        with pytest.raises(ValueError, match="Conversation not found: missing_id"):
            svc.save_checkpoint("missing_id", {"ts": "2026-01-01"})


def test_clear_nulls_both_columns(svc, mock_conversation):
    mock_conversation.pending_checkpoint = {"ts": "2026-01-01"}
    mock_conversation.pending_tool_call = {"pending_tool_call_id": "call_abc"}
    with patch(
        "codemie.service.conversation_checkpoint_service.Conversation.find_by_conversation_id",
        return_value=mock_conversation,
    ):
        svc.clear("conv_1")
    assert mock_conversation.pending_checkpoint is None
    assert mock_conversation.pending_tool_call is None
    mock_conversation.update.assert_called_once()
