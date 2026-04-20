"""Add budget_category to budget rows unique index

Revision ID: 011c8192193e
Revises: c6a7b8d9e0f1
Create Date: 2026-04-17 15:23:56.201666

Changes the budget rows partial unique index to include budget_category,
allowing the same (project_name, budget_id, spend_date) combination
across different categories (platform, cli, premium_models).

OLD: (project_name, budget_id, spend_date) WHERE spend_subject_type = 'budget'
NEW: (project_name, budget_id, budget_category, spend_date) WHERE spend_subject_type = 'budget'
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '011c8192193e'
down_revision: Union[str, None] = 'c6a7b8d9e0f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop old budget rows index and create new one with budget_category."""
    # Drop the old partial unique index on (project_name, budget_id, spend_date)
    op.drop_index(
        "uix_project_spend_tracking_budget_rows",
        table_name="project_spend_tracking",
    )

    # Create new partial unique index including budget_category
    op.create_index(
        "uix_project_spend_tracking_budget_rows",
        "project_spend_tracking",
        ["project_name", "budget_id", "budget_category", "spend_date"],
        unique=True,
        postgresql_where=sa.text("spend_subject_type = 'budget'"),
    )


def downgrade() -> None:
    """Restore old budget rows index without budget_category."""
    # Drop the new index
    op.drop_index(
        "uix_project_spend_tracking_budget_rows",
        table_name="project_spend_tracking",
    )

    # Restore the old partial unique index (may fail if data has duplicates)
    op.create_index(
        "uix_project_spend_tracking_budget_rows",
        "project_spend_tracking",
        ["project_name", "budget_id", "spend_date"],
        unique=True,
        postgresql_where=sa.text("spend_subject_type = 'budget'"),
    )
