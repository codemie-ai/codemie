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

"""add project_budget_plans table and plan_id to project_budget_assignments

Revision ID: bplnk5l6m7n8
Revises: f9g0h1i2j3k4
Create Date: 2026-05-29

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision: str = "bplnk5l6m7n8"
down_revision: Union[str, None] = "f9g0h1i2j3k4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_budget_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_name", sa.String(length=100), nullable=False),
        sa.Column("budget_duration", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_name"], ["applications.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pbp_project_name", "project_budget_plans", ["project_name"])
    op.create_index(
        "uix_pbp_project_active",
        "project_budget_plans",
        ["project_name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.add_column(
        "project_budget_assignments",
        sa.Column("plan_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_pba_plan_id",
        "project_budget_assignments",
        "project_budget_plans",
        ["plan_id"],
        ["id"],
    )
    op.create_index("ix_pba_plan_id", "project_budget_assignments", ["plan_id"])


def downgrade() -> None:
    op.drop_index("ix_pba_plan_id", table_name="project_budget_assignments")
    op.drop_constraint("fk_pba_plan_id", "project_budget_assignments", type_="foreignkey")
    op.drop_column("project_budget_assignments", "plan_id")

    op.drop_index("uix_pbp_project_active", table_name="project_budget_plans")
    op.drop_index("ix_pbp_project_name", table_name="project_budget_plans")
    op.drop_table("project_budget_plans")
