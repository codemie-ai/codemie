"""add_owner_and_context_to_a2a_tasks

Revision ID: c9d8e7f6a5b4
Revises: a4c9e1b7d2f3
Create Date: 2026-09-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d8e7f6a5b4"
down_revision: Union[str, None] = "a4c9e1b7d2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("a2a_tasks", sa.Column("contextId", sa.String(), nullable=True))
    op.add_column("a2a_tasks", sa.Column("assistant_id", sa.String(), nullable=True))
    op.add_column("a2a_tasks", sa.Column("user_id", sa.String(), nullable=True))
    op.create_index(op.f("ix_a2a_tasks_assistant_id"), "a2a_tasks", ["assistant_id"], unique=False)
    op.create_index(op.f("ix_a2a_tasks_user_id"), "a2a_tasks", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_a2a_tasks_user_id"), table_name="a2a_tasks")
    op.drop_index(op.f("ix_a2a_tasks_assistant_id"), table_name="a2a_tasks")
    op.drop_column("a2a_tasks", "user_id")
    op.drop_column("a2a_tasks", "assistant_id")
    op.drop_column("a2a_tasks", "contextId")
