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

"""``include_object`` of alembic's env.py keeps autogenerate from dropping the old pause columns (CR-012)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

import alembic

ENV_PATH = Path(__file__).resolve().parents[4] / "src" / "external" / "alembic" / "env.py"


@pytest.fixture(scope="module")
def include_object():
    """env.py runs a migration on import; load it against a stand-in for alembic's ``context`` in offline mode."""
    spec = importlib.util.spec_from_file_location("alembic_env_under_test", ENV_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.object(alembic, "context", MagicMock()):
        spec.loader.exec_module(module)
    return module.include_object


def _column(table_name: str, name: str) -> sa.Column:
    table = sa.Table(table_name, sa.MetaData(), sa.Column(name, sa.Text))
    return table.c[name]


@pytest.mark.parametrize("name", ["pending_tool_call", "pending_checkpoint"])
def test_the_old_reflected_pause_columns_are_left_out_of_autogenerate(include_object, name):
    column = _column("conversations", name)

    assert include_object(column, name, "column", True, None) is False


def test_another_conversations_column_is_still_compared(include_object):
    column = _column("conversations", "history")

    assert include_object(column, "history", "column", True, None) is True
