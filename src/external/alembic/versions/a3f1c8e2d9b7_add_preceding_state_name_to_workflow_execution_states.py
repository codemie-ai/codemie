"""add_preceding_state_id_to_workflow_execution_states

Revision ID: a3f1c8e2d9b7
Revises: fqjps0yd6kck
Create Date: 2026-03-31 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f1c8e2d9b7'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'workflow_execution_states',
        sa.Column('preceding_state_id', sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('workflow_execution_states', 'preceding_state_id')
