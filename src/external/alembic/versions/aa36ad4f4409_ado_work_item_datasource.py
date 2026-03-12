"""ADO Work Item Datasource

Revision ID: aa36ad4f4409
Revises: c3f7a1b2d9e4
Create Date: 2026-02-24 15:13:59.223258

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'aa36ad4f4409'
down_revision: Union[str, None] = 'c3f7a1b2d9e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'index_info', sa.Column('azure_devops_work_item', postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('index_info', 'azure_devops_work_item')
