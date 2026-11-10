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


"""Platform sharing for named grants and share links: permissions grant source, common share_links table.

Revision ID: f8d24a6c0b17
Revises: e3b7c1a95d42

Changes tables other entities use too, kept apart from the hosted tables:

- ``resourcetype`` gains HOSTED_APPLICATION (additive; never synced to a fixed list, so values other branches added
  survive);
- ``share_links``: one common table of revocable links keyed like permissions (resource_type, resource_id) and, like
  it, without a foreign key to the resource; each link-sharing type deletes its rows explicitly. A revoked link stays
  as a tombstone and at most one link per resource is live;
- ``permissions`` gets ``grant_source`` ('name' | 'link') and ``source_link_id``, with a CHECK tying them together and
  a RESTRICT foreign key to share_links, so a link row cannot take its grants with it;
- assistant USER grants written before the shared named-grant module (grant_source NULL) are marked 'name'.

The permissions steps are skipped where the table does not resolve on the search path (isolated hosted-only test
schemas run this chain without it).
"""

from alembic import op

revision = "f8d24a6c0b17"
down_revision = "e3b7c1a95d42"
branch_labels = None
depends_on = None

FOREIGN_KEY = "permissions_source_link_id_fkey"
CHECK = "permissions_grant_source_check"

SHARE_LINKS_DDL = """
CREATE TABLE share_links (
    id uuid PRIMARY KEY,
    resource_type codemie.resourcetype NOT NULL,
    resource_id uuid NOT NULL,
    token text NOT NULL,
    token_sha256 text NOT NULL UNIQUE,
    created_by jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    revoked_at timestamptz,
    revoked_by jsonb
);

CREATE UNIQUE INDEX uq_share_links_live ON share_links (resource_type, resource_id) WHERE revoked_at IS NULL;
"""


def upgrade():
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on older PostgreSQL
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE codemie.resourcetype ADD VALUE IF NOT EXISTS 'HOSTED_APPLICATION'")
    op.execute(SHARE_LINKS_DDL)
    # Unqualified on purpose: the platform schema is on the search path.
    op.execute(
        f"""
DO $$
BEGIN
    IF to_regclass('permissions') IS NOT NULL THEN
        ALTER TABLE permissions ADD COLUMN grant_source varchar(16), ADD COLUMN source_link_id uuid;
        ALTER TABLE permissions ADD CONSTRAINT {CHECK} CHECK (
            (grant_source IS NULL AND source_link_id IS NULL)
            OR (grant_source = 'name' AND source_link_id IS NULL)
            OR (grant_source = 'link' AND source_link_id IS NOT NULL)
        );
        ALTER TABLE permissions ADD CONSTRAINT {FOREIGN_KEY} FOREIGN KEY (source_link_id) REFERENCES share_links (id) ON DELETE RESTRICT;
        UPDATE permissions SET grant_source = 'name'
        WHERE resource_type = 'ASSISTANT' AND principal_type = 'USER' AND grant_source IS NULL;
    END IF;
END $$;
"""
    )


def downgrade():
    # Link grants point at the links dropped here and hosted grants at a type the hosted revision removes; nothing
    # could revoke them afterwards. Assistant grants keep 'name' until the column goes.
    op.execute(
        f"""
DO $$
BEGIN
    IF to_regclass('permissions') IS NOT NULL THEN
        DELETE FROM permissions WHERE grant_source = 'link' OR resource_type = 'HOSTED_APPLICATION';
        ALTER TABLE permissions DROP CONSTRAINT IF EXISTS {FOREIGN_KEY};
        ALTER TABLE permissions DROP CONSTRAINT IF EXISTS {CHECK};
        ALTER TABLE permissions DROP COLUMN IF EXISTS source_link_id, DROP COLUMN IF EXISTS grant_source;
    END IF;
END $$;
"""
    )
    op.execute("DROP TABLE share_links")
