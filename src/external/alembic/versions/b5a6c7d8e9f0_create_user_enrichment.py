"""create_user_enrichment

Revision ID: b5a6c7d8e9f0
Revises: f2c3d4e5f6a7
Create Date: 2026-04-09 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5a6c7d8e9f0"
down_revision: Union[str, None] = "9b1c2d3e4f5a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_enrichment",
        sa.Column("email", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("first_name", sa.String(), nullable=True),
        sa.Column("last_name", sa.String(), nullable=True),
        sa.Column("job_title", sa.String(), nullable=True),
        sa.Column("job_function", sa.String(), nullable=True),
        sa.Column("level", sa.String(), nullable=True),
        sa.Column("primary_skill", sa.String(), nullable=True),
        sa.Column("country", sa.String(), nullable=True),
        sa.Column("city", sa.String(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["email"], ["codemie.users.email"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["codemie.users.id"]),
        schema="codemie",
    )

    op.create_index("ix_user_enrichment_user_id", "user_enrichment", ["user_id"], schema="codemie")
    op.create_index("ix_user_enrichment_job_title", "user_enrichment", ["job_title"], schema="codemie")
    op.create_index("ix_user_enrichment_country", "user_enrichment", ["country"], schema="codemie")
    op.create_index("ix_user_enrichment_city", "user_enrichment", ["city"], schema="codemie")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_user_enrichment_city", table_name="user_enrichment", schema="codemie")
    op.drop_index("ix_user_enrichment_country", table_name="user_enrichment", schema="codemie")
    op.drop_index("ix_user_enrichment_job_title", table_name="user_enrichment", schema="codemie")
    op.drop_index("ix_user_enrichment_user_id", table_name="user_enrichment", schema="codemie")
    op.drop_table("user_enrichment", schema="codemie")
