"""update_project_cost_tracking_for_snapshot_chargeback

Revision ID: a7c9e1d2f4b6
Revises: c9b887e959ba
Create Date: 2026-03-23 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a7c9e1d2f4b6"
down_revision: Union[str, None] = "c9b887e959ba"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade project_cost_tracking to reset-aware snapshot storage."""
    op.alter_column(
        "project_cost_tracking",
        "spend_date",
        existing_type=sa.Date(),
        type_=sa.TIMESTAMP(timezone=True),
        existing_nullable=False,
        postgresql_using="spend_date::timestamp AT TIME ZONE 'UTC'",
    )
    op.add_column(
        "project_cost_tracking",
        sa.Column("budget_period_spend", sa.Numeric(18, 9), nullable=True),
    )
    op.add_column(
        "project_cost_tracking",
        sa.Column("budget_reset_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute("UPDATE project_cost_tracking SET budget_period_spend = cumulative_spend")
    op.alter_column(
        "project_cost_tracking",
        "budget_period_spend",
        existing_type=sa.Numeric(18, 9),
        nullable=False,
    )


def downgrade() -> None:
    """Downgrade project_cost_tracking to the original daily schema."""
    op.drop_column("project_cost_tracking", "budget_reset_at")
    op.drop_column("project_cost_tracking", "budget_period_spend")
    op.alter_column(
        "project_cost_tracking",
        "spend_date",
        existing_type=sa.TIMESTAMP(timezone=True),
        type_=sa.Date(),
        existing_nullable=False,
        postgresql_using="spend_date::date",
    )
