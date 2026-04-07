"""add leaderboard tables

Revision ID: b3a4c5d6e7f8
Revises: a7c9e1d2f4b6, d1e2f3a4b5c6, d225ff6f4239
Create Date: 2026-04-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b3a4c5d6e7f8"
down_revision: Union[str, Sequence[str]] = ("a7c9e1d2f4b6", "d1e2f3a4b5c6", "d225ff6f4239")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create leaderboard_snapshots and leaderboard_entries tables."""
    op.create_table(
        "leaderboard_snapshots",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("date", sa.DateTime(), nullable=True),
        sa.Column("update_date", sa.DateTime(), nullable=True),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("period_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("total_users", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
            server_default="running",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_leaderboard_snapshots_status",
        "leaderboard_snapshots",
        ["status"],
        unique=False,
    )

    op.create_table(
        "leaderboard_entries",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("date", sa.DateTime(), nullable=True),
        sa.Column("update_date", sa.DateTime(), nullable=True),
        sa.Column(
            "snapshot_id",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
        ),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("user_email", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("tier_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("tier_level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("usage_intent", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("dimensions", postgresql.JSONB(), nullable=True),
        sa.Column("summary_metrics", postgresql.JSONB(), nullable=True),
        sa.Column("projects", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["leaderboard_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_leaderboard_entries_snapshot_id",
        "leaderboard_entries",
        ["snapshot_id"],
        unique=False,
    )
    op.create_index(
        "idx_leaderboard_entries_user_id",
        "leaderboard_entries",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "idx_leaderboard_entries_snapshot_rank",
        "leaderboard_entries",
        ["snapshot_id", "rank"],
        unique=False,
    )


def downgrade() -> None:
    """Drop leaderboard tables."""
    op.drop_index("idx_leaderboard_entries_snapshot_rank", table_name="leaderboard_entries")
    op.drop_index("idx_leaderboard_entries_user_id", table_name="leaderboard_entries")
    op.drop_index("idx_leaderboard_entries_snapshot_id", table_name="leaderboard_entries")
    op.drop_table("leaderboard_entries")
    op.drop_index("idx_leaderboard_snapshots_status", table_name="leaderboard_snapshots")
    op.drop_table("leaderboard_snapshots")
