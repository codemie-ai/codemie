"""add_entity_label_to_activity_events

Revision ID: 223b83f7458b
Revises: y2z3a4b5c6d7
Create Date: 2026-09-22 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "223b83f7458b"
down_revision: Union[str, None] = "y2z3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add a nullable entity_label column to activity_events.

    Nullable, no FK, no backfill of pre-existing rows — entity_label is
    populated only on newly written activity events.
    """
    op.add_column("activity_events", sa.Column("entity_label", sa.VARCHAR(255), nullable=True))


def downgrade() -> None:
    op.drop_column("activity_events", "entity_label")
