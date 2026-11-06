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

"""Statements on the tool confirmation pause tables of a conversation.

``conversation_checkpoints`` holds the latest LangGraph checkpoint per (conversation, namespace) with the writes of its
checkpoint id. Each write is one ``INSERT ... ON CONFLICT DO UPDATE`` whose SET expression merges the writes inside
the database, so concurrent connections never lose each other's writes and Python holds no read-modify-write.

Sessions come from ``get_session``: inside a ``transactional_session`` the statements join the caller's transaction,
otherwise each call commits its own.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import cast, collate, delete, exists, func, literal, literal_column, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from codemie.clients.postgres import get_session
from codemie.rest_api.models.conversation_pause import ConversationCheckpointRow, ConversationPendingToolCallRow

_TABLE = ConversationCheckpointRow.__table__
_PENDING_TABLE = ConversationPendingToolCallRow.__table__
_PENDING_COLUMNS = (
    "root_assistant_id",
    "history_index",
    "original_user_message",
    "items",
    "thoughts",
    "payload",
)
_CHECKPOINT_KEY = ["conversation_id", "checkpoint_ns"]

_C = _TABLE.c
_P = _PENDING_TABLE.c
_PENDING_COLUMNS_LIST = [_P[name] for name in _PENDING_COLUMNS]


def _bytewise(value):
    """Compare ids bytewise (COLLATE "C"), as LangGraph checkpoint ids sort."""
    return collate(value, "C")


_OLD_WRITES = func.coalesce(_C.writes, cast(literal("{}"), JSONB))
_OLD_WRITE_ENTRIES = func.jsonb_each(_OLD_WRITES).table_valued("key", "value").alias("w")
# put: keep the stored writes of the new checkpoint id or newer; an older id's writes are dropped.
_WRITES_OF_THIS_ID_OR_NEWER = func.coalesce(
    select(func.jsonb_object_agg(_OLD_WRITE_ENTRIES.c.key, _OLD_WRITE_ENTRIES.c.value))
    .where(
        _bytewise(_OLD_WRITE_ENTRIES.c.value.op("->>")("checkpoint_id"))
        >= _bytewise(literal_column("excluded.checkpoint_id"))
    )
    .correlate(_TABLE)
    .scalar_subquery(),
    cast(literal("{}"), JSONB),
)


def _pending_of(conversation_id: str):
    return select(*_PENDING_COLUMNS_LIST).where(_P.conversation_id == conversation_id)


def _delete_pending_returning(conversation_id: str):
    return delete(_PENDING_TABLE).where(_P.conversation_id == conversation_id).returning(*_PENDING_COLUMNS_LIST)


def _delete_checkpoints(conversation_id: str):
    return delete(_TABLE).where(_C.conversation_id == conversation_id)


class ConversationPauseRepository:
    """Reads and writes the pause tables. Stateless; every method opens or joins a session."""

    def put_checkpoint(self, conversation_id: str, ns: str, entry: dict[str, Any]) -> None:
        """Replace the checkpoint of one namespace; its stored writes of this id or newer stay.

        ``entry`` carries ``checkpoint_id``, ``checkpoint`` and ``metadata`` (JSON values).
        """
        statement = pg_insert(_TABLE).values(
            conversation_id=conversation_id,
            checkpoint_ns=ns,
            checkpoint_id=entry["checkpoint_id"],
            checkpoint=entry["checkpoint"],
            metadata=entry["metadata"],
            writes={},
        )
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=_CHECKPOINT_KEY,
            set_={
                "checkpoint_id": excluded.checkpoint_id,
                "checkpoint": excluded.checkpoint,
                "metadata": excluded["metadata"],
                "writes": _WRITES_OF_THIS_ID_OR_NEWER,
            },
        )
        self._execute(statement, conversation_id)

    def put_writes(
        self,
        conversation_id: str,
        ns: str,
        checkpoint_id: str,
        regular: dict[str, Any],
        special: dict[str, Any],
    ) -> None:
        """Merge the writes of one checkpoint into the namespace row, creating a writes-only row when none exists.

        A regular key already stored is kept, a special key replaces it. Writes for a checkpoint id older than the
        stored one are ignored.
        """
        regular_writes = literal(regular, JSONB)
        special_writes = literal(special, JSONB)
        statement = pg_insert(_TABLE).values(
            conversation_id=conversation_id, checkpoint_ns=ns, writes=regular_writes.concat(special_writes)
        )
        statement = statement.on_conflict_do_update(
            index_elements=_CHECKPOINT_KEY,
            set_={"writes": regular_writes.concat(_OLD_WRITES).concat(special_writes)},
            where=or_(_C.checkpoint_id.is_(None), _bytewise(literal(checkpoint_id)) >= _bytewise(_C.checkpoint_id)),
        )
        self._execute(statement, conversation_id)

    def get_checkpoint(self, conversation_id: str, ns: str) -> dict[str, Any] | None:
        """The namespace row as ``{"checkpoint_id", "checkpoint", "metadata", "writes"}``, or None when absent.

        One select filtered by the conversation and the namespace. A writes-only row has no ``checkpoint`` key: it is
        not a checkpoint.
        """
        statement = select(_C.checkpoint_id, _C.checkpoint, _C.metadata, _C.writes).where(
            _C.conversation_id == conversation_id, _C.checkpoint_ns == ns
        )
        with get_session() as session:
            row = session.execute(statement).first()
        if row is None:
            return None
        checkpoint_id, checkpoint, metadata, writes = row
        entry = {"checkpoint_id": checkpoint_id, "writes": writes or {}}
        if checkpoint is not None:
            entry.update(checkpoint=checkpoint, metadata=metadata)
        return entry

    def delete_checkpoints(self, conversation_id: str) -> int:
        """Delete every checkpoint row of the conversation; returns how many went."""
        return self._execute(_delete_checkpoints(conversation_id), conversation_id)

    # --- pending row ---------------------------------------------------------------------------------------

    def write_pause(self, conversation_id: str, record: dict[str, Any]) -> None:
        """Store the pause of a conversation in one upsert, replacing any row.

        ``record`` carries ``root_assistant_id``, ``history_index``, ``original_user_message``, ``items``,
        ``thoughts`` and ``payload`` (JSON values).
        """
        values = {"conversation_id": conversation_id, **{column: record[column] for column in _PENDING_COLUMNS}}
        statement = pg_insert(_PENDING_TABLE).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["conversation_id"], set_={column: values[column] for column in _PENDING_COLUMNS}
        )
        self._execute(statement, conversation_id)

    def get_pending(self, conversation_id: str) -> dict[str, Any] | None:
        """The pending row as a dict of its columns, or None when the conversation has none."""
        with get_session() as session:
            row = session.execute(_pending_of(conversation_id)).mappings().first()
        return dict(row) if row is not None else None

    # --- consume and take -------------------------------------------------------------------------------------

    def consume(self, conversation_id: str, validate: Callable[[dict[str, Any]], None]) -> dict[str, Any] | None:
        """Take the pause for a resume in one transaction.

        It runs ``SELECT ... FOR UPDATE``, ``validate`` and ``DELETE ... RETURNING``.

        ``validate`` gets the row as a dict and raises to refuse it; the refusal rolls back and leaves the row as it
        was. Returns the deleted row, or None when there is none (of two concurrent calls exactly one gets the row).
        The checkpoint rows stay: the resumed run reads them.
        """
        with get_session() as session:
            row = session.execute(_pending_of(conversation_id).with_for_update()).mappings().first()
            if row is None:
                return None
            validate(dict(row))
            deleted = session.execute(_delete_pending_returning(conversation_id)).mappings().first()
            session.commit()
            return dict(deleted) if deleted is not None else None

    def take_pause(self, conversation_id: str) -> dict[str, Any] | None:
        """End a pause that will never resume: ``DELETE ... RETURNING`` its row; only on a row, its checkpoint rows.

        Without a row it is one statement: leftover checkpoint rows stay for the run-start and run-end deletes.
        Returns the deleted row, or None.
        """
        with get_session() as session:
            row = session.execute(_delete_pending_returning(conversation_id)).mappings().first()
            if row is not None:
                session.execute(_delete_checkpoints(conversation_id))
            session.commit()
            return dict(row) if row is not None else None

    def delete_checkpoints_if_no_pending(self, conversation_id: str) -> int:
        """Delete the checkpoint rows only when the conversation has no pending row, so a newer pause keeps them."""
        no_pending = ~exists().where(_P.conversation_id == conversation_id)
        return self._execute(_delete_checkpoints(conversation_id).where(no_pending), conversation_id)

    @staticmethod
    def _execute(statement, conversation_id: str) -> int:
        try:
            with get_session() as session:
                result = session.execute(statement)
                session.commit()
                return result.rowcount
        except IntegrityError as error:  # the foreign key: the conversation row must exist
            raise ValueError(f"Conversation not found: {conversation_id}") from error
