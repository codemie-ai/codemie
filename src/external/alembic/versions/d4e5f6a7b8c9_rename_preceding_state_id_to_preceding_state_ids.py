"""rename_preceding_state_id_to_preceding_state_ids

Revision ID: d4e5f6a7b8c9
Revises: c1d2e3f4a5b6
Create Date: 2026-04-15 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: replace preceding_state_id (String) with preceding_state_ids (JSONB array)."""
    op.add_column(
        'workflow_execution_states',
        sa.Column('preceding_state_ids', postgresql.JSONB(), nullable=True),
    )
    op.execute(
        "UPDATE workflow_execution_states "
        "SET preceding_state_ids = jsonb_build_array(preceding_state_id) "
        "WHERE preceding_state_id IS NOT NULL"
    )
    op.drop_column('workflow_execution_states', 'preceding_state_id')


def downgrade() -> None:
    """Downgrade schema: replace preceding_state_ids (JSONB array) with preceding_state_id (String)."""
    op.add_column(
        'workflow_execution_states',
        sa.Column('preceding_state_id', sa.String(), nullable=True),
    )
    op.execute(
        "UPDATE workflow_execution_states "
        "SET preceding_state_id = preceding_state_ids->>0 "
        "WHERE preceding_state_ids IS NOT NULL AND jsonb_array_length(preceding_state_ids) > 0"
    )
    op.drop_column('workflow_execution_states', 'preceding_state_ids')
