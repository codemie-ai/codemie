"""add_user_model_overrides_table

Revision ID: bbba81be075b
Revises: epmcdme14325
Create Date: 2026-10-16 00:00:00.000000

Create the user_model_overrides table for user-level model override enforcement.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

# revision identifiers, used by Alembic.
revision: str = 'bbba81be075b'
down_revision: Union[str, None] = 'epmcdme14325'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_model_overrides',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('date', sa.DateTime(), nullable=True),
        sa.Column('update_date', sa.DateTime(), nullable=True),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('project_name', sa.String(length=100), nullable=False),
        sa.Column('allowed_models', ARRAY(sa.String()), nullable=False),
        sa.Column('default_model', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name='fk_user_model_overrides_user_id',
            ondelete='CASCADE',
            deferrable=True,
            initially='DEFERRED',
        ),
        sa.ForeignKeyConstraint(
            ['user_id', 'project_name'],
            ['user_projects.user_id', 'user_projects.project_name'],
            name='fk_user_model_overrides_membership',
            ondelete='CASCADE',
            onupdate='CASCADE',
            deferrable=True,
            initially='DEFERRED',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'project_name', name='uq_user_model_override_user_project'),
        sa.CheckConstraint(
            'array_length(allowed_models, 1) IS NOT NULL',
            name='ck_user_model_override_has_models',
        ),
    )
    op.create_index('ix_user_model_overrides_user_id', 'user_model_overrides', ['user_id'])
    op.create_index('ix_user_model_overrides_project_name', 'user_model_overrides', ['project_name'])


def downgrade() -> None:
    op.drop_index('ix_user_model_overrides_project_name', table_name='user_model_overrides')
    op.drop_index('ix_user_model_overrides_user_id', table_name='user_model_overrides')
    op.drop_table('user_model_overrides')
