"""added the file_names JSONB column to workflow_executions

Revision ID: d64ac374f28c
Revises: a1f2b3c4d5e6
Create Date: 2026-03-17 14:08:24.538696

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd64ac374f28c'
down_revision: Union[str, None] = 'f1e2d3c4b5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'workflow_executions',
        sa.Column(
            'file_names',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # Migrate existing file_name values into the new file_names array
    op.execute(
        """
        UPDATE workflow_executions
        SET file_names = jsonb_build_array(file_name)
        WHERE file_name IS NOT NULL
          AND (file_names IS NULL OR file_names = '[]'::jsonb)
        """
    )
    op.execute("UPDATE workflow_executions SET file_names = '[]'::jsonb WHERE file_names IS NULL")
    op.drop_column('workflow_executions', 'file_name')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'workflow_executions',
        sa.Column('file_name', sa.VARCHAR(), nullable=True),
    )
    # Restore the first element of file_names back to file_name
    op.execute(
        """
        UPDATE workflow_executions
        SET file_name = file_names ->> 0
        WHERE jsonb_array_length(file_names) > 0
        """
    )
    op.drop_column('workflow_executions', 'file_names')
