"""create_workflow_execution_transitions

Revision ID: a1f2b3c4d5e6
Revises: c3f7a1b2d9e4
Create Date: 2026-02-26 17:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'a1f2b3c4d5e6'
down_revision: Union[str, None] = '6f5ecc907909'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create workflow_execution_transitions table with indexes."""
    op.create_table(
        'workflow_execution_transitions',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('date', sa.DateTime(), nullable=True),
        sa.Column('update_date', sa.DateTime(), nullable=True),
        sa.Column('execution_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('from_state_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('to_state_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column(
            'workflow_context',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_index(
        'ix_workflow_execution_transitions_execution_id',
        'workflow_execution_transitions',
        ['execution_id'],
        unique=False,
    )
    op.create_index(
        'ix_workflow_execution_transitions_from_state_id',
        'workflow_execution_transitions',
        ['from_state_id'],
        unique=False,
    )
    op.create_index(
        'ix_workflow_execution_transitions_to_state_id',
        'workflow_execution_transitions',
        ['to_state_id'],
        unique=False,
    )


def downgrade() -> None:
    """Drop workflow_execution_transitions table and indexes."""
    op.drop_index(
        'ix_workflow_execution_transitions_to_state_id',
        table_name='workflow_execution_transitions',
    )
    op.drop_index(
        'ix_workflow_execution_transitions_from_state_id',
        table_name='workflow_execution_transitions',
    )
    op.drop_index(
        'ix_workflow_execution_transitions_execution_id',
        table_name='workflow_execution_transitions',
    )
    op.drop_table('workflow_execution_transitions')
