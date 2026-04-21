"""add_iteration_number_to_workflow_execution_states

Revision ID: c1d2e3f4a5b6
Revises: 083aa0973f84
Create Date: 2026-04-10 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'workflow_execution_states',
        sa.Column('iteration_number', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('workflow_execution_states', 'iteration_number')
