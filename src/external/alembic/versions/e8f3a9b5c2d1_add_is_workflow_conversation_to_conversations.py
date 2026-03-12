"""Add is_workflow_conversation to conversations

Revision ID: e8f3a9b5c2d1
Revises: 4c09917e4fad
Create Date: 2024-11-14 12:00:00.000000

This migration adds is_workflow_conversation field to conversations table to mark
conversations that are based on workflows (as opposed to assistant conversations).

This follows the assistant conversation pattern - conversations own their history,
and we simply mark which type of conversation it is.
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text, inspect

# revision identifiers, used by Alembic.
revision: str = 'e8f3a9b5c2d1'
down_revision: Union[str, None] = '09f5fd7b6530'
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add is_workflow_conversation column to conversations table and create an index"""
    op.add_column('conversations', sa.Column('is_workflow_conversation', sa.Boolean(), nullable=True, default=False))
    op.create_index('ix_conversations_is_workflow_conversation', 'conversations', ['is_workflow_conversation'])


def downgrade() -> None:
    """Remove is_workflow_conversation column and its index from conversations table"""
    op.drop_index('ix_conversations_is_workflow_conversation', table_name='conversations')
    op.drop_column('conversations', 'is_workflow_conversation')
