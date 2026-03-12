"""add_description_to_applications

Revision ID: 5d6358638299
Revises: 98451a34b9d2
Create Date: 2026-02-09 12:00:00.000000

Add description column to applications table for personal project descriptions.

Story: EPMCDME-10160 Story 9 - Personal Project Auto-Creation
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d6358638299'
down_revision: Union[str, None] = '98451a34b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add description column to applications table."""
    # Add description column (nullable, max 500 characters)
    # NULL for existing shared projects, set explicitly for personal projects
    op.add_column('applications', sa.Column('description', sa.String(500), nullable=True))


def downgrade() -> None:
    """Remove description column from applications table."""
    op.drop_column('applications', 'description')
