"""added azure_devops_wiki column to the index_info table

Revision ID: 2b461b2f3d10
Revises: 73222d7f9d9e
Create Date: 2025-12-02 12:37:57.662638

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '2b461b2f3d10'
down_revision: Union[str, None] = '73222d7f9d9e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('index_info', sa.Column('azure_devops_wiki', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('index_info', 'azure_devops_wiki')
