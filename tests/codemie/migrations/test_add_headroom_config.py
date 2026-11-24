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

"""Unit tests for the migration adding headroom_config to assistants and workflows."""

import importlib
from pathlib import Path
from unittest.mock import patch

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql

REVISION = "d5241114c3dd"
EXPECTED_DOWN_REVISION = "bbba81be075b"


def _module():
    return importlib.import_module(
        "external.alembic.versions.d5241114c3dd_add_headroom_config_to_assistants_and_workflows"
    )


def _script_directory() -> ScriptDirectory:
    import external.alembic as alembic_pkg

    versions_root = Path(next(iter(alembic_pkg.__path__)))
    config = Config()
    config.set_main_option("script_location", str(versions_root))
    return ScriptDirectory.from_config(config)


def test_revision_chain():
    module = _module()
    assert module.revision == REVISION
    assert module.down_revision == EXPECTED_DOWN_REVISION


def test_upgrade_adds_nullable_jsonb_column_to_both_tables():
    module = _module()
    with patch.object(module, "op") as op:
        module.upgrade()

    assert op.add_column.call_count == 2
    added = {}
    for call in op.add_column.call_args_list:
        table, column = call.args
        added[table] = column
    assert set(added) == {"assistants", "workflows"}
    for column in added.values():
        assert column.name == "headroom_config"
        assert column.nullable is True
        assert isinstance(column.type, postgresql.JSONB)


def test_downgrade_drops_column_from_both_tables():
    module = _module()
    with patch.object(module, "op") as op:
        module.downgrade()

    assert [call.args for call in op.drop_column.call_args_list] == [
        ("workflows", "headroom_config"),
        ("assistants", "headroom_config"),
    ]


def test_migration_graph_has_single_head():
    script = _script_directory()
    heads = script.get_heads()
    assert len(heads) == 1, f"expected a single Alembic head, got {heads}"
    # Own revision is reachable by walking back from the head, without pinning which revision the head is.
    reachable = {revision.revision for revision in script.walk_revisions(base="base", head=heads[0])}
    assert REVISION in reachable
