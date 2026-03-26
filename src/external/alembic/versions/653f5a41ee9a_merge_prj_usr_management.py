"""merge prj/usr management

Revision ID: 653f5a41ee9a
Revises: a2b3c4d5e6f8, a7c9e1d2f4b6
Create Date: 2026-03-24 11:19:50.950014

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '653f5a41ee9a'
down_revision: Union[str, None] = ('a2b3c4d5e6f8', 'a7c9e1d2f4b6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
