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

from typing import Optional

from codemie.agents.tool_confirmation.models import ToolCallPendingEvent
from codemie.rest_api.models.conversation import Conversation


class ConversationCheckpointService:
    """Persists LangGraph checkpoint and pending tool call info for a conversation.

    Graph state is stored in ``pending_checkpoint`` (JSONB).
    Pending tool call is stored in the separate ``pending_tool_call`` (JSONB) column.
    """

    def _get_conversation(self, conversation_id: str) -> Conversation:
        conv = Conversation.find_by_conversation_id(conversation_id)
        if conv is None:
            raise ValueError(f"Conversation not found: {conversation_id}")
        return conv

    def save_checkpoint(self, conversation_id: str, checkpoint: dict) -> None:
        conv = self._get_conversation(conversation_id)
        conv.pending_checkpoint = checkpoint
        conv.update()

    def save_pending_tool_call(self, conversation_id: str, tool_call: ToolCallPendingEvent) -> None:
        conv = self._get_conversation(conversation_id)
        conv.pending_tool_call = tool_call.model_dump()
        conv.update()

    def get_checkpoint(self, conversation_id: str) -> Optional[dict]:
        conv = self._get_conversation(conversation_id)
        return conv.pending_checkpoint

    def get_pending_tool_call(self, conversation_id: str) -> Optional[ToolCallPendingEvent]:
        conv = self._get_conversation(conversation_id)
        data = conv.pending_tool_call
        if data is None:
            return None
        return ToolCallPendingEvent(**data)

    def save_interrupt_context(
        self,
        conversation_id: str,
        history_index: Optional[int],
        original_user_message: str,
    ) -> None:
        """Attach history_index and original_user_message to the pending tool call after chat history is saved."""
        conv = self._get_conversation(conversation_id)
        pending_data = conv.pending_tool_call
        if pending_data is None:
            return
        if history_index is None:
            history_index = max(
                (m.history_index for m in (conv.history or []) if m.history_index is not None),
                default=0,
            )
        pending_data["history_index"] = history_index
        pending_data["original_user_message"] = original_user_message
        conv.pending_tool_call = pending_data
        conv.update()

    def clear(self, conversation_id: str) -> None:
        conv = self._get_conversation(conversation_id)
        conv.pending_checkpoint = None
        conv.pending_tool_call = None
        conv.update()
