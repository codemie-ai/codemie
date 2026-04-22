"""backfill_conversation_names

Revision ID: 234f8f339638
Revises: d7e8f9a0b1c2
Create Date: 2026-04-20 12:51:28.246010

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = '234f8f339638'
down_revision: Union[str, None] = 'd7e8f9a0b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Backfill conversation_name from the first history message for rows where it is null or empty.

    Uses a chunked loop (1 000 rows per batch, FOR UPDATE SKIP LOCKED) to avoid
    a single long-running row-level lock that could block concurrent writes on a
    production-sized table.
    """
    connection = op.get_bind()
    while True:
        result = connection.execute(
            text("""
            WITH batch AS (
                SELECT id FROM conversations
                WHERE (conversation_name IS NULL OR conversation_name = '')
                  AND jsonb_array_length(COALESCE(history, '[]'::jsonb)) > 0
                  AND NULLIF(TRIM(history->0->>'message'), '') IS NOT NULL
                LIMIT 1000
                FOR UPDATE SKIP LOCKED
            )
            UPDATE conversations c
            SET conversation_name = CASE
                WHEN LENGTH(history->0->>'message') > 50
                THEN LEFT(history->0->>'message', 50) || '...'
                ELSE history->0->>'message'
            END
            FROM batch WHERE c.id = batch.id
            """)
        )
        if result.rowcount == 0:
            break


def downgrade() -> None:
    """No-op — cannot safely restore previously null/empty names."""
    pass
