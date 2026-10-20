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

"""Transaction-composition semantics of get_session()/transactional_session().

These guard the atomicity fix for changing a project-scoped integration's project: the field
update (SettingsService.update_settings -> BaseModelWithSQLSupport.update()) and the move
(SettingsTransferService.transfer_setting) must land in one transaction, so a failure in the move
rolls back the field update too instead of leaving the setting updated-but-not-moved.
"""

from __future__ import annotations

from typing import Optional

import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

from codemie.clients import postgres
from codemie.clients.postgres import get_session, transactional_session
from codemie.rest_api.models.base import BaseModelWithSQLSupport


class _TxnProbe(BaseModelWithSQLSupport, table=True):
    __tablename__ = "txn_probe"

    value: Optional[str] = None


@pytest.fixture
def sqlite_engine(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _TxnProbe.__table__.create(engine)
    monkeypatch.setattr(postgres.PostgresClient, "get_engine", classmethod(lambda cls: engine))
    return engine


def _values(engine) -> list[str]:
    with get_session() as session:
        return [row[0] for row in session.execute(text("SELECT value FROM txn_probe ORDER BY value")).all()]


class TestTransactionalSession:
    def test_nested_get_session_joins_ambient_and_commits_once(self, sqlite_engine):
        # Arrange / Act
        with transactional_session() as session:
            session.execute(text("INSERT INTO txn_probe (id, value) VALUES ('1', 'a')"))
            with get_session() as joined:
                joined.execute(text("INSERT INTO txn_probe (id, value) VALUES ('2', 'b')"))
                joined.commit()  # no-op: only the owner finalizes the shared transaction

        # Assert: both writes are visible, i.e. they committed together
        assert _values(sqlite_engine) == ["a", "b"]

    def test_exception_rolls_back_all_joined_writes(self, sqlite_engine):
        # Arrange / Act
        with pytest.raises(RuntimeError):
            with transactional_session() as session:
                session.execute(text("INSERT INTO txn_probe (id, value) VALUES ('1', 'a')"))
                with get_session() as joined:
                    joined.execute(text("INSERT INTO txn_probe (id, value) VALUES ('2', 'b')"))
                    joined.commit()  # no-op
                raise RuntimeError("boom")

        # Assert: the no-op commit did not persist 'b', and rollback undid 'a' too
        assert _values(sqlite_engine) == []

    def test_get_session_without_ambient_owns_its_transaction(self, sqlite_engine):
        # Arrange / Act: default path is unchanged — each get_session commits independently
        with get_session() as session:
            session.execute(text("INSERT INTO txn_probe (id, value) VALUES ('1', 'a')"))
            session.commit()

        # Assert
        assert _values(sqlite_engine) == ["a"]

    def test_nested_transactional_session_defers_commit_to_outer_owner(self, sqlite_engine):
        # Arrange / Act: an inner transactional_session must not finalize the outer transaction
        with pytest.raises(RuntimeError):
            with transactional_session():
                with transactional_session() as inner:
                    inner.execute(text("INSERT INTO txn_probe (id, value) VALUES ('1', 'a')"))
                raise RuntimeError("boom after inner block")

        # Assert: inner block leaving scope did not commit; the outer rollback wins
        assert _values(sqlite_engine) == []

    def test_model_update_joins_ambient_transaction_and_rolls_back(self, sqlite_engine):
        # Arrange: an existing row committed on its own
        _TxnProbe(id="p1", value="before").save()

        # Act: update the field inside a transaction that then fails
        with pytest.raises(RuntimeError):
            with transactional_session():
                probe = _TxnProbe.get_by_id("p1")
                probe.value = "after"
                probe.update()  # routes through get_session(), joins the ambient transaction
                raise RuntimeError("move failed")

        # Assert: the field update was rolled back with the transaction, not left committed
        assert _TxnProbe.get_by_id("p1").value == "before"

    def test_model_update_commits_with_ambient_transaction(self, sqlite_engine):
        # Arrange
        _TxnProbe(id="p1", value="before").save()

        # Act
        with transactional_session():
            probe = _TxnProbe.get_by_id("p1")
            probe.value = "after"
            probe.update()

        # Assert
        assert _TxnProbe.get_by_id("p1").value == "after"
