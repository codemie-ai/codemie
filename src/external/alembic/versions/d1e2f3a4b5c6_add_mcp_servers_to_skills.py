"""add_mcp_servers_to_skills

Revision ID: d1e2f3a4b5c6
Revises: 653f5a41ee9a
Create Date: 2026-03-20 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = '653f5a41ee9a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add mcp_servers JSONB column to skills table."""
    op.add_column(
        'skills',
        sa.Column(
            'mcp_servers',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default='[]',
        ),
    )


def downgrade() -> None:
    """Remove mcp_servers column from skills table."""
    op.drop_column('skills', 'mcp_servers')
