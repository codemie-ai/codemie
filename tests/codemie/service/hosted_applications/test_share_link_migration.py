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

"""The share-link migration's hand-written SQL, checked without PostgreSQL against the ShareLink model."""

import re
from contextlib import nullcontext

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from codemie.rest_api.models.permission import ShareLink
from tests.codemie.service.hosted_applications.conftest import load_migration

MIGRATION = "f8d24a6c0b17_platform_sharing_grants_and_links.py"


class RecordingOp:
    def __init__(self):
        self.statements = []

    def get_context(self):
        return self

    def autocommit_block(self):
        return nullcontext()

    def execute(self, statement):
        self.statements.append(" ".join(str(statement).split()))


def run(step):
    migration = load_migration(MIGRATION)
    migration.op = RecordingOp()
    getattr(migration, step)()
    return migration.op.statements


def test_upgrade_creates_the_models_live_link_index_with_its_predicate():
    sql = " ".join(run("upgrade"))
    live = {index.name: index for index in ShareLink.__table__.indexes}["uq_share_links_live"]
    expected = " ".join(str(CreateIndex(live).compile(dialect=postgresql.dialect())).split())
    assert expected in sql
    assert expected.endswith("WHERE revoked_at IS NULL")


def test_upgrade_creates_one_common_table_keyed_like_permissions_without_a_resource_foreign_key():
    sql = " ".join(run("upgrade"))
    table = re.search(r"CREATE TABLE share_links \((.*?)\);", sql).group(1)
    assert "resource_type codemie.resourcetype NOT NULL" in table
    assert "resource_id uuid NOT NULL" in table
    assert "token_sha256 text NOT NULL UNIQUE" in table
    assert "REFERENCES" not in table
    assert "application_share_links" not in sql


def test_upgrade_points_permissions_source_link_id_at_share_links_with_restrict():
    sql = " ".join(run("upgrade"))
    assert (
        "ALTER TABLE permissions ADD CONSTRAINT permissions_source_link_id_fkey FOREIGN KEY (source_link_id) "
        "REFERENCES share_links (id) ON DELETE RESTRICT"
    ) in sql
    # Isolated hosted-only schemas run this chain without the platform permissions table.
    assert "to_regclass('permissions') IS NOT NULL" in sql


def test_downgrade_removes_link_grants_and_the_foreign_key_before_dropping_the_links():
    statements = run("downgrade")
    joined = " ".join(statements)
    assert re.search(r"DELETE FROM permissions WHERE grant_source = 'link'", joined)
    assert "DROP CONSTRAINT IF EXISTS permissions_source_link_id_fkey" in joined
    drops = [i for i, s in enumerate(statements) if s == "DROP TABLE share_links"]
    guarded = [i for i, s in enumerate(statements) if "DELETE FROM permissions" in s]
    assert drops and guarded and guarded[0] < drops[0]
