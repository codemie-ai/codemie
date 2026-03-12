"""add_toolkits_to_skills

Revision ID: c3f7a1b2d9e4
Revises: b12fe8f816da
Create Date: 2026-02-19 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3f7a1b2d9e4'
down_revision: Union[str, None] = 'b12fe8f816da'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add toolkits JSONB column to skills table."""
    op.add_column(
        'skills',
        sa.Column(
            'toolkits',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default='[]',
        ),
    )


def downgrade() -> None:
    """Remove toolkits column from skills table."""
    op.drop_column('skills', 'toolkits')
