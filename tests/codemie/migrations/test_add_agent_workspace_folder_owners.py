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

"""Unit tests for the add_agent_workspace_folder_owners migration."""

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.script import ScriptDirectory


class _OpRecorder:
    """Minimal stand-in for alembic's `op`, recording the calls a migration makes."""

    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def create_table(self, table_name, *columns):
        self.calls.append(("create_table", (table_name, columns)))

    def create_index(self, index_name, table_name, columns, unique=False):
        self.calls.append(("create_index", (index_name, table_name, tuple(columns), unique)))

    def create_unique_constraint(self, constraint_name, table_name, columns):
        self.calls.append(("create_unique_constraint", (constraint_name, table_name, tuple(columns))))

    def add_column(self, table_name, column):
        self.calls.append(("add_column", (table_name, column)))

    def drop_column(self, table_name, column_name):
        self.calls.append(("drop_column", (table_name, column_name)))

    def drop_table(self, table_name):
        self.calls.append(("drop_table", table_name))

    def drop_index(self, index_name, table_name=None):
        self.calls.append(("drop_index", (index_name, table_name)))

    def drop_constraint(self, constraint_name, table_name, type_=None):
        self.calls.append(("drop_constraint", (constraint_name, table_name, type_)))

    def f(self, name):
        return name


def _module():
    return importlib.import_module("external.alembic.versions.y1z2a3b4c5d6_add_agent_workspace_folder_owners")


def _upgrade(monkeypatch):
    module = _module()
    recorder = _OpRecorder()
    monkeypatch.setattr(module, "op", recorder)
    module.upgrade()
    return recorder


def test_upgrade_creates_only_the_folder_owner_table(monkeypatch):
    recorder = _upgrade(monkeypatch)

    create_calls = [c for c in recorder.calls if c[0] == "create_table"]
    assert len(create_calls) == 1
    table_name, columns = create_calls[0][1]
    assert table_name == "agent_workspace_folder_owners"
    by_name = {col.name: col for col in columns if isinstance(col, sa.Column)}
    assert set(by_name) == {"id", "date", "update_date", "workspace_id", "source_kind", "source_id", "folder"}
    assert by_name["folder"].nullable is False
    assert not [c for c in recorder.calls if c[0] in {"add_column", "drop_column"}]


def test_upgrade_adds_unique_constraints_on_workspace_source_and_folder(monkeypatch):
    recorder = _upgrade(monkeypatch)

    constraints = {call[1] for call in recorder.calls if call[0] == "create_unique_constraint"}
    assert constraints == {
        (
            "uq_agent_workspace_folder_owners_workspace_source",
            "agent_workspace_folder_owners",
            ("workspace_id", "source_kind", "source_id"),
        ),
        (
            "uq_agent_workspace_folder_owners_workspace_folder",
            "agent_workspace_folder_owners",
            ("workspace_id", "folder"),
        ),
    }


def test_workspace_foreign_key_cascades(monkeypatch):
    recorder = _upgrade(monkeypatch)

    _, columns = next(c[1] for c in recorder.calls if c[0] == "create_table")
    foreign_keys = [col for col in columns if isinstance(col, sa.ForeignKeyConstraint)]
    assert [fk.ondelete for fk in foreign_keys] == ["CASCADE"]


def test_downgrade_drops_the_table(monkeypatch):
    module = _module()
    recorder = _OpRecorder()
    monkeypatch.setattr(module, "op", recorder)

    module.downgrade()

    assert recorder.calls == [("drop_table", "agent_workspace_folder_owners")]


def test_the_rewritten_revision_keeps_a_single_alembic_head():
    """The revision was rewritten in place; it must still chain onto its parent and leave one head."""
    script = ScriptDirectory(str(Path(__file__).resolve().parents[3] / "src" / "external" / "alembic"))
    revision = script.get_revision(_module().revision)

    assert revision is not None
    assert revision.down_revision == "a8f23cf09c37"
    assert len(script.get_heads()) == 1, f"expected a single Alembic head, got {script.get_heads()}"
