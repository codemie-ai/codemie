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

"""add agent_workspace_folder_owners

Revision ID: y1z2a3b4c5d6
Revises: a8f23cf09c37
Create Date: 2026-10-06 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "y1z2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "a8f23cf09c37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_workspace_folder_owners",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("date", sa.DateTime(), nullable=True),
        sa.Column("update_date", sa.DateTime(), nullable=True),
        sa.Column("workspace_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("folder", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["agent_workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agent_workspace_folder_owners_workspace_id"),
        "agent_workspace_folder_owners",
        ["workspace_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_agent_workspace_folder_owners_workspace_source",
        "agent_workspace_folder_owners",
        ["workspace_id", "source_kind", "source_id"],
    )
    op.create_unique_constraint(
        "uq_agent_workspace_folder_owners_workspace_folder",
        "agent_workspace_folder_owners",
        ["workspace_id", "folder"],
    )


def downgrade() -> None:
    op.drop_table("agent_workspace_folder_owners")
