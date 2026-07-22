"""add_interactive_features_to_assistants

Revision ID: i1n2t3e4r5a6
Revises: s9t0u1v2w3x4
Create Date: 2026-07-16 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'i1n2t3e4r5a6'
down_revision: Union[str, None] = 's9t0u1v2w3x4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('assistants', sa.Column('interactive_features', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('assistants', 'interactive_features')
