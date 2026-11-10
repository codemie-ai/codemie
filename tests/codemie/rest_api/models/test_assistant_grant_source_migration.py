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

"""Assistant named grants made before the move onto the shared module get ``grant_source = 'name'``."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

VERSIONS = Path(__file__).parents[4] / "src/external/alembic/versions"
MIGRATION = VERSIONS / "f8d24a6c0b17_platform_sharing_grants_and_links.py"


@pytest.fixture
def migration():
    spec = importlib.util.spec_from_file_location("platform_sharing_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def statements(migration, step):
    with patch.object(migration, "op", MagicMock()) as op:
        getattr(migration, step)()
    return [" ".join(call.args[0].split()) for call in op.execute.call_args_list]


def test_revision_follows_the_hosted_tables(migration):
    assert (migration.revision, migration.down_revision) == ("f8d24a6c0b17", "e3b7c1a95d42")


def test_upgrade_marks_only_legacy_assistant_user_grants(migration):
    sql = " ".join(statements(migration, "upgrade"))
    update = sql[sql.index("UPDATE permissions SET grant_source = 'name'") :]
    update = update[: update.index(";")]
    assert "resource_type = 'ASSISTANT'" in update
    assert "principal_type = 'USER'" in update
    assert "grant_source IS NULL" in update


def test_downgrade_keeps_assistant_name_grants_until_the_column_goes(migration):
    """NULL and 'name' both satisfy the grant-source check; the downgrade drops the column, not the rows."""
    sql = " ".join(statements(migration, "downgrade"))
    assert "DELETE FROM permissions WHERE grant_source = 'link' OR resource_type = 'HOSTED_APPLICATION'" in sql
    assert "resource_type = 'ASSISTANT'" not in sql
