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

"""Unit tests for the y2z3a4b5c6d7 migration: it must only add the three new
denormalized snapshot columns and the supporting index on activity_events,
never touch the pre-existing protected columns or backfill any row.
"""

import importlib
from datetime import datetime

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

PROTECTED_COLUMNS = {"domain", "event_type", "entity_id", "actor_id", "created_at"}


class _OpRecorder:
    """Minimal stand-in for alembic's `op`, recording the calls a migration makes."""

    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def add_column(self, table, column):
        self.calls.append(("add_column", (table, column)))

    def create_index(self, index_name, table, columns, **kwargs):
        self.calls.append(("create_index", (index_name, table, columns)))

    def drop_index(self, index_name, table_name=None):
        self.calls.append(("drop_index", (index_name, table_name)))

    def drop_column(self, table, column):
        self.calls.append(("drop_column", (table, column)))

    def alter_column(self, *args, **kwargs):
        self.calls.append(("alter_column", (args, kwargs)))

    def execute(self, statement):
        self.calls.append(("execute", str(statement)))


def _module():
    return importlib.import_module(
        "external.alembic.versions.y2z3a4b5c6d7_add_project_and_actor_snapshot_to_activity_events"
    )


def _run_upgrade(monkeypatch) -> _OpRecorder:
    module = _module()
    recorder = _OpRecorder()
    monkeypatch.setattr(module, "op", recorder)
    module.upgrade()
    return recorder


def test_upgrade_only_adds_three_nullable_columns_and_one_index(monkeypatch):
    calls = _run_upgrade(monkeypatch).calls
    add_column_calls = [call for call in calls if call[0] == "add_column"]
    create_index_calls = [call for call in calls if call[0] == "create_index"]

    assert len(add_column_calls) == 3
    for _, (table, column) in add_column_calls:
        assert table == "activity_events"
        assert column.nullable is True

    assert len(create_index_calls) == 1


def test_upgrade_adds_expected_columns_and_index(monkeypatch):
    calls = _run_upgrade(monkeypatch).calls
    added_column_names = {payload[1].name for name, payload in calls if name == "add_column"}
    assert added_column_names == {"project_name", "actor_email", "actor_display_name"}

    index_calls = [payload for name, payload in calls if name == "create_index"]
    index_name, index_table, index_columns = index_calls[0]
    assert index_name == "ix_activity_events_project_name_created_at"
    assert index_table == "activity_events"
    assert list(index_columns) == ["project_name", "created_at"]


def test_upgrade_never_touches_protected_columns(monkeypatch):
    calls = _run_upgrade(monkeypatch).calls
    for name, payload in calls:
        assert name not in ("alter_column", "execute"), f"unexpected {name} call in upgrade()"
        if name == "add_column":
            _, column = payload
            assert column.name not in PROTECTED_COLUMNS


def test_upgrade_preserves_pre_existing_row_values_against_a_real_db(monkeypatch):
    """End-to-end guarantee: run upgrade() against a real (in-memory SQLite) database
    seeded with a pre-existing activity_events row, and assert every protected column's
    value is bit-for-bit unchanged afterward, and the new columns are added as NULL.
    """
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    created_at = datetime(2026, 1, 1, 12, 0, 0)
    activity_events = sa.Table(
        "activity_events",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("domain", sa.VARCHAR(255), nullable=False),
        sa.Column("event_type", sa.VARCHAR(255), nullable=False),
        sa.Column("entity_id", sa.VARCHAR(255), nullable=True),
        sa.Column("actor_id", sa.VARCHAR(255), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            activity_events.insert().values(
                domain="budget_management",
                event_type="budget.project_budget.updated",
                entity_id="budget-1",
                actor_id="actor-1",
                created_at=created_at,
            )
        )

        module = _module()
        migration_context = MigrationContext.configure(connection)
        real_op = Operations(migration_context)
        monkeypatch.setattr(module, "op", real_op)
        module.upgrade()

        row = connection.execute(sa.select(activity_events.c).select_from(activity_events)).mappings().first()

    assert row["domain"] == "budget_management"
    assert row["event_type"] == "budget.project_budget.updated"
    assert row["entity_id"] == "budget-1"
    assert row["actor_id"] == "actor-1"
    assert row["created_at"] == created_at

    inspector = sa.inspect(engine)
    column_names = {col["name"] for col in inspector.get_columns("activity_events")}
    assert {"project_name", "actor_email", "actor_display_name"} <= column_names

    with engine.connect() as connection:
        new_values = connection.execute(
            sa.text("SELECT project_name, actor_email, actor_display_name FROM activity_events")
        ).fetchone()
    assert tuple(new_values) == (None, None, None)
