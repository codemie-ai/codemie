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

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.models.permission import Permission

MIGRATION = Path(__file__).parents[4] / "src/external/alembic/versions/0ba28b31fa53_add_assistant_to_resourcetype.py"


@pytest.fixture
def migration():
    spec = importlib.util.spec_from_file_location("assistant_resourcetype_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_adds_only_assistant_to_resourcetype(migration):
    with patch.object(migration, "op", MagicMock()) as op:
        migration.upgrade()

    op.sync_enum_values.assert_called_once()
    kwargs = op.sync_enum_values.call_args.kwargs
    assert kwargs["enum_name"] == "resourcetype"
    assert kwargs["new_values"] == ["DATASOURCE", "ASSISTANT"]
    assert "enum_values_to_rename" not in kwargs


def test_downgrade_deletes_assistant_rows_before_shrinking_enum(migration):
    with patch.object(migration, "op", MagicMock()) as op:
        migration.downgrade()

    assert [c[0] for c in op.method_calls] == ["execute", "sync_enum_values"]
    assert "'ASSISTANT'" in op.execute.call_args.args[0]
    op.sync_enum_values.assert_called_once()
    assert op.sync_enum_values.call_args.kwargs["new_values"] == ["DATASOURCE"]
    assert "enum_values_to_rename" not in op.sync_enum_values.call_args.kwargs


@pytest.mark.parametrize("column", ["resource_type", "principal_type", "permission_level"])
def test_permission_enum_columns_persist_names(column):
    assert Permission.__table__.c[column].type.enums == [e.name for e in Permission.__table__.c[column].type.enum_class]
