"""rename_is_super_admin_to_is_admin

Revision ID: a2b3c4d5e6f8
Revises: e1f2a3b4c5d6
Create Date: 2026-03-23 00:00:00.000000

Rename users.is_super_admin column to is_admin for naming consistency.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a2b3c4d5e6f8'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('users', 'is_super_admin', new_column_name='is_admin')


def downgrade() -> None:
    op.alter_column('users', 'is_admin', new_column_name='is_super_admin')
