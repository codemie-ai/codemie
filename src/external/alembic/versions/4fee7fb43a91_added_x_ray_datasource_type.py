"""added x-ray datasource type

Revision ID: 4fee7fb43a91
Revises: cec900168e76
Create Date: 2026-01-15 15:34:59.485294

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '4fee7fb43a91'
down_revision: Union[str, None] = 'cec900168e76'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('index_info', sa.Column('xray', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('index_info', 'xray')
