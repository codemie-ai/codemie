"""add_disabled_context_tools

Revision ID: 64825fe31931
Revises: y1e2f3o4r5t6
Create Date: 2026-10-19 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '64825fe31931'
down_revision: Union[str, None] = 'y1e2f3o4r5t6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add disabled_context_tools to assistants and assistant configurations."""
    op.add_column(
        'assistants',
        sa.Column(
            'disabled_context_tools',
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        'assistant_configurations',
        sa.Column(
            'disabled_context_tools',
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    """Remove disabled_context_tools from assistants and assistant configurations."""
    op.drop_column('assistant_configurations', 'disabled_context_tools')
    op.drop_column('assistants', 'disabled_context_tools')
