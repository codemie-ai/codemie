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

"""Unit tests for the add_cli_analytics_enabled_to_applications migration (EPMCDME-16025)."""

import importlib
from pathlib import Path
from unittest.mock import MagicMock

import sqlalchemy as sa
from alembic.script import ScriptDirectory

_MODULE = "external.alembic.versions.a9c4e7b2d1f3_add_cli_analytics_enabled_to_applications"


def test_upgrade_adds_non_null_boolean_column_defaulting_to_false(monkeypatch):
    module = importlib.import_module(_MODULE)
    op = MagicMock()
    monkeypatch.setattr(module, "op", op)

    module.upgrade()

    table, column = op.add_column.call_args.args
    assert table == "applications"
    assert column.name == "cli_analytics_enabled"
    assert isinstance(column.type, sa.Boolean)
    assert column.nullable is False
    assert str(column.server_default.arg) == "false"


def test_downgrade_drops_the_column(monkeypatch):
    module = importlib.import_module(_MODULE)
    op = MagicMock()
    monkeypatch.setattr(module, "op", op)

    module.downgrade()

    op.drop_column.assert_called_once_with("applications", "cli_analytics_enabled")


def test_revision_chains_onto_its_parent():
    script = ScriptDirectory(str(Path(__file__).resolve().parents[3] / "src" / "external" / "alembic"))
    module = importlib.import_module(_MODULE)

    revision = script.get_revision(module.revision)
    assert revision is not None
    assert revision.down_revision == "e5f9a1c3b7d2"
