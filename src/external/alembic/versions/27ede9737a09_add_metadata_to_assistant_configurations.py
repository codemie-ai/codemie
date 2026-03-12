"""Add metadata to assistant configurations

Revision ID: 27ede9737a09
Revises: 40eed2dcf469
Create Date: 2025-10-29 17:26:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '27ede9737a09'
down_revision: Union[str, None] = '40eed2dcf469'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: Add custom_metadata column to assistant_configurations table."""
    op.add_column(
        'assistant_configurations', sa.Column('custom_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column('assistants', sa.Column('custom_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Downgrade schema: Remove custom_metadata column from assistant_configurations table."""
    op.drop_column('assistants', 'custom_metadata')
    op.drop_column('assistant_configurations', 'custom_metadata')
