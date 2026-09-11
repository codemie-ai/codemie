"""add project_enrichment table and is_active to budgets

Revision ID: d3c838ee6ab9
Revises: fa14587c0de1
Create Date: 2026-08-25 14:17:32.102696

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP

# revision identifiers, used by Alembic.
revision: str = 'd3c838ee6ab9'
down_revision: Union[str, None] = 'b3d7f1a9c2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'project_enrichment',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('application_id', sa.String(length=100), nullable=True),
        sa.Column('cost_center_id', sa.UUID(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('synced_at', TIMESTAMP(timezone=True), nullable=True),
        sa.Column('created_at', TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            '(application_id IS NOT NULL AND cost_center_id IS NULL) OR '
            '(application_id IS NULL AND cost_center_id IS NOT NULL)',
            name='ck_project_enrichment_xor_application_cost_center',
        ),
        sa.ForeignKeyConstraint(
            ['application_id'],
            ['applications.id'],
            name='fk_project_enrichment_application_id',
        ),
        sa.ForeignKeyConstraint(
            ['cost_center_id'],
            ['cost_centers.id'],
            name='fk_project_enrichment_cost_center_id',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_project_enrichment_is_active', 'project_enrichment', ['is_active'])
    op.create_index('ix_project_enrichment_application_id', 'project_enrichment', ['application_id'])
    op.create_index('ix_project_enrichment_cost_center_id', 'project_enrichment', ['cost_center_id'])

    op.add_column(
        'budgets',
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    )
    op.create_index('ix_budgets_is_active', 'budgets', ['is_active'])


def downgrade() -> None:
    op.drop_index('ix_budgets_is_active', table_name='budgets')
    op.drop_column('budgets', 'is_active')

    op.drop_index('ix_project_enrichment_cost_center_id', table_name='project_enrichment')
    op.drop_index('ix_project_enrichment_application_id', table_name='project_enrichment')
    op.drop_index('ix_project_enrichment_is_active', table_name='project_enrichment')
    op.drop_table('project_enrichment')
