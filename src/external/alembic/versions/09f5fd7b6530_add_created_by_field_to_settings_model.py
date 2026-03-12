"""Add created_by field to Settings model

Revision ID: 09f5fd7b6530
Revises: 4c09917e4fad
Create Date: 2025-11-19 09:44:05.503539

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '09f5fd7b6530'
down_revision: Union[str, None] = '4c09917e4fad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column('settings', sa.Column('created_by', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_index('ix_settings_created_by', 'settings', ['created_by'], postgresql_using='gin')


def downgrade():
    op.drop_index('ix_settings_created_by', table_name='settings')
    op.drop_column('settings', 'created_by')
