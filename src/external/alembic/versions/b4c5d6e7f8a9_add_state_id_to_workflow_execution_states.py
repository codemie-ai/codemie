"""add_state_id_to_workflow_execution_states

Revision ID: b4c5d6e7f8a9
Revises: a3f1c8e2d9b7
Create Date: 2026-04-03 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, None] = 'a3f1c8e2d9b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add state_id column to workflow_execution_states to store the raw node name."""
    op.add_column(
        'workflow_execution_states',
        sa.Column('state_id', sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Remove state_id column from workflow_execution_states."""
    op.drop_column('workflow_execution_states', 'state_id')
