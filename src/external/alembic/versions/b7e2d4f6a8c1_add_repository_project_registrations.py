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

"""add_repository_project_registrations

Revision ID: b7e2d4f6a8c1
Revises: 79ab1c5725cc
Create Date: 2026-10-28 00:00:00.000000

EPMCDME-16440: maps one normalized repository remote to the shared project that receives its CLI analytics.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'b7e2d4f6a8c1'
down_revision = '79ab1c5725cc'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repository_project_registrations",
        sa.Column("normalized_remote", sa.String(), nullable=False),
        sa.Column("project_name", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("normalized_remote"),
        schema="codemie",
    )
    op.create_index(
        "ix_codemie_repository_project_registrations_project_name",
        "repository_project_registrations",
        ["project_name"],
        schema="codemie",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_codemie_repository_project_registrations_project_name",
        table_name="repository_project_registrations",
        schema="codemie",
    )
    op.drop_table("repository_project_registrations", schema="codemie")
