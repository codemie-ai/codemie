"""add_job_title_group_to_user_enrichment

Revision ID: d7e8f9a0b1c2
Revises: c6a7b8d9e0f1
Create Date: 2026-04-21 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "user_enrichment",
        sa.Column("job_title_group", sa.String(), nullable=True),
        schema="codemie",
    )
    op.create_index("ix_user_enrichment_job_title_group", "user_enrichment", ["job_title_group"], schema="codemie")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_user_enrichment_job_title_group", table_name="user_enrichment", schema="codemie")
    op.drop_column("user_enrichment", "job_title_group", schema="codemie")
