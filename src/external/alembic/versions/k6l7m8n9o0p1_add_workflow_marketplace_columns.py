"""add_workflow_marketplace_columns

Revision ID: k6l7m8n9o0p1
Revises: k5l6m7n8o9p0
Create Date: 2026-05-18 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "k6l7m8n9o0p1"
down_revision: Union[str, Sequence[str], None] = "22bf0f3ba27b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

IS_GLOBAL_INDEX = "ix_workflows_is_global"
CATEGORIES_INDEX = "ix_workflows_categories"


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("is_global", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "workflows",
        sa.Column(
            "categories",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "workflows",
        sa.Column("unique_users_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(IS_GLOBAL_INDEX, "workflows", ["is_global"])
    op.create_index(
        CATEGORIES_INDEX,
        "workflows",
        ["categories"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(CATEGORIES_INDEX, table_name="workflows")
    op.drop_index(IS_GLOBAL_INDEX, table_name="workflows")
    op.drop_column("workflows", "unique_users_count")
    op.drop_column("workflows", "categories")
    op.drop_column("workflows", "is_global")
