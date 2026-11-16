"""Create the singleton administrator global model choice.

Revision ID: ab20f38d912e
Revises: 854f0f549445
Create Date: 2026-09-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "ab20f38d912e"
down_revision: Union[str, None] = "854f0f549445"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_global_default",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("base_name", sa.String(length=255), nullable=False),
        sa.Column("updated_by_id", sa.String(length=255), nullable=False),
        sa.Column("updated_by_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id = 1", name="ck_admin_global_default_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("admin_global_default")
