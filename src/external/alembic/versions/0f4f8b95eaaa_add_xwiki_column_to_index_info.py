"""add xwiki column to the index_info table

Revision ID: 0f4f8b95eaaa
Revises: b1c2d3e4f5a6
Create Date: 2026-08-04 18:00:38.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0f4f8b95eaaa"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("index_info", sa.Column("xwiki", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("index_info", "xwiki")
