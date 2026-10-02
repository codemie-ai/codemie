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

"""Real-session (in-memory SQLite) regression tests for BaseModelWithSQLSupport.update().

The existing suite in test_base_model_update_deleted.py fully mocks `Session`, so it cannot
observe query count, StaleDataError semantics under a genuine flush, mutable-JSON round-trips,
or the concurrent-delete race. These tests use a real SQLAlchemy `Session` bound to an
in-memory SQLite engine (patched onto the model's `get_engine()` classmethod) to close that gap.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import JSON, Column, Engine, create_engine, event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Session as SQLAlchemySession
from sqlalchemy.orm.exc import StaleDataError
from sqlmodel import Field, Session, SQLModel

from codemie.rest_api.models.base import BaseModelWithSQLSupport


class _UpdateSqliteTestModel(BaseModelWithSQLSupport, table=True):
    __tablename__ = "update_sqlite_test_model"

    name: str | None = Field(default=None)
    created_by: dict[str, Any] | None = Field(
        default=None,
        # JSONB has no SQLite compiler; fall back to plain JSON there while keeping the
        # production JSONB type on Postgres. MutableDict lets in-place mutation round-trip.
        sa_column=Column(MutableDict.as_mutable(JSONB().with_variant(JSON(), "sqlite"))),
    )


@pytest.fixture()
def sqlite_engine() -> Engine:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine, tables=[_UpdateSqliteTestModel.__table__])
    return engine


def _row_count(engine: Engine) -> int:
    with Session(engine) as session:
        return session.exec(select(func.count()).select_from(_UpdateSqliteTestModel)).one()[0]


def test_update_issues_exactly_one_select(sqlite_engine):
    model = _UpdateSqliteTestModel(id="row-1", name="initial")
    with patch.object(_UpdateSqliteTestModel, "get_engine", return_value=sqlite_engine):
        model.save()

        select_statements = []

        def _capture_select(conn, cursor, statement, parameters, context, executemany):
            if statement.strip().upper().startswith("SELECT"):
                select_statements.append(statement)

        event.listen(sqlite_engine, "before_cursor_execute", _capture_select)
        try:
            model.name = "updated"
            model.update()
        finally:
            event.remove(sqlite_engine, "before_cursor_execute", _capture_select)

    table_selects = [s for s in select_statements if "update_sqlite_test_model" in s]
    assert len(table_selects) == 1, f"expected exactly one SELECT, got {len(table_selects)}: {table_selects}"


def test_update_raises_stale_data_error_for_deleted_row_real_session(sqlite_engine):
    model = _UpdateSqliteTestModel(id="row-2", name="initial")
    with patch.object(_UpdateSqliteTestModel, "get_engine", return_value=sqlite_engine):
        model.save()

        with Session(sqlite_engine) as session:
            obj = session.get(_UpdateSqliteTestModel, "row-2")
            session.delete(obj)
            session.commit()

        with pytest.raises(StaleDataError):
            model.update()

        assert _row_count(sqlite_engine) == 0


def test_update_round_trips_mutable_json_column(sqlite_engine):
    model = _UpdateSqliteTestModel(id="row-3", name="initial", created_by={"user": "a"})
    with patch.object(_UpdateSqliteTestModel, "get_engine", return_value=sqlite_engine):
        model.save()

        model.created_by["role"] = "admin"
        model.update()

        with Session(sqlite_engine) as session:
            refreshed = session.get(_UpdateSqliteTestModel, "row-3")
            assert refreshed.created_by == {"user": "a", "role": "admin"}


def test_update_concurrent_delete_race_still_raises(sqlite_engine):
    model = _UpdateSqliteTestModel(id="row-4", name="initial")
    with patch.object(_UpdateSqliteTestModel, "get_engine", return_value=sqlite_engine):
        model.save()

        real_commit = SQLAlchemySession.commit
        triggered = {"done": False}

        def _commit_after_concurrent_delete(self, *args, **kwargs):
            # Fire only once, for the outer session's commit (model.update()'s own session).
            # The inner session we open here would otherwise re-enter this same patched
            # method and recurse forever.
            if not triggered["done"]:
                triggered["done"] = True
                with Session(sqlite_engine) as other_session:
                    obj = other_session.get(_UpdateSqliteTestModel, "row-4")
                    if obj is not None:
                        other_session.delete(obj)
                        other_session.commit()
            return real_commit(self, *args, **kwargs)

        with patch.object(SQLAlchemySession, "commit", _commit_after_concurrent_delete):
            model.name = "updated"
            with pytest.raises(StaleDataError):
                model.update()

        assert _row_count(sqlite_engine) == 0
