"""Add finished_at to conversations

Revision ID: 67b7c43d6090
Revises: 0f4f8b95eaaa
Create Date: 2026-08-11 00:00:00.000000

Adds finished_at to conversations, plus a partial index on (date)
WHERE finished_at IS NULL for efficient scheduler queries finding unfinished
conversations (EPMCDME-11067). See spec.md "Data model" for why the index
predicate is safe for interactive chat-write performance — date does not change
on a per-message write, and finished_at is set once at finish time.
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "67b7c43d6090"
down_revision: Union[str, None] = "0f4f8b95eaaa"
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("finished_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_conversations_unfinished",
        "conversations",
        ["date"],
        postgresql_where=sa.text("finished_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_unfinished", table_name="conversations")
    op.drop_column("conversations", "finished_at")
