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

"""The checkpoint service's SQL on a real Postgres (AC4, rule 9): namespace merge rules, the pending row, old records.

Live only (TEST_LOCAL_PG_URL); every test runs on a throwaway table, dropped afterwards. Run it with
``TEST_LOCAL_PG_URL=postgresql://postgres:password@localhost:5432/postgres poetry run pytest <this file>``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from codemie.chains.base import Thought
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage

from codemie.agents.tool_confirmation.models import DecisionItem, StoredPendingToolCall, ToolCallPendingEvent
from codemie.repository.conversation_pause_repository import ConversationPauseRepository
from codemie.service.conversation_checkpoint_service import ConversationCheckpointService
from tests.codemie.service.pg_conversations import live, pg_conversations

pytestmark = live

CONV = "conv-pg"
NS = "s:1"


@pytest.fixture
def db():
    with pg_conversations() as conversations:
        yield conversations


def _write(cid: str, idx: int, task: str = "t1", channel: str = "messages") -> dict:
    return {
        f"{cid}|{task}|{idx}": {
            "checkpoint_id": cid,
            "task_id": task,
            "idx": idx,
            "channel": channel,
            "value": f"{cid}/{idx}",
        }
    }


def _entry(cid: str) -> dict:
    return {"checkpoint_id": cid, "checkpoint": f"cp-{cid}", "metadata": "m"}


# --- the checkpoint rows: put_checkpoint / put_writes merge rules on the conversation_checkpoints table -----


def _repo() -> ConversationPauseRepository:
    return ConversationPauseRepository()


def _row(db, ns: str = NS) -> dict:
    return db.checkpoint_rows(CONV)[ns]


# --- concurrent connections: each statement merges inside the database --------------------------------------


# --- the pending row -----------------------------------------------------------------------------------------


def _item(index: int, auto_allowed: bool = False) -> DecisionItem:
    return DecisionItem(
        thought_id=f"thought_{index}",
        tool_call_id=f"call_{index}",
        location_key="",
        checkpoint_ns="post_model_hook:1",
        event=ToolCallPendingEvent(pending_tool_call_id=f"call_{index}", tool_name="write", tool_args={}),
        auto_allowed=auto_allowed,
    )


def _stored(history_index: int | None = 1, items: list[DecisionItem] | None = None) -> StoredPendingToolCall:
    return StoredPendingToolCall(
        items=items or [_item(0)],
        root_assistant_id="a",
        history_index=history_index,
        original_user_message="go",
        thoughts=[{"id": "thought_0", "interrupted": True}],
        payload={"carried_ids": ["c1"]},
    )


def _pause(db, history_index: int | None = 1) -> ConversationCheckpointService:
    db.insert(CONV)
    service = ConversationCheckpointService()
    service.write_pause(CONV, _stored(history_index))
    return service


def test_the_orm_does_not_map_the_old_columns_so_a_whole_row_save_cannot_restore_them():
    columns = {column.name for column in Conversation.__table__.columns}
    assert "pending_tool_call" not in columns and "pending_checkpoint" not in columns


# --- consume: one transaction, select for update, validate, delete returning ------------------------------------


# --- cancel_pause (rule 7) ------------------------------------------------------------------------------------


def _thought(step_id: str, interrupted: bool, message: str | None = None, children=None) -> Thought:
    return Thought(id=step_id, interrupted=interrupted, message=message, children=children or [])


def _conversation(history_index: int = 1) -> Conversation:
    nested = _thought("delegation", False, children=[_thought("w0", True), _thought("done", False, "result")])
    other = _thought("w9", True)
    return Conversation(
        id=CONV,
        conversation_id=CONV,
        user_id="u",
        history=[
            GeneratedMessage(role="User", message="a", history_index=0),
            GeneratedMessage(role="Assistant", message="", history_index=0, thoughts=[other]),
            GeneratedMessage(role="User", message="b", history_index=history_index),
            # two variants of the paused turn (a regeneration keeps the older one)
            GeneratedMessage(role="Assistant", message="", history_index=history_index, thoughts=[nested]),
            GeneratedMessage(
                role="Assistant", message="", history_index=history_index, thoughts=[_thought("w1", True)]
            ),
        ],
    )


def _cancel(conversation: Conversation) -> tuple[bool, MagicMock]:
    with patch.object(Conversation, "update") as update:
        return ConversationCheckpointService().cancel_pause(conversation), update
