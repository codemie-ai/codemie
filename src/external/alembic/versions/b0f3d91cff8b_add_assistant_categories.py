"""add assistant categories

Revision ID: b0f3d91cff8b
Revises: 974fb16cd138
Create Date: 2025-09-17 17:26:07.764091

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b0f3d91cff8b'
down_revision: Union[str, None] = '974fb16cd138'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CATEGORIES_INDEX_NAME = 'ix_assistants_categories'


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('assistants', sa.Column('categories', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_index(CATEGORIES_INDEX_NAME, 'assistants', ['categories'], postgresql_using='gin')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(CATEGORIES_INDEX_NAME, table_name='assistants', if_exists=True)
    op.drop_column('assistants', 'categories')
