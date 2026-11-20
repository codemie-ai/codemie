"""add_project_and_actor_snapshot_to_activity_events

Revision ID: y2z3a4b5c6d7
Revises: 64825fe31931
Create Date: 2026-09-17 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "y2z3a4b5c6d7"
down_revision: Union[str, None] = "2394f0863c6a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add denormalized project/actor snapshot columns and a supporting index.

    Nullable, no FK, no backfill of pre-existing rows — project_name, actor_email,
    and actor_display_name are populated only on newly written activity events.
    """
    op.add_column("activity_events", sa.Column("project_name", sa.VARCHAR(255), nullable=True))
    op.add_column("activity_events", sa.Column("actor_email", sa.VARCHAR(255), nullable=True))
    op.add_column("activity_events", sa.Column("actor_display_name", sa.VARCHAR(255), nullable=True))
    op.create_index(
        "ix_activity_events_project_name_created_at",
        "activity_events",
        ["project_name", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_activity_events_project_name_created_at", table_name="activity_events")
    op.drop_column("activity_events", "actor_display_name")
    op.drop_column("activity_events", "actor_email")
    op.drop_column("activity_events", "project_name")
