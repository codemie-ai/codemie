"""create_llm_model_status_table

Revision ID: 854f0f549445
Revises: c6dee3b8b5f2
Create Date: 2026-09-21 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "854f0f549445"
down_revision: Union[str, None] = "c6dee3b8b5f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "llm_model_status",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="enabled"),
        sa.Column("updated_by_id", sa.String(length=255), nullable=True),
        sa.Column("updated_by_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("base_name"),
    )
    op.create_index("ix_llm_model_status_base_name", "llm_model_status", ["base_name"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_llm_model_status_base_name", table_name="llm_model_status")
    op.drop_table("llm_model_status")
