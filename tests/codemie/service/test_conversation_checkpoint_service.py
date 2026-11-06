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

from unittest.mock import patch

import pytest

from codemie.core.tool_step_states import cancel_waiting_steps
from codemie.agents.tool_confirmation.models import DecisionItem, StoredPendingToolCall, ToolCallPendingEvent
from codemie.chains.base import Thought
from codemie.core.tool_step_states import CANCELLED_MESSAGE
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage
from codemie.service.conversation_checkpoint_service import ConversationCheckpointService

_REPOSITORY = "codemie.service.conversation_checkpoint_service.ConversationPauseRepository"


@pytest.fixture
def svc():
    return ConversationCheckpointService()


def _stored() -> StoredPendingToolCall:
    item = DecisionItem(
        thought_id="t0",
        tool_call_id="call_0",
        location_key="",
        checkpoint_ns="ns",
        event=ToolCallPendingEvent(pending_tool_call_id="call_0", tool_name="w", tool_args={}),
    )
    return StoredPendingToolCall(
        items=[item],
        root_assistant_id="a",
        history_index=1,
        original_user_message="go",
        thoughts=[{"id": "t0", "interrupted": True}],
        payload={"carried_ids": []},
    )


def _conversation(index: int) -> Conversation:
    return Conversation(
        id="conv_1",
        conversation_id="conv_1",
        user_id="u",
        history=[
            GeneratedMessage(
                role="Assistant", message="", history_index=index, thoughts=[Thought(id="w", interrupted=True)]
            ),
            GeneratedMessage(
                role="Assistant", message="", history_index=index + 1, thoughts=[Thought(id="x", interrupted=True)]
            ),
        ],
    )


def test_cancel_pause_with_nothing_pending_and_nothing_waiting_writes_nothing(svc):
    conversation = Conversation(
        id="conv_1",
        conversation_id="conv_1",
        user_id="u",
        history=[GeneratedMessage(role="Assistant", message="", history_index=0, thoughts=[Thought(id="done")])],
    )
    with patch(_REPOSITORY) as repository, patch.object(Conversation, "update") as update:
        repository.return_value.take_pause.return_value = None
        cancelled = svc.cancel_pause(conversation)
    assert cancelled is False
    repository.return_value.take_pause.assert_called_once_with("conv_1")
    update.assert_not_called()


# --- the pure step helper ----------------------------------------------------------------------------------


def _step(step_id: str, children=None, **fields) -> dict:
    return {"id": step_id, "interrupted": False, "children": children or [], **fields}


def test_cancel_waiting_steps_cancels_only_waiting_steps_at_any_depth():
    thoughts = [
        _step("a", [_step("b", interrupted=True, message=None)]),
        _step("done", message="result"),
        _step("top", interrupted=True),
    ]

    cancel_waiting_steps(thoughts)

    nested = thoughts[0]["children"][0]
    assert (nested["interrupted"], nested["aborted"], nested["in_progress"], nested["message"]) == (
        False,
        True,
        False,
        CANCELLED_MESSAGE,
    )
    assert thoughts[1] == _step("done", message="result")
    assert thoughts[2]["message"] == CANCELLED_MESSAGE
