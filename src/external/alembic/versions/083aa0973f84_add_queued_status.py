"""Add queued status

Revision ID: 083aa0973f84
Revises: 7d4e5f6a8b9c
Create Date: 2026-04-09 09:46:17.102035

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '083aa0973f84'
down_revision: Union[str, None] = '7d4e5f6a8b9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column(
        'index_info',
        sa.Column('is_queued', sa.Boolean(), nullable=False, server_default='false'),
        schema='codemie',
    )


def downgrade():
    op.drop_column('index_info', 'is_queued', schema='codemie')
