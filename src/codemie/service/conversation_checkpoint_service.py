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

from collections.abc import Callable
from typing import Any

from codemie.core.tool_step_states import cancel_waiting_steps
from codemie.agents.tool_confirmation.models import StoredPendingToolCall
from codemie.chains.base import Thought
from codemie.core.constants import ChatRole
from codemie.repository.conversation_pause_repository import ConversationPauseRepository
from codemie.rest_api.models.conversation import Conversation


class ConversationCheckpointService:
    """The pause lifecycle over ``ConversationPauseRepository``: the pending row and the deletes of checkpoint rows.

    The graph state lives in ``conversation_checkpoints`` (read and written by ``ConversationCheckpointSaver``
    directly) and the pause in ``conversation_pending_tool_calls``. Every write is one statement on those tables,
    except the ordinary ``Conversation`` save of cancelled steps.
    """

    @staticmethod
    def _repository() -> ConversationPauseRepository:
        return ConversationPauseRepository()

    # --- pending row ---------------------------------------------------------------------------------------

    def get_pending(self, conversation_id: str) -> StoredPendingToolCall | None:
        """The conversation's pending row, or None when it has none."""
        row = self._repository().get_pending(conversation_id)
        if row is None:
            return None
        return StoredPendingToolCall.model_validate(row)

    def write_pause(self, conversation_id: str, record: StoredPendingToolCall) -> None:
        """Store a pause in one upsert, replacing the conversation's row."""
        self._repository().write_pause(conversation_id, record.model_dump(mode="json"))

    def delete_checkpoints(self, conversation_id: str) -> int:
        """Delete the conversation's checkpoint rows only (a run's start); returns how many went."""
        return self._repository().delete_checkpoints(conversation_id)

    def consume(
        self, conversation_id: str, validate: Callable[[StoredPendingToolCall], None]
    ) -> StoredPendingToolCall | None:
        """Take the pause for a resume in one transaction (see ``ConversationPauseRepository.consume``).

        ``validate`` gets the parsed row and raises to refuse it, which leaves the row as it was. None when there is
        no row (a lost race included).
        """
        row = self._repository().consume(
            conversation_id, lambda raw: validate(StoredPendingToolCall.model_validate(raw))
        )
        return StoredPendingToolCall.model_validate(row) if row is not None else None

    def cancel_pause(self, conversation: Conversation) -> bool:
        """A new message arrived (rule 7): end a waiting pause and cancel the steps it leaves waiting.

        One ``DELETE ... RETURNING`` of the pending row. On a row its checkpoint rows go too and the still-waiting
        steps of every entry at its ``history_index`` become Cancelled. Without a row the still-waiting steps of the
        conversation's last assistant entry, whose pause is gone, become Cancelled. ``conversation`` is the one the
        turn start already loaded; it is saved with an ordinary ``update`` only when a step changed. Returns whether
        a pending row was found.
        """
        taken = self._repository().take_pause(conversation.id)
        if taken is None:
            self.cancel_stale_steps(conversation)
            return False
        entries = [m for m in conversation.history or [] if m.history_index == taken.get("history_index")]
        if _cancel_waiting_steps_of(entries):
            conversation.update()
        return True

    @staticmethod
    def cancel_stale_steps(conversation: Conversation) -> None:
        """Cancel the still-waiting steps of the last assistant entry, whose pause is gone (rule 7.2).

        Used with no pending row: by a new message and by a resume that found none (404), so a reload shows no
        Allow/Deny that only answers 404. Saves with an ordinary ``update`` only when a step changed.
        """
        last = next((m for m in reversed(conversation.history or []) if m.role == ChatRole.ASSISTANT.value), None)
        if last is not None and _cancel_waiting_steps_of([last]):
            conversation.update()

    def delete_checkpoints_if_no_pending(self, conversation_id: str) -> int:
        """Delete the checkpoint rows when the conversation has no pending row; returns how many went."""
        return self._repository().delete_checkpoints_if_no_pending(conversation_id)


def _cancel_waiting_steps_of(entries: list[Any]) -> bool:
    """Cancel the still-waiting steps of ``entries``; returns whether any step changed."""
    changed = False
    for message in entries:
        if not message.thoughts:
            continue
        thoughts = [thought.model_dump() for thought in message.thoughts]
        if cancel_waiting_steps(thoughts):
            message.thoughts = [Thought.model_validate(thought) for thought in thoughts]
            changed = True
    return changed
