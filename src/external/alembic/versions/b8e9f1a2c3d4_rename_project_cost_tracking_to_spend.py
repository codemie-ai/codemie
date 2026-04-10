"""rename_project_cost_tracking_to_spend_tracking

Revision ID: b8e9f1a2c3d4
Revises: 7d4e5f6a8b9c
Create Date: 2026-04-09 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b8e9f1a2c3d4"
down_revision: Union[str, None] = "083aa0973f84"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename project_cost_tracking to project_spend_tracking and extend the schema.

    Steps:
    1. Rename table project_cost_tracking -> project_spend_tracking
    2. Rename associated indexes and constraints
    3. Drop NOT NULL constraint on key_hash
    4. Add new nullable columns: budget_id, soft_budget, max_budget, budget_duration, spend_subject_type
    5. Backfill spend_subject_type = 'key' for all existing rows
    6. Drop old unique constraint uix_project_cost_tracking_key_hash_spend_date
    7. Add check constraints for spend_subject_type values and key_hash/budget nullability rules
    8. Add partial unique indexes for key rows and budget rows
    """
    # 1. Rename table
    op.rename_table("project_cost_tracking", "project_spend_tracking")

    # 2. Rename indexes from old table name to new
    op.execute("ALTER INDEX ix_project_cost_tracking_project_name " "RENAME TO ix_project_spend_tracking_project_name")
    op.execute("ALTER INDEX ix_project_cost_tracking_key_hash " "RENAME TO ix_project_spend_tracking_key_hash")
    op.execute("ALTER INDEX ix_project_cost_tracking_spend_date " "RENAME TO ix_project_spend_tracking_spend_date")

    # 3. Drop NOT NULL constraint on key_hash
    op.alter_column(
        "project_spend_tracking",
        "key_hash",
        existing_type=sa.VARCHAR(),
        nullable=True,
    )

    # 4. Add new nullable columns
    op.add_column("project_spend_tracking", sa.Column("budget_id", sa.VARCHAR(), nullable=True))
    op.add_column("project_spend_tracking", sa.Column("soft_budget", sa.Numeric(18, 9), nullable=True))
    op.add_column("project_spend_tracking", sa.Column("max_budget", sa.Numeric(18, 9), nullable=True))
    op.add_column("project_spend_tracking", sa.Column("budget_duration", sa.VARCHAR(), nullable=True))
    op.add_column("project_spend_tracking", sa.Column("spend_subject_type", sa.VARCHAR(), nullable=True))

    # 5. Backfill spend_subject_type = 'key' for all existing rows
    op.execute("UPDATE project_spend_tracking SET spend_subject_type = 'key'")

    # 6. Drop old unique constraint
    op.drop_constraint(
        "uix_project_cost_tracking_key_hash_spend_date",
        "project_spend_tracking",
        type_="unique",
    )

    # 7. Add check constraints
    op.create_check_constraint(
        "ck_project_spend_tracking_subject_type_values",
        "project_spend_tracking",
        "spend_subject_type IN ('key', 'budget')",
    )
    op.create_check_constraint(
        "ck_project_spend_tracking_key_hash_shape",
        "project_spend_tracking",
        "(spend_subject_type = 'key' AND key_hash IS NOT NULL) OR "
        "(spend_subject_type = 'budget' AND key_hash IS NULL) OR "
        "spend_subject_type IS NULL",
    )

    # 8. Add partial unique indexes
    op.create_index(
        "uix_project_spend_tracking_key_rows",
        "project_spend_tracking",
        ["project_name", "key_hash", "spend_date"],
        unique=True,
        postgresql_where=sa.text("spend_subject_type = 'key'"),
    )
    op.create_index(
        "uix_project_spend_tracking_budget_rows",
        "project_spend_tracking",
        ["project_name", "budget_id", "spend_date"],
        unique=True,
        postgresql_where=sa.text("spend_subject_type = 'budget'"),
    )


def downgrade() -> None:
    """Reverse the rename and schema extension."""
    # Drop partial unique indexes
    op.drop_index("uix_project_spend_tracking_budget_rows", table_name="project_spend_tracking")
    op.drop_index("uix_project_spend_tracking_key_rows", table_name="project_spend_tracking")

    # Drop check constraints
    op.drop_constraint(
        "ck_project_spend_tracking_key_hash_shape",
        "project_spend_tracking",
        type_="check",
    )
    op.drop_constraint(
        "ck_project_spend_tracking_subject_type_values",
        "project_spend_tracking",
        type_="check",
    )

    # Restore old unique constraint (only valid if no budget rows exist)
    op.create_unique_constraint(
        "uix_project_cost_tracking_key_hash_spend_date",
        "project_spend_tracking",
        ["key_hash", "spend_date"],
    )

    # Drop new columns
    op.drop_column("project_spend_tracking", "spend_subject_type")
    op.drop_column("project_spend_tracking", "budget_duration")
    op.drop_column("project_spend_tracking", "max_budget")
    op.drop_column("project_spend_tracking", "soft_budget")
    op.drop_column("project_spend_tracking", "budget_id")

    # Restore NOT NULL on key_hash
    op.alter_column(
        "project_spend_tracking",
        "key_hash",
        existing_type=sa.VARCHAR(),
        nullable=False,
    )

    # Rename indexes back
    op.execute("ALTER INDEX ix_project_spend_tracking_spend_date " "RENAME TO ix_project_cost_tracking_spend_date")
    op.execute("ALTER INDEX ix_project_spend_tracking_key_hash " "RENAME TO ix_project_cost_tracking_key_hash")
    op.execute("ALTER INDEX ix_project_spend_tracking_project_name " "RENAME TO ix_project_cost_tracking_project_name")

    # Rename table back
    op.rename_table("project_spend_tracking", "project_cost_tracking")
