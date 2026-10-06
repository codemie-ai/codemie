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

"""Shared real-session SQLite harness for ``Conversation.update()`` tests.

``conversation_sqlite_engine`` creates the real ``conversations`` table on an in-memory SQLite engine
and points ``Conversation.get_engine`` / ``PostgresClient.get_engine`` at it.
``conversation_update_sql`` records the SQL statements issued while each ``Conversation.update`` call runs
(one list per call), so per-site tests can assert "1 UPDATE, 0 SELECT" without counting the site's own reads.
"""

from __future__ import annotations

import json

import pytest
from pydantic.json import pydantic_encoder
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from codemie.clients.postgres import PostgresClient
from codemie.rest_api.models.conversation import Conversation


# JSONB has no SQLite compiler. Render it as plain JSON on SQLite (test-only). The same hook is registered in
# tests/codemie/core/workflow_models/test_workflow_config.py; re-registering simply replaces the identical rule.
@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def conversation_sqlite_engine(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        # Match PostgresClient's engine (src/codemie/clients/postgres.py): datetimes inside
        # JSONB-typed columns (e.g. a chat message's `date`) round-trip via pydantic_encoder in
        # production. SQLite's default JSON serializer has no datetime support, so a site that
        # persists a real (non-mocked) timestamp would otherwise fail here only, not in prod.
        json_serializer=lambda v: json.dumps(v, default=pydantic_encoder),
    )
    SQLModel.metadata.create_all(engine, tables=[Conversation.__table__])
    monkeypatch.setattr(Conversation, "get_engine", classmethod(lambda cls: engine))
    monkeypatch.setattr(PostgresClient, "get_engine", classmethod(lambda cls: engine))
    yield engine
    engine.dispose()


@pytest.fixture
def conversation_update_sql(conversation_sqlite_engine, monkeypatch):
    calls: list[list[str]] = []
    active: list[list[str]] = []

    def _listener(conn, cursor, statement, *a):
        if active:
            active[-1].append(statement)

    event.listen(conversation_sqlite_engine, "before_cursor_execute", _listener)
    original = Conversation.update

    def _wrapped(self, *args, **kwargs):
        active.append([])
        try:
            return original(self, *args, **kwargs)
        finally:
            calls.append(active.pop())

    monkeypatch.setattr(Conversation, "update", _wrapped)
    yield calls
    event.remove(conversation_sqlite_engine, "before_cursor_execute", _listener)
