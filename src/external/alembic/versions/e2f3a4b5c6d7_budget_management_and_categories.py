"""budget_management_and_categories

Revision ID: e2f3a4b5c6d7
Revises: b8e9f1a2c3d4
Create Date: 2026-04-13 12:00:00.000000

Combined migration covering:
  Phase 1 — project_spend_tracking cleanup (drop 4 legacy budget columns, add budget_category)
  Phase 2 — budgets table and user_budget_assignments join table
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, None] = "b8e9f1a2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Phase 2: budgets table
    # ------------------------------------------------------------------
    op.create_table(
        "budgets",
        sa.Column("budget_id", sa.VARCHAR(128), nullable=False),
        sa.Column("name", sa.VARCHAR(128), nullable=False),
        sa.Column("description", sa.VARCHAR(500), nullable=True),
        sa.Column("soft_budget", sa.Float(), nullable=False),
        sa.Column("max_budget", sa.Float(), nullable=False),
        sa.Column("budget_duration", sa.VARCHAR(16), nullable=False),
        sa.Column("budget_category", sa.VARCHAR(32), nullable=False),
        sa.Column("budget_reset_at", sa.VARCHAR(64), nullable=True),
        sa.Column("created_by", sa.VARCHAR(255), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("budget_id"),
        sa.UniqueConstraint("name", name="uix_budgets_name"),
    )
    op.create_index("ix_budgets_budget_category", "budgets", ["budget_category"])
    op.create_index("ix_budgets_created_by", "budgets", ["created_by"])

    # ------------------------------------------------------------------
    # Phase 2: user_budget_assignments join table
    # One row per (user, category); composite PK enforces one budget per category.
    # Users can have up to one budget per category (platform/cli/premium_models).
    # ------------------------------------------------------------------
    op.create_table(
        "user_budget_assignments",
        sa.Column("user_id", sa.VARCHAR(36), nullable=False),
        sa.Column("category", sa.VARCHAR(32), nullable=False),
        sa.Column("budget_id", sa.VARCHAR(128), nullable=False),
        sa.Column(
            "assigned_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("assigned_by", sa.VARCHAR(255), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["budget_id"],
            ["budgets.budget_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "category"),
    )
    op.create_index("ix_uba_budget_id", "user_budget_assignments", ["budget_id"])
    op.create_index("ix_uba_user_id", "user_budget_assignments", ["user_id"])

    # ------------------------------------------------------------------
    # Phase 1: project_spend_tracking column cleanup
    # Drop 4 legacy budget-metadata columns (values now come via JOIN on budgets).
    # Add budget_category for stable per-snapshot category tracking.
    # ------------------------------------------------------------------
    op.drop_column("project_spend_tracking", "soft_budget")
    op.drop_column("project_spend_tracking", "max_budget")
    op.drop_column("project_spend_tracking", "budget_duration")
    op.drop_column("project_spend_tracking", "budget_reset_at")

    op.add_column(
        "project_spend_tracking",
        sa.Column("budget_category", sa.VARCHAR(), nullable=True),
    )
    op.create_index(
        "ix_project_spend_tracking_budget_category",
        "project_spend_tracking",
        ["budget_category"],
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # Reverse Phase 1 cleanup
    # ------------------------------------------------------------------
    op.drop_index("ix_project_spend_tracking_budget_category", "project_spend_tracking")
    op.drop_column("project_spend_tracking", "budget_category")

    op.add_column("project_spend_tracking", sa.Column("budget_reset_at", sa.VARCHAR(64), nullable=True))
    op.add_column("project_spend_tracking", sa.Column("budget_duration", sa.VARCHAR(16), nullable=True))
    op.add_column("project_spend_tracking", sa.Column("max_budget", sa.Float(), nullable=True))
    op.add_column("project_spend_tracking", sa.Column("soft_budget", sa.Float(), nullable=True))

    # ------------------------------------------------------------------
    # Reverse Phase 2
    # ------------------------------------------------------------------
    op.drop_index("ix_uba_user_id", "user_budget_assignments")
    op.drop_index("ix_uba_budget_id", "user_budget_assignments")
    op.drop_table("user_budget_assignments")

    op.drop_index("ix_budgets_created_by", "budgets")
    op.drop_index("ix_budgets_budget_category", "budgets")
    op.drop_table("budgets")
