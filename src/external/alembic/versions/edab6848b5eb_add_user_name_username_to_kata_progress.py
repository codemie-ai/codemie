"""add_user_name_username_to_kata_progress

Revision ID: edab6848b5eb
Revises: 2c67f46aea18
Create Date: 2025-12-10 23:01:27.708797

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'edab6848b5eb'
down_revision: Union[str, None] = '2c67f46aea18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add user_name and user_username columns to user_kata_progress table
    op.add_column(
        'user_kata_progress',
        sa.Column('user_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
    )
    op.add_column(
        'user_kata_progress',
        sa.Column('user_username', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Remove user_name and user_username columns from user_kata_progress table
    op.drop_column('user_kata_progress', 'user_username')
    op.drop_column('user_kata_progress', 'user_name')
