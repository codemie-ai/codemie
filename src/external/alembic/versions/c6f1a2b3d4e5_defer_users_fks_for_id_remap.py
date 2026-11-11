# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""defer every foreign key into users for the pre-Keycloak id remap (EPMCDME-16560)

Revision ID: c6f1a2b3d4e5
Revises: f7c3a9d1e4b6
Create Date: 2026-11-02 00:00:00.000000

UserRemapService moves a users.id inside one transaction. Every foreign key that references
users.id or users.email must therefore be DEFERRABLE INITIALLY DEFERRED, so the check runs at
COMMIT, when all children already point at the new id. The constraints are discovered from
pg_constraint rather than listed by name, because most are auto-named by Postgres and the
set grows with new migrations.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6f1a2b3d4e5"
down_revision: Union[str, None] = "f7c3a9d1e4b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USERS_FK_CONSTRAINTS_SQL = sa.text(
    """
    SELECT c.conrelid::regclass::text AS table_name, c.conname AS constraint_name, c.condeferrable AS deferrable
    FROM pg_constraint c
    JOIN pg_class r ON r.oid = c.confrelid
    JOIN pg_namespace n ON n.oid = r.relnamespace
    WHERE c.contype = 'f'
      AND r.relname = 'users'
      AND n.nspname IN ('public', 'codemie')
    """
)


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def upgrade() -> None:
    for row in op.get_bind().execute(_USERS_FK_CONSTRAINTS_SQL):
        if row.deferrable:
            continue
        op.execute(
            f"ALTER TABLE {row.table_name} ALTER CONSTRAINT {_quoted(row.constraint_name)} "
            "DEFERRABLE INITIALLY DEFERRED"
        )


def downgrade() -> None:
    for row in op.get_bind().execute(_USERS_FK_CONSTRAINTS_SQL):
        if not row.deferrable:
            continue
        op.execute(f"ALTER TABLE {row.table_name} ALTER CONSTRAINT {_quoted(row.constraint_name)} NOT DEFERRABLE")
