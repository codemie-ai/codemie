"""add project field to conversation_analytics

Revision ID: cec900168e76
Revises: faf267b187af
Create Date: 2026-01-09 19:08:57.042016

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'cec900168e76'
down_revision: Union[str, None] = 'faf267b187af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add project column to conversation_analytics table
    op.add_column('conversation_analytics', sa.Column('project', sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove project column from conversation_analytics table
    op.drop_column('conversation_analytics', 'project')
