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

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

ALEMBIC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "src", "external", "alembic"))
VERSIONS_DIR = os.path.join(ALEMBIC_DIR, "versions")

MIGRATION_REVISION = "a8f23cf09c37"
MIGRATION_FILENAME = f"{MIGRATION_REVISION}_add_cache_tokens_to_litellm_spend_logs.py"
PARENT_REVISION = "5189d4d2afaf"

TABLE = "litellm_spend_logs"
COLUMNS = ("cache_read_input_tokens", "cache_creation_input_tokens")


def _load_migration():
    path = os.path.abspath(os.path.join(VERSIONS_DIR, MIGRATION_FILENAME))
    spec = importlib.util.spec_from_file_location("test_add_cache_tokens_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _script_directory():
    config = Config(os.path.join(ALEMBIC_DIR, "alembic.ini"))
    config.set_main_option("script_location", ALEMBIC_DIR)
    return ScriptDirectory.from_config(config)


def test_migration_declares_expected_revision_chain():
    migration = _load_migration()
    assert migration.revision == MIGRATION_REVISION
    assert migration.down_revision == PARENT_REVISION


def test_alembic_chain_has_a_single_head_that_includes_this_migration():
    script = _script_directory()
    heads = script.get_heads()
    assert len(heads) == 1
    chain = {rev.revision for rev in script.iterate_revisions(heads[0], "base")}
    assert MIGRATION_REVISION in chain


def test_upgrade_adds_two_nullable_bigint_columns_without_default():
    migration = _load_migration()
    with mock.patch.object(migration, "op") as op_mock:
        migration.upgrade()

    assert [call.args[0] for call in op_mock.add_column.call_args_list] == [TABLE, TABLE]
    added = {call.args[1].name: call.args[1] for call in op_mock.add_column.call_args_list}
    assert set(added) == set(COLUMNS)
    for column in added.values():
        assert isinstance(column.type, sa.BigInteger)
        assert column.nullable is True
        assert column.server_default is None
        assert column.default is None
    assert not op_mock.execute.called, "no data migration: a legacy row derives nothing"
    assert not op_mock.create_index.called, "no index: the columns are only ever read in aggregates"


def test_downgrade_drops_both_columns():
    migration = _load_migration()
    with mock.patch.object(migration, "op") as op_mock:
        migration.downgrade()

    assert {call.args for call in op_mock.drop_column.call_args_list} == {(TABLE, name) for name in COLUMNS}
