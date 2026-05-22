"""merge_heads

Revision ID: 8eb9522661cf
Revises: a9f3c1d2e4b5, c4f2a8b6d1e9
Create Date: 2026-05-18 18:02:07.545102

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '8eb9522661cf'
down_revision: Union[str, None] = ('a9f3c1d2e4b5', 'c4f2a8b6d1e9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
