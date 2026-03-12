"""mark_legacy_es_naming

Revision ID: 6f5ecc907909
Revises: 93e2d3c3b1c0
Create Date: 2026-03-05 16:48:04.283361

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '6f5ecc907909'
down_revision: Union[str, None] = '93e2d3c3b1c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Add uses_legacy_es_naming boolean field to index_info table and mark all existing
    knowledge_base and google_doc datasources as using legacy naming (True) for backward
    compatibility with old naming datasources.
    """
    # Add the column with default False
    op.add_column(
        'index_info', sa.Column('uses_legacy_es_naming', sa.Boolean(), nullable=False, server_default='false')
    )

    # Update all existing knowledge_base and google_doc datasources to use legacy naming
    # Only KB types need this - code, platform, and other types weren't affected by old naming
    op.execute("""
        UPDATE index_info
        SET uses_legacy_es_naming = true
        WHERE index_type LIKE 'knowledge_base%' OR index_type LIKE 'llm_routing_google%'
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('index_info', 'uses_legacy_es_naming')
