"""add_cost_centers

Revision ID: e1f2a3b4c5d6
Revises: d64ac374f28c
Create Date: 2026-03-19 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "c9b887e959ba"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cost_centers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("date", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("update_date", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_cost_centers_deleted_at", "cost_centers", ["deleted_at"])
    op.create_index("ux_cost_centers_name_lower", "cost_centers", [sa.text("LOWER(name)")], unique=True)

    op.add_column("applications", sa.Column("cost_center_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_applications_cost_center_id", "applications", ["cost_center_id"])
    op.create_foreign_key(
        "fk_applications_cost_center_id",
        "applications",
        "cost_centers",
        ["cost_center_id"],
        ["id"],
    )

    op.add_column("project_cost_tracking", sa.Column("cost_center_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("project_cost_tracking", sa.Column("cost_center_name", sa.String(length=255), nullable=True))
    op.create_foreign_key(
        "fk_project_cost_tracking_cost_center_id",
        "project_cost_tracking",
        "cost_centers",
        ["cost_center_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_project_cost_tracking_cost_center_id", "project_cost_tracking", type_="foreignkey")
    op.drop_column("project_cost_tracking", "cost_center_name")
    op.drop_column("project_cost_tracking", "cost_center_id")

    op.drop_constraint("fk_applications_cost_center_id", "applications", type_="foreignkey")
    op.drop_index("ix_applications_cost_center_id", table_name="applications")
    op.drop_column("applications", "cost_center_id")

    op.drop_index("ux_cost_centers_name_lower", table_name="cost_centers")
    op.drop_index("ix_cost_centers_deleted_at", table_name="cost_centers")
    op.drop_table("cost_centers")
