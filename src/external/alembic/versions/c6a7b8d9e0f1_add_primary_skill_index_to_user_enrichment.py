"""add_primary_skill_index_to_user_enrichment

Revision ID: c6a7b8d9e0f1
Revises: b5a6c7d8e9f0
Create Date: 2026-04-17 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6a7b8d9e0f1"
down_revision: Union[str, None] = "b5a6c7d8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index("ix_user_enrichment_primary_skill", "user_enrichment", ["primary_skill"], schema="codemie")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_user_enrichment_primary_skill", table_name="user_enrichment", schema="codemie")
