"""extend leaderboard snapshots for seasons

Revision ID: c7e9b4a1d2f3
Revises: b3a4c5d6e7f8
Create Date: 2026-04-05 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c7e9b4a1d2f3"
down_revision: Union[str, Sequence[str], None] = "b3a4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add seasonal snapshot columns for existing leaderboard tables."""
    op.execute("""
        ALTER TABLE leaderboard_snapshots
        ADD COLUMN IF NOT EXISTS snapshot_type VARCHAR NOT NULL DEFAULT 'rolling_live'
    """)
    op.execute("""
        ALTER TABLE leaderboard_snapshots
        ADD COLUMN IF NOT EXISTS season_key VARCHAR NULL
    """)
    op.execute("""
        ALTER TABLE leaderboard_snapshots
        ADD COLUMN IF NOT EXISTS period_label VARCHAR NULL
    """)
    op.execute("""
        ALTER TABLE leaderboard_snapshots
        ADD COLUMN IF NOT EXISTS is_final BOOLEAN NOT NULL DEFAULT FALSE
    """)
    op.execute("""
        ALTER TABLE leaderboard_snapshots
        ADD COLUMN IF NOT EXISTS source_run_type VARCHAR NOT NULL DEFAULT 'scheduled'
    """)
    op.execute("""
        ALTER TABLE leaderboard_snapshots
        ADD COLUMN IF NOT EXISTS comparison_snapshot_id VARCHAR NULL
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'leaderboard_snapshots_comparison_snapshot_id_fkey'
            ) THEN
                ALTER TABLE leaderboard_snapshots
                ADD CONSTRAINT leaderboard_snapshots_comparison_snapshot_id_fkey
                FOREIGN KEY (comparison_snapshot_id)
                REFERENCES leaderboard_snapshots(id);
            END IF;
        END $$;
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_leaderboard_snapshots_type
        ON leaderboard_snapshots (snapshot_type)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_leaderboard_snapshots_type_key
        ON leaderboard_snapshots (snapshot_type, season_key)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_leaderboard_snapshots_type_final_date
        ON leaderboard_snapshots (snapshot_type, is_final, date)
    """)


def downgrade() -> None:
    """Remove seasonal snapshot columns and indexes."""
    op.execute("DROP INDEX IF EXISTS idx_leaderboard_snapshots_type_final_date")
    op.execute("DROP INDEX IF EXISTS idx_leaderboard_snapshots_type_key")
    op.execute("DROP INDEX IF EXISTS idx_leaderboard_snapshots_type")
    op.execute("""
        ALTER TABLE leaderboard_snapshots
        DROP CONSTRAINT IF EXISTS leaderboard_snapshots_comparison_snapshot_id_fkey
    """)
    op.execute("""
        ALTER TABLE leaderboard_snapshots
        DROP COLUMN IF EXISTS comparison_snapshot_id
    """)
    op.execute("""
        ALTER TABLE leaderboard_snapshots
        DROP COLUMN IF EXISTS source_run_type
    """)
    op.execute("""
        ALTER TABLE leaderboard_snapshots
        DROP COLUMN IF EXISTS is_final
    """)
    op.execute("""
        ALTER TABLE leaderboard_snapshots
        DROP COLUMN IF EXISTS period_label
    """)
    op.execute("""
        ALTER TABLE leaderboard_snapshots
        DROP COLUMN IF EXISTS season_key
    """)
    op.execute("""
        ALTER TABLE leaderboard_snapshots
        DROP COLUMN IF EXISTS snapshot_type
    """)
