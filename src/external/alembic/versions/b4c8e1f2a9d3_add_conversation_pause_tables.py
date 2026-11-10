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
nothing is copied. Old-format pauses end here: both columns are nulled wherever either is set, and for a
conversation whose pending_tool_call is set the waiting steps of its last assistant entry become Cancelled. The
runtime never reads the old columns.

"Set" means a JSON value other than null. The columns were mapped as Column(JSONB), so a conversation saved with no
pause holds the JSON 'null', not SQL NULL: `IS NOT NULL` matches almost every conversation and made this revision
read and rewrite the whole table at startup. Those rows are left as they are; the columns are unmapped.

Locks: the migrations run at startup in one transaction while the previous version keeps serving. The old pauses are
ended before the tables are created, because a foreign key to conversations locks the table against writes until the
commit; done first, the scan holds no such lock and only the found rows are locked, each while it is changed. The wait
for any lock is bounded (`_LOCK_TIMEOUT`, for the rest of the transaction): a pod that cannot get one fails and retries
instead of queueing every writer of conversations behind it.
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
# Conversations read or written per round trip, by primary key. Small: a history can take megabytes.
_BATCH_SIZE = 50
# The longest wait for a lock, for this and every later statement of the migration transaction.
_LOCK_TIMEOUT = "10s"


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


def _is_set(column: Any) -> Any:
    """A stored pause: neither SQL NULL nor the JSON 'null' that Column(JSONB) wrote for Python None.

    ``jsonb_typeof`` is SQL NULL for SQL NULL and 'null' for the JSON null, so the comparison is false for both.
    """
    return sa.func.jsonb_typeof(column) != "null"


def _end_old_format_records(connection: sa.engine.Connection, batch_size: int = _BATCH_SIZE) -> None:
    """End every pause left in the old columns.

    The table is scanned once, without locking it (no index serves the filter), for the ids of the conversations where
    either column is set (see ``_is_set``). Everything after that is by primary key, in batches, each row locked
    (``FOR UPDATE``) and read again: a conversation the previous version changed after the scan is judged on what it
    holds now, and a message it appends cannot be overwritten by the history written back here. Only a conversation
    whose ``pending_tool_call`` is set has a waiting pause: its history is written back when a waiting step of its last
    assistant entry became Cancelled. Both columns are then nulled on every row of the batch.
    """
    conversations = sa.table(
        "conversations",
        sa.column("id"),
        sa.column("history", postgresql.JSONB),
        sa.column("pending_tool_call", postgresql.JSONB),
        sa.column("pending_checkpoint", postgresql.JSONB),
    )
    found_ids = list(
        connection.execute(
            sa.select(conversations.c.id).where(
                sa.or_(_is_set(conversations.c.pending_tool_call), _is_set(conversations.c.pending_checkpoint))
            )
        ).scalars()
    )
    paused = _is_set(conversations.c.pending_tool_call)

    for start in range(0, len(found_ids), batch_size):
        batch = found_ids[start : start + batch_size]
        rows = connection.execute(
            sa.select(conversations.c.id, conversations.c.history)
            .where(conversations.c.id.in_(batch), paused)
            .with_for_update()
        ).all()
        # The other rows of the batch (a checkpoint only, or no longer paused) are locked by the UPDATE below.
        for conversation_id, history in rows:
            cancelled = _cancelled_history(history)
            if isinstance(history, list) and cancelled != history:
                connection.execute(
                    sa.update(conversations).where(conversations.c.id == conversation_id).values(history=cancelled)
                )
        connection.execute(
            sa.update(conversations)
            .where(conversations.c.id.in_(batch))
            .values(pending_tool_call=sa.null(), pending_checkpoint=sa.null())
        )


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    # Before the tables: their foreign keys lock conversations against writes until the commit.
    _end_old_format_records(op.get_bind())
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
