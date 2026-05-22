"""create_user_preferences_table

Revision ID: a9f3c1d2e4b5
Revises: d2e3f4a5b6c7
Create Date: 2026-05-04 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'a9f3c1d2e4b5'
down_revision: Union[str, None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'user_preferences',
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('pinned_assistants', JSONB(), nullable=False, server_default='[]'),
        sa.Column(
            'favorites',
            JSONB(),
            nullable=False,
            server_default='{"assistants":[],"workflows":[],"skills":[]}',
        ),
        sa.PrimaryKeyConstraint('user_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('user_preferences')
