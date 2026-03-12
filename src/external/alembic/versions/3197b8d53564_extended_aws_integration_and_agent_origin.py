"""Merge branches a2b3c4d5e6f7 and d225ff6f4239

Revision ID: 3197b8d53564
Revises: a2b3c4d5e6f7, d225ff6f4239
Create Date: 2025-12-09 16:29:14.217970

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '3197b8d53564'
down_revision: Union[str, None] = ('a2b3c4d5e6f7', 'd225ff6f4239')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
