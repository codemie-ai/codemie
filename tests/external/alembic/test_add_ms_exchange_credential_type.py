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
import os
from unittest import mock

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic_postgresql_enum import TableReference

ALEMBIC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "src", "external", "alembic"))
VERSIONS_DIR = os.path.join(ALEMBIC_DIR, "versions")

MIGRATION_FILENAME = "5189d4d2afaf_add_ms_exchange_credential_type.py"
MIGRATION_REVISION = "5189d4d2afaf"

_AFFECTED_COLUMNS = [
    TableReference(table_schema="codemie", table_name="settings", column_name="credential_type"),
]


def _load_module(filename, module_name):
    path = os.path.abspath(os.path.join(VERSIONS_DIR, filename))
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_migration():
    return _load_module(MIGRATION_FILENAME, "test_add_ms_exchange_credential_type_migration")


def _script_directory():
    config = Config(os.path.join(ALEMBIC_DIR, "alembic.ini"))
    config.set_main_option("script_location", ALEMBIC_DIR)
    return ScriptDirectory.from_config(config)


def _credentialtypes_values_after_upgrade(module):
    """Return the credentialtypes labels ``module.upgrade()`` leaves behind, or None if it does not sync them."""
    with mock.patch.object(module, "op") as op_mock:
        module.upgrade()
    synced = [
        c.kwargs["new_values"]
        for c in op_mock.sync_enum_values.call_args_list
        if c.kwargs.get("enum_name") == "credentialtypes"
    ]
    return list(synced[-1]) if synced else None


def _latest_ancestor_credentialtypes_values():
    """Walk the chain below this migration and return the labels set by the nearest credentialtypes sync."""
    script = _script_directory()
    for rev in script.iterate_revisions(script.get_revision(MIGRATION_REVISION).down_revision, "base"):
        values = _credentialtypes_values_after_upgrade(rev.module)
        if values is not None:
            return values
    raise AssertionError("no ancestor migration syncs the credentialtypes enum")


PRE = _latest_ancestor_credentialtypes_values()


def test_migration_declares_expected_revision_chain():
    migration = _load_migration()
    assert migration.revision == MIGRATION_REVISION
    assert migration.down_revision == "f88ad134467d"


def test_alembic_chain_has_single_head_at_this_migration():
    assert _script_directory().get_heads() == [MIGRATION_REVISION]


def test_pre_matches_latest_credentialtypes_sync_in_chain():
    migration = _load_migration()
    assert _credentialtypes_values_after_upgrade(migration) == PRE + ["MS_EXCHANGE"]


def test_upgrade_adds_ms_exchange_to_enum():
    migration = _load_migration()
    with mock.patch.object(migration, "op") as op_mock:
        migration.upgrade()

    op_mock.sync_enum_values.assert_called_once_with(
        enum_schema="codemie",
        enum_name="credentialtypes",
        new_values=PRE + ["MS_EXCHANGE"],
        affected_columns=_AFFECTED_COLUMNS,
        enum_values_to_rename=[],
    )


def test_downgrade_deletes_rows_before_shrinking_enum():
    migration = _load_migration()
    with mock.patch.object(migration, "op") as op_mock:
        migration.downgrade()

    op_mock.sync_enum_values.assert_called_once_with(
        enum_schema="codemie",
        enum_name="credentialtypes",
        new_values=PRE,
        affected_columns=_AFFECTED_COLUMNS,
        enum_values_to_rename=[],
    )

    execute_call = mock.call.execute("DELETE FROM codemie.settings WHERE credential_type = 'MS_EXCHANGE'")
    sync_call = mock.call.sync_enum_values(
        enum_schema="codemie",
        enum_name="credentialtypes",
        new_values=PRE,
        affected_columns=_AFFECTED_COLUMNS,
        enum_values_to_rename=[],
    )
    assert op_mock.mock_calls.index(execute_call) < op_mock.mock_calls.index(sync_call)
