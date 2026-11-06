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


"""add conversation pause tables

Revision ID: b4c8e1f2a9d3
Revises: b7e2d4f6a8c1
Create Date: 2026-10-30 00:00:00.000000

EPMCDME-16463: the pause of a tool confirmation moves out of the conversations.pending_checkpoint and
conversations.pending_tool_call columns into two tables. The old columns are left in place and unmapped;
nothing is copied. Old-format pauses end here: both columns are nulled in one set-based UPDATE, and for a
conversation whose pending_tool_call is set the waiting steps of its last assistant entry become Cancelled. The
runtime never reads the old columns.
"""

from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
import sqlmodel

revision: str = "b4c8e1f2a9d3"
down_revision: Union[str, None] = "b7e2d4f6a8c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The text of a Cancelled step. A copy on purpose: a migration does not import runtime code.
_CANCELLED_MESSAGE = "Tool call was cancelled without a decision."
# Conversations with an old-format pause read per round trip.
_BATCH_SIZE = 500


def _cancel_waiting_steps(thoughts: Any) -> Any:
    """The thoughts with every step still waiting (``interrupted``), at any depth, turned into a Cancelled one."""
    if not isinstance(thoughts, list):
        return thoughts
    result = []
    for thought in thoughts:
        if isinstance(thought, dict):
            if thought.get("interrupted") is True:
                thought = {
                    **thought,
                    "interrupted": False,
                    "aborted": True,
                    "in_progress": False,
                    "message": _CANCELLED_MESSAGE,
                }
            if isinstance(thought.get("children"), list):
                thought = {**thought, "children": _cancel_waiting_steps(thought["children"])}
        result.append(thought)
    return result


def _cancelled_history(history: Any) -> list[Any]:
    """The history with the waiting steps of its last Assistant entry Cancelled; every other entry is unchanged."""
    entries = list(history) if isinstance(history, list) else []
    for index in range(len(entries) - 1, -1, -1):
        entry = entries[index]
        if isinstance(entry, dict) and entry.get("role") == "Assistant":
            entries[index] = {**entry, "thoughts": _cancel_waiting_steps(entry.get("thoughts"))}
            break
    return entries


def _end_old_format_records(connection: sa.engine.Connection, batch_size: int = _BATCH_SIZE) -> None:
    """End every pause left in the old columns.

    Only a conversation whose ``pending_tool_call`` is set has a waiting pause: those rows are read in batches
    (ordered by id) and a history is written back only when a waiting step of its last assistant entry became
    Cancelled. Then one set-based UPDATE nulls both columns wherever either is set.
    """
    conversations = sa.table(
        "conversations",
        sa.column("id"),
        sa.column("history", postgresql.JSONB),
        sa.column("pending_tool_call", postgresql.JSONB),
        sa.column("pending_checkpoint", postgresql.JSONB),
    )
    paused = sa.select(conversations.c.id, conversations.c.history).where(
        conversations.c.pending_tool_call.is_not(None)
    )
    last_id = None
    while True:
        batch = paused if last_id is None else paused.where(conversations.c.id > last_id)
        rows = connection.execute(batch.order_by(conversations.c.id).limit(batch_size)).all()
        if not rows:
            break
        for conversation_id, history in rows:
            cancelled = _cancelled_history(history)
            if isinstance(history, list) and cancelled != history:
                connection.execute(
                    sa.update(conversations).where(conversations.c.id == conversation_id).values(history=cancelled)
                )
        last_id = rows[-1][0]
    connection.execute(
        sa.update(conversations)
        .where(sa.or_(conversations.c.pending_tool_call.is_not(None), conversations.c.pending_checkpoint.is_not(None)))
        .values(pending_tool_call=sa.null(), pending_checkpoint=sa.null())
    )


def upgrade() -> None:
    op.create_table(
        "conversation_checkpoints",
        sa.Column("conversation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("checkpoint_ns", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("checkpoint_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("checkpoint", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("writes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("conversation_id", "checkpoint_ns"),
    )
    op.create_table(
        "conversation_pending_tool_calls",
        sa.Column("conversation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("root_assistant_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("history_index", sa.Integer(), nullable=True),
        sa.Column("original_user_message", sa.Text(), nullable=True),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("thoughts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    _end_old_format_records(op.get_bind())


def _end_new_format_pauses(connection: sa.engine.Connection, batch_size: int = _BATCH_SIZE) -> None:
    """End every pause kept in ``conversation_pending_tool_calls`` before its tables are dropped.

    The backend this downgrade returns to reads pauses only from the old columns, so a pause of this version can
    never be resumed there. Its waiting steps become Cancelled, as ``upgrade`` does for old-format pauses: the
    conversations with a pending row are read in batches (ordered by id) and a history is written back only when
    a waiting step of its last assistant entry became Cancelled.
    """
    conversations = sa.table("conversations", sa.column("id"), sa.column("history", postgresql.JSONB))
    pending = sa.table("conversation_pending_tool_calls", sa.column("conversation_id"))
    paused = sa.select(conversations.c.id, conversations.c.history).join(
        pending, pending.c.conversation_id == conversations.c.id
    )
    last_id = None
    while True:
        batch = paused if last_id is None else paused.where(conversations.c.id > last_id)
        rows = connection.execute(batch.order_by(conversations.c.id).limit(batch_size)).all()
        if not rows:
            break
        for conversation_id, history in rows:
            cancelled = _cancelled_history(history)
            if isinstance(history, list) and cancelled != history:
                connection.execute(
                    sa.update(conversations).where(conversations.c.id == conversation_id).values(history=cancelled)
                )
        last_id = rows[-1][0]


def downgrade() -> None:
    _end_new_format_pauses(op.get_bind())
    op.drop_table("conversation_pending_tool_calls")
    op.drop_table("conversation_checkpoints")
