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

"""A throwaway ``conversations`` table and the pause tables on a real Postgres for the checkpoint SQL.

Each use creates a schema with the columns the service writes and the pause tables the repository writes, points every connection's search path at it and
drops it afterwards: no existing table is read or written. ``ConversationCheckpointService`` runs on it unchanged
(its ``get_session`` opens a session of that engine; each statement commits as in production).
Live only: TEST_LOCAL_PG_URL names the server (e.g. the docker-compose ``postgres`` service).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session

import codemie.repository.conversation_pause_repository as pause_repository_module
from codemie.rest_api.models.conversation import Conversation  # noqa: F401  registers the table the FKs point at
from codemie.rest_api.models.conversation_pause import ConversationCheckpointRow, ConversationPendingToolCallRow
from codemie.service.conversation_checkpoint_service import ConversationCheckpointService

LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
live = pytest.mark.skipif(not LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

# ``id`` is the key the new pause tables reference. The old pause columns stay so the migration's old-format step
# (``_end_old_format_records``) can run on this table; the runtime never touches them.
_TABLE = (
    "CREATE TABLE conversations (id text PRIMARY KEY, conversation_id text GENERATED ALWAYS AS (id) STORED, "
    "pending_tool_call jsonb, pending_checkpoint jsonb, history jsonb)"
)


class PgCheckpointService(ConversationCheckpointService):
    """The real service: its checkpoint calls run through the repository on the throwaway schema."""


class PgConversations:
    def __init__(self, connection, engine=None):
        self.connection = connection
        # The engine the repository's sessions run on: a test can count the statements it sends.
        self.engine = engine

    def insert(self, conversation_id: str, history: list | None = None) -> None:
        self.connection.execute(
            text("INSERT INTO conversations (id, history) VALUES (:c, CAST(:h AS jsonb))"),
            {"c": conversation_id, "h": json.dumps(history or [])},
        )

    def checkpoint_rows(self, conversation_id: str) -> dict[str, dict]:
        """The conversation_checkpoints rows by namespace."""
        found = self.connection.execute(
            text(
                "SELECT checkpoint_ns, checkpoint_id, checkpoint, metadata, writes "
                "FROM conversation_checkpoints WHERE conversation_id = :c"
            ),
            {"c": conversation_id},
        ).all()
        return {
            ns: {"checkpoint_id": cid, "checkpoint": cp, "metadata": md, "writes": writes}
            for ns, cid, cp, md, writes in found
        }

    def pending_row(self, conversation_id: str) -> dict | None:
        """The conversation_pending_tool_calls row, or None."""
        found = (
            self.connection.execute(
                text(
                    "SELECT root_assistant_id, history_index, original_user_message, items, thoughts, payload "
                    "FROM conversation_pending_tool_calls WHERE conversation_id = :c"
                ),
                {"c": conversation_id},
            )
            .mappings()
            .first()
        )
        return dict(found) if found is not None else None


@contextmanager
def pg_conversations() -> Iterator[PgConversations]:
    """A throwaway schema (dropped afterwards); every connection sees only its table, so graph threads can write
    concurrently through their own sessions, as they do in production."""
    schema = f"cr_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(LOCAL_PG_URL, isolation_level="AUTOCOMMIT")
    engine = create_engine(LOCAL_PG_URL, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with admin.connect() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))
            connection.execute(text(f"SET search_path TO {schema}"))
            connection.execute(text(_TABLE))
            ConversationCheckpointRow.__table__.create(connection)
            ConversationPendingToolCallRow.__table__.create(connection)

        @contextmanager
        def session() -> Iterator[Session]:
            with Session(engine) as bound:
                yield bound

        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as reader:
            with patch.object(pause_repository_module, "get_session", session):
                yield PgConversations(reader, engine)
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        admin.dispose()
